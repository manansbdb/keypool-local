# STATUS — KeyPool Local

Updated: 2026-09-17 23:50 Europe/Dublin (UTC+1)

## Git hygiene
- **main** = baseline (+ CI ImportError fix). Do not force-push conflicting WIP.
- Executor WIP branch: `wip/executor-etapa-b` (this work)
- Other executor: CI get_best_key None / rebrand / public repo — coordinate before merging main

## Etapa A
| Item | State | Notes |
|------|-------|-------|
| Install deps | done | uv `.venv` + `.[all,dev]` + cryptography |
| Upstream pytest | done | historical 66 passed on stock |
| Baseline on origin/main | done | `0955619` + `fcdf7c3` |
| Runnable locally | done | add (hidden prompt), proxy, tui, audit |

## Etapa B
| Area | State | Notes |
|------|-------|-------|
| crypto / FREE_ONLY / quotas | delivered | Fernet at rest; FREE_ONLY; quota_scope; reservations |
| rotator / proxy / SSE / errors | delivered on WIP | real SSE path; auth Bearer; no silent swap; slot consume on handle_* |
| Docs PT / UPSTREAM / CHANGELOG | done (Docs) | README.pt.md present |
| Docker / .env.example | delivered on WIP | non-root Dockerfile; compose `127.0.0.1:8000`; placeholders |
| Offline tests | delivered on WIP | **94 passed** (2026-09-17) |

## Pytest (WIP branch)
```
94 passed, 9 warnings
```

## Delivered
- Encrypted secrets (Fernet), fingerprint dedupe, migrate+backup
- FREE_ONLY=true / ALLOW_PAID_FALLBACK=false; unknown/missing free_tier blocked
- Shared quota_scope; WAL; reservations; rotate_every=5; AAAAA→BBBBB covered
- Proxy auth, /health, /audit, /v1/models aliases, SSE first-chunk-before-DONE
- CLI: hidden key prompt; --key legacy warn; clear-cooldown --yes + audit
- Docker non-root + loopback publish; .env.example

## Blocked / gaps
- No provider credentials → no live API / stress_test.py
- TUI rebrand polish may land via other executor
- Merge `wip/executor-etapa-b` → main after CI/rebrand executor finishes
- Real multi-process concurrent proxy stress not fully automated

## Disclaimer (product)
Software does **not** create credits, unlock subscriptions, or make paid models free.
