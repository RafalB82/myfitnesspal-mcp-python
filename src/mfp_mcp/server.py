"""
MyFitnessPal MCP Server

A Model Context Protocol (MCP) server that provides tools for interacting
with MyFitnessPal data including food diary, exercises, measurements, goals,
water intake, and food search.

Authentication Methods (in order of priority):
1. Environment variables: MFP_USERNAME and MFP_PASSWORD
2. Stored session cookies: ~/.mfp_mcp/cookies.json
3. Browser cookies: Chrome/Firefox (fallback)
"""

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
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# Monkey-patch: disable Host-header validation in mcp.server.transport_security
# This is required when the MCP server runs behind a reverse proxy (e.g. Traefik)
# that forwards requests with the public Host header instead of "localhost".
# The patch replaces the SecurityMiddleware dispatch with a pass-through that
# skips the host check while keeping everything else intact.
# ---------------------------------------------------------------------------
try:
    import mcp.server.transport_security as _ts

    class _PermissiveSecurityMiddleware(_ts.SecurityMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[override]
            return await call_next(request)

    _ts.SecurityMiddleware = _PermissiveSecurityMiddleware
except Exception as _patch_err:  # pragma: no cover
    # If the module layout changes in a future mcp release, log and continue.
    logging.getLogger("mfp_mcp").warning(
        "Could not patch transport_security: %s", _patch_err
    )
# ---------------------------------------------------------------------------

# Configure logging to stderr (required for stdio transport)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mfp_mcp")

# Initialize MCP server
mcp = FastMCP("myfitnesspal_mcp")

# Configuration paths
CONFIG_DIR = Path.home() / ".mfp_mcp"
COOKIES_FILE = CONFIG_DIR / "cookies.json"


# ============================================================================
# Authentication Helper Functions
# ============================================================================


def ensure_config_dir():
    """Ensure the config directory exists."""
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


def authenticate_with_credentials(username: str, password: str) -> Dict[str, str]:
    logger.info("Authenticating with credentials")
    LOGIN_URL = "https://www.myfitnesspal.com/account/login"
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(LOGIN_URL)
            response.raise_for_status()
            cookies = dict(response.cookies)
            login_data = {"username": username, "password": password}
            client.post(
                LOGIN_URL,
                data=login_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": LOGIN_URL,
                },
            )
            all_cookies = dict(client.cookies)
            session_indicators = ["user", "session", "auth", "logged_in"]
            has_session = any(
                any(indicator in name.lower() for indicator in session_indicators)
                for name in all_cookies.keys()
            )
            if has_session or len(all_cookies) > len(cookies):
                logger.info("Successfully authenticated with credentials")
                return all_cookies
            test_response = client.get("https://www.myfitnesspal.com/food/diary")
            if test_response.status_code == 200 and "login" not in str(test_response.url).lower():
                return dict(client.cookies)
            raise RuntimeError("Login appeared to fail - no session cookies received")
    except httpx.HTTPError as e:
        raise RuntimeError(f"HTTP error during authentication: {e}")
    except Exception as e:
        raise RuntimeError(f"Authentication failed: {e}")


def get_mfp_client():
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
                logger.info(f"Stored cookies invalid: {e}, re-authenticating...")
        try:
            cookies = authenticate_with_credentials(username, password)
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
            "Please try one of these solutions:\n"
            "1. Set MFP_USERNAME and MFP_PASSWORD environment variables in Claude Desktop config\n"
            "2. Log into myfitnesspal.com in Chrome or Firefox\n"
            "3. Check ~/.mfp_mcp/cookies.json for stored session"
        )


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
        client = get_mfp_client()
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
            meal_data = {
                "entries": [format_meal_entry(entry) for entry in meal.entries],
                "totals": format_nutrition_dict(meal.totals),
            }
            data["meals"][meal.name] = meal_data
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
        client = get_mfp_client()
        results = client.get_food_search_results(params.query)
        results = results[: params.limit]
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
        client = get_mfp_client()
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
            "servings": [],
        }
        if hasattr(item, "servings"):
            for serving in item.servings:
                data["servings"].append(str(serving))
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
        client = get_mfp_client()
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
        client = get_mfp_client()
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
        client = get_mfp_client()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        data = {"date": str(target_date), "exercises": []}
        for exercise in day.exercises:
            data["exercises"].append(format_exercise(exercise))
        total_burned = 0
        for ex in data["exercises"]:
            for entry in ex.get("entries", []):
                if "nutrition_information" in entry:
                    total_burned += entry["nutrition_information"].get("calories burned", 0)
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
        client = get_mfp_client()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        data = {"date": str(target_date), "goals": day.goals}
        return format_response(data, params.response_format, "Daily Nutrition Goals")
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
        client = get_mfp_client()
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
        client = get_mfp_client()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)
        data = {
            "date": str(target_date),
            "water_cups": day.water,
            "water_ml": day.water * 236.588,
        }
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error getting water intake: {str(e)}"


