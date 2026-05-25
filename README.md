# MyFitnessPal MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that enables AI assistants
to interact with your MyFitnessPal data — food diary, body measurements, food search, and nutrition info.

## Quick Start

```bash
# 1. Clone & install
git clone https://github.com/RafalB82/myfitnesspal-mcp-python.git
cd myfitnesspal-mcp-python
cp .env.example .env   # fill in your MFP credentials

# 2. Build & run (Docker)
docker compose up -d --build

# 3. First login — VNC (port 5900, password: mfpvnc)
#    Open VNC client to <host>:5900, complete reCAPTCHA if shown.
#    Once logged in, the session persists automatically.

# 4. Verify
curl -s http://localhost:8000/health
# → {"status":"ok"}
```

## Authentication

MyFitnessPal uses **reCAPTCHA** and **Cloudflare Bot Management**, making automated credential login unreliable.
The server uses **Camoufox** (stealth-hardened Firefox fork) for auth:

1. **Persistent browser profile** — Firefox session saved to Docker volume `mfp_browser_profile`.
   On first run, connect via VNC (port 5900, password `mfpvnc`) to complete reCAPTCHA manually.
2. **Cookies fallback** — `~/.mfp_mcp/cookies.json` saves the session token. If Camoufox fails,
   the server falls back to stored cookies.
3. **mfp_quick.py** — standalone cookie-based reader (~3s, no browser).

### First-time VNC login

```bash
# Server running, connect VNC to <host>:5900
# password: mfpvnc
# Complete login in the Firefox window
# Session is saved permanently in the browser_profile volume
```

## Tools

| Tool | Status | Description |
|------|--------|-------------|
| `mfp_get_diary` | ✅ Works | Get food diary for any date |
| `mfp_search_food` | ✅ Works | Search the MFP food database |
| `mfp_get_food_details` | ✅ Works | Detailed nutrition for a food item |
| `mfp_get_measurements` | ✅ Works | Weight / body measurement history |
| `mfp_set_measurement` | ❌ 404 | MFP API changed — write endpoint no longer available |
| `mfp_add_food_to_diary` | ❌ 404 | MFP API changed — write endpoint no longer available |
| `mfp_set_water` | ❌ 404 | MFP API changed — write endpoint no longer available |

> **Read tools work. Write tools are broken** — MyFitnessPal migrated to a GraphQL/API backend
> and the old form-based URLs (`/food/diary/{user}/add`, `/food/diary/{user}/water`) no longer exist.
> Use the MFP website or app to log food/water.

## Fast Reader — `mfp_quick.py`

Standalone script that reads the diary via cached cookies (~3s) without spawning a browser.

```bash
# From the project directory
python3 mfp_quick.py              # today
python3 mfp_quick.py 2026-05-01   # specific date
```

Uses `~/.mfp_mcp/cookies.json` — falls back to MCP server (via Camoufox, ~23s) if cookies are expired.

## Architecture

```
┌─────────────────────────┐     streamable-http      ┌────────────────────────────────────┐
│  AI Assistant (Claude,  │ ◄──────────────────────► │  MFP MCP Server                    │
│  Perplexity, OpenClaw)  │      POST /mcp           │  port 8000                         │
└─────────────────────────┘                          │                                    │
                                                     │  ┌────────────────┐  ┌───────────┐  │
              ┌──────────────────────────────────────┤  │  SQLite Cache  │  │ Camoufox  │  │
              │                                      │  │  (mfp_cache.db)│  │ (auth)    │  │
              ▼                                      │  └───────┬────────┘  └─────┬─────┘  │
     ┌───────────────┐                               └─────────┼────────────────────┼────────┘
     │ mfp_quick.py  │  cookies-first (~3s)                    │                    │
     │ (host side)   │─────────────────────────────────────────┤                    │
     └───────────────┘                                          ▼                    ▼
                                                         ┌──────────────┐   ┌──────────────┐
                                                         │ myfitnesspal │   │  Background  │
                                                         │   .com API   │   │  Sync (cron) │
                                                         └──────────────┘   └──────┬───────┘
                                                                                   │
                                                                                   ▼
                                                                           ┌──────────────┐
                                                                           │  SQLite Cache│
                                                                           │  (same db)   │
                                                                           └──────────────┘
```

### Read path (fast path)

```
AI Agent → mfp_get_diary / mfp_get_measurements
         → SQLite cache (sub‑ms response)
         → fallback: live MFP via Camoufox (only if date is not synced)
```

### Data freshness (background sync)

A cron job inside the container syncs MFP data into SQLite 3 times per day
(default schedule: 06:00, 14:00, 22:00). An initial sync runs on container start.

Write tools (`mfp_set_measurement`, `mfp_add_food_to_diary`, `mfp_set_water`)
mark affected dates as `stale` — the next sync run picks them up automatically.

