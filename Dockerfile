# Runs mcp-usc over Streamable HTTP on any container host.
#   docker build -t mcp-usc .
#   docker run -p 127.0.0.1:8000:8000 -e MCP_AUTH_TOKEN=... mcp-usc
# Keep a single container replica per deployment: write confirmations are process-local.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so source changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY src ./src
RUN uv sync --frozen \
    && useradd --system --uid 10001 --create-home --home-dir /home/mcp mcp

USER mcp
ENV PATH="/app/.venv/bin:${PATH}" \
    MCP_USC_TRANSPORT=http \
    MCP_USC_HOST=0.0.0.0 \
    MCP_USC_PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["mcp-usc", "serve"]
