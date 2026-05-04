# MyFitnessPal MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that enables AI assistants like Claude and **Perplexity** to interact with your MyFitnessPal data, including food diary, exercises, body measurements, nutrition goals, and water intake.

Supports three transports:
- **`streamable-http`** (default) — required for **Perplexity Remote Connector** and any remote MCP client
- **`sse`** — legacy HTTP/SSE transport
- **`stdio`** — local stdin/stdout for Claude Desktop and CLI usage

## Features

| Tool | Type | Description |
|------|------|-------------|
| `mfp_get_diary` | Read | Get food diary entries for any date |
| `mfp_search_food` | Read | Search the MyFitnessPal food database |
| `mfp_get_food_details` | Read | Get detailed nutrition info for a food item |
| `mfp_add_food_to_diary` | Write | Add a food item to your diary for a specific meal and date |
| `mfp_get_measurements` | Read | Get weight/body measurement history |
| `mfp_set_measurement` | Write | Log a new weight or body measurement |
| `mfp_get_exercises` | Read | Get logged exercises (cardio & strength) |
| `mfp_get_goals` | Read | Get daily nutrition goals |
| `mfp_set_goals` | Write | Update daily nutrition goals |
| `mfp_get_water` | Read | Get water intake for a date |
| `mfp_set_water` | Write | Log water intake for a date |
| `mfp_get_report` | Read | Get nutrition reports over a date range |

## Authentication

MyFitnessPal enforces **reCAPTCHA** on the login form and uses **Cloudflare Bot Management**.
Automated credential-based login is therefore unreliable. The server tries the following methods in order:

### Method 1 — Credentials via Playwright (automatic, may fail due to reCAPTCHA)

