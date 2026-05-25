"""
MyFitnessPal MCP Server

A Model Context Protocol (MCP) server that provides tools for interacting
with MyFitnessPal data including food diary, exercises, measurements, goals,
water intake, and food search.

Authentication Methods (in order of priority):
1. Persistent Browser Profile: ~/.mfp_mcp/browser_profile (Camoufox/Firefox)
2. Environment variables: MFP_USERNAME and MFP_PASSWORD (via Camoufox)
3. Stored session cookies: ~/.mfp_mcp/cookies.json
"""

import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from http.cookiejar import CookieJar, Cookie
from pathlib import Path
from typing import Optional, Dict, Any, List
from enum import Enum
from collections import OrderedDict
import time

import httpx
from pydantic import BaseModel, Field, ConfigDict

from mfp_mcp.cache import MFPCache


# ---------------------------------------------------------------------------
try:
    from mcp.server.transport_security import TransportSecurityMiddleware as _TSM

    def _always_valid_host(self, host):
        return True

    def _always_valid_origin(self, origin):
        return True

    _TSM._validate_host = _always_valid_host    # type: ignore[method-assign]
    _TSM._validate_origin = _always_valid_origin  # type: ignore[method-assign]
except Exception as _patch_err:
    import logging as _log
    _log.getLogger("mfp_mcp").warning("Could not patch transport_security: %s", _patch_err)

# ---------------------------------------------------------------------------
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mfp_mcp")

_HOST = os.environ.get("MFP_HOST", "0.0.0.0")
_PORT = int(os.environ.get("MFP_PORT", "8000"))

# Window size for Camoufox (useful for VNC compatibility)
_WINDOW_SIZE = os.environ.get("MFP_WINDOW_SIZE", "1280,720")
try:
    _WIDTH, _HEIGHT = map(int, _WINDOW_SIZE.split(","))
except Exception:
    _WIDTH, _HEIGHT = 1280, 720

mcp = FastMCP("myfitnesspal_mcp", host=_HOST, port=_PORT)

# Add a simple health check endpoint for Docker/Traefik
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok"})

CONFIG_DIR = Path.home() / ".mfp_mcp"
COOKIES_FILE = CONFIG_DIR / "cookies.json"
BROWSER_PROFILE_DIR = CONFIG_DIR / "browser_profile"

# SQLite cache instance (lazily initialised on first use)
_cache: Optional[MFPCache] = None

def get_cache() -> MFPCache:
    global _cache
    if _cache is None:
        _cache = MFPCache(CONFIG_DIR)
    return _cache

# Detect if Xvfb virtual display is available
_DISPLAY = os.environ.get("DISPLAY", "")
_USE_HEADED = bool(_DISPLAY)

def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def save_cookies(cookies: Dict[str, str]):
    ensure_config_dir()
    cookie_data = {
        "cookies": cookies,
        "saved_at": datetime.now().isoformat(),
    }
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookie_data, f, indent=2)
    logger.info(f"Saved session cookies to {COOKIES_FILE}")


def load_cookies() -> Optional[Dict[str, str]]:
    if not COOKIES_FILE.exists():
        return None
    try:
        with open(COOKIES_FILE, "r") as f:
            cookie_data = json.load(f)
        saved_at = datetime.fromisoformat(cookie_data.get("saved_at", "2000-01-01"))
        if datetime.now() - saved_at > timedelta(days=30):
            logger.info("Stored cookies are expired (>30 days old)")
            return None
        return cookie_data.get("cookies")
    except Exception as e:
        logger.warning(f"Failed to load cookies: {e}")
        return None


def dict_to_cookiejar(cookies_dict: Dict[str, str], domain: str = ".myfitnesspal.com") -> CookieJar:
    jar = CookieJar()
    for name, value in cookies_dict.items():
        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith('.'),
            path="/",
            path_specified=True,
            secure=True,
            expires=int(time.time()) + 86400 * 30,
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None},
            rfc2109=False,
        )
        jar.set_cookie(cookie)
    return jar


