"""MCP server exposing cheapest_dates. Run: cheapdates-mcp  (stdio)."""
from __future__ import annotations

import datetime as dt
import asyncio
from typing import Annotated

from pydantic import Field

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .core import Backend, Seat, cheapest_dates
from .search_models import SearchRequest
from .flight_search import search_flights, select_flight
from .fares import compare_fares
from .partners import Program, airline_partners

mcp = FastMCP("cheapdates")


@mcp.tool()
async def cheapest_dates_tool(
    origin: Annotated[str, Field(description="Departure IATA airport code, e.g. JFK or EWR. City names such as New York are not supported; ask which airport if ambiguous.")],
    destination: Annotated[str, Field(description="Arrival IATA airport code, e.g. SCL for Santiago, Chile. City names are not supported.")],
    start: str,
    end: str,
    trip_length: Annotated[int | None, Field(gt=0, strict=True, description="Positive number of nights, or null for one-way.")] = None,
    currency: str = "USD",
    nonstop: bool = False,
    seat: Seat = "economy",
    backend: Backend = "auto",
    include_airlines: bool = False,
) -> dict:
    """Cheapest fare for each departure date between start and end (YYYY-MM-DD) on origin→destination.

    trip_length: None for one-way, or N for a round trip returning N days after departure.
    Use IATA airport codes. Do not silently narrow a city to one airport.
    When the user asks for airline names, set include_airlines=true. This uses sweep
    (one search per date), so each airline belongs to its accompanying price.
    Returns {status, cheapest, days:[{depart, ret, price, airlines, status, error}], backend, warnings}.
    The 'graph' backend reads Google Flights' own price calendar (fast, ~60 days per call);
    its airlines are always null. 'sweep' searches each date individually.
    'auto' uses browserless sweep; graph requires explicit opt-in. Partial failures have status=partial
    and per-day errors; no_results means the search completed without priced offers.
    """
    res = await asyncio.to_thread(
        cheapest_dates, origin, destination, dt.date.fromisoformat(start), dt.date.fromisoformat(end),
        trip_length=trip_length, currency=currency, backend=backend,
        max_stops=0 if nonstop else None, seat=seat, include_airlines=include_airlines,
    )
    if res.status == "error":
        raise RuntimeError("Flight search failed for every requested date. " + "; ".join(res.warnings[:3]))
    return res.to_json()


@mcp.tool()
async def search_flights_tool(request: SearchRequest) -> dict:
    """Search flight alternatives for explicit dates, with details and airline/alliance filters.

    origin/destination are IATA codes. Use outbound.max_stops=0 and inbound.max_stops=0
    for nonstop both ways. Times are inclusive local-hour ranges (7..18 includes 18:59).
    Free browserless Google search shows outbound options with round-trip prices.
    Use select_flight_tool to retrieve return options for the chosen outbound.
    Offers expose unverified_properties; null is unknown, not free/eligible/nonstop.
    Prices cover the requested passenger party. References expire after 15 minutes
    and are valid only in this MCP process. Search results do not reserve flights.
    """
    return await asyncio.to_thread(search_flights, request)


@mcp.tool()
async def select_flight_tool(offer_id: str) -> dict:
    """Select a search offer and retrieve return alternatives without a browser.

    Returned offers contain the chosen outbound plus a return and a combined price.
    Use one of those offer IDs with compare_fares_tool for free Matrix fare research.
    """
    return await asyncio.to_thread(select_flight, offer_id)


@mcp.tool()
async def compare_fares_tool(offer_id: str, booking_codes: list[str] | None = None) -> dict:
    """Research free ITA Matrix fares for selected flights, without Chromium or API-key setup.

    Round trips require a selected return. Returns booking classes, fare basis,
    taxes and ticket restriction notes. Optional booking_codes (up to four uppercase
    letters) requests additional airline-specific booking classes on all segments.
    Matrix prices and conditions are separate from the Google quote, even when the
    flights match. Unknown baggage charges and fare brands stay unknown. No booking.
    """
    return await asyncio.to_thread(compare_fares, offer_id, booking_codes)


@mcp.tool()
async def airline_partners_tool(program: Program, airline: str | None = None) -> dict:
    """Look up sourced partner relationships for AAdvantage, SkyMiles or LATAM Pass.

    Optional airline is a two-character IATA code. Coverage is a curated subset,
    checked_on is the source review date. Unlisted partners and stale entries are
    unknown. This tool does not establish earning eligibility or award availability.
    """
    return airline_partners(program, airline)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
