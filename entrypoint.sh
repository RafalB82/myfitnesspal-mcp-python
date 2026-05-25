#!/bin/bash
set -e

# 1. Start virtual display
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x800x24 -ac &
XVFB_PID=$!
echo "[entrypoint] Xvfb started (PID $XVFB_PID)"
sleep 1

# 2. Start lightweight window manager
openbox &
echo "[entrypoint] openbox started"
sleep 0.5

# 3. Start VNC server
x11vnc \
  -display :99 \
  -rfbauth /home/mcp/.vnc/passwd \
  -rfbport 5900 \
  -forever \
  -noxdamage \
  -quiet &
VNC_PID=$!
echo "[entrypoint] x11vnc started (PID $VNC_PID) — connect on port 5900, password: mfpvnc"

# 4. Start cron daemon for periodic MFP sync
echo "[entrypoint] starting cron daemon"
# Read schedule from env or use default
CRON_SCHEDULE="${MFP_SYNC_SCHEDULE:-0 6,14,22 * * *}"
SYNC_DAYS="${MFP_SYNC_DAYS:-30}"
echo "$CRON_SCHEDULE cd /app && python -m mfp_mcp.sync --days $SYNC_DAYS --force >> /tmp/mfp_sync.log 2>&1" | crontab -
service cron start 2>/dev/null || true

# 5. Initial sync (async, non-blocking — runs in bg)
echo "[entrypoint] triggering initial MFP sync in background..."
(
  sleep 5  # wait for MCP server to be ready
  cd /app && python -m mfp_mcp.sync --days $SYNC_DAYS --force >> /tmp/mfp_sync.log 2>&1
  echo "[entrypoint] initial sync complete"
) &

# Cleanup on exit
trap "kill $XVFB_PID $VNC_PID 2>/dev/null; exit" SIGTERM SIGINT

# 6. Start MCP server
exec python -m mfp_mcp.server
