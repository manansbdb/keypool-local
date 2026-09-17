# KeyPool Local (`llm-keypool`)

Pool local de chaves de API de LLM (free tier / contas próprias). Você cadastra as chaves uma vez; o `llm-keypool` faz round-robin, trata cooldown de 429 e tenta outra chave automaticamente.

Expõe CLI, TUI (Textual), wrapper LangChain (`AggregatorChat`) e um proxy local compatível com a API OpenAI — assim qualquer agente/ferramenta que fale OpenAI pode usar o pool sem mudar código.

Integração documentada com Hermes Agent: veja [docs/hermes-agent.md](docs/hermes-agent.md).

> **Fork local** deste checkout, baseado no upstream
> [`piyush-tyagi-13/llm-keypool@bc283bb778f5ba9054b547ffd93b6da2a235f253`](https://github.com/piyush-tyagi-13/llm-keypool/commit/bc283bb778f5ba9054b547ffd93b6da2a235f253)
> (consultado em 2026-09-17T22:35:26Z, licença MIT). Ver [UPSTREAM.md](UPSTREAM.md) e [CHANGELOG.md](CHANGELOG.md).

---

## Aviso importante (o que isto NÃO é)

**Esta ferramenta NÃO gera créditos de API e NÃO torna modelos pagos gratuitos.**
Ela **apenas rotaciona chaves que você já possui** (em geral free tiers ou contas suas).
Não há “hack” de cota, bypass de billing nem desbloqueio de modelos pagos.

---

## O que faz

- Pool multi-provedor (Groq, Cerebras, Mistral, OpenRouter, SambaNova, Google, Cloudflare, Cohere, …)
- Tags de capacidades (`general_purpose`, `agentic`, `fast`, `code`, `vision`, `large_context`)
- Rotação automática (round-robin; padrão: a cada 5 pedidos)
- Tratamento de 429 (cooldown + nova chave)
- Proxy OpenAI-compatível (`llm-keypool proxy`) com SSE
- Auditoria por subscriber (`llm-keypool audit`)
- Persistência SQLite (WAL); no fork local, chaves podem ficar **criptografadas em repouso** (Fernet)
- Política local `LLM_KEYPOOL_FREE_ONLY=true` (padrão) — não inventa crédito; só filtra por free tier / cost tier

DB padrão: `~/.llm-keypool/keys.db`  
Override: `export LLM_KEYPOOL_DB=/caminho/keys.db`

---

## Instalação (deste checkout — comandos verificados)

Neste ambiente: Python 3.13 + `uv` + `.venv` em `/workspace/keypool-local`.

```bash
cd /workspace/keypool-local

# criar venv (se ainda não existir) e instalar extras
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[all,dev]"
# necessário para crypto local (Fernet); ainda não declarado no pyproject neste snapshot
uv pip install cryptography

# verificar CLI
llm-keypool --help
llm-keypool providers
LLM_KEYPOOL_DB=/tmp/keypool-empty/keys.db llm-keypool status
# → mensagem de DB vazio / sem chaves (esperado)
```

Alternativa com pip:

```bash
source .venv/bin/activate
pip install -e ".[all,dev]"
pip install cryptography
```

O pacote PyPI `llm-keypool` é o upstream público — **não** garante os hardenings deste fork.

---

## Quickstart

```bash
source .venv/bin/activate

# cadastrar chaves suas / free tier
llm-keypool add --provider groq --key gsk_... --model llama-3.3-70b-versatile --capabilities general_purpose,fast
llm-keypool add --provider cerebras --key csk_... --model llama3.3-70b --capabilities general_purpose,fast
llm-keypool add --provider mistral --key ... --model mistral-large-latest --capabilities agentic

llm-keypool status
llm-keypool gui          # TUI (extra gui)
```

---

## CLI (essencial)

Comandos verificados neste checkout (`llm-keypool --help`):

| Comando | Função |
|---------|--------|
| `llm-keypool status` | Lista chaves / cooldown / uso |
| `llm-keypool add --provider <p> --key <k> [--model ...] [--capabilities ...]` | Registra chave |
| `llm-keypool deactivate --id N` | Desativa chave |
| `llm-keypool clear-cooldown --id N --yes` | Limpa cooldown (exige `--yes`; auditado) |
| `llm-keypool providers` | Catálogo local |
| `llm-keypool audit` | Auditoria (ver `--help` para filtros) |
| `llm-keypool gui` | TUI Textual |
| `llm-keypool proxy [--host 127.0.0.1] [--port 8000] [--capabilities general_purpose] [--rotate-every 5]` | Proxy OpenAI |

Proxy (dois processos — padrão Hermes):

```bash
llm-keypool proxy --port 8000 --capabilities agentic
llm-keypool proxy --port 8001 --capabilities general_purpose,fast
```

Endpoints: `POST /v1/chat/completions` (SSE se `stream=true`), `GET /v1/models`, `GET /health`, `GET /audit`.

Headers úteis: `X-Subscriber-ID`, `X-Keypool-Capabilities`.

Auth local (fork): `LLM_KEYPOOL_PROXY_TOKEN` (ou `LLM_KEYPOOL_AUTH_TOKEN`); `LLM_KEYPOOL_PROXY_REQUIRE_AUTH` padrão `true`.

---

## Tabela de provedores

Detalhe completo: **[docs/PROVIDERS.md](docs/PROVIDERS.md)** (status `verificado` / `não verificado`; limites **somente** de `providers.json`, sem probe ao vivo).

| Provider | Status | Limites |
|----------|--------|---------|
| groq | verificado | de `providers.json` (não verificado ao vivo) |
| cerebras | verificado | de `providers.json` (não verificado ao vivo) |
| sambanova | verificado | de `providers.json` (não verificado ao vivo) |
| mistral | verificado | de `providers.json` (não verificado ao vivo) |
| openrouter | verificado | de `providers.json` (não verificado ao vivo) |
| cloudflare | verificado | de `providers.json` (não verificado ao vivo) |
| google | verificado | de `providers.json` (não verificado ao vivo) |
| cohere | verificado | de `providers.json` (não verificado ao vivo) |

Guia de signup (upstream): [PROVIDER_GUIDE.md](PROVIDER_GUIDE.md).

---

## LangChain

```python
from llm_keypool import AggregatorChat

llm = AggregatorChat(
    capabilities=["general_purpose", "fast"],
    subscriber_id="meu-app",
    max_tokens=4096,
    temperature=0.7,
    rotate_every=5,
)
print(llm.invoke("Olá").content)
```

---

## Variáveis de ambiente (fork local)

| Variável | Padrão | Função |
|----------|--------|--------|
| `LLM_KEYPOOL_DB` | `~/.llm-keypool/keys.db` | Caminho do SQLite |
| `LLM_KEYPOOL_FREE_ONLY` | `true` | Preferir/exigir free tier |
| `LLM_KEYPOOL_ALLOW_PAID_FALLBACK` | `false` | Permitir cost_tier pago |
| `LLM_KEYPOOL_ROTATE_EVERY` | `5` | Pedidos por chave antes de rotacionar |
| `LLM_KEYPOOL_LEGACY_IGNORE_MODEL` | `false` | Ignorar model do request (comportamento upstream) |
| `LLM_KEYPOOL_PROXY_TOKEN` / `LLM_KEYPOOL_AUTH_TOKEN` | vazio | Bearer do proxy |
| `LLM_KEYPOOL_PROXY_REQUIRE_AUTH` | `true` | Exigir auth no proxy |
| `LLM_KEYPOOL_MASTER_KEY` / `LLM_KEYPOOL_MASTER_KEY_FILE` | `~/.llm-keypool/master.key` | Chave Fernet |

---

## Testes (verificados neste box)

```bash
source .venv/bin/activate
python -m pytest -q
```

Snapshot 2026-09-17 (Europe/Dublin, UTC+1): **86 passed**, 8 warnings (fecho da docs, 2026-09-17 ~23:55 Europe/Dublin). Ao longo do dia houve dips transitórios (64/66/78) durante hardening paralelo.

`stress_test.py` **não** foi executado (sem credenciais de provedor).

---

## Documentação relacionada

- [UPSTREAM.md](UPSTREAM.md) — matriz upstream vs local
- [CHANGELOG.md](CHANGELOG.md) — deltas do fork
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — status dos provedores
- [docs/hermes-agent.md](docs/hermes-agent.md) — Hermes
- [README.md](README.md) — README em inglês (upstream + avisos do fork)

---

## Licença

MIT (upstream e este fork).
