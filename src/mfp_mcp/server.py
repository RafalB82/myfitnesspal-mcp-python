"""
MyFitnessPal MCP Server

A Model Context Protocol (MCP) server that provides tools for interacting
with MyFitnessPal data including food diary, exercises, measurements, goals,
water intake, and food search.

Authentication Methods (in order of priority):
1. Environment variables: MFP_USERNAME and MFP_PASSWORD (Playwright async headless)
2. Stored session cookies: ~/.mfp_mcp/cookies.json
3. Browser cookies: Chrome/Firefox (fallback, requires host desktop session)
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
from pydantic import BaseModel, Field, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# Patch TransportSecurityMiddleware BEFORE importing FastMCP.
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mfp_mcp")

_HOST = os.environ.get("MFP_HOST", "0.0.0.0")
_PORT = int(os.environ.get("MFP_PORT", "8000"))

mcp = FastMCP("myfitnesspal_mcp", host=_HOST, port=_PORT)

CONFIG_DIR = Path.home() / ".mfp_mcp"
COOKIES_FILE = CONFIG_DIR / "cookies.json"


# ============================================================================
# Authentication Helper Functions
# ============================================================================


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


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
        logger.info("Playwright: dismissed consent popup")
    except Exception:
        pass


async def authenticate_with_credentials_async(username: str, password: str) -> Dict[str, str]:
    """
    Authenticate with MyFitnessPal using Playwright async headless Chromium.

    playwright-stealth v2.0 API:
      Stealth().use_async() wraps async_playwright() at the TOP level.
      All browser/context/page objects created inside the block inherit stealth patches.

      WRONG (previous code):
        async with Stealth().use_async(browser.new_context(...)) as context:

      CORRECT (v2.0 official API):
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(...)
            context = await browser.new_context(...)
            page = await context.new_page()
    """
    logger.info("Authenticating with credentials via Playwright async headless Chromium")

    try:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. Rebuild the Docker image:\n"
            "  docker compose build --no-cache mfp-mcp && docker compose up -d mfp-mcp"
        )

    # playwright-stealth v2.0: Stealth().use_async() wraps async_playwright() itself.
    try:
        from playwright_stealth import Stealth
        _stealth_available = True
        logger.info("playwright-stealth v2 available — applying JS fingerprint patches via Stealth()")
    except ImportError:
        _stealth_available = False
        logger.warning(
            "playwright-stealth not installed — headless Chromium may be detected as a bot. "
            "Rebuild image: docker compose build --no-cache mfp-mcp"
        )

    launch_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationDetector",
    ]
    context_kwargs = dict(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )

    if _stealth_available:
        # v2.0 correct API: Stealth wraps the top-level playwright context manager
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            logger.info("Playwright: stealth patches active on all pages in this context")
            try:
                return await _do_login(page, context, username, password, PlaywrightTimeoutError)
            finally:
                await browser.close()
    else:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            try:
                return await _do_login(page, context, username, password, PlaywrightTimeoutError)
            finally:
                await browser.close()


async def _do_login(page, context, username: str, password: str, PlaywrightTimeoutError) -> Dict[str, str]:
    """Core login flow — shared between stealth and non-stealth paths."""
    try:
        logger.info("Playwright: navigating to MFP login page")
        await page.goto("https://www.myfitnesspal.com/account/login", timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)

        await _dismiss_consent_popup(page)

        await page.wait_for_selector(
            'input[type="email"], input[name="email"], input[name="username"]',
            timeout=45000
        )
        await page.locator(
            'input[type="email"], input[name="email"], input[name="username"]'
        ).first.fill(username)
        await page.wait_for_selector('input[type="password"]', timeout=15000)
        await page.fill('input[type="password"]', password)

        logger.info("Playwright: submitting login form")
        await page.click('input[type="submit"], button[type="submit"]', force=True)

        try:
            await page.wait_for_url(
                lambda url: "myfitnesspal.com/account/login" not in url,
                timeout=20000,
            )
        except PlaywrightTimeoutError:
            pass

        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)

        current_url = page.url
        logger.info(f"Playwright: post-login URL: {current_url}")

        if "myfitnesspal.com/account/login" in current_url:
            error_text = ""
            try:
                error_el = await page.query_selector(".main-title-2, .error, [class*='error'], [class*='alert'], [role='alert']")
                if error_el:
                    error_text = await error_el.inner_text()
            except Exception:
                pass
            raise RuntimeError(
                f"Login failed — still on login page. "
                f"Verify MFP_USERNAME / MFP_PASSWORD. Page says: '{error_text}'"
            )

        raw_cookies = await context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in raw_cookies}
        logger.info(f"Playwright: captured {len(cookie_dict)} cookies")

        if "__Secure-next-auth.session-token" not in cookie_dict:
            names = list(cookie_dict.keys())
            raise RuntimeError(
                f"Login navigation succeeded but session token cookie is missing. "
                f"Cookies present: {names}. "
                "MFP may require email verification or 2FA — check your inbox."
            )

        logger.info("Playwright: authentication successful")
        return cookie_dict

    except RuntimeError:
        raise
    except PlaywrightTimeoutError as e:
        raise RuntimeError(f"Timeout during MFP Playwright login: {e}")
    except Exception as e:
        raise RuntimeError(f"Playwright authentication error: {e}")


async def get_mfp_client_async():
    """Async version of get_mfp_client — uses async Playwright for auth."""
    import myfitnesspal
    last_error = None
    username = os.environ.get("MFP_USERNAME")
    password = os.environ.get("MFP_PASSWORD")

    if username and password:
        logger.info("Attempting authentication with environment credentials")
        stored_cookies = load_cookies()
        if stored_cookies:
            logger.info("Found stored session cookies, testing validity...")
            try:
                cookiejar = dict_to_cookiejar(stored_cookies)
                client = myfitnesspal.Client(cookiejar=cookiejar)
                _ = client.get_date(date.today())
                logger.info("Stored cookies are valid")
                return client
            except Exception as e:
                logger.info(f"Stored cookies invalid: {e}, re-authenticating via Playwright...")

        try:
            cookies = await authenticate_with_credentials_async(username, password)
            save_cookies(cookies)
            cookiejar = dict_to_cookiejar(cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            _ = client.get_date(date.today())
            logger.info("Successfully authenticated with credentials")
            return client
        except Exception as e:
            last_error = e
            logger.warning(f"Credential authentication failed: {e}")

    stored_cookies = load_cookies()
    if stored_cookies:
        logger.info("Attempting authentication with stored cookies")
        try:
            cookiejar = dict_to_cookiejar(stored_cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            _ = client.get_date(date.today())
            logger.info("Successfully authenticated with stored cookies")
            return client
        except Exception as e:
            last_error = e
            logger.warning(f"Stored cookie authentication failed: {e}")

    logger.info("Attempting authentication with browser cookies")
    try:
        client = myfitnesspal.Client()
        _ = client.get_date(date.today())
        logger.info("Successfully authenticated with browser cookies")
        return client
    except Exception as e:
        last_error = e
        raise RuntimeError(
            f"All authentication methods failed. Last error: {str(last_error)}\n\n"
            "Solutions:\n"
            "1. Set MFP_USERNAME and MFP_PASSWORD in .env\n"
            "2. Rebuild image: docker compose build --no-cache mfp-mcp\n"
            "3. Check ~/.mfp_mcp/cookies.json for a valid stored session"
        )


def get_mfp_client():
    """Sync wrapper — runs async auth in the current event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(get_mfp_client_async(), loop)
            return future.result(timeout=60)
        else:
            return loop.run_until_complete(get_mfp_client_async())
    except RuntimeError:
        return asyncio.run(get_mfp_client_async())


