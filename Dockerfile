FROM python:3.12-slim

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin keypool
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY llm_keypool ./llm_keypool
RUN pip install --no-cache-dir -e ".[proxy,gui]" \
    && mkdir -p /data \
    && chown -R keypool:keypool /app /data

USER keypool
ENV LLM_KEYPOOL_DB=/data/keys.db \
    LLM_KEYPOOL_MASTER_KEY_FILE=/data/master.key \
    LLM_KEYPOOL_FREE_ONLY=true \
    LLM_KEYPOOL_ALLOW_PAID_FALLBACK=false \
    HOME=/home/keypool

EXPOSE 8000
CMD ["llm-keypool", "proxy", "--host", "127.0.0.1", "--port", "8000"]
