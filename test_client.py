import asyncio
import json
import httpx
from typing import Any, Dict, Optional

class MFP_MCP_Client:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.session_id = None
        self.endpoint = None

    async def establish_session(self):
        """Establishes an SSE session and gets the message endpoint."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"Connecting to {self.base_url}/sse...")
            async with client.stream("GET", f"{self.base_url}/sse") as response:
                # We only need the first few lines to get the endpoint
                async for line in response.aiter_lines():
                    if line.startswith("event: endpoint"):
                        continue
                    if line.startswith("data: "):
                        # The data contains the URL with sessionId
                        url_path = line[6:].strip()
                        self.endpoint = f"{self.base_url}{url_path}"
                        print(f"Session established. Endpoint: {self.endpoint}")
                        return
        raise RuntimeError("Failed to establish MCP session via SSE")

    async def call_rpc(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        if not self.endpoint:
            await self.establish_session()
            
        payload = {
            "jsonrpc": "2.0",
            "id": "test-id",
            "method": method,
            "params": params or {}
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(self.endpoint, json=payload)
            return response.json()

async def main():
    client = MFP_MCP_Client()
    
    try:
        print("--- Listing Tools ---")
        tools = await client.call_rpc("tools/list")
        print(json.dumps(tools, indent=2))
        
        print("\n--- Testing mfp_get_diary ---")
        # This will trigger the browser in VNC
        result = await client.call_rpc("tools/call", {"name": "mfp_get_diary", "arguments": {}})
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
