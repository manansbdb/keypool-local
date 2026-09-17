"""SQLite key store with authenticated encryption, WAL, and versioned migrations."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .crypto import (
    decrypt_secret,
    encrypt_secret,
    key_fingerprint,
    looks_encrypted,
    mask_secret,
)
from .settings import free_only as settings_free_only

_NEW_DB_DEFAULT = Path.home() / ".llm-keypool" / "keys.db"
_OLD_DB_DEFAULT = Path.home() / ".llm-aggregator" / "keys.db"

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    api_key TEXT NOT NULL,
    key_fingerprint TEXT,
    capabilities TEXT NOT NULL DEFAULT '["general_purpose"]',
    model TEXT,
    extra_params TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    cost_tier TEXT NOT NULL DEFAULT 'free',
    quota_scope TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    tokens_used_today INTEGER NOT NULL DEFAULT 0,
    tokens_used_month INTEGER NOT NULL DEFAULT 0,
    requests_today INTEGER NOT NULL DEFAULT 0,
    requests_month INTEGER NOT NULL DEFAULT 0,
    last_429_at TEXT,
    cooldown_until TEXT,
    daily_reset_date TEXT,
    monthly_reset_month TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    UNIQUE(provider, key_fingerprint)
);

CREATE TABLE IF NOT EXISTS rotation_state (
    cap_key TEXT PRIMARY KEY,
    cursor INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rotation_slot_counts (
    key_id INTEGER NOT NULL PRIMARY KEY,
    slot_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    subscriber_id TEXT NOT NULL DEFAULT 'unknown',
    key_id        INTEGER,
    provider      TEXT,
    model         TEXT,
    tokens_in     INTEGER DEFAULT 0,
    tokens_out    INTEGER DEFAULT 0,
    latency_ms    INTEGER DEFAULT 0,
    success       INTEGER DEFAULT 1,
    error         TEXT,
    event         TEXT
);

CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY,
    key_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    request_id TEXT
);

CREATE TABLE IF NOT EXISTS quota_scopes (
    scope TEXT PRIMARY KEY,
    tokens_used_today INTEGER NOT NULL DEFAULT 0,
    tokens_used_month INTEGER NOT NULL DEFAULT 0,
    requests_today INTEGER NOT NULL DEFAULT 0,
    requests_month INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    daily_reset_date TEXT,
    monthly_reset_month TEXT
);

CREATE INDEX IF NOT EXISTS idx_reservations_key ON reservations(key_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_fp ON api_keys(provider, key_fingerprint);
"""

MIGRATIONS_V1 = [
    "ALTER TABLE api_keys ADD COLUMN model TEXT",
    "ALTER TABLE api_keys ADD COLUMN capabilities TEXT",
    "ALTER TABLE rotation_state RENAME COLUMN category TO cap_key",
    (
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT NOT NULL, "
        "subscriber_id TEXT NOT NULL DEFAULT 'unknown', "
        "key_id INTEGER, "
        "provider TEXT, "
        "model TEXT, "
        "tokens_in INTEGER DEFAULT 0, "
        "tokens_out INTEGER DEFAULT 0, "
        "latency_ms INTEGER DEFAULT 0, "
        "success INTEGER DEFAULT 1, "
        "error TEXT)"
    ),
]

MIGRATIONS_V2 = [
    "ALTER TABLE api_keys ADD COLUMN key_fingerprint TEXT",
    "ALTER TABLE api_keys ADD COLUMN cost_tier TEXT NOT NULL DEFAULT 'free'",
    "ALTER TABLE api_keys ADD COLUMN quota_scope TEXT",
    "ALTER TABLE api_keys ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE audit_log ADD COLUMN event TEXT",
    (
        "CREATE TABLE IF NOT EXISTS reservations ("
        "id TEXT PRIMARY KEY, "
        "key_id INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, "
        "request_id TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS quota_scopes ("
        "scope TEXT PRIMARY KEY, "
        "tokens_used_today INTEGER NOT NULL DEFAULT 0, "
        "tokens_used_month INTEGER NOT NULL DEFAULT 0, "
        "requests_today INTEGER NOT NULL DEFAULT 0, "
        "requests_month INTEGER NOT NULL DEFAULT 0, "
        "cooldown_until TEXT, "
        "daily_reset_date TEXT, "
        "monthly_reset_month TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS schema_meta ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL)"
    ),
]

_MIGRATE_CAPABILITIES = (
    "UPDATE api_keys SET capabilities = json_array(category) "
    "WHERE capabilities IS NULL AND category IS NOT NULL"
)


