# Providers / Provedores

PT | EN — status from **code path + `llm_keypool/config/providers.json` + tests** only.

## Disclaimer / Aviso

EN: This tool does **not** generate API credits and does **not** unlock paid models. It only rotates keys you already own (free tiers / your own accounts).

PT: Esta ferramenta **não** gera créditos de API e **não** libera modelos pagos. Ela apenas rotaciona chaves que você já possui (free tiers / suas próprias contas).

## Status table

| Provider | free_tier | OpenAI-compat | Default model | Limits (from providers.json — **not** live-verified) | Status | Evidence |
|----------|-----------|---------------|---------------|------------------------------------------------------|--------|----------|
| groq | True | yes | `llama-3.3-70b-versatile` | `{"rpm":30,"tpm":6000,"rpd":14400}` | **verificado** | config + openai_compat + tests + headers.py |
| cerebras | True | yes | `llama3.3-70b` | `{"rpm":30,"rph":900,"rpd":14400,"tpm":60000,"tph":1000000,"tokens_per_day":1000000,"max_context_tokens":8192}` | **verificado** | config + openai_compat + headers.py |
| sambanova | True | yes | `Meta-Llama-3.3-70B-Instruct` | `{"rpm_per_model":{"Meta-Llama-3.1-8B-Instruct":30,"Meta-Llama-3.3-70B-Instruct":20,"Qwen2.5-72B-Instruct":20,"Meta-Llama-3.1-405B-Instruct":10}}` | **verificado** | config + openai_compat |
| mistral | True | yes | `mistral-large-latest` | `{"rpm":2,"tpm":500000,"tokens_per_month":1000000000}` | **verificado** | config + openai_compat + tests + headers.py |
| openrouter | True | yes | `meta-llama/llama-3.3-70b-instruct:free` | `{"rpm":20,"rpd":200}` | **verificado** | config + openai_compat + tests |
| cloudflare | True | no | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | `{"neurons_per_day":10000}` | **verificado** | config + dedicated module |
| google | True | yes | `gemini-2.0-flash` | `{"rpm":15,"rpd":1500,"tpm":1000000}` | **verificado** | config + openai_compat |
| cohere | True | no | `command-r-plus-08-2024` | `{"calls_per_month":1000,"rpm_chat":20}` | **verificado** | config + dedicated module + tests |

**verificado** = entry in `providers.json` and a code path under `llm_keypool/providers/`. Limits are copied from config and were **not** live-probed in this docs pass.

Providers mentioned only in older prose without a catalog entry (e.g. Jina, HuggingFace): **não verificado** (not in local catalog).

See also: [PROVIDER_GUIDE.md](../PROVIDER_GUIDE.md) (signup guide; may drift from JSON).

