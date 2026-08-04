FROM ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install third-party dependencies from manifests only, so this layer is
# invalidated by lockfile changes, not source edits.
COPY pyproject.toml uv.lock ./
COPY services/catalog/pyproject.toml services/catalog/pyproject.toml
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
COPY services/mcp_gateway/pyproject.toml services/mcp_gateway/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package storecipe-mcp-gateway

COPY services/mcp_gateway services/mcp_gateway
RUN uv sync --frozen --no-dev --package storecipe-mcp-gateway
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app

USER app

EXPOSE 8002
CMD ["uvicorn", "storecipe_mcp.main:app", "--host", "0.0.0.0", "--port", "8002"]