def _resolve_db_path() -> Path:
    env = os.environ.get("LLM_KEYPOOL_DB") or os.environ.get("LLM_AGGREGATOR_DB")
    if env:
        return Path(env)
    if not _NEW_DB_DEFAULT.exists() and _OLD_DB_DEFAULT.exists():
        _NEW_DB_DEFAULT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_OLD_DB_DEFAULT, _NEW_DB_DEFAULT)
    return _NEW_DB_DEFAULT


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class KeyStore:
    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _resolve_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            for migration in MIGRATIONS_V1 + MIGRATIONS_V2:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute(_MIGRATE_CAPABILITIES)
            except sqlite3.OperationalError:
                pass
            self._ensure_fingerprints_and_encrypt(conn)
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def _backup_db(self) -> Path | None:
        if not self._db_path.exists():
            return None
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        backup = self._db_path.with_suffix(f".bak.{stamp}")
        shutil.copy2(self._db_path, backup)
        return backup

    def _ensure_fingerprints_and_encrypt(self, conn: sqlite3.Connection):
        """Migrate plaintext api_key rows → encrypted + fingerprint (with backup once)."""
        try:
            rows = conn.execute(
                "SELECT id, api_key, key_fingerprint FROM api_keys"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        needs = []
        for r in rows:
            raw = r["api_key"] or ""
            fp = r["key_fingerprint"]
            if not looks_encrypted(raw) or not fp:
                needs.append(r)
        if not needs:
            return
        # Backup before mutating secrets
        try:
            self._backup_db()
        except OSError:
            pass
        for r in needs:
            raw = r["api_key"] or ""
            if looks_encrypted(raw):
                try:
                    plain = decrypt_secret(raw)
                except ValueError:
                    continue
                enc = raw
            else:
                plain = raw
                enc = encrypt_secret(plain)
            fp = key_fingerprint(plain)
            try:
                conn.execute(
                    "UPDATE api_keys SET api_key = ?, key_fingerprint = ? WHERE id = ?",
                    (enc, fp, r["id"]),
                )
            except sqlite3.IntegrityError:
                # fingerprint collision — keep existing unique row
                conn.execute(
                    "UPDATE api_keys SET api_key = ? WHERE id = ?",
                    (enc, r["id"]),
                )

    # --- row helpers ---

    def _decrypt_row(self, row: dict, *, reveal: bool = True) -> dict:
        out = dict(row)
        raw = out.get("api_key") or ""
        if looks_encrypted(raw):
            try:
                plain = decrypt_secret(raw)
            except ValueError:
                plain = ""
                out["_decrypt_error"] = True
        else:
            plain = raw
        if reveal:
            out["api_key"] = plain
        else:
            out["api_key"] = mask_secret(plain)
        out["api_key_masked"] = mask_secret(plain)
        if not out.get("key_fingerprint") and plain:
            out["key_fingerprint"] = key_fingerprint(plain)
        return out

    @staticmethod
    def parse_capabilities(row: dict) -> list[str]:
        caps = row.get("capabilities")
        if caps:
            try:
                parsed = json.loads(caps)
                if isinstance(parsed, list) and parsed:
                    return [str(c) for c in parsed]
            except (json.JSONDecodeError, TypeError):
                return [str(caps)]
        cat = row.get("category", "general_purpose")
        return [cat] if cat else ["general_purpose"]

    @staticmethod
    def parse_tags(row: dict) -> list[str]:
        tags = row.get("tags")
        if not tags:
            return []
        try:
            parsed = json.loads(tags)
            if isinstance(parsed, list):
                return [str(t) for t in parsed]
        except (json.JSONDecodeError, TypeError):
            return [str(tags)]
        return []

    def cost_tier_of(self, row: dict, provider_configs: dict | None = None) -> str:
        tier = (row.get("cost_tier") or "").lower().strip()
        if tier in ("free", "paid", "unknown"):
            if tier != "unknown":
                return tier
        if provider_configs:
            cfg = provider_configs.get(row.get("provider", ""), {})
            if cfg.get("free_tier") is True:
                return "free"
            if cfg.get("free_tier") is False:
                return "paid"
        return tier or "unknown"

    # --- key management ---

    def register_key(
        self,
        provider: str,
        api_key: str,
        capabilities: list[str] | str | None = None,
        model: Optional[str] = None,
        extra_params: dict | None = None,
        category: str | None = None,
        cost_tier: str | None = None,
        quota_scope: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        if capabilities is None:
            capabilities = [category] if category else ["general_purpose"]
        elif isinstance(capabilities, str):
            capabilities = [capabilities]

        tier = (cost_tier or "free").lower().strip()
        if tier not in ("free", "paid", "unknown"):
            tier = "unknown"

        # FREE_ONLY: block registering unknown/paid into free pool unless allowed
        if settings_free_only() and tier != "free":
            from .settings import allow_paid_fallback
            if not allow_paid_fallback():
                return {
                    "success": False,
                    "message": (
                        f"cost_tier={tier} blocked (FREE_ONLY=true, "
                        "ALLOW_PAID_FALLBACK=false). Set cost_tier=free or "
                        "LLM_KEYPOOL_ALLOW_PAID_FALLBACK=true."
                    ),
                }

        fp = key_fingerprint(api_key)
        enc = encrypt_secret(api_key)
        scope = quota_scope or f"{provider}:{fp[:12]}"

        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO api_keys
                       (provider, api_key, key_fingerprint, capabilities, model,
                        extra_params, cost_tier, quota_scope, tags)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        provider,
                        enc,
                        fp,
                        json.dumps(capabilities),
                        model or None,
                        json.dumps(extra_params or {}),
                        tier,
                        scope,
                        json.dumps(tags or []),
                    ),
                )
                caps_str = ", ".join(capabilities)
                return {
                    "success": True,
                    "message": (
                        f"Key registered for {provider} ({caps_str}) "
                        f"model={model or 'default'} cost_tier={tier}"
                    ),
                }
            except sqlite3.IntegrityError:
                return {
                    "success": False,
                    "message": f"Key already registered for {provider}. Deactivate existing key first.",
                }

    def get_active_keys(
        self,
        capabilities: list[str] | str,
        *,
        match: str = "any",
        cost_filter: str | None = None,
        provider_configs: dict | None = None,
        require_tags: list[str] | None = None,
        tags_mode: str = "any",
    ) -> list[dict]:
        """Return active, non-cooled-down keys matching capabilities.

        match: 'any' (default) — key has ANY requested capability
               'all' — key has ALL requested capabilities (technical filter)
        cost_filter: 'free' | 'paid' | None
        tags_mode: 'any' vs 'all' for require_tags
        """
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        now = _now_iso()
        with self._conn() as conn:
            self._expire_reservations(conn, now)
            reserved = {
                r["key_id"]
                for r in conn.execute(
                    "SELECT key_id FROM reservations WHERE expires_at > ?", (now,)
                ).fetchall()
            }
            rows = conn.execute(
                """SELECT * FROM api_keys
                   WHERE is_active = 1
                     AND (cooldown_until IS NULL OR cooldown_until < ?)
                   ORDER BY id ASC""",
                (now,),
            ).fetchall()

        result = []
        for r in rows:
            if r["id"] in reserved:
                continue
            row = self._decrypt_row(dict(r), reveal=True)
            # shared quota_scope cooldown
            scope = row.get("quota_scope")
            if scope and self._scope_in_cooldown(scope, now):
                continue
            key_caps = self.parse_capabilities(row)
            if match == "all":
                if not all(c in key_caps for c in capabilities):
                    continue
            else:
                if not any(c in key_caps for c in capabilities):
                    continue
            if require_tags:
                key_tags = self.parse_tags(row)
                if tags_mode == "all":
                    if not all(t in key_tags for t in require_tags):
                        continue
                else:
                    if not any(t in key_tags for t in require_tags):
                        continue
            tier = self.cost_tier_of(row, provider_configs)
            if cost_filter and tier != cost_filter:
                continue
            if settings_free_only() and cost_filter is None:
                from .settings import allow_paid_fallback
                if tier != "free" and not allow_paid_fallback():
                    continue
            row["cost_tier_resolved"] = tier
            result.append(row)
        return result

    def _scope_in_cooldown(self, scope: str, now: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM quota_scopes WHERE scope = ?", (scope,)
            ).fetchone()
        if not row or not row["cooldown_until"]:
            return False
        return row["cooldown_until"] > now

    def get_all_keys(self, *, reveal: bool = True) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY provider, id").fetchall()
            return [self._decrypt_row(dict(r), reveal=reveal) for r in rows]

    def get_key_by_id(self, key_id: int, *, reveal: bool = True) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            return self._decrypt_row(dict(row), reveal=reveal) if row else None

    def record_usage(
        self,
        key_id: int,
        tokens: int,
        was_429: bool,
        cooldown_until: Optional[str] = None,
    ):
        now = _now_iso()
        today = _now().strftime("%Y-%m-%d")
        month = _now().strftime("%Y-%m")

        with self._conn() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            if not row:
                return
            row = dict(row)

            tokens_today = row["tokens_used_today"] if row["daily_reset_date"] == today else 0
            requests_today = row["requests_today"] if row["daily_reset_date"] == today else 0
            tokens_month = row["tokens_used_month"] if row["monthly_reset_month"] == month else 0
            requests_month = row["requests_month"] if row["monthly_reset_month"] == month else 0

            conn.execute(
                """UPDATE api_keys SET
                    tokens_used_today   = ?,
                    tokens_used_month   = ?,
                    requests_today      = ?,
                    requests_month      = ?,
                    last_used_at        = ?,
                    last_429_at         = CASE WHEN ? THEN ? ELSE last_429_at END,
                    cooldown_until      = ?,
                    daily_reset_date    = ?,
                    monthly_reset_month = ?
                WHERE id = ?""",
                (
                    tokens_today + tokens, tokens_month + tokens,
                    requests_today + 1, requests_month + 1,
                    now,
                    was_429, now if was_429 else None,
                    cooldown_until,
                    today, month,
                    key_id,
                ),
            )
            scope = row.get("quota_scope")
            if scope:
                self._bump_quota_scope(
                    conn, scope, tokens, was_429, cooldown_until, today, month, now
                )

    def _bump_quota_scope(
        self, conn, scope, tokens, was_429, cooldown_until, today, month, now
    ):
        srow = conn.execute(
            "SELECT * FROM quota_scopes WHERE scope = ?", (scope,)
        ).fetchone()
        if srow:
            srow = dict(srow)
            tokens_today = srow["tokens_used_today"] if srow["daily_reset_date"] == today else 0
            requests_today = srow["requests_today"] if srow["daily_reset_date"] == today else 0
            tokens_month = srow["tokens_used_month"] if srow["monthly_reset_month"] == month else 0
            requests_month = srow["requests_month"] if srow["monthly_reset_month"] == month else 0
            cd = cooldown_until if was_429 or cooldown_until else srow.get("cooldown_until")
            conn.execute(
                """UPDATE quota_scopes SET
                    tokens_used_today=?, tokens_used_month=?,
                    requests_today=?, requests_month=?,
                    cooldown_until=?, daily_reset_date=?, monthly_reset_month=?
                   WHERE scope=?""",
                (
                    tokens_today + tokens, tokens_month + tokens,
                    requests_today + 1, requests_month + 1,
                    cd, today, month, scope,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO quota_scopes
                   (scope, tokens_used_today, tokens_used_month, requests_today,
                    requests_month, cooldown_until, daily_reset_date, monthly_reset_month)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope, tokens, tokens, 1, 1,
                    cooldown_until if was_429 else None,
                    today, month,
                ),
            )

    def update_key(
        self,
        key_id: int,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        cost_tier: Optional[str] = None,
        quota_scope: Optional[str] = None,
    ) -> bool:
        updates = []
        params: list = []
        if model is not None:
            updates.append("model = ?")
            params.append(model or None)
        if api_key is not None:
            updates.append("api_key = ?")
            params.append(encrypt_secret(api_key))
            updates.append("key_fingerprint = ?")
            params.append(key_fingerprint(api_key))
        if cost_tier is not None:
            updates.append("cost_tier = ?")
            params.append(cost_tier)
        if quota_scope is not None:
            updates.append("quota_scope = ?")
            params.append(quota_scope)
        if not updates:
            return False
        params.append(key_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE api_keys SET {', '.join(updates)} WHERE id = ?", params)
        return True

    def deactivate_key(self, key_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))

    def clear_cooldown(self, key_id: int, *, confirmed: bool = True, actor: str = "cli"):
        if not confirmed:
            raise PermissionError("clear-cooldown requires confirmation")
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            conn.execute("UPDATE api_keys SET cooldown_until = NULL WHERE id = ?", (key_id,))
            if row and row["quota_scope"]:
                conn.execute(
                    "UPDATE quota_scopes SET cooldown_until = NULL WHERE scope = ?",
                    (row["quota_scope"],),
                )
            conn.execute(
                """INSERT INTO audit_log
                   (ts, subscriber_id, key_id, provider, model, success, error, event)
                   VALUES (?, ?, ?, ?, ?, 1, NULL, ?)""",
                (
                    _now_iso(),
                    actor,
                    key_id,
                    row["provider"] if row else None,
                    row["model"] if row else None,
                    "clear_cooldown",
                ),
            )

    # --- reservations (atomic selection) ---

    def _expire_reservations(self, conn: sqlite3.Connection, now: str | None = None):
        now = now or _now_iso()
        conn.execute("DELETE FROM reservations WHERE expires_at <= ?", (now,))

    def reserve_key(
        self,
        key_id: int,
        *,
        ttl_seconds: float = 60.0,
        request_id: str | None = None,
    ) -> str | None:
        """Atomically reserve a key. Returns reservation id or None if unavailable."""
        now = _now()
        now_iso = now.isoformat()
        exp = (now + timedelta(seconds=ttl_seconds)).isoformat()
        rid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_reservations(conn, now_iso)
            row = conn.execute(
                """SELECT id, is_active, cooldown_until FROM api_keys WHERE id = ?""",
                (key_id,),
            ).fetchone()
            if not row or not row["is_active"]:
                return None
            if row["cooldown_until"] and row["cooldown_until"] > now_iso:
                return None
            taken = conn.execute(
                "SELECT 1 FROM reservations WHERE key_id = ? AND expires_at > ?",
                (key_id, now_iso),
            ).fetchone()
            if taken:
                return None
            conn.execute(
                "INSERT INTO reservations (id, key_id, created_at, expires_at, request_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (rid, key_id, now_iso, exp, request_id),
            )
            return rid

    def release_reservation(self, reservation_id: str | None):
        if not reservation_id:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))

    # --- audit log ---

    def log_audit(
        self,
        subscriber_id: str,
        key_id: int,
        provider: str,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        success: bool = True,
        error: str | None = None,
        event: str | None = None,
    ):
        # Never put secrets in error/model strings beyond known safe fields
        if error and ("gsk_" in error or "sk-" in error or "key-" in error.lower()):
            error = "[redacted error]"
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO audit_log
                   (ts, subscriber_id, key_id, provider, model, tokens_in, tokens_out,
                    latency_ms, success, error, event)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now, subscriber_id, key_id, provider, model,
                    tokens_in, tokens_out, latency_ms,
                    1 if success else 0, error, event,
                ),
            )

    def get_audit_log(
        self,
        subscriber_id: str | None = None,
        days: int = 7,
        limit: int = 200,
    ) -> list[dict]:
        cutoff = (_now() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            if subscriber_id:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE ts > ? AND subscriber_id = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (cutoff, subscriber_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE ts > ? ORDER BY ts DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_audit_summary(self, days: int = 7) -> list[dict]:
        cutoff = (_now() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT subscriber_id,
                          COUNT(*) as requests,
                          SUM(tokens_in) as tokens_in,
                          SUM(tokens_out) as tokens_out,
                          SUM(tokens_in + tokens_out) as tokens_total,
                          SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors
                   FROM audit_log
                   WHERE ts > ?
                   GROUP BY subscriber_id
                   ORDER BY tokens_total DESC""",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- rotation state persistence ---

    def save_rotation_state(self, cap_key: str, cursor: int, slot_counts: dict[int, int]):
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rotation_state (cap_key, cursor, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(cap_key) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
                (cap_key, cursor, now),
            )
            for key_id, count in slot_counts.items():
                conn.execute(
                    "INSERT INTO rotation_slot_counts (key_id, slot_count) VALUES (?, ?) "
                    "ON CONFLICT(key_id) DO UPDATE SET slot_count=excluded.slot_count",
                    (key_id, count),
                )

    def load_rotation_state(self, cap_key: str) -> tuple[int, dict[int, int]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cursor FROM rotation_state WHERE cap_key = ?", (cap_key,)
            ).fetchone()
            cursor = row["cursor"] if row else 0
            rows = conn.execute(
                "SELECT key_id, slot_count FROM rotation_slot_counts"
            ).fetchall()
            slot_counts = {r["key_id"]: r["slot_count"] for r in rows}
        return cursor, slot_counts

    def schema_version(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
        return int(row["value"]) if row else 0

    def db_contains_plaintext_secrets(self) -> bool:
        """True if any api_key column value is not Fernet-encrypted."""
        with self._conn() as conn:
            rows = conn.execute("SELECT api_key FROM api_keys").fetchall()
        for r in rows:
            if r["api_key"] and not looks_encrypted(r["api_key"]):
                return True
        return False
