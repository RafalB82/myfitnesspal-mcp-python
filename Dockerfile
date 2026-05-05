# MyFitnessPal MCP Server — Docker image
#
# Auth uses Playwright HEADED Chromium via Xvfb virtual display.
# VNC server (port 5900) allows manual reCAPTCHA interaction on first login.
#
# Build:  docker compose build --no-cache mfp-mcp
# Run:    docker compose up -d mfp-mcp
# VNC:    connect to <host>:5900 (password: mfpvnc)

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    DISPLAY=:99

WORKDIR /app

# System deps: Playwright Chromium + Xvfb + x11vnc + window manager
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
    xvfb \
    x11vnc \
    openbox \
    xterm \
    && rm -rf /var/lib/apt/lists/*

# Install Python package + all deps
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . && \
    playwright install chromium

# Non-root user
RUN useradd --create-home --shell /bin/bash mcp && \
    mkdir -p /home/mcp/.mfp_mcp && \
    chown -R mcp:mcp /home/mcp/.mfp_mcp && \
    chown -R mcp:mcp /opt/playwright 2>/dev/null || true

# VNC password
RUN mkdir -p /home/mcp/.vnc && \
    x11vnc -storepasswd mfpvnc /home/mcp/.vnc/passwd && \
    chown -R mcp:mcp /home/mcp/.vnc

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER mcp
ENV HOME=/home/mcp

# MCP server port + VNC port
EXPOSE 8000 5900

ENTRYPOINT ["/entrypoint.sh"]
