# Changelog

Notable **local** changes versus upstream
[`piyush-tyagi-13/llm-keypool@bc283bb778f5ba9054b547ffd93b6da2a235f253`](https://github.com/piyush-tyagi-13/llm-keypool/commit/bc283bb778f5ba9054b547ffd93b6da2a235f253)
(consulted 2026-09-17T22:35:26Z). Keep a Changelog style.

## [Unreleased]

### Added

- `llm_keypool/crypto.py` — Fernet helpers, fingerprint, masking; master key from `LLM_KEYPOOL_MASTER_KEY` / `LLM_KEYPOOL_MASTER_KEY_FILE` or `~/.llm-keypool/master.key`.
- `llm_keypool/settings.py` — `LLM_KEYPOOL_FREE_ONLY` (default `true`), `LLM_KEYPOOL_ALLOW_PAID_FALLBACK` (default `false`), `LLM_KEYPOOL_LEGACY_IGNORE_MODEL`, `LLM_KEYPOOL_ROTATE_EVERY`, `LLM_KEYPOOL_PROXY_TOKEN` / `LLM_KEYPOOL_AUTH_TOKEN`, `LLM_KEYPOOL_PROXY_REQUIRE_AUTH`.
- `llm_keypool/errors.py` — `ErrorKind` + HTTP/exit-code map (pool unconfigured, all limited, auth, rate limit, blocked_cost, …).
- Documentation: `README.pt.md`, this `CHANGELOG.md`, filled `UPSTREAM.md`, `docs/PROVIDERS.md`.

### Changed

- `llm_keypool/key_store.py` — vs upstream plaintext store: encrypt-at-rest, fingerprint uniqueness, schema versioning/migrations, `cost_tier` / quota_scope / reservations support (Product hardening present in the working tree). DB path still overridable via `LLM_KEYPOOL_DB` (default `~/.llm-keypool/keys.db`).
- `llm_keypool/proxy.py` — proxy auth settings hooks; SSE (`text/event-stream`) streaming path present.
- `llm_keypool/rotator.py` — FREE_ONLY / cost-tier filtering and reservation-related hooks observed in tree. **Docs did not modify rotator.py** (Executor owns rotation).
- Runtime: `cryptography` present in local `.venv` for Fernet, but **not declared** yet in `pyproject.toml` / `requirements.txt` at docs time.

### Fixed

- None claimed in this docs-only pass.

### Security

- Keys intended to be encrypted at rest (Fernet) through the local key store path.
- Proxy may require bearer token via `LLM_KEYPOOL_PROXY_TOKEN` / `LLM_KEYPOOL_AUTH_TOKEN`.

### Verified test snapshot (2026-09-17, Europe/Dublin)

- `python -m pytest -q` → **86 passed** at docs close (2026-09-17 ~23:55 Europe/Dublin). Same-day earlier mid-hardening dips observed (64/66/78 with transient failures); final verified run: **86 passed**, 8 warnings.
- `llm-keypool --help`, `llm-keypool providers`, `llm-keypool status` OK on a **fresh** `LLM_KEYPOOL_DB` (empty DB message expected). An older default DB may fail mid-migration until Product finishes schema migrate.
- No live provider credentials → `stress_test.py` not run; limits not live-probed.

### Not in scope

- Does **not** generate free credits or unlock paid models — only rotates keys the operator already owns.
- MIT license retained from upstream.
