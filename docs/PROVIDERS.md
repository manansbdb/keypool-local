# Providers / Provedores

Bilingual brief (PT + EN). Status comes from **code paths + `llm_keypool/config/providers.json` + tests** only.

## Disclaimer / Aviso

EN: This tool does **not** generate API credits and does **not** unlock paid models. It only rotates keys you already have (free tiers / your own accounts).

PT: Esta ferramenta **não** gera créditos de API e **não** libera modelos pagos. Ela apenas rotaciona chaves que você já possui (free tiers / suas próprias contas).

## Status

| Provider | free_tier | OpenAI-compat | Default model | Limits (from providers.json — **not** live-verified) | Status | Evidence |
|----------|-----------|---------------|---------------|------------------------------------------------------|--------|----------|
| groq | true | yes | `llama-3.3-70b-versatile` | rpm=30, tpm=6000, rpd=14400 | **verificado** | config + `openai_compat` + tests (`test_cli`, `test_key_store`, `test_rotator`, `test_langchain_wrapper`) |
| cerebras | true | yes | `llama3.3-70b` | rpm=30, rph=900, rpd=14400, tpm=60000, tph=1000000, tokens_per_day=1000000, max_context_tokens=8192 | **verificado** | config + `openai_compat` + header helper `_cerebras` |
| sambanova | true | yes | `Meta-Llama-3.3-70B-Instruct` | rpm_per_model: 8B=30, 70B=20, Qwen2.5-72B=20, 405B=10 | **verificado** | config + `openai_compat` (no dedicated test name) |
| mistral | true | yes | `mistral-large-latest` | rpm=2, tpm=500000, tokens_per_month=1000000000 | **verificado** | config + `openai_compat` + header helper + tests |
| openrouter | true | yes | `meta-llama/llama-3.3-70b-instruct:free` | rpm=20, rpd=200 | **verificado** | config + `openai_compat` + CLI provider-loop tests |
| cloudflare | true | no | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | neurons_per_day=10000 | **verificado** | config + `providers/cloudflare.py` in dispatch |
| google | true | yes | `gemini-2.0-flash` | rpm=15, rpd=1500, tpm=1000000 | **verificado** | config + `openai_compat` (no dedicated test name) |
| cohere | true | no | `command-r-plus-08-2024` | calls_per_month=1000, rpm_chat=20 | **verificado** | config + `providers/cohere.py` + CLI provider-loop tests |

**verificado** = entry in `providers.json` **and** a callable code path under `llm_keypool/providers/`. Prefer tests when present. rpm/tpm/rpd/etc. are copied from config — **not live-verified** in this docs pass (no provider credentials on the box).

Names that appear only in older prose (e.g. Jina, HuggingFace) without a `providers.json` entry: **não verificado** (not in local catalog).

See also: [PROVIDER_GUIDE.md](../PROVIDER_GUIDE.md) (upstream signup guide; may drift from JSON).
