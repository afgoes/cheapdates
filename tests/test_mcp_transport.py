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
                assert set(tools) == {'cheapest_dates_tool','search_flights_tool','select_flight_tool','compare_fares_tool','airline_partners_tool','assess_benefits_tool'}
                search_schema = tools['search_flights_tool'].model_dump(by_alias=True)['inputSchema']
                assert 'exclude_basic_economy' not in json.dumps(search_schema)
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
                result=await session.call_tool('search_flights_tool',{'request':request | {'exclude_basic_economy':True}})
                assert result.model_dump(by_alias=True)['isError']
                assert 'Retrying cannot enable it' in result.content[0].text
                result=await session.call_tool('airline_partners_tool',{'program':'skymiles','airline':'LA'})
                assert not result.model_dump(by_alias=True)['isError']
                assert json.loads(result.content[0].text)['airlines'][0]['earning_eligibility'] == 'unknown'
                result=await session.call_tool('assess_benefits_tool',{'offer_id':offer['offer_id'],
                    'traveler':{'program':'mileageplus','tier':'gold','required_benefits':['extra_baggage']}})
                assert not result.model_dump(by_alias=True)['isError']
                review=json.loads(result.content[0].text)
                assert not review['requirements_verified'] and not review['itinerary_complete']
                assert review['segments'][0]['benefits'][0]['policy_status'] == 'unknown'
                result=await session.call_tool('assess_benefits_tool',{'operating_airline':'LA',
                    'traveler':{'program':'Delta SkyMiles','tier':'Platinum Medallion','required_benefits':['extra_baggage']}})
                assert not result.model_dump(by_alias=True)['isError']
                review=json.loads(result.content[0].text)
                assert review['traveler']['tier'] == 'platinum' and review['sources']
                result=await session.call_tool('assess_benefits_tool',{'operating_airline':'LA'})
                assert result.model_dump(by_alias=True)['isError']

    asyncio.run(check())


def test_browserless_google_matrix_workflow_over_stdio():
    source = '''
import builtins,json
from pathlib import Path
original=builtins.__import__
def no_browser(name,*a,**kw):
    if name.startswith('playwright'): raise AssertionError('Browser import forbidden')
    return original(name,*a,**kw)
builtins.__import__=no_browser
from cheapdates import mcp_server as server,flight_search,fares
from cheapdates.matrix import parse_detail
from cheapdates.search_models import SearchRequest
req=SearchRequest(origin='LAX',destination='AUS',departure_date='2026-10-14')
quote=parse_detail(json.loads(Path('tests/fixtures/matrix-detail.json').read_text()),req)
quote['requested_booking_code']=None
flight_search.fetch_google=lambda request: ([{'price':'159','journey':quote['journeys'][0]}],0)
fares.fetch_fares=lambda *a: {'quotes':[quote],'warnings':[],'malformed_rows':0}
server.main()
'''
    async def check():
        params=StdioServerParameters(command=sys.executable,args=['-c',source])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                result=await session.call_tool('search_flights_tool',{'request':dict(origin='LAX',destination='AUS',departure_date='2026-10-14',
                    traveler={'program':'latam_pass','tier':'black','required_benefits':['seat_selection']})})
                assert not result.model_dump(by_alias=True)['isError']
                offer=json.loads(result.content[0].text)['offers'][0]
                result=await session.call_tool('select_flight_tool',{'offer_id':offer['offer_id']})
                assert not result.model_dump(by_alias=True)['isError']
                result=await session.call_tool('compare_fares_tool',{'offer_id':offer['offer_id']})
                assert not result.model_dump(by_alias=True)['isError']
                data=json.loads(result.content[0].text)
                assert data['search_quote']['price']=='159' and data['lowest_fare']['price']=='158.40'
                assert data['lowest_fare']['fare_components'][0]['fare_basis']=='KAG5AKBN'
                assert data['same_fare_as_search_quote_verified'] is False
                assert data['lowest_fare']['benefit_assessment']['quote_provider'] == 'ita_matrix'
                result=await session.call_tool('assess_benefits_tool',{'offer_id':offer['offer_id']})
                assert not result.model_dump(by_alias=True)['isError']
                assert json.loads(result.content[0].text)['traveler']['program'] == 'latam_pass'
    asyncio.run(check())


def test_matrix_zero_fares_is_not_an_mcp_error():
    source = '''
import builtins,json
from pathlib import Path
original=builtins.__import__
def no_browser(name,*a,**kw):
    if name.startswith('playwright'): raise AssertionError('Browser import forbidden')
    return original(name,*a,**kw)
builtins.__import__=no_browser
from cheapdates import mcp_server as server,flight_search,matrix
from cheapdates.flight_details import parse_google_journey
j=parse_google_journey(json.loads(Path('tests/fixtures/google-latam-return.json').read_text()))
flight_search.fetch_google=lambda request: ([{'price':'736','journey':j}],0)
matrix.MatrixClient.call=lambda *a: json.loads(Path('tests/fixtures/matrix-no-fares.json').read_text())
server.main()
'''

    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-c', source])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool('search_flights_tool', {'request': dict(
                    origin='SCL', destination='JFK', departure_date='2026-09-30')})
                offer = json.loads(result.content[0].text)['offers'][0]
                assert offer['outbound']['segments'][0]['operating_airline_name'] == 'Latam Airlines Group'
                result = await session.call_tool('compare_fares_tool', {'offer_id': offer['offer_id']})
                assert not result.model_dump(by_alias=True)['isError']
                data = json.loads(result.content[0].text)
                assert data['status'] == 'no_results' and data['fares'] == []
                assert data['fare_brand_verification'] == 'unavailable'
                assert data['search_quote']['price'] == '736'
    asyncio.run(check())
