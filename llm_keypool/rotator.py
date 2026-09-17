"""Key rotation with persistent cursor, reservations, and FREE_ONLY filtering."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from .key_store import KeyStore
from .providers.headers import extract_cooldown, parse_retry_after
from .settings import free_only, allow_paid_fallback, rotate_every_default


def _next_utc_midnight() -> str:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()


def _next_first_of_month() -> str:
    now = datetime.now(timezone.utc)
    month = now.month + 1
    year = now.year + (1 if month > 12 else 0)
    month = 1 if month > 12 else month
    return now.replace(
        year=year, month=month, day=1,
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()


def _rolling(seconds: int) -> Callable[[], str]:
    def _inner() -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return _inner


_FALLBACK_STRATEGIES = {
    "daily_utc_midnight": _next_utc_midnight,
    "first_of_calendar_month": _next_first_of_month,
    "rolling_60": _rolling(60),
    "rolling_65": _rolling(65),
    "rolling_120": _rolling(120),
}
_DEFAULT_FALLBACK = _rolling(60)


def _fallback_from_config(cfg: dict) -> Callable[[], str]:
    key = cfg.get("cooldown_fallback", {}).get("strategy", "rolling_60")
    return _FALLBACK_STRATEGIES.get(key, _DEFAULT_FALLBACK)


def _score_key(key: dict, cfg: dict) -> float:
    rpd = cfg.get("limits", {}).get("rpd")
    return float(rpd - key["requests_today"]) if rpd else float(-key["requests_today"])


def _resolve_model(cfg: dict, cap_key: str) -> str:
    models = cfg.get("models", {})
    if isinstance(models, list):
        return models[0] if models else ""
    if isinstance(models, dict):
        cat_models = models.get(cap_key, [])
        return cat_models[0] if cat_models else ""
    return cfg.get("default_model", "")


def _cap_key(capabilities: list[str]) -> str:
    return ",".join(sorted(capabilities))


class Rotator:
    def __init__(
        self,
        store: KeyStore,
        provider_configs: dict,
        rotate_every: int | None = None,
    ):
        self.store = store
        self.configs = provider_configs
        self.rotate_every = rotate_every if rotate_every is not None else rotate_every_default()

        self._order: dict[str, list[int]] = {}
        self._cursor: dict[str, int] = {}
        self._slot_count: dict[int, int] = {}
        self._loaded_cap_keys: set[str] = set()
        self._key_last_cap_key: dict[int, str] = {}
        # blocked scopes within a single request (never retry same blocked scope)
        self._request_blocked_scopes: set[str] = set()

    def begin_request(self):
        """Reset per-request blocked-scope tracking."""
        self._request_blocked_scopes.clear()

    def _load_state(self, ck: str):
        if ck in self._loaded_cap_keys:
            return
        cursor, slot_counts = self.store.load_rotation_state(ck)
        self._cursor[ck] = cursor
        self._slot_count.update(slot_counts)
        self._loaded_cap_keys.add(ck)

    def _persist_state(self, ck: str):
        self.store.save_rotation_state(
            ck,
            self._cursor.get(ck, 0),
            self._slot_count,
        )

    def _cost_filter(self) -> str | None:
        if free_only() and not allow_paid_fallback():
            return "free"
        return None

    def _is_free_provider(self, provider: str) -> bool:
        return self.configs.get(provider, {}).get("free_tier") is True

    def _is_free_provider(self, provider: str) -> bool:
        """True only when configs mark free_tier=True; missing free_tier => False."""
        return self.configs.get(provider, {}).get("free_tier") is True

    def _is_free_key(self, key: dict) -> bool:
        """Provider must be free_tier; key cost_tier paid/unknown still blocked."""
        tier = (key.get("cost_tier_resolved") or key.get("cost_tier") or "free").lower().strip()
        if tier in ("paid", "unknown"):
            return False
        return self._is_free_provider(key.get("provider", ""))

    def _filter_by_free_policy(self, keys: list[dict]) -> list[dict]:
        """FREE_ONLY / ALLOW_PAID_FALLBACK selection.

        - free_only + !allow_paid_fallback: only free_tier providers (unknown blocked)
        - free_only + allow_paid_fallback: prefer free; paid only if no free active
        - !free_only: unchanged
        """
        if not free_only():
            return keys
        free_keys = [k for k in keys if self._is_free_key(k)]
        if not allow_paid_fallback():
            return free_keys
        return free_keys if free_keys else [
            k for k in keys if not self._is_free_key(k)
        ]


    def _ensure_order(self, ck: str, capabilities: list[str], active_ids: set[int]):
        self._load_state(ck)
        current = self._order.get(ck, [])
        if set(current) == active_ids and current:
            return
        all_keys = self.store.get_all_keys()
        candidates = [
            k for k in all_keys
            if k["is_active"] and any(
                c in self.store.parse_capabilities(k) for c in capabilities
            )
        ]
        ordered = sorted(
            candidates,
            key=lambda k: (
                -_score_key(k, self.configs.get(k["provider"], {})),
                k["id"],
            ),
        )
        # Keep only currently active ids, preserve deterministic order
        ordered_ids = [k["id"] for k in ordered if k["id"] in active_ids]
        # Append any active ids missing from candidates
        for i in sorted(active_ids):
            if i not in ordered_ids:
                ordered_ids.append(i)
        self._order[ck] = ordered_ids
        if ck not in self._cursor:
            self._cursor[ck] = 0
        else:
            self._cursor[ck] %= max(len(self._order[ck]), 1)
        for kid in ordered_ids:
            self._slot_count.setdefault(kid, 0)

    def get_best_key(
        self,
        capabilities: list[str] | str,
        subscriber_id: str = "unknown",
        *,
        model: str | None = None,
        require_tags: list[str] | None = None,
        tags_mode: str = "any",
        caps_match: str = "any",
        reserve: bool = False,
        request_id: str | None = None,
    ) -> Optional[dict]:
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        ck = _cap_key(capabilities)

        active = self.store.get_active_keys(
            capabilities,
            match=caps_match,
            cost_filter=self._cost_filter(),
            provider_configs=self.configs,
            require_tags=require_tags,
            tags_mode=tags_mode,
        )
        # Filter out blocked scopes for this request
        active = [
            k for k in active
            if (k.get("quota_scope") or f"id:{k['id']}") not in self._request_blocked_scopes
        ]
        active = self._filter_by_free_policy(active)
        # Optional model filter: key must support model if key has explicit model
        if model:
            filtered = []
            for k in active:
                cfg = self.configs.get(k["provider"], {})
                key_model = k.get("model")
                models = cfg.get("models", [])
                if isinstance(models, dict):
                    models = [m for ms in models.values() for m in ms]
                default = cfg.get("default_model")
                allowed = set(models) if isinstance(models, list) else set()
                if default:
                    allowed.add(default)
                if key_model:
                    if key_model == model or model in allowed:
                        filtered.append(k)
                elif not allowed or model in allowed or model in ("any", "keypool-auto"):
                    filtered.append(k)
                else:
                    # no silent swap: skip if model not in allowed set
                    filtered.append(k) if not allowed else None
                    if allowed and model in allowed:
                        filtered.append(k)
            # rebuild cleanly
            filtered = []
            for k in active:
                cfg = self.configs.get(k["provider"], {})
                models = cfg.get("models", [])
                if isinstance(models, dict):
                    models = [m for ms in models.values() for m in ms]
                allowed = set(models) if isinstance(models, list) else set()
                default = cfg.get("default_model")
                if default:
                    allowed.add(default)
                key_model = k.get("model")
                if model in ("any", "keypool-auto"):
                    filtered.append(k)
                elif key_model and key_model == model:
                    filtered.append(k)
                elif not key_model and (not allowed or model in allowed):
                    filtered.append(k)
                elif model in allowed:
                    filtered.append(k)
            active = filtered

        if not active:
            return None

        active_map = {k["id"]: k for k in active}
        self._ensure_order(ck, capabilities, set(active_map.keys()))

        order = [kid for kid in self._order[ck] if kid in active_map]
        if not order:
            order = sorted(active_map.keys())
            self._order[ck] = order

        cursor = self._cursor.get(ck, 0) % len(order)

        reset_done = False
        chosen_id = None
        for _ in range(len(order) + 1):
            key_id = order[cursor % len(order)]
            if key_id in active_map and self._slot_count.get(key_id, 0) < self.rotate_every:
                chosen_id = key_id
                break
            cursor = (cursor + 1) % len(order)
            if not reset_done and cursor == self._cursor.get(ck, 0) % len(order):
                for kid in order:
                    self._slot_count[kid] = 0
                reset_done = True
        if chosen_id is None:
            return None

        reservation_id = None
        if reserve:
            reservation_id = self.store.reserve_key(
                chosen_id, ttl_seconds=90, request_id=request_id
            )
            if reservation_id is None:
                # concurrency conflict — try next keys
                for offset in range(1, len(order)):
                    alt = order[(cursor + offset) % len(order)]
                    if alt not in active_map:
                        continue
                    reservation_id = self.store.reserve_key(
                        alt, ttl_seconds=90, request_id=request_id
                    )
                    if reservation_id:
                        chosen_id = alt
                        cursor = (cursor + offset) % len(order)
                        break
                if reservation_id is None:
                    return None

        self._cursor[ck] = cursor
        best = active_map[chosen_id]
        cfg = self.configs.get(best["provider"], {})
        extra = json.loads(best["extra_params"] or "{}")

        base_url = cfg.get("base_url", "")
        if "{account_id}" in base_url:
            base_url = base_url.format(account_id=extra.get("account_id", ""))

        # Slot consumed only when attempt is dispatched (caller calls mark_dispatched)
        slot_pos = self._slot_count.get(best["id"], 0) + 1
        self._key_last_cap_key[best["id"]] = ck

        resolved_model = best["model"] or _resolve_model(cfg, ck)
        if model and model not in ("any", "keypool-auto"):
            # Prefer requested model when allowed — no silent swap away from request
            resolved_model = model

        return {
            "key_id": best["id"],
            "provider": best["provider"],
            "api_key": best["api_key"],
            "base_url": base_url,
            "model": resolved_model,
            "capabilities": self.store.parse_capabilities(best),
            "cap_key": ck,
            "subscriber_id": subscriber_id,
            "openai_compatible": cfg.get("openai_compatible", True),
            "extra_params": extra,
            "requests_today": best["requests_today"],
            "tokens_used_today": best["tokens_used_today"],
            "cycle_position": slot_pos,
            "rotate_every": self.rotate_every,
            "reservation_id": reservation_id,
            "quota_scope": best.get("quota_scope"),
            "cost_tier": best.get("cost_tier_resolved") or best.get("cost_tier") or "free",
        }

    def mark_dispatched(self, key_data: dict):
        """Note a dispatched attempt; slot counted in handle_success/handle_429."""
        ck = key_data.get("cap_key") or self._key_last_cap_key.get(key_data["key_id"], "")
        if ck:
            self._persist_state(ck)

    def release(self, key_data: dict | None):
        if key_data and key_data.get("reservation_id"):
            self.store.release_reservation(key_data["reservation_id"])

    def block_scope_for_request(self, scope: str | None, key_id: int | None = None):
        if scope:
            self._request_blocked_scopes.add(scope)
        elif key_id is not None:
            self._request_blocked_scopes.add(f"id:{key_id}")

    def peek_current_key(self, capabilities: list[str] | str) -> Optional[dict]:
        """Return the key that would be selected next without mutating state / reserving."""
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        ck = _cap_key(capabilities)
        active = self.store.get_active_keys(
            capabilities,
            cost_filter=self._cost_filter(),
            provider_configs=self.configs,
        )
        active = self._filter_by_free_policy(active)
        if not active:
            return None

        active_map = {k["id"]: k for k in active}
        self._ensure_order(ck, capabilities, set(active_map.keys()))

        order = [kid for kid in self._order[ck] if kid in active_map] or list(active_map.keys())
        cursor = self._cursor.get(ck, 0) % len(order)
        slot_count = dict(self._slot_count)

        for _ in range(len(order) + 1):
            key_id = order[cursor % len(order)]
            if key_id in active_map and slot_count.get(key_id, 0) < self.rotate_every:
                break
            cursor = (cursor + 1) % len(order)
        else:
            return None

        best = active_map[order[cursor % len(order)]]
        cfg = self.configs.get(best["provider"], {})
        slot_pos = slot_count.get(best["id"], 0) + 1
        return {
            "key_id": best["id"],
            "provider": best["provider"],
            "model": best["model"] or _resolve_model(cfg, ck),
            "capabilities": self.store.parse_capabilities(best),
            "requests_today": best["requests_today"],
            "tokens_used_today": best["tokens_used_today"],
            "cooldown_until": best.get("cooldown_until"),
            "cycle_position": slot_pos,
            "rotate_every": self.rotate_every,
        }

    def handle_429(
        self,
        key_id: int,
        provider: str,
        headers: dict | None = None,
        subscriber_id: str = "unknown",
        model: str = "",
        quota_scope: str | None = None,
    ) -> str:
        headers = headers or {}
        cooldown = extract_cooldown(provider, headers, was_429=True)
        if cooldown is None:
            # Prefer Retry-After seconds or HTTP-date
            ra = parse_retry_after(headers.get("retry-after"))
            if ra:
                cooldown = ra
            else:
                cfg = self.configs.get(provider, {})
                cooldown = _fallback_from_config(cfg)()
        self.store.record_usage(key_id, tokens=0, was_429=True, cooldown_until=cooldown)
        self._slot_count[key_id] = self._slot_count.get(key_id, 0) + 1
        # dispatched attempt already counted via mark_dispatched
        ck = self._key_last_cap_key.get(key_id, "")
        if ck:
            self._persist_state(ck)
        self.block_scope_for_request(quota_scope, key_id)
        self.store.log_audit(
            subscriber_id=subscriber_id,
            key_id=key_id,
            provider=provider,
            model=model,
            success=False,
            error="429 rate limit",
            event="rate_limit",
        )
        return cooldown

    def handle_success(
        self,
        key_id: int,
        tokens_used: int,
        headers: dict | None = None,
        provider: str = "",
        tokens_in: int = 0,
        latency_ms: int = 0,
        subscriber_id: str = "unknown",
        model: str = "",
    ):
        headers = headers or {}
        cooldown = extract_cooldown(provider, headers, was_429=False) if provider else None
        self.store.record_usage(
            key_id, tokens=tokens_used, was_429=False, cooldown_until=cooldown
        )
        self._slot_count[key_id] = self._slot_count.get(key_id, 0) + 1
        ck = self._key_last_cap_key.get(key_id, "")
        if ck:
            self._persist_state(ck)
        self.store.log_audit(
            subscriber_id=subscriber_id,
            key_id=key_id,
            provider=provider,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_used,
            latency_ms=latency_ms,
            success=True,
            event="success",
        )

    def get_earliest_retry(self, capabilities: list[str] | str) -> Optional[str]:
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        all_keys = self.store.get_all_keys()
        cooldowns = [
            k["cooldown_until"] for k in all_keys
            if k["is_active"] and k["cooldown_until"]
            and any(c in self.store.parse_capabilities(k) for c in capabilities)
        ]
        return min(cooldowns) if cooldowns else None
