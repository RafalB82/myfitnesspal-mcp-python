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
| `refresh_browser_cookies` | Utility | Extract and save session cookies from browser |

## Prerequisites

- **Python 3.10+** (check with `python3 --version`)
- **pip 21.3+** (for pyproject.toml support; upgrade with `pip install --upgrade pip`)
- **MyFitnessPal account**
- **One of the following for authentication:**
  - Your MFP username/email and password (recommended), OR
  - Chrome or Firefox with an active MyFitnessPal login session

### Authentication Options

This MCP supports multiple authentication methods:

| Method | Setup | Persistence |
|--------|-------|-------------|
| **Credentials in config** | Add `MFP_USERNAME` and `MFP_PASSWORD` to env | Automatic (session cached 30 days) |
| **Browser cookies** | Log into myfitnesspal.com in Chrome/Firefox | Until browser session expires |

## Installation

### Option 1: Install from Source (Recommended)

```bash
git clone https://github.com/RafalB82/myfitnesspal-mcp-python.git
cd myfitnesspal-mcp-python

python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# On Windows: .\venv\Scripts\activate

pip install --upgrade pip
pip install -e .
```

### Option 2: Install with pip (when published)

```bash
pip install mfp-mcp
```

> **Note**: Option 2 requires the package to be published to PyPI. For now, use Option 1.

### Verify Installation

```bash
# With venv activated — defaults to streamable-http on :8000
python -m mfp_mcp.server

# Force stdio mode (for local Claude Desktop testing)
MCP_TRANSPORT=stdio python -m mfp_mcp.server
```

To test authentication:

```bash
MFP_USERNAME="your_email" MFP_PASSWORD="your_password" python -c "
from mfp_mcp.server import get_mfp_client
client = get_mfp_client()
print('Authentication successful!')
"
```

---

## Docker Deployment (Raspberry Pi / server)

The repo ships with a ready-to-use `Dockerfile` and `docker-compose.yml` optimised for
Raspberry Pi (linux/arm64) but compatible with any amd64 host.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MFP_USERNAME` | *(required)* | MyFitnessPal username or email |
| `MFP_PASSWORD` | *(required)* | MyFitnessPal password |
| `MCP_TRANSPORT` | `streamable-http` | Transport: `streamable-http` / `sse` / `stdio` |
| `MCP_HOST` | `0.0.0.0` | Bind address inside the container |
| `MCP_PORT` | `8000` | Bind port inside the container |
| `DOMAIN` | — | Public domain handled by Traefik (e.g. `mfp.example.com`) |
| `CERT_RESOLVER` | `letsencrypt` | Name of certresolver configured in Traefik |
| `TRAEFIK_NETWORK` | `traefik` | Docker network name used by Traefik |

### Quick start (without reverse proxy — local testing only)

```bash
cp .env.example .env
# fill in MFP_USERNAME and MFP_PASSWORD

docker compose up -d --build
# MCP server now available at http://localhost:8000/mcp
```

> Uncomment `ports` in `docker-compose.yml` if you need direct host access.

---

## Traefik Reverse Proxy (Raspberry Pi)

The `docker-compose.yml` uses Traefik labels for automatic TLS termination.
Traefik must already be running on the same host with:
- an external Docker network (default name: `traefik`)
- a configured certresolver (default name: `letsencrypt`)
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

Edit `.env`:

```dotenv
MFP_USERNAME=your_mfp_login
MFP_PASSWORD=your_mfp_password
DOMAIN=mfp.twojadomena.pl
CERT_RESOLVER=letsencrypt       # must match your traefik.yml certresolver name
TRAEFIK_NETWORK=traefik         # must match the Traefik Docker network name
```

### Step 3 — Deploy

```bash
docker compose up -d --build
```

Traefik automatically:
- issues a Let’s Encrypt TLS certificate for `DOMAIN`
- redirects HTTP → HTTPS
- routes `https://DOMAIN/mcp` to the container

### Verify

```bash
curl -sf https://mfp.twojadomena.pl/mcp
# should return a 200 or MCP handshake response
```

---

## Perplexity Remote Connector

Perplexity supports **Custom Remote MCP Connectors** via `Streamable HTTP` or `SSE` transport.
The server must be reachable over public HTTPS — the Traefik setup above provides exactly that.

### Add the connector in Perplexity

1. Open Perplexity → **Settings** → **MCP Connectors** → **+ Add Custom Connector**
2. Fill in the form:

