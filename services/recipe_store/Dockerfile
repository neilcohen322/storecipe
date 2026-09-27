FROM ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim@sha256:08477888ac23d6cfbeb8c7dc6fc70cf297fd38b7bf35522be33ce832750ca242

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORECIPE_TRANSPORT=http \
    STORECIPE_HOST=0.0.0.0 \
    STORECIPE_PORT=8765 \
    STORECIPE_DATA_PATH=/data/storecipe.sqlite

COPY pyproject.toml uv.lock ./
COPY packages/storecipe_auth/pyproject.toml packages/storecipe_auth/pyproject.toml
COPY services/catalog/pyproject.toml services/catalog/pyproject.toml
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
COPY services/mcp_gateway/pyproject.toml services/mcp_gateway/pyproject.toml
COPY services/recipe_store/pyproject.toml services/recipe_store/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package storecipe-store

COPY services/recipe_store services/recipe_store
RUN uv sync --frozen --no-dev --package storecipe-store \
    && mkdir -p /data \
    && addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app /data

EXPOSE 8765
CMD ["python", "-m", "storecipe_store"]
