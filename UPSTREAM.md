# UPSTREAM

- URL: https://github.com/piyush-tyagi-13/llm-keypool
- Branch: `main`
- Commit SHA: `bc283bb778f5ba9054b547ffd93b6da2a235f253`
- Consulted (UTC): `2026-09-17T22:35:26Z`
- License: MIT (preserve notices)
- Local docs snapshot: 2026-09-17 evening Europe/Dublin (UTC+1)

## Matrix

| Feature | File(s) | Observed | Preserved | Improvement |
|---------|---------|----------|-----------|-------------|
| Key store | `llm_keypool/key_store.py` | SQLite at `~/.llm-keypool/keys.db` (`LLM_KEYPOOL_DB`); WAL; active/cooldown filters; usage counters; rotation state; audit log | Upstream persistence, capability filter, audit, rotation cursor/slots | Local: Fernet + `key_fingerprint`; schema versioning; `cost_tier` / quota_scope / reservations (Product, in tree) |
| Crypto | `llm_keypool/crypto.py` | Fernet encrypt/decrypt; master key env/file; fingerprint; mask | N/A upstream | **Local-only**; wired into key store |
| Settings / FREE_ONLY | `llm_keypool/settings.py` | `LLM_KEYPOOL_FREE_ONLY` default true; `LLM_KEYPOOL_ALLOW_PAID_FALLBACK` default false; rotate-every; proxy auth; legacy ignore-model | Partial (`LLM_KEYPOOL_DB`) | **Local** policy module |
| Quotas | `key_store.py`, `config/providers.json` | Per-key counters; `limits` in JSON; optional quota_scope tables | Upstream counters + config limits | Numbers from config only — **not live-probed** |
| Rotation | `llm_keypool/rotator.py` | Capability-keyed round-robin; persistent cursor; per-key slots; `rotate_every` (default 5) | Upstream rotation + cooldown recording | Local FREE_ONLY/cost filter + reservations observed — **Docs did not edit rotator.py** |
| Cooldown / 429 | `rotator.py`, `providers/headers.py`, `providers/dispatch.py` | 429 → header-derived or `cooldown_fallback` from JSON; try next key | Upstream header + fallback design | Local `errors.py`; header helpers may still be mid-change |
| Providers dispatch | `providers/dispatch.py`, `openai_compat.py`, `cohere.py`, `cloudflare.py` | OpenAI-compat + Cohere + Cloudflare; think-token strip | Upstream routing | Preserved |
| free_tier flags | `config/providers.json` | All 8 providers `"free_tier": true` | Upstream flag | Used with local FREE_ONLY |
| Capabilities | `key_store.py`, `cli.py`, `proxy.py`, `langchain_wrapper.py`, `tui.py` | e.g. `general_purpose`, `agentic`, `fast`, `code`, `vision`, `large_context` | Upstream model | Preserved |
| CLI | `cli.py` | `status`, `add`, `deactivate`, `clear-cooldown`, `providers`, `audit`, `gui`, `proxy` | Upstream CLI | Commands verified in README.pt.md |
| TUI | `tui.py` | Textual GUI | Upstream | Preserved (`[gui]` extra) |
| Proxy | `proxy.py` | `/v1/chat/completions`, `/v1/models`, `/health`, `/audit`; capability + subscriber headers | Upstream proxy | Local auth-token settings; SSE streaming |
| SSE | `proxy.py` | `stream=true` → `text/event-stream` + `[DONE]` | Upstream streaming | Preserved |
| LangChain | `langchain_wrapper.py` | `AggregatorChat`; subscriber_id | Upstream | Preserved; LangSmith in/out split still TODO |
| Audit | `key_store.py`, CLI `audit`, proxy `/audit` | Call log + subscriber summary | Upstream | May add `event` field locally |
| Errors | `llm_keypool/errors.py` | `ErrorKind` + HTTP/exit map | N/A | **Local-only** |
| Hermes | `docs/hermes-agent.md` | Two-proxy agentic vs fast guide | Upstream doc | Preserved |
| Stress test | `stress_test.py` | Live rotation stress | Upstream | Not run (no credentials) |

Roadmap-only (OpenClaw AgentSkill, session affinity) are **not** listed as implemented.

## Product boundary

This tool **does not** generate API credits and **does not** unlock paid models. It only rotates keys the operator already owns (free tiers / own accounts).