This means:
- **AI agents get answers in milliseconds** — no 20‑second Camoufox delay per query
- **Data is never older than ~8 hours** at worst (between sync runs)
- **No change to the authentication flow** — sync uses the same cookies/Camoufox chain

### Transports

| Transport | Config | Use case |
|-----------|--------|----------|
| `streamable-http` | `MCP_TRANSPORT=streamable-http` (default) | Remote clients, Perplexity, AI agents |
| `sse` | `MCP_TRANSPORT=sse` | Legacy SSE clients |
| `stdio` | `MCP_TRANSPORT=stdio` | Claude Desktop (local) |

## Docker Deployment

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MFP_USERNAME` | — | MFP email (for Camoufox login) |
| `MFP_PASSWORD` | — | MFP password (for Camoufox login) |
| `MFP_HOST` | `0.0.0.0` | Bind address |
| `MFP_PORT` | `8000` | Bind port |
| `MFP_TRANSPORT` | `streamable-http` | Transport type |
| `DOMAIN` | — | Public domain for Traefik |
| `CERT_RESOLVER` | `letsencrypt` | Traefik certresolver |
| `TRAEFIK_NETWORK` | `traefik` | Docker network for Traefik |
| `MFP_SYNC_SCHEDULE` | `0 6,14,22 * * *` | Cron expression for background sync |
| `MFP_SYNC_DAYS` | `30` | Number of days to sync each run |

### Volumes

| Volume | Mount | Purpose |
|--------|-------|---------|
| `mfp_cookies` | `/home/mcp/.mfp_mcp` | Cookies.json persistence |
| `mfp_browser_profile` | `/home/mcp/.mfp_mcp/browser_profile` | Full Firefox profile (persistent login) |
| `mfp_cache` | `/home/mcp/.mfp_mcp` | SQLite cache database (mfp_cache.db) |

### Traefik reverse proxy

```bash
docker network create traefik  # if not exists
docker compose up -d --build
# Server available at https://<DOMAIN>/mcp
```

## Perplexity Remote Connector

1. Settings → MCP Connectors → + Add Custom Connector
2. Name: `MyFitnessPal`, Transport: `Streamable HTTP`, URL: `https://<domain>/mcp`

## Claude Desktop (local)

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "mfp_mcp.server"],
      "env": {
        "MFP_USERNAME": "email",
        "MFP_PASSWORD": "password",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Project Structure

```
myfitnesspal-mcp-python/
├── .env.example               # Environment template
├── docker-compose.yml         # Traefik, VNC, volumes, sync config
├── Dockerfile                 # python:3.12-slim + Camoufox + Xvfb + cron
├── entrypoint.sh              # Xvfb → openbox → x11vnc → cron → MFP server
├── pyproject.toml
├── README.md
├── mfp_quick.py               # Fast cookie-based reader (~3s)
├── test_client.py             # MCP SSE test client
└── src/
    └── mfp_mcp/
        ├── __init__.py
        ├── server.py          # Main MCP server (FastMCP)
        ├── cache.py           # SQLite cache — MFPCache class
        └── sync.py            # Background sync script (python -m mfp_mcp.sync)
```

## Manual sync

```bash
# Inside the running container
docker exec mfp-mcp python -m mfp_mcp.sync --days 7

# Options:
#   --days N          Number of days to sync (default: 14)
#   --end-date YYYY-MM-DD  End date (default: today)
#   --force           Re-sync already synced dates
#
# Examples:
#   python -m mfp_mcp.sync --days 30
#   python -m mfp_mcp.sync --days 90 --end-date 2025-12-31 --force
```

The sync script authenticates via stored cookies first (sub‑second),
falling back to Camoufox only if cookies are expired.

## Troubleshooting

### `Permission denied: /home/mcp/.mfp_mcp/cookies.json`
Fix: `docker exec -u root mfp-mcp chown -R mcp:mcp /home/mcp/.mfp_mcp/`

### `Stored cookies are invalid`
Session token expired (~30 days). Log in via VNC to refresh, or re-inject cookies manually:
```bash
docker cp ~/.mfp_mcp/cookies.json mfp-mcp:/home/mcp/.mfp_mcp/cookies.json
docker exec -u root mfp-mcp chown mcp:mcp /home/mcp/.mfp_mcp/cookies.json
```

### `All authentication methods failed`
VNC to port 5900 (password: `mfpvnc`), check if logged into myfitnesspal.com.
Relogin if needed.

### Write tools return 404
MFP migrated to GraphQL API — write endpoints removed. Use the MFP website/app.
Read tools continue to work.

### Data is stale / missing recent days
The cron sync runs 3x/day. You can trigger an immediate sync:
```bash
docker exec mfp-mcp python -m mfp_mcp.sync --days 7 --force
```

## License

MIT — see [LICENSE](LICENSE)