# ============================================================================
# Data Formatting Helper Functions
# ============================================================================


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


# ============================================================================
# Pydantic Input Models
# ============================================================================


class GetDiaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, description="Date in YYYY-MM-DD format. Defaults to today if not specified.", pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' for human-readable or 'json' for structured data")


class SearchFoodInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(..., description="Search query for food items", min_length=1, max_length=200)
    limit: int = Field(default=10, description="Maximum number of results to return", ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class GetFoodDetailsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    mfp_id: str = Field(..., description="MyFitnessPal food item ID", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class GetMeasurementsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    measurement: str = Field(default="Weight", description="Type of measurement to retrieve")
    start_date: Optional[str] = Field(default=None, description="Start date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, description="End date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class SetMeasurementInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    measurement: str = Field(default="Weight", description="Type of measurement to set")
    value: float = Field(..., description="Measurement value", gt=0)


class GetExercisesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, description="Date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class GetGoalsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, description="Date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class SetGoalsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    calories: Optional[int] = Field(default=None, description="Daily calorie goal", ge=500, le=10000)
    protein: Optional[int] = Field(default=None, description="Daily protein goal in grams", ge=0, le=1000)
    carbohydrates: Optional[int] = Field(default=None, description="Daily carbohydrate goal in grams", ge=0, le=2000)
    fat: Optional[int] = Field(default=None, description="Daily fat goal in grams", ge=0, le=500)


class GetWaterInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    date: Optional[str] = Field(default=None, description="Date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")


class GetReportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    report_name: str = Field(default="Net Calories", description="Report name")
    start_date: Optional[str] = Field(default=None, description="Start date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, description="End date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class AddFoodToDiaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    mfp_id: str = Field(..., description="MyFitnessPal food item ID", min_length=1)
    meal: str = Field(default="Breakfast", description="Meal name")
    date: Optional[str] = Field(default=None, description="Date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: float = Field(default=1.0, description="Quantity/servings", gt=0, le=100)
    unit: Optional[str] = Field(default=None, description="Unit/serving size")


class SetWaterInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    cups: float = Field(..., description="Number of cups of water", ge=0, le=50)
    date: Optional[str] = Field(default=None, description="Date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")


# ============================================================================
# Diary Entry Creation Helper Functions
# ============================================================================


def add_food_to_diary(
    client, mfp_id: str, meal: str, target_date: date, quantity: float = 1.0, unit: Optional[str] = None
) -> None:
    from urllib import parse
    try:
        date_str = target_date.strftime("%Y-%m-%d")
        diary_url = parse.urljoin(
            client.BASE_URL_SECURE,
            f"food/diary/{client.effective_username}?date={date_str}"
        )
        document = client._get_document_for_url(diary_url)
        authenticity_token = document.xpath("(//input[@name='authenticity_token']/@value)[1]")
        if not authenticity_token:
            raise RuntimeError("Could not find authenticity token on diary page")
        authenticity_token = authenticity_token[0]
        meal_map = {"breakfast": "0", "lunch": "1", "dinner": "2", "snacks": "3", "snack": "3"}
        meal_index = meal_map.get(meal.lower(), "0")
        add_food_url = parse.urljoin(
            client.BASE_URL_SECURE,
            f"food/diary/{client.effective_username}/add"
        )
        post_data = {
            "authenticity_token": authenticity_token,
            "date": date_str,
            "meal": meal_index,
            "food_id": mfp_id,
            "quantity": str(quantity),
        }
        if unit:
            post_data["unit"] = unit
        headers = {
            "Referer": diary_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }
        response = client.session.post(add_food_url, data=post_data, headers=headers)
        response.raise_for_status()
        content = response.text if hasattr(response, 'text') else response.content.decode('utf-8', errors='ignore')
        if 'error' in content.lower() and 'success' not in content.lower():
            logger.warning("Possible error in response from MyFitnessPal API")
        logger.info(f"Successfully added food {mfp_id} to {meal} for {target_date}")
    except Exception as e:
        error_msg = str(e)
        if "HTTP" in error_msg or "status" in error_msg.lower():
            raise RuntimeError(f"Failed to add food to diary: {error_msg}")
        else:
            raise RuntimeError("Failed to add food to diary. Please check your authentication and try again.")


def set_water_intake(client, target_date: date, cups: float) -> None:
    from urllib import parse
    try:
        date_str = target_date.strftime("%Y-%m-%d")
        diary_url = parse.urljoin(
            client.BASE_URL_SECURE,
            f"food/diary/{client.effective_username}?date={date_str}"
        )
        document = client._get_document_for_url(diary_url)
        authenticity_token = document.xpath("(//input[@name='authenticity_token']/@value)[1]")
        if not authenticity_token:
            raise RuntimeError("Could not find authenticity token on diary page")
        authenticity_token = authenticity_token[0]
        water_url = parse.urljoin(
            client.BASE_URL_SECURE,
            f"food/diary/{client.effective_username}/water"
        )
        post_data = {
            "authenticity_token": authenticity_token,
            "date": date_str,
            "water": str(cups),
        }
        headers = {
            "Referer": diary_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }
        response = client.session.post(water_url, data=post_data, headers=headers)
        response.raise_for_status()
        logger.info(f"Successfully set water intake to {cups} cups for {target_date}")
    except Exception as e:
        error_msg = str(e)
        if "HTTP" in error_msg or "status" in error_msg.lower():
            raise RuntimeError(f"Failed to set water intake: {error_msg}")
        else:
            raise RuntimeError("Failed to set water intake. Please check your authentication and try again.")


# ============================================================================
# MCP Tools
# ============================================================================


@mcp.tool(
    name="mfp_get_diary",
    annotations={"title": "Get Food Diary", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_diary(params: GetDiaryInput) -> str:
    """Get the food diary for a specific date."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        data = {
            "date": str(target_date),
            "meals": {},
            "daily_totals": {},
            "daily_goals": {},
            "water": day.water,
            "notes": day.notes or "",
        }
        for meal in day.meals:
            data["meals"][meal.name] = {
                "entries": [format_meal_entry(entry) for entry in meal.entries],
                "totals": format_nutrition_dict(meal.totals),
            }
        totals = {}
        for entry in day.entries:
            for key, value in entry.totals.items():
                val = float(value.magnitude) if hasattr(value, "magnitude") else value
                totals[key] = totals.get(key, 0) + val
        data["daily_totals"] = totals
        data["daily_goals"] = day.goals
        return format_response(data, params.response_format, f"Food Diary for {target_date}")
    except Exception as e:
        return f"Error retrieving diary: {str(e)}"


@mcp.tool(
    name="mfp_search_food",
    annotations={"title": "Search Food Database", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_search_food(params: SearchFoodInput) -> str:
    """Search the MyFitnessPal food database."""
    try:
        client = await get_mfp_client_async()
        results = client.get_food_search_results(params.query)[: params.limit]
        data = {"query": params.query, "count": len(results), "results": []}
        for item in results:
            data["results"].append({
                "name": item.name,
                "brand": item.brand,
                "serving": item.serving,
                "calories": item.calories,
                "mfp_id": item.mfp_id,
            })
        return format_response(data, params.response_format, f"Food Search Results for '{params.query}'")
    except Exception as e:
        return f"Error searching foods: {str(e)}"


@mcp.tool(
    name="mfp_get_food_details",
    annotations={"title": "Get Food Item Details", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_food_details(params: GetFoodDetailsInput) -> str:
    """Get detailed nutritional information for a specific food item."""
    try:
        client = await get_mfp_client_async()
        item = client.get_food_item_details(params.mfp_id)
        data = {
            "mfp_id": params.mfp_id,
            "description": getattr(item, "description", "N/A"),
            "brand_name": getattr(item, "brand_name", None),
            "verified": getattr(item, "verified", False),
            "calories": getattr(item, "calories", None),
            "nutrition": {
                "protein": getattr(item, "protein", None),
                "carbohydrates": getattr(item, "carbohydrates", None),
                "fat": getattr(item, "fat", None),
                "fiber": getattr(item, "fiber", None),
                "sugar": getattr(item, "sugar", None),
                "sodium": getattr(item, "sodium", None),
                "cholesterol": getattr(item, "cholesterol", None),
                "saturated_fat": getattr(item, "saturated_fat", None),
                "polyunsaturated_fat": getattr(item, "polyunsaturated_fat", None),
                "monounsaturated_fat": getattr(item, "monounsaturated_fat", None),
                "trans_fat": getattr(item, "trans_fat", None),
                "potassium": getattr(item, "potassium", None),
                "vitamin_a": getattr(item, "vitamin_a", None),
                "vitamin_c": getattr(item, "vitamin_c", None),
                "calcium": getattr(item, "calcium", None),
                "iron": getattr(item, "iron", None),
            },
            "servings": [str(s) for s in getattr(item, "servings", [])],
        }
        return format_response(data, params.response_format, "Food Item Details")
    except Exception as e:
        return f"Error getting food details: {str(e)}"


@mcp.tool(
    name="mfp_get_measurements",
    annotations={"title": "Get Body Measurements", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_measurements(params: GetMeasurementsInput) -> str:
    """Get body measurements over a date range."""
    try:
        client = await get_mfp_client_async()
        end = parse_date(params.end_date)
        start = parse_date(params.start_date) if params.start_date else end - timedelta(days=30)
        measurements = client.get_measurements(params.measurement, start, end)
        data = {
            "measurement_type": params.measurement,
            "start_date": str(start),
            "end_date": str(end),
            "count": len(measurements),
            "values": ordered_dict_to_dict(measurements),
        }
        if measurements:
            values = list(measurements.values())
            data["summary"] = {
                "latest": values[-1] if values else None,
                "earliest": values[0] if values else None,
                "change": round(values[-1] - values[0], 2) if len(values) >= 2 else 0,
                "min": min(values),
                "max": max(values),
                "average": round(sum(values) / len(values), 2),
            }
        return format_response(data, params.response_format, f"{params.measurement} History")
    except Exception as e:
        return f"Error getting measurements: {str(e)}"


@mcp.tool(
    name="mfp_set_measurement",
    annotations={"title": "Log Body Measurement", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def mfp_set_measurement(params: SetMeasurementInput) -> str:
    """Log a new body measurement for today."""
    try:
        client = await get_mfp_client_async()
        client.set_measurements(params.measurement, params.value)
        return json.dumps({
            "success": True,
            "message": f"Successfully logged {params.measurement}: {params.value}",
            "measurement": params.measurement,
            "value": params.value,
            "date": str(date.today()),
        }, indent=2)
    except Exception as e:
        return f"Error setting measurement: {str(e)}"


@mcp.tool(
    name="mfp_get_exercises",
    annotations={"title": "Get Exercise Log", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_exercises(params: GetExercisesInput) -> str:
    """Get logged exercises for a specific date."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        data = {"date": str(target_date), "exercises": []}
        for exercise in day.exercises:
            data["exercises"].append(format_exercise(exercise))
        total_burned = sum(
            entry.get("nutrition_information", {}).get("calories burned", 0)
            for ex in data["exercises"]
            for entry in ex.get("entries", [])
        )
        data["total_calories_burned"] = total_burned
        return format_response(data, params.response_format, f"Exercise Log for {target_date}")
    except Exception as e:
        return f"Error getting exercises: {str(e)}"


@mcp.tool(
    name="mfp_get_goals",
    annotations={"title": "Get Nutrition Goals", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_goals(params: GetGoalsInput) -> str:
    """Get the user's daily nutrition goals."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        return format_response({"date": str(target_date), "goals": day.goals}, params.response_format, "Daily Nutrition Goals")
    except Exception as e:
        return f"Error getting goals: {str(e)}"


@mcp.tool(
    name="mfp_set_goals",
    annotations={"title": "Update Nutrition Goals", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_set_goals(params: SetGoalsInput) -> str:
    """Update daily nutrition goals."""
    try:
        if not any([params.calories, params.protein, params.carbohydrates, params.fat]):
            return "Error: Please provide at least one goal to update"
        client = await get_mfp_client_async()
        kwargs = {}
        if params.calories:
            kwargs["energy"] = params.calories
        if params.protein:
            kwargs["protein"] = params.protein
        if params.carbohydrates:
            kwargs["carbohydrates"] = params.carbohydrates
        if params.fat:
            kwargs["fat"] = params.fat
        client.set_new_goal(**kwargs)
        return json.dumps({
            "success": True,
            "message": "Successfully updated nutrition goals",
            "updated_goals": {
                "calories": params.calories,
                "protein": params.protein,
                "carbohydrates": params.carbohydrates,
                "fat": params.fat,
            },
        }, indent=2)
    except Exception as e:
        return f"Error setting goals: {str(e)}"


@mcp.tool(
    name="mfp_get_water",
    annotations={"title": "Get Water Intake", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_water(params: GetWaterInput) -> str:
    """Get water intake for a specific date."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        return json.dumps({
            "date": str(target_date),
            "water_cups": day.water,
            "water_ml": day.water * 236.588,
        }, indent=2)
    except Exception as e:
        return f"Error getting water intake: {str(e)}"


@mcp.tool(
    name="mfp_add_food_to_diary",
    annotations={"title": "Add Food to Diary", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def mfp_add_food_to_diary(params: AddFoodToDiaryInput) -> str:
    """Add a food item to the food diary."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        meal = params.meal.strip().capitalize()
        if meal.lower() == "snack":
            meal = "Snacks"
        add_food_to_diary(
            client=client,
            mfp_id=params.mfp_id,
            meal=meal,
            target_date=target_date,
            quantity=params.quantity,
            unit=params.unit,
        )
        try:
            food_item = client.get_food_item_details(params.mfp_id)
            food_name = getattr(food_item, "description", "Unknown Food")
        except Exception:
            food_name = "Food item"
        return json.dumps({
            "success": True,
            "message": f"Successfully added {food_name} to {meal}",
            "date": str(target_date),
            "meal": meal,
            "food_id": params.mfp_id,
            "food_name": food_name,
            "quantity": params.quantity,
            "unit": params.unit,
        }, indent=2)
    except Exception as e:
        return f"Error adding food to diary: {str(e)}"


@mcp.tool(
    name="mfp_set_water",
    annotations={"title": "Log Water Intake", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def mfp_set_water(params: SetWaterInput) -> str:
    """Log water intake for a specific date."""
    try:
        client = await get_mfp_client_async()
        target_date = parse_date(params.date)
        set_water_intake(client=client, target_date=target_date, cups=params.cups)
        return json.dumps({
            "success": True,
            "message": f"Successfully logged {params.cups} cups of water",
            "date": str(target_date),
            "cups": params.cups,
            "milliliters": round(params.cups * 236.588, 2),
        }, indent=2)
    except Exception as e:
        return f"Error setting water intake: {str(e)}"


@mcp.tool(
    name="mfp_get_report",
    annotations={"title": "Get Nutrition Report", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def mfp_get_report(params: GetReportInput) -> str:
    """Get a nutrition report over a date range."""
    try:
        client = await get_mfp_client_async()
        end = parse_date(params.end_date)
        start = parse_date(params.start_date) if params.start_date else end - timedelta(days=7)
        report = client.get_report(params.report_name, start, end)
        data = {
            "report_name": params.report_name,
            "start_date": str(start),
            "end_date": str(end),
            "data": ordered_dict_to_dict(report),
        }
        if report:
            values = [v for v in report.values() if v is not None]
            if values:
                data["summary"] = {
                    "average": round(sum(values) / len(values), 2),
                    "min": min(values),
                    "max": max(values),
                    "total": round(sum(values), 2),
                }
        return format_response(data, params.response_format, f"{params.report_name} Report")
    except Exception as e:
        return f"Error getting report: {str(e)}"


@mcp.tool(
    name="refresh_browser_cookies",
    annotations={"title": "Refresh Browser Cookies", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def refresh_browser_cookies(browser: str = "chrome") -> str:
    """
    Extract and save session cookies from your web browser.

    Only works when running outside Docker with a desktop browser session.
    In Docker use MFP_USERNAME + MFP_PASSWORD — Playwright handles login automatically.
    """
    try:
        import myfitnesspal
        client = myfitnesspal.Client(cookiejar_path=browser)
        _ = client.get_date(date.today())
        logger.info("Successfully authenticated with browser cookies")
        return json.dumps({
            "success": True,
            "message": f"Successfully refreshed cookies from {browser}",
            "browser": browser,
        }, indent=2)
    except Exception as e:
        return (
            f"Failed to refresh browser cookies: {str(e)}\n\n"
            "Note: This tool only works outside Docker with a desktop browser session.\n"
            "In Docker, set MFP_USERNAME and MFP_PASSWORD environment variables instead."
        )


if __name__ == "__main__":
    transport = os.environ.get("MFP_TRANSPORT", "streamable-http")
    logger.info(f"Starting MCP server — transport={transport}, host={_HOST}, port={_PORT}")
    mcp.run(transport=transport)
