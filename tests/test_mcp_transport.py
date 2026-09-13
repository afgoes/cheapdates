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


def test_detailed_search_workflow_over_stdio():
    source = '''
from pathlib import Path
from cheapdates import mcp_server as server
from cheapdates import flight_search
from cheapdates.flight_details import parse_google_details
flight_search.fetch_google = lambda request: parse_google_details(Path("tests/fixtures/flight_details.html").read_text())
server.main()
'''

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-c", source])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert set(tools) == {'cheapest_dates_tool','search_flights_tool','select_flight_tool','compare_fares_tool','airline_partners_tool'}
                request = dict(origin='JFK',destination='SCL',departure_date='2026-09-23',return_date='2026-09-30',include_airlines=['AA'])
                result = await session.call_tool('search_flights_tool',{'request':request})
                assert not result.model_dump(by_alias=True)['isError']
                data=json.loads(result.content[0].text)
                offer=data['offers'][0]
                assert offer['price'] == '858' and offer['outbound']['segments'][0]['flight_number'] == 'AA1174'
                assert not offer['itinerary_complete']
                result=await session.call_tool('compare_fares_tool',{'offer_id':offer['offer_id']})
                assert result.model_dump(by_alias=True)['isError'] and 'Select a return' in result.content[0].text
                result=await session.call_tool('search_flights_tool',{'request':request | {'limit':0}})
                assert result.model_dump(by_alias=True)['isError']
                result=await session.call_tool('airline_partners_tool',{'program':'skymiles','airline':'LA'})
                assert not result.model_dump(by_alias=True)['isError']
                assert json.loads(result.content[0].text)['airlines'][0]['earning_eligibility'] == 'unknown'

    asyncio.run(check())
