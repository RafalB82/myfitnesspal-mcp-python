# MyFitnessPal MCP Server — Docker image
#
# Auth uses Playwright headless Chromium + playwright-stealth to bypass bot detection.
# Set MFP_USERNAME and MFP_PASSWORD in docker-compose.yml.
#
# Build:  docker compose build --no-cache mfp-mcp
# Run:    docker compose up -d mfp-mcp

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app

# System deps for Playwright Chromium (ARM64 + AMD64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python package
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Install Playwright + stealth + Chromium browser binary
# playwright-stealth patches JS fingerprints (navigator.webdriver, plugins, etc.)
# that headless Chromium exposes and that Cloudflare/reCAPTCHA use for bot detection.
RUN pip install --no-cache-dir playwright playwright-stealth && \
    playwright install chromium

# Non-root user
RUN useradd --create-home --shell /bin/bash mcp && \
    mkdir -p /home/mcp/.mfp_mcp && \
    chown -R mcp:mcp /home/mcp/.mfp_mcp && \
    chown -R mcp:mcp /opt/playwright 2>/dev/null || true
USER mcp
ENV HOME=/home/mcp

EXPOSE 8000

ENTRYPOINT ["python", "-m", "mfp_mcp.server"]
