#!/usr/bin/env python3
"""Fast MFP diary reader - cookies first (3-4s), Camoufox MCP fallback (23s).
Usage: python3 mfp_quick.py [YYYY-MM-DD]
"""
import asyncio, json, sys, time, subprocess, re, os
from datetime import date, datetime
from http.cookiejar import CookieJar, Cookie
from pathlib import Path

# --- Config ---
MCP_URL = os.environ.get("MFP_URL", "http://localhost:8000/mcp")
COOKIES_FILE = Path.home() / ".mfp_mcp" / "cookies.json"
# Use inside Docker: /home/mcp/.mfp_mcp/cookies.json

def get_mcp_session():
    r = subprocess.run(
        ["curl", "-s", "-D", "-", "--max-time", "10", "-X", "POST", MCP_URL,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-d", '{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}'],
        capture_output=True, text=True, timeout=15)
    m = re.search(r'(?i)mcp-session-id:\s*(\S+)', r.stdout)
    return m.group(1) if m else None

def mcp_call(sid, method, params=None, timeout=45):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "mfp",
        "method": method, "params": params or {}})
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-X", "POST", MCP_URL,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-H", f"Mcp-Session-Id: {sid}", "-d", payload],
        capture_output=True, text=True, timeout=timeout+5)
    for line in r.stdout.split('\n'):
        line = line.strip()
        if line.startswith('data: '):
            return json.loads(line[6:])
    return None

def load_cookiejar(path: Path):
    with open(path) as f:
        data = json.load(f)
    cookies = data.get("cookies", {})
    jar = CookieJar()
    for name, value in cookies.items():
        cookie = Cookie(version=0, name=name, value=value, port=None, port_specified=False,
                        domain=".myfitnesspal.com", domain_specified=True, domain_initial_dot=True,
                        path="/", path_specified=True, secure=True,
                        expires=int(time.time())+86400*30, discard=False,
                        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False)
        jar.set_cookie(cookie)
    return jar

def diary_via_cookies(target_date: date):
    import myfitnesspal
    client = myfitnesspal.Client(cookiejar=load_cookiejar(COOKIES_FILE))
    diary = client.get_date(target_date)
    
    totals = {}
    for meal in diary.meals:
        for k, v in meal.totals.items():
            totals[k] = totals.get(k, 0) + (float(v.magnitude) if hasattr(v, 'magnitude') else v)
    goals = {}
    for k, v in diary.goals.items():
        goals[k] = float(v.magnitude) if hasattr(v, 'magnitude') else v
    
    return {
        "date": str(target_date),
        "totals": {k: round(v, 1) for k, v in totals.items()} if totals else {},
        "goals": {k: round(v, 1) for k, v in goals.items()} if goals else {},
        "meals": {m.name.lower(): {
            "entries": len(m.entries),
            "kcal": round(float(m.totals.get("calories", 0)), 1)} 
            for m in diary.meals if m.entries},
        "water": float(diary.water) if hasattr(diary, 'water') else 0
    }

def diary_via_mcp(target_date: date):
    sid = get_mcp_session()
    if not sid:
        return {"error": "No MCP session"}
    mcp_call(sid, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "mfp-agent", "version": "1.0"}})
    params = {"response_format": "json"}
    if target_date:
        params["date"] = str(target_date)
    resp = mcp_call(sid, "tools/call", {
        "name": "mfp_get_diary", "arguments": {"params": params}})
    if resp and "result" in resp:
        text = resp["result"]["content"][0]["text"]
        return json.loads(text)
    return {"error": "MCP call failed"}

def main():
    target = date.today()
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    
    t0 = time.time()
    
    if COOKIES_FILE.exists():
        try:
            result = diary_via_cookies(target)
            result["_mode"] = "cookies"
        except Exception as e:
            print(f"Cookies failed ({e}), falling back to MCP...", file=sys.stderr)
            result = diary_via_mcp(target)
            result["_mode"] = "mcp"
    else:
        result = diary_via_mcp(target)
        result["_mode"] = "mcp"
    
    result["_ms"] = round((time.time() - t0) * 1000)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
