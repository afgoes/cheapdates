"""MCP server exposing cheapest_dates. Run: cheapdates-mcp  (stdio)."""
from __future__ import annotations

import datetime as dt

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .core import cheapest_dates

mcp = FastMCP("cheapdates")


@mcp.tool()
def cheapest_dates_tool(
    origin: str,
    destination: str,
    start: str,
    end: str,
    trip_length: int | None = None,
    currency: str = "USD",
    nonstop: bool = False,
    seat: str = "economy",
    backend: str = "auto",
) -> dict:
    """Cheapest fare for each departure date between start and end (YYYY-MM-DD) on origin→destination.

    trip_length: None for one-way, or N for a round trip returning N days after departure.
    Returns {cheapest, days:[{depart, ret, price, airlines}], backend, warnings}.
    The 'graph' backend reads Google Flights' own price calendar (fast, ~60 days per call);
    'sweep' searches each date individually. 'auto' picks graph when a browser is available.
    """
    res = cheapest_dates(origin, destination, dt.date.fromisoformat(start), dt.date.fromisoformat(end),
                         trip_length=trip_length, currency=currency, backend=backend,  # type: ignore[arg-type]
                         max_stops=0 if nonstop else None, seat=seat)
    return res.to_json()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