Set `MFP_USERNAME` and `MFP_PASSWORD` in `.env`. The server launches headless Chromium with
[playwright-stealth](https://github.com/AtuboDad/playwright_stealth) to submit the login form and
caches the resulting session cookies in `~/.mfp_mcp/cookies.json` (or the Docker volume `mfp_cookies`).
Cached cookies are reused for subsequent requests (≈30 days validity).

> **Limitation**: MFP’s reCAPTCHA blocks headless browsers. If you see
> `Login failed — Recaptcha verification failed` in the logs, use Method 2 instead.

### Method 2 — Manual session cookie injection (recommended for Docker / server deployments)

1. Log in to [myfitnesspal.com](https://www.myfitnesspal.com) in **Chrome** on any machine.
2. Open DevTools → Application → Cookies → `https://www.myfitnesspal.com`.
3. Copy the value of **`__Secure-next-auth.session-token`**.
4. Create `~/.mfp_mcp/cookies.json` on the host:

```json
{
  "cookies": {
    "__Secure-next-auth.session-token": "<paste token here>"
  },
  "saved_at": "2026-01-01T00:00:00"
}
```

5. Inject into the Docker named volume (no rebuild needed):

```bash
docker run --rm \
  -v myfitnesspal-mcp-python_mfp_cookies:/target \
  -v ~/.mfp_mcp/cookies.json:/src/cookies.json:ro \
  alpine sh -c "cp /src/cookies.json /target/cookies.json"

docker compose restart mfp-mcp
```

When the token expires (≈30 days) you will see `Stored cookies are invalid` in the logs — repeat the steps above to inject a fresh token.

> **Security**: Never commit `cookies.json` or `.env` to version control.
> Never share your session token — it provides full access to your MFP account.

---

## Prerequisites

- **Python 3.10+** (`python3 --version`)
- **pip 21.3+** (`pip install --upgrade pip`)
- **MyFitnessPal account**
- For Docker: Docker Engine + Docker Compose v2

---

## Installation

### Option 1: Install from source

```bash
git clone https://github.com/RafalB82/myfitnesspal-mcp-python.git
cd myfitnesspal-mcp-python

python3 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

pip install --upgrade pip
pip install -e .
```

### Verify

```bash
# Defaults to streamable-http on :8000
python -m mfp_mcp.server

# stdio mode (Claude Desktop)
MCP_TRANSPORT=stdio python -m mfp_mcp.server
```

---

## Docker Deployment (Raspberry Pi / server)

The repo ships a `Dockerfile` and `docker-compose.yml` optimised for Raspberry Pi (linux/arm64)
but compatible with any amd64 host. The image bundles **Playwright Chromium** and
**playwright-stealth** for the automated auth flow.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MFP_USERNAME` | — | MyFitnessPal username or email |
| `MFP_PASSWORD` | — | MyFitnessPal password |
| `MCP_TRANSPORT` | `streamable-http` | Transport: `streamable-http` / `sse` / `stdio` |
| `MCP_HOST` | `0.0.0.0` | Bind address inside the container |
| `MCP_PORT` | `8000` | Bind port inside the container |
| `DOMAIN` | — | Public domain handled by Traefik (e.g. `mfp.example.com`) |
| `CERT_RESOLVER` | `letsencrypt` | Certresolver name configured in Traefik |
| `TRAEFIK_NETWORK` | `traefik` | Docker network name used by Traefik |

### Cookie persistence

The Docker Compose file mounts a named volume `mfp_cookies` at `/home/mcp/.mfp_mcp` inside
the container. This is where `cookies.json` is stored and read from.

### Quick start (without reverse proxy — local testing only)

```bash
cp .env.example .env
# fill in MFP_USERNAME and MFP_PASSWORD (optional if you use manual cookie injection)

# Uncomment the `ports` block in docker-compose.yml first:
#   ports:
#     - "127.0.0.1:8000:8000"

docker compose up -d --build
# MCP server available at http://localhost:8000/mcp
```

---

## Traefik Reverse Proxy (Raspberry Pi)

The `docker-compose.yml` uses Traefik labels for automatic TLS termination.
Traefik must already be running on the same host with:
- an external Docker network (default: `traefik`)
- a configured certresolver (default: `letsencrypt`)
- entrypoints named `web` (80) and `websecure` (443)

### Step 1 — Verify Traefik network

```bash
docker network ls | grep traefik
# if missing:
docker network create traefik
```

### Step 2 — Configure `.env`

```bash
cp .env.example .env
```

```dotenv
MFP_USERNAME=your_mfp_login
MFP_PASSWORD=your_mfp_password
DOMAIN=mfp.example.com
CERT_RESOLVER=letsencrypt
TRAEFIK_NETWORK=traefik
```

### Step 3 — Deploy

```bash
docker compose up -d --build
```

Traefik automatically issues a Let’s Encrypt TLS certificate, redirects HTTP → HTTPS, and routes
`https://DOMAIN/mcp` to the container.

### Verify

```bash
curl -sf https://mfp.example.com/mcp
```

---

## Perplexity Remote Connector

1. Open Perplexity → **Settings** → **MCP Connectors** → **+ Add Custom Connector**
2. Fill in:

| Field | Value |
|-------|-------|
| Name | `MyFitnessPal` |
| Transport | `Streamable HTTP` |
| URL | `https://mfp.example.com/mcp` |

3. Click **Save** — Perplexity performs a handshake and lists available tools.

---

## Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "/Users/yourname/myfitnesspal-mcp-python/venv/bin/python",
      "args": ["-m", "mfp_mcp.server"],
      "env": {
        "MFP_USERNAME": "your_email@example.com",
        "MFP_PASSWORD": "your_password",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

> Always use **absolute paths** and always set `MCP_TRANSPORT=stdio` for Claude Desktop.

Restart Claude Desktop after saving. You should see a hammer icon (🔨). Try:
> „Show my MyFitnessPal diary for today”

---

## Usage Examples

```
„Show me what I ate today”
„Get my food diary for 2026-01-05”
„Search MyFitnessPal for chicken breast”
„Show my weight history for the past 30 days”
„Log my weight as 85 kg”
„Compare my nutrition goals to what I actually ate today”
„How many grams of protein do I still need today?”
„What exercises did I log today?”
„Show my calorie intake over the past week”
```

---

## Project Structure

```
myfitnesspal-mcp-python/
├── .env.example            # Environment variable template
├── Dockerfile              # python:3.12-slim, Playwright Chromium, playwright-stealth
├── docker-compose.yml      # Traefik + named volume mfp_cookies
├── pyproject.toml
├── README.md
└── src/
    └── mfp_mcp/
        ├── __init__.py
        └── server.py
```

---

## Troubleshooting

### `Recaptcha verification failed` in logs

MFP blocks headless Chromium even with stealth patches. Use **Method 2** (manual cookie injection) — see the Authentication section above.

### `Stored cookies are invalid` in logs

Your session token has expired (≈30 days). Log in to MFP in Chrome, copy a fresh
`__Secure-next-auth.session-token` and re-inject it:

```bash
# Update ~/.mfp_mcp/cookies.json with the new token, then:
docker run --rm \
  -v myfitnesspal-mcp-python_mfp_cookies:/target \
  -v ~/.mfp_mcp/cookies.json:/src/cookies.json:ro \
  alpine sh -c "cp /src/cookies.json /target/cookies.json"
docker compose restart mfp-mcp
```

### Server starts but Perplexity connector shows error

1. `curl -v https://your-domain/mcp`
2. `docker compose ps`
3. Check Traefik dashboard for routing errors
4. Confirm `DOMAIN` in `.env` matches the DNS record

### `Your kernel does not support memory limit capabilities`

Warning only — safe to ignore on Raspberry Pi. The container starts normally.

### `No module named 'mfp_mcp'`

Ensure the venv is active and the package is installed: `pip install -e .`

### Tools not appearing in Claude Desktop

1. Validate JSON syntax in config
2. Use absolute paths (no `~`)
3. Set `MCP_TRANSPORT=stdio`
4. Restart Claude Desktop completely
5. Logs: macOS `~/Library/Logs/Claude/`, Windows `%APPDATA%\Claude\logs\`

---

## API Reference

### mfp_get_diary
- `date` (optional): YYYY-MM-DD, defaults to today
- `response_format`: `"markdown"` or `"json"`

### mfp_search_food
- `query` (required): Search term
- `limit` (optional): Max results (default 10, max 50)
- `response_format`: `"markdown"` or `"json"`

### mfp_get_food_details
- `mfp_id` (required): MyFitnessPal food ID from search results
- `response_format`: `"markdown"` or `"json"`

### mfp_add_food_to_diary
- `mfp_id` (required): Food ID from `mfp_search_food`
- `meal` (optional): `"Breakfast"` / `"Lunch"` / `"Dinner"` / `"Snacks"` (default: `"Breakfast"`)
- `date` (optional): YYYY-MM-DD (default: today)
- `quantity` (optional): Servings (default: 1.0)
- `unit` (optional): Serving size description (e.g. `"100g"`)

### mfp_get_measurements
- `measurement` (optional): `"Weight"`, `"Body Fat"`, `"Waist"`, etc.
- `start_date` / `end_date` (optional): YYYY-MM-DD
- `response_format`: `"markdown"` or `"json"`

### mfp_set_measurement
- `measurement` (optional): Type (default `"Weight"`)
- `value` (required): Numeric value

### mfp_get_exercises
- `date` (optional): YYYY-MM-DD (default today)
- `response_format`: `"markdown"` or `"json"`

### mfp_get_goals / mfp_set_goals
- `calories`, `protein`, `carbohydrates`, `fat` (all optional)

### mfp_get_water / mfp_set_water
- `cups` (required for set): Number of cups (1 cup ≈ 237 ml)
- `date` (optional): YYYY-MM-DD

### mfp_get_report
- `report_name` (optional): `"Net Calories"`, `"Protein"`, `"Fat"`, `"Carbs"`
- `start_date` / `end_date` (optional): YYYY-MM-DD
- `response_format`: `"markdown"` or `"json"`

---

## Security & Privacy

- Never commit `.env` or `cookies.json` to version control.
- Session token in `cookies.json` grants full access to your MFP account — treat it like a password.
- Always run HTTP transports behind TLS (Traefik handles this automatically).
- Data flows only between your machine and myfitnesspal.com — no third-party servers involved.

---

## License

MIT — see [LICENSE](LICENSE)

## Acknowledgments

- [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal) — underlying MFP library
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Model Context Protocol framework
- [playwright-stealth](https://github.com/AtuboDad/playwright_stealth) — bot detection evasion