| Field | Value |
|-------|-------|
| Name | `MyFitnessPal` |
| Transport | `Streamable HTTP` |
| URL | `https://mfp.twojadomena.pl/mcp` |

3. Click **Save** — Perplexity will perform a handshake and list the available tools.

> If the handshake fails, verify the container is running and the domain resolves correctly:
> ```bash
> docker compose ps
> curl -v https://mfp.twojadomena.pl/mcp
> ```

### Available transports

| Transport | `MCP_TRANSPORT` value | Use case |
|-----------|-----------------------|----------|
| Streamable HTTP | `streamable-http` (default) | Perplexity Remote Connector, any HTTP MCP client |
| SSE | `sse` | Legacy HTTP clients |
| stdio | `stdio` | Claude Desktop, local CLI |

---

## Configuration for Claude Desktop

### Step 1: Locate Your Config File

| OS | Config File Location |
|----|---------------------|
| **macOS** | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` |

### Step 2: Add the MCP Server Configuration

#### Option A: With Credentials (Recommended)

**macOS Example:**
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

**Windows Example:**
```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "C:\\Users\\YourName\\myfitnesspal-mcp-python\\venv\\Scripts\\python.exe",
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

#### Option B: Without Credentials (Browser Cookie Fallback)

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "/Users/yourname/myfitnesspal-mcp-python/venv/bin/python",
      "args": ["-m", "mfp_mcp.server"],
      "env": {
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

> ⚠️ **Important**: Use **full absolute paths** to the Python executable.
> Always set `MCP_TRANSPORT=stdio` for Claude Desktop — it communicates via stdin/stdout.

### Step 3: Restart Claude Desktop

After saving the config, **completely quit and restart Claude Desktop**.

### Step 4: Verify Connection

In Claude Desktop you should see a hammer icon (🔨). Try:

> „Show my MyFitnessPal diary for today”

## Authentication Methods

The MCP server supports three authentication methods, tried in this order:

### 1. Environment Variables (Recommended)
Set `MFP_USERNAME` and `MFP_PASSWORD` in env. Works in Docker, Claude Desktop config, and CLI.

### 2. Stored Session Cookies
After successful authentication, session cookies are saved to `~/.mfp_mcp/cookies.json` and persist for 30 days.

### 3. Browser Cookies (Fallback)
If no credentials are provided and no stored cookies exist, the server reads cookies from Chrome or Firefox. You must be logged into myfitnesspal.com in your browser.

## Security Note on Credentials

- Credentials in Claude Desktop config are stored locally and readable only by your user account.
- In Docker, credentials are passed via `.env` (never commit `.env` to version control).
- Session cookies are cached in `~/.mfp_mcp/cookies.json` (or the Docker volume `mfp_cookies`).
- With HTTP transport (`streamable-http` / `sse`), always put the server behind TLS — the included Traefik setup handles this automatically.

## Usage Examples

Once configured (via Perplexity connector or Claude Desktop), you can ask:

### Food Diary
```
„Show me what I ate today”
„Get my food diary for 2026-01-05”
```

### Track Weight Progress
```
„Show my weight history for the past 30 days”
„Log my weight as 85 kg”
```

### Search Foods
```
„Search MyFitnessPal for chicken breast”
„Find nutrition info for Greek yogurt”
```

### Check Goals vs Actual
```
„Compare my nutrition goals to what I actually ate today”
„How many grams of protein do I still need today?”
```

### Exercise Log
```
„What exercises did I log today?”
```

### Nutrition Reports
```
„Show my calorie intake over the past week”
„What’s my average protein intake this week?”
```

## Project Structure

```
myfitnesspal-mcp-python/
├── .env.example            # Environment variable template
├── Dockerfile              # Container image (python:3.11-slim, arm64/amd64)
├── docker-compose.yml      # Docker Compose with Traefik labels
├── pyproject.toml          # Package configuration
├── README.md               # This file
└── src/
    └── mfp_mcp/
        ├── __init__.py     # Package initialization
        └── server.py       # MCP server implementation
```

## Development

```bash
git clone https://github.com/RafalB82/myfitnesspal-mcp-python.git
cd myfitnesspal-mcp-python

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

pytest
black src/
isort src/
ruff check src/
mypy src/
```

## Troubleshooting

### Server starts but Perplexity connector shows error

1. Verify TLS: `curl -v https://your-domain/mcp`
2. Verify container is running: `docker compose ps`
3. Check Traefik dashboard for routing errors
4. Confirm `DOMAIN` in `.env` matches the DNS record pointing to your RPi

### "python: command not found" or wrong Python version

1. Use `python3` instead of `python`
2. Check version: `python3 --version` (must be 3.10+)
3. Install Python 3.12 via Homebrew: `brew install python@3.12`

### "pip install -e ." fails with "setup.py not found"

```bash
pip install --upgrade pip
pip install -e .
```

### "Failed to authenticate with MyFitnessPal"

1. Double-check `MFP_USERNAME` and `MFP_PASSWORD`
2. If using browser cookies: ensure you’re logged into myfitnesspal.com
3. On macOS: grant **Full Disk Access** to Claude Desktop in System Settings → Privacy & Security

### "No module named 'mfp_mcp'"

1. Ensure you’re using the correct Python from your venv
2. Reinstall: `pip install -e .`

### Tools not appearing in Claude Desktop

1. Validate JSON syntax in the config file
2. Use **absolute paths** (no `~` or relative paths)
3. Make sure `MCP_TRANSPORT=stdio` is set in the Claude Desktop config env
4. Restart Claude Desktop completely
5. Check logs: macOS `~/Library/Logs/Claude/`, Windows `%APPDATA%\Claude\logs\`

### Double parentheses in terminal prompt like "((venv) )"

VS Code/Cursor Python extension bug. Fix: edit `venv/bin/activate` line ~70:
```bash
# Change from:
PS1="("'(venv) '") ${PS1:-}"
# To:
PS1="(venv) ${PS1:-}"
```

## API Reference

### mfp_get_diary
Get food diary for a specific date.
- `date` (optional): YYYY-MM-DD format, defaults to today
- `response_format`: `"markdown"` or `"json"`

### mfp_search_food
Search the MyFitnessPal food database.
- `query` (required): Search term
- `limit` (optional): Max results (default 10, max 50)
- `response_format`: `"markdown"` or `"json"`

### mfp_get_food_details
Get detailed nutrition for a food item.
- `mfp_id` (required): MyFitnessPal food ID from search results
- `response_format`: `"markdown"` or `"json"`

### mfp_add_food_to_diary
Add a food item to your diary.
- `mfp_id` (required): MyFitnessPal food ID (from `mfp_search_food`)
- `meal` (optional): `"Breakfast"`, `"Lunch"`, `"Dinner"`, `"Snacks"` (default: `"Breakfast"`)
- `date` (optional): YYYY-MM-DD (default: today)
- `quantity` (optional): Number of servings (default: 1.0)
- `unit` (optional): Serving size description (e.g. `"100g"`)

### mfp_get_measurements
Get body measurement history.
- `measurement` (optional): `"Weight"`, `"Body Fat"`, `"Waist"`, etc.
- `start_date` / `end_date` (optional): YYYY-MM-DD
- `response_format`: `"markdown"` or `"json"`

### mfp_set_measurement
Log a body measurement for today.
- `measurement` (optional): Type (default `"Weight"`)
- `value` (required): Numeric value

### mfp_get_exercises
Get exercise log for a date.
- `date` (optional): YYYY-MM-DD (default today)
- `response_format`: `"markdown"` or `"json"`

### mfp_get_goals / mfp_set_goals
Get or update daily nutrition goals.
- `calories`, `protein`, `carbohydrates`, `fat` (all optional)

### mfp_get_water / mfp_set_water
Get or log water intake.
- `cups` (required for set): Number of cups (1 cup ≈ 237 ml)
- `date` (optional): YYYY-MM-DD

### mfp_get_report
Get nutrition report over a date range.
- `report_name` (optional): `"Net Calories"`, `"Protein"`, `"Fat"`, `"Carbs"`
- `start_date` / `end_date` (optional): YYYY-MM-DD
- `response_format`: `"markdown"` or `"json"`

## Security & Privacy

- **Credentials**: Stored locally in `.env` or Claude Desktop config (readable only by your user).
- **Docker**: Credentials passed via `.env` — never commit `.env` to version control.
- **TLS**: Always run the HTTP transport behind TLS (Traefik handles this automatically).
- **No third-party servers**: Data flows only between your machine and myfitnesspal.com.

## License

MIT License — See [LICENSE](LICENSE) file for details.

## Acknowledgments

- [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal) — underlying library for MyFitnessPal access
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Model Context Protocol framework
- [Anthropic](https://anthropic.com) — Claude and the MCP specification
