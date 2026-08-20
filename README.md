# cheapdates

Cheapest dates to fly a route, straight from Google Flights. CLI + MCP server.

Why this exists: Google's price calendar (`GetCalendarGraph`) is signed per-request by BotGuard, so
every pure-HTTP client (fli `dates`, google-flights-mcp, …) has returned empty results since mid-2026.
`cheapdates` has two backends:

- **graph** — drives headless Chromium to the route page, opens *Price graph*, and reads the calendar the
  page itself receives. One call ≈ 60 departure dates, one-way or round-trip. ~3 s per 60 days.
- **sweep** — one [fast-flights](https://github.com/AWeirdDev/flights) search per date, no browser.
  ~0.5 s per date with 4 workers; also tells you the airline.

`auto` (default) uses graph when Playwright + Chromium are installed, else sweep; if graph fails it falls back to sweep.

## Install
```
uv tool install /path/to/cheapdates        # or: uv sync && uv run cheapdates ...
python -m playwright install chromium       # for the graph backend
```

## CLI
```
cheapdates JFK LHR 2026-10-01 2026-11-30            # one-way
cheapdates JFK LHR 2026-10-01 2026-11-30 -r 7       # round trip, 7 nights
cheapdates JFK LHR 2026-10-01 2026-11-30 --nonstop --json
cheapdates JFK LHR 2026-10-01 2026-11-30 -b sweep   # force fast-flights
```

## MCP (Claude Code)
```
claude mcp add cheapdates -- uv run --directory /path/to/cheapdates cheapdates-mcp
```
Tool: `cheapest_dates_tool(origin, destination, start, end, trip_length?, currency?, nonstop?, seat?, backend?)`.

## Python
```python
import datetime as dt
from cheapdates import cheapest_dates
r = cheapest_dates("JFK", "LHR", dt.date(2026,10,1), dt.date(2026,11,30), trip_length=7)
print(r.cheapest)
```

## Caveats
Reverse-engineered; Google can change the page or response at any time. The parser is fixture-tested
(`pytest`), and the graph backend falls back to sweep on failure.