async def _dismiss_consent_popup(page) -> None:
    """Remove GDPR/consent popup iframe that intercepts pointer events."""
    try:
        await page.wait_for_selector(
            '[id^="sp_message_container"], [id^="sp_message_iframe"]',
            timeout=5000,
            state="attached",
        )
        await page.evaluate("""
            document.querySelectorAll('[id^="sp_message_container"]').forEach(el => el.remove());
            document.querySelectorAll('[id^="sp_message_iframe"]').forEach(el => el.remove());
        """)
        logger.info("Camoufox: dismissed consent popup")
    except Exception:
        pass


async def authenticate_with_camoufox_async(username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, str]:
    """
    Authenticate with MyFitnessPal using Camoufox (hardened Firefox fork).
    Uses Persistent Context to save the session in a volume.
    """
    logger.info("Authenticating with Camoufox (Persistent Context)")
    ensure_config_dir()

    try:
        from camoufox import AsyncCamoufox
    except ImportError:
        raise RuntimeError("Camoufox library is not installed.")

    # Camoufox settings for maximum stealth
    camoufox_args = {
        "headless": not _USE_HEADED,
        "persistent_context": True,
        "user_data_dir": str(BROWSER_PROFILE_DIR),
        "humanize": True,
        "window": (_WIDTH, _HEIGHT),
    }

    async with AsyncCamoufox(**camoufox_args) as browser:
        # We don't use 'context' here because Camoufox manages the persistent context internally
        page = await browser.new_page()
        
        try:
            logger.info("Camoufox: checking session validity by visiting home page")
            await page.goto("https://www.myfitnesspal.com/", timeout=60000)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            
            # Check if we are already logged in
            current_url = page.url
            if "myfitnesspal.com/account/login" not in current_url and "login" not in current_url:
                # Try to find a logged-in indicator
                logged_in = await page.query_selector("a[href*='logout'], .user-avatar, .nav-item-user")
                if logged_in:
                    logger.info("Camoufox: persistent session is valid")
                    raw_cookies = await browser.cookies()
                    return {c["name"]: c["value"] for c in raw_cookies}

            if not username or not password:
                logger.warning("No valid session and no credentials provided. Please log in via VNC.")
                # If in headed mode, we can wait for manual login
                if _USE_HEADED:
                    logger.info("WAITING FOR MANUAL LOGIN VIA VNC (120s timeout)...")
                    await page.goto("https://www.myfitnesspal.com/account/login")
                    try:
                        await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=120000)
                        logger.info("Manual login detected!")
                        raw_cookies = await browser.cookies()
                        return {c["name"]: c["value"] for c in raw_cookies}
                    except Exception as te:
                        raise RuntimeError(f"Manual login timeout: {te}")
                else:
                    raise RuntimeError("Persistent session invalid and no credentials provided for headless login.")

            # Headless or Headed automated login
            logger.info(f"Camoufox: navigating to login page for {username}")
            await page.goto("https://www.myfitnesspal.com/account/login", timeout=60000)
            await _dismiss_consent_popup(page)

            await page.wait_for_selector('input[name="email"], input[name="username"]', timeout=30000)
            await page.fill('input[name="email"], input[name="username"]', username)
            await page.fill('input[name="password"]', password)
            
            logger.info("Camoufox: submitting login form")
            await page.click('button[type="submit"], input[type="submit"]', force=True)

            # Wait for navigation away from login
            try:
                await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=60000)
            except Exception:
                # Might be stuck on a captcha
                if _USE_HEADED:
                    logger.info("Stuck on login page? Check VNC for Captcha. Waiting 60s...")
                    await asyncio.sleep(60)
                else:
                    raise RuntimeError("Login failed or blocked by Captcha in headless mode.")

            raw_cookies = await browser.cookies()
            cookie_dict = {c["name"]: c["value"] for c in raw_cookies}
            
            if "__Secure-next-auth.session-token" not in cookie_dict:
                raise RuntimeError("Login completed but session token is missing.")

            logger.info("Camoufox: authentication successful")
            return cookie_dict

        except Exception as e:
            logger.error(f"Camoufox error: {e}")
            raise

