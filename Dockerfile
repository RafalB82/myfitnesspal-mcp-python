# syntax=docker/dockerfile:1
# ============================================================================
# MyFitnessPal MCP Server — Docker image
# Optimised for Raspberry Pi (linux/arm64) but works on amd64 too.
# ============================================================================

FROM python:3.11-slim

LABEL maintainer="RafalB82"
LABEL description="MyFitnessPal MCP server with Streamable-HTTP transport for Perplexity Remote Connector"

# System deps — keep minimal
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . /app

# Install the package and all dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Cookie storage volume — mount a named volume or host dir here
# so that cookies persist across container restarts.
VOLUME ["/root/.mfp_mcp"]

# Expose MCP HTTP port
EXPOSE 8000

# Environment defaults (override in docker-compose or -e flags)
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    PYTHONUNBUFFERED=1

# Health-check — MCP streamable-http responds with 400 (Missing session ID)
# when called without a session — that means the server is alive and healthy.
# Without the Accept header the server returns 406 which curl -f treats as failure.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf \
        -H "Accept: application/json, text/event-stream" \
        -o /dev/null \
        -w "%{http_code}" \
        http://localhost:8000/mcp | grep -qE "^(200|400)$" || exit 1

CMD ["python", "-m", "mfp_mcp.server"]
