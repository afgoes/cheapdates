import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_mcp_schema_and_error_flags_over_stdio():
    # Simulated backend; the real MCP server and client exchange JSON-RPC.
    source = '''
import datetime as dt
from cheapdates import mcp_server as server
from cheapdates.core import DayPrice, Result, _airport
def search(origin, destination, start, end, **kwargs):
    _airport(origin, "origin")
    res = Result(origin, destination, start, end, "USD", 7, "sweep")
    if origin == "JFK":
        res.days = [DayPrice(start, None, error="test upstream failure")]
        res.warnings = ["test upstream failure"]
    else:
        res.days = [DayPrice(start, 858, airlines="American"), DayPrice(end, None, error="test upstream failure")]
        res.warnings = ["test upstream failure"]
    return res
server.cheapest_dates = search
server.main()
'''

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-c", source])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).model_dump(by_alias=True)["tools"]
                schema = tools[0]["inputSchema"]["properties"]
                assert "include_airlines" in schema
                assert schema["backend"]["enum"] == ["auto", "graph", "sweep"]
                assert "IATA" in schema["origin"]["description"]
                for origin, is_error in [("New York", True), ("JFK", True), ("EWR", False)]:
                    result = await session.call_tool("cheapest_dates_tool", dict(
                        origin=origin, destination="SCL", start="2026-09-23", end="2026-09-24", include_airlines=True,
                    ))
                    data = result.model_dump(by_alias=True)
                    assert data["isError"] is is_error
                    if not is_error:
                        payload = json.loads(data["content"][0]["text"])
                        assert payload["status"] == "partial"
                        assert payload["cheapest"]["airlines"] == "American"

    asyncio.run(check())
