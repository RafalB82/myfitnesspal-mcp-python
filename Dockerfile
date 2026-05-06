# MyFitnessPal MCP Server — Docker image
#
# Auth uses Camoufox (stealthy Firefox) via Xvfb virtual display.
# VNC server (port 5900) allows manual reCAPTCHA interaction on first login.
# Browser profile is persisted in /home/mcp/.mfp_mcp/browser_profile.
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

# System deps: Essential build tools + Xvfb + x11vnc + window manager
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    # GUI / VNC
    xvfb \
    x11vnc \
    openbox \
    xterm \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Pre-create X11 directory for non-root user
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

# Install Python package + Playwright dependencies
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . && \
    python -m camoufox fetch && \
    python -m playwright install-deps firefox && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --shell /bin/bash mcp && \
    mkdir -p /home/mcp/.mfp_mcp/browser_profile && \
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