@mcp.tool(
    name="mfp_add_food_to_diary",
    annotations={"title": "Add Food to Diary", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def mfp_add_food_to_diary(params: AddFoodToDiaryInput) -> str:
    """Add a food item to the food diary."""
    try:
        client = get_mfp_client()
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
        client = get_mfp_client()
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
        client = get_mfp_client()
        end = parse_date(params.end_date)
        start = parse_date(params.start_date) if params.start_date else end - timedelta(days=7)
        report = client.get_report(
            report_name=params.report_name,
            report_category="Nutrition",
            lower_bound=start,
            upper_bound=end,
        )
        data = {
            "report_name": params.report_name,
            "start_date": str(start),
            "end_date": str(end),
            "values": ordered_dict_to_dict(report) if isinstance(report, OrderedDict) else report,
        }
        if report:
            values = list(report.values())
            numeric_values = [v for v in values if isinstance(v, (int, float))]
            if numeric_values:
                data["summary"] = {
                    "total": sum(numeric_values),
                    "average": round(sum(numeric_values) / len(numeric_values), 2),
                    "min": min(numeric_values),
                    "max": max(numeric_values),
                }
        return format_response(data, params.response_format, f"{params.report_name} Report")
    except Exception as e:
        return f"Error getting report: {str(e)}"


# ============================================================================
# Cookie Management Tool
# ============================================================================


@mcp.tool()
def refresh_browser_cookies(browser: str = "chrome") -> str:
    """Extract and save session cookies from your web browser."""
    import browser_cookie3
    try:
        if browser.lower() == "chrome":
            cj = browser_cookie3.chrome(domain_name='.myfitnesspal.com')
        elif browser.lower() == "firefox":
            cj = browser_cookie3.firefox(domain_name='.myfitnesspal.com')
        else:
            return f"Unsupported browser: {browser}. Use 'chrome' or 'firefox'."
        cookies = {c.name: c.value for c in cj}
        if '__Secure-next-auth.session-token' not in cookies:
            return (
                f"No session token found in {browser}. "
                "Please make sure you are logged into myfitnesspal.com in your browser."
            )
        save_cookies(cookies)
        try:
            import myfitnesspal
            cookiejar = dict_to_cookiejar(cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            _ = client.get_date(date.today())
            return f"Successfully extracted and verified {len(cookies)} cookies from {browser}. Authentication is now working!"
        except Exception as e:
            return f"Cookies extracted from {browser} but verification failed: {e}."
    except Exception as e:
        error_msg = str(e)
        if "Operation not permitted" in error_msg:
            return f"Permission denied reading {browser} cookies (macOS security restriction)."
        return f"Error extracting cookies from {browser}: {e}"


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    """Run the MCP server.

    Transport is selected via the MCP_TRANSPORT environment variable:
      - 'streamable-http'  (default) — HTTP server on MCP_HOST:MCP_PORT
      - 'sse'              — Server-Sent Events transport (legacy HTTP)
      - 'stdio'            — stdin/stdout for local desktop clients
    """
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    logger.info(f"Starting MCP server — transport={transport}, host={host}, port={port}")
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        import uvicorn
        app = mcp.streamable_http_app()
        uvicorn.run(
            app,
            host=host,
            port=port,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )


if __name__ == "__main__":
    main()
