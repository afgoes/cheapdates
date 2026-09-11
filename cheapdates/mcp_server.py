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
    'auto' tries graph and falls back to sweep. Partial failures have status=partial
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


def main():
    mcp.run()


if __name__ == "__main__":
    main()
