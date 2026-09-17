# KeyPool Local

Gerenciador **local** de chaves de APIs de IA: rotação entre provedores autorizados, cotas/cooldown, auditoria, TUI no terminal e proxy HTTP compatível com um subconjunto de Chat Completions.

> Este repositório é um trabalho derivado com endurecimento próprio (criptografia em repouso, `FREE_ONLY`, cotas partilhadas, bind em loopback).  
> Base MIT: [piyush-tyagi-13/llm-keypool](https://github.com/piyush-tyagi-13/llm-keypool) — ver `UPSTREAM.md`. **Não** somos os autores do projeto original.

## O que este software NÃO faz

- Não gera créditos nem “desbloqueia” assinaturas.
- Não torna modelos pagos automaticamente gratuitos.
- Não obtém chaves de terceiros nem contorna limites da conta.

Com `FREE_ONLY=true` (padrão) só entram rotas com `free_tier` verificado no catálogo. Custo desconhecido fica fora do pool gratuito.

## Instalação (deste repositório)

```bash
git clone https://github.com/manansbdb/keypool-local.git
cd keypool-local
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
```

CLI de compatibilidade: `llm-keypool` (pacote Python interno `llm_keypool`).

## Arranque rápido

```bash
# Chave mestra (fora do Git) — obrigatória para encriptar segredos
export LLM_KEYPOOL_MASTER_KEY_FILE="$HOME/.keypool-local/master.key"
mkdir -p "$(dirname "$LLM_KEYPOOL_MASTER_KEY_FILE")"
python -c "from cryptography.fernet import Fernet; open('$LLM_KEYPOOL_MASTER_KEY_FILE','wb').write(Fernet.generate_key())"

# Cadastrar chave (prompt oculto — evita --key)
llm-keypool add --provider groq

# Proxy só em loopback
llm-keypool proxy --host 127.0.0.1 --port 8000

# TUI
llm-keypool gui

# Auditoria
llm-keypool audit
```

Variáveis úteis: `LLM_KEYPOOL_DB`, `FREE_ONLY`, `ALLOW_PAID_FALLBACK`, token do proxy (ver `.env.example`).

## Testes

```bash
pytest -q --ignore=stress_test.py
```

`stress_test.py` não corre em CI (chamadas reais).

## Documentação

- `UPSTREAM.md` — SHA e matriz face ao upstream  
- `CHANGELOG.md` — alterações nossas  
- `docs/PROVIDERS.md` — estado dos provedores  
- `README.pt.md` — notas adicionais em PT (se presente)

## Licença

MIT (preserva avisos do upstream + contribuições KeyPool Local).