async def get_mfp_client_async():
    """Async version of get_mfp_client — uses Camoufox for auth."""
    import myfitnesspal
    last_error = None
    username = os.environ.get("MFP_USERNAME")
    password = os.environ.get("MFP_PASSWORD")

    logger.info("Attempting authentication via Camoufox Persistent Context")
    try:
        cookies = await authenticate_with_camoufox_async(username, password)
        save_cookies(cookies)
        cookiejar = dict_to_cookiejar(cookies)
        client = myfitnesspal.Client(cookiejar=cookiejar)
        # Test client
        _ = client.get_date(date.today())
        logger.info("Successfully authenticated with Camoufox session")
        return client
    except Exception as e:
        last_error = e
        logger.warning(f"Camoufox authentication failed: {e}")

    # Fallback to stored cookies
    stored_cookies = load_cookies()
    if stored_cookies:
        logger.info("Attempting fallback to stored cookies.json")
        try:
            cookiejar = dict_to_cookiejar(stored_cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            _ = client.get_date(date.today())
            return client
        except Exception as e:
            last_error = e
            logger.warning(f"Stored cookie fallback failed: {e}")

    raise RuntimeError(f"All authentication methods failed. Last error: {str(last_error)}")


def get_mfp_client():
    """Sync wrapper."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(get_mfp_client_async(), loop)
            return future.result(timeout=180) # Longer timeout for Camoufox
        else:
            return loop.run_until_complete(get_mfp_client_async())
    except RuntimeError:
        return asyncio.run(get_mfp_client_async())

def parse_date(date_str: Optional[str] = None) -> date:
    if date_str is None:
        return date.today()
    return datetime.strptime(date_str, "%Y-%m-%d").date()

def format_nutrition_dict(nutrition: Dict[str, Any]) -> Dict[str, Any]:
    formatted = {}
    for key, value in nutrition.items():
        if hasattr(value, "magnitude"):
            formatted[key] = float(value.magnitude)
        else:
            formatted[key] = value
    return formatted

def format_meal_entry(entry) -> Dict[str, Any]:
    return {
        "name": entry.name,
        "short_name": getattr(entry, "short_name", None),
        "quantity": getattr(entry, "quantity", None),
        "unit": getattr(entry, "unit", None),
        "nutrition": format_nutrition_dict(entry.totals),
    }

def format_exercise(exercise) -> Dict[str, Any]:
    entries = exercise.get_as_list()
    return {"name": exercise.name, "entries": entries}

def ordered_dict_to_dict(od: OrderedDict) -> Dict[str, Any]:
    return {str(k): v for k, v in od.items()}

class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"

def format_response(data: Any, format_type: ResponseFormat, title: str = "") -> str:
    if format_type == ResponseFormat.JSON:
        return json.dumps(data, indent=2, default=str)
    lines = []
    if title:
        lines.append(f"## {title}\n")
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                lines.append(f"### {key}")
                for k, v in value.items():
                    lines.append(f"- **{k}**: {v}")
            elif isinstance(value, list):
                lines.append(f"### {key}")
                for item in value:
                    if isinstance(item, dict):
                        lines.append(f"- {item.get('name', str(item))}")
                        for k, v in item.items():
                            if k != "name":
                                lines.append(f"  - {k}: {v}")
                    else:
                        lines.append(f"- {item}")
            else:
                lines.append(f"- **{key}**: {value}")
    else:
        lines.append(str(data))
    return "\n".join(lines)

class GetDiaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, description="Date in YYYY-MM-DD format.", pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

class SearchFoodInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(..., description="Search query for food items")
    limit: int = Field(default=10, ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

class GetFoodDetailsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    mfp_id: str = Field(..., description="MyFitnessPal food item ID")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

class GetMeasurementsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    measurement: str = Field(default="Weight")
    start_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

class SetMeasurementInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    measurement: str = Field(default="Weight")
    value: float = Field(..., gt=0)

class GetExercisesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

class GetGoalsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

class SetGoalsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    calories: Optional[int] = Field(default=None, ge=500, le=10000)
    protein: Optional[int] = Field(default=None, ge=0)
    carbohydrates: Optional[int] = Field(default=None, ge=0)
    fat: Optional[int] = Field(default=None, ge=0)

class GetWaterInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

class GetReportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    report_name: str = Field(default="Net Calories")
    start_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

class AddFoodToDiaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    mfp_id: str = Field(...)
    meal: str = Field(default="Breakfast")
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: float = Field(default=1.0, gt=0)
    unit: Optional[str] = Field(default=None)

class SetWaterInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    cups: float = Field(..., ge=0, le=50)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

# ============================================================================

def add_food_to_diary(client, mfp_id: str, meal: str, target_date: date, quantity: float = 1.0, unit: Optional[str] = None) -> None:
    from urllib import parse
    date_str = target_date.strftime("%Y-%m-%d")
    diary_url = parse.urljoin(client.BASE_URL_SECURE, f"food/diary/{client.effective_username}?date={date_str}")
    document = client._get_document_for_url(diary_url)
    authenticity_token = document.xpath("(//input[@name='authenticity_token']/@value)[1]")
    if not authenticity_token:
        raise RuntimeError("Could not find authenticity token on diary page")
    authenticity_token = authenticity_token[0]
    meal_map = {"breakfast": "0", "lunch": "1", "dinner": "2", "snacks": "3", "snack": "3"}
    meal_index = meal_map.get(meal.lower(), "0")
    add_food_url = parse.urljoin(client.BASE_URL_SECURE, f"food/diary/{client.effective_username}/add")
    post_data = {"authenticity_token": authenticity_token, "date": date_str, "meal": meal_index, "food_id": mfp_id, "quantity": str(quantity)}
    if unit: post_data["unit"] = unit
    headers = {"Referer": diary_url, "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"}
    response = client.session.post(add_food_url, data=post_data, headers=headers)
    response.raise_for_status()

def set_water_intake(client, target_date: date, cups: float) -> None:
    from urllib import parse
    date_str = target_date.strftime("%Y-%m-%d")
    diary_url = parse.urljoin(client.BASE_URL_SECURE, f"food/diary/{client.effective_username}?date={date_str}")
    document = client._get_document_for_url(diary_url)
    authenticity_token = document.xpath("(//input[@name='authenticity_token']/@value)[1]")
    if not authenticity_token:
        raise RuntimeError("Could not find authenticity token on diary page")
    authenticity_token = authenticity_token[0]
    water_url = parse.urljoin(client.BASE_URL_SECURE, f"food/diary/{client.effective_username}/water")
    post_data = {"authenticity_token": authenticity_token, "date": date_str, "water": str(cups)}
    headers = {"Referer": diary_url, "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"}
    response = client.session.post(water_url, data=post_data, headers=headers)
    response.raise_for_status()

@mcp.tool(name="mfp_get_diary")
async def mfp_get_diary(params: GetDiaryInput) -> str:
    """Get the food diary for a specific date.

    Reads from the local SQLite cache when available (fast, ~10ms).
    Falls back to live MFP API when the date hasn't been synced yet.
    """
    try:
        target_date = parse_date(params.date)
        cache = get_cache()

        # 1. Try cache first
        daily = cache.get_diary_date(target_date)
        if daily is not None:
            # Build the response from cache
            entries = cache.get_diary_entries(target_date)
            meals: Dict[str, Any] = {}
            for e in entries:
                meal_name = e["meal"]
                if meal_name not in meals:
                    meals[meal_name] = {"entries": [], "totals": {}}
                meals[meal_name]["entries"].append({
                    "name": e["food_name"],
                    "nutrition": {
                        "calories": e["calories"],
                        "protein": e["protein"],
                        "carbohydrates": e["carbs"],
                        "fat": e["fat"],
                        "fiber": e["fiber"],
                        "sugar": e["sugar"],
                        "sodium": e["sodium"],
                    },
                })
            data = {
                "date": str(target_date),
                "meals": meals,
                "daily_totals": {
                    k: v for k, v in daily.items()
                    if k not in ("date", "updated_at", "water_ml")
                },
                "water": daily.get("water_ml"),
                "source": "cache",
            }
            return format_response(data, params.response_format, f"Food Diary for {target_date}")

        # 2. Fallback: live MFP
        client = await get_mfp_client_async()
        day = client.get_date(target_date)
        data = {"date": str(target_date), "meals": {}, "daily_totals": {}, "daily_goals": day.goals, "water": day.water, "notes": day.notes or "", "source": "live"}
        for meal in day.meals:
            data["meals"][meal.name] = {"entries": [format_meal_entry(entry) for entry in meal.entries], "totals": format_nutrition_dict(meal.totals)}
        totals = {}
        for entry in day.entries:
            for key, value in entry.totals.items():
                val = float(value.magnitude) if hasattr(value, "magnitude") else value
                totals[key] = totals.get(key, 0) + val
        data["daily_totals"] = totals
        return format_response(data, params.response_format, f"Food Diary for {target_date}")
    except Exception as e: return f"Error retrieving diary: {str(e)}"

@mcp.tool(name="mfp_search_food")
async def mfp_search_food(params: SearchFoodInput) -> str:
    """Search the MyFitnessPal food database."""
    try:
        client = await get_mfp_client_async()
        results = client.get_food_search_results(params.query)[: params.limit]
        data = {"query": params.query, "results": [{"name": i.name, "brand": i.brand, "serving": i.serving, "calories": i.calories, "mfp_id": i.mfp_id} for i in results]}
        return format_response(data, params.response_format, f"Food Search Results for '{params.query}'")
    except Exception as e: return f"Error searching foods: {str(e)}"

@mcp.tool(name="mfp_get_food_details")
async def mfp_get_food_details(params: GetFoodDetailsInput) -> str:
    """Get detailed nutritional information for a specific food item."""
    try:
        client = await get_mfp_client_async()
        item = client.get_food_item_details(params.mfp_id)
        data = {"mfp_id": params.mfp_id, "description": getattr(item, "description", "N/A"), "nutrition": {"protein": getattr(item, "protein", None), "carbohydrates": getattr(item, "carbohydrates", None), "fat": getattr(item, "fat", None)}, "servings": [str(s) for s in getattr(item, "servings", [])]}
        return format_response(data, params.response_format, "Food Item Details")
    except Exception as e: return f"Error getting food details: {str(e)}"

@mcp.tool(name="mfp_get_measurements")
async def mfp_get_measurements(params: GetMeasurementsInput) -> str:
    """Get body measurements over a date range.

    Reads from the local SQLite cache when available (fast, ~10ms).
    Falls back to live MFP API when the data hasn't been synced.
    """
    try:
        end = parse_date(params.end_date)
        start = parse_date(params.start_date) if params.start_date else end - timedelta(days=30)
        cache = get_cache()

        # 1. Try cache first
        cached = cache.get_measurements(params.measurement, start, end)
        if cached:
            values = {r["date"]: r["value"] for r in cached}
            return format_response({"values": values, "source": "cache"}, params.response_format, f"{params.measurement} History")

        # 2. Fallback: live MFP
        client = await get_mfp_client_async()
        measurements = client.get_measurements(params.measurement, start, end)
        return format_response({"values": ordered_dict_to_dict(measurements), "source": "live"}, params.response_format, f"{params.measurement} History")
    except Exception as e: return f"Error getting measurements: {str(e)}"

@mcp.tool(name="mfp_set_measurement")
async def mfp_set_measurement(params: SetMeasurementInput) -> str:
    """Log a new body measurement for today."""
    try:
        client = await get_mfp_client_async()
        client.set_measurements(params.measurement, params.value)
        # Update local cache immediately
        get_cache().upsert_measurement(date.today(), params.measurement, params.value)
        return f"Successfully logged {params.measurement}: {params.value}"
    except Exception as e: return f"Error setting measurement: {str(e)}"

@mcp.tool(name="mfp_add_food_to_diary")
async def mfp_add_food_to_diary(params: AddFoodToDiaryInput) -> str:
    """Add a food item to the food diary."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        add_food_to_diary(client=client, mfp_id=params.mfp_id, meal=params.meal, target_date=target_date, quantity=params.quantity, unit=params.unit)
        # Invalidate cache so next sync picks up the change
        get_cache().mark_synced(target_date, status="stale")
        return f"Successfully added {params.mfp_id} to {params.meal}"
    except Exception as e: return f"Error adding food to diary: {str(e)}"

@mcp.tool(name="mfp_set_water")
async def mfp_set_water(params: SetWaterInput) -> str:
    """Log water intake for a specific date."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        set_water_intake(client=client, target_date=target_date, cups=params.cups)
        # Invalidate cache so next sync picks up the change
        get_cache().mark_synced(target_date, status="stale")
        return f"Successfully logged {params.cups} cups of water"
    except Exception as e: return f"Error setting water intake: {str(e)}"

if __name__ == "__main__":
    transport = os.environ.get("MFP_TRANSPORT", "streamable-http")
    logger.info(f"Starting MCP server with Camoufox — transport={transport}, host={_HOST}, port={_PORT}")
    mcp.run(transport=transport)
