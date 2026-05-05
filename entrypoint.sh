#!/bin/bash
set -e

# 1. Start virtual display
Xvfb :99 -screen 0 1280x800x24 -ac &
XVFB_PID=$!
echo "[entrypoint] Xvfb started (PID $XVFB_PID)"
sleep 1

# 2. Start lightweight window manager (needed for proper window rendering)
openbox &
echo "[entrypoint] openbox started"
sleep 0.5

# 3. Start VNC server — connect with any VNC client to port 5900, password: mfpvnc
x11vnc \
  -display :99 \
  -rfbauth /home/mcp/.vnc/passwd \
  -rfbport 5900 \
  -forever \
  -noxdamage \
  -quiet &
VNC_PID=$!
echo "[entrypoint] x11vnc started (PID $VNC_PID) — connect on port 5900, password: mfpvnc"

# Cleanup on exit
trap "kill $XVFB_PID $VNC_PID 2>/dev/null; exit" SIGTERM SIGINT

# 4. Start MCP server
exec python -m mfp_mcp.server
