import asyncio
import json
import httpx
from typing import Any, Dict, Optional

class MFP_MCP_Client:
    def __init__(self, url: str = "http://localhost:8000/mcp"):
        self.url = url
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }

    async def call_tool(self, name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """Calls a tool on the MCP server."""
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {}
            }
        }
        
        async with httpx.AsyncClient() as client:
            # Note: For streamable-http, a full client would first establish an SSE session.
            # This is a simplified direct call for testing purposes.
            response = await client.post(self.url, json=payload, headers=self.headers)
            return response.json()

    async def list_tools(self) -> Dict[str, Any]:
        """Lists available tools."""
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/list"
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(self.url, json=payload, headers=self.headers)
            return response.json()

async def main():
    client = MFP_MCP_Client()
    
    print("--- Listing Tools ---")
    try:
        tools = await client.list_tools()
        print(json.dumps(tools, indent=2))
    except Exception as e:
        print(f"Error listing tools: {e}")
    
    print("\n--- Testing mfp_get_diary ---")
    # This will trigger the browser in VNC
    try:
        result = await client.call_tool("mfp_get_diary")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error calling tool: {e}")

if __name__ == "__main__":
    asyncio.run(main())
