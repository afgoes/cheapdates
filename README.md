# cheapdates

Cheapest dates to fly a route, straight from Google Flights. CLI + MCP server.

Why this exists: Google's price calendar (`GetCalendarGraph`) is signed per-request by BotGuard, so
every pure-HTTP client (fli `dates`, google-flights-mcp, …) has returned empty results since mid-2026.
`cheapdates` has two backends:

- **graph** — drives headless Chromium to the route page, opens *Price graph*, and reads the calendar the
  page itself receives. One call ≈ 60 departure dates, one-way or round-trip. ~3 s per 60 days.
- **sweep** — one [fast-flights](https://github.com/AWeirdDev/flights) search per date, no browser.
  ~0.5 s per date with 4 workers; also tells you the airline.

Sweep uses fast-flights to build the query and reads **both** Google flight-result groups with
a local parser. Version 0.1 relied on fast-flights 3.1's parser, which skipped one group and
could miss a cheaper offer (the regression fixture contains $858 American versus $977 LATAM).

`auto` (default) tries graph when Playwright is installed; if the browser is missing or graph fails,
it falls back to sweep and reports why. Custom `PLAYWRIGHT_BROWSERS_PATH` locations are supported.

**Use IATA airport codes, not city names.** For Santiago, Chile use `SCL`. For New York, choose
the intended airport (`JFK`, `EWR`, or `LGA`); the tool does not silently pick one for you.
Whitespace and lowercase codes are accepted. Validation checks code format, not airport existence
or route availability.

**Need the airline? Set `include_airlines=true` in MCP/Python or `--include-airlines` in the CLI.**
This selects sweep, including when `backend="graph"` was requested. The price and airline come from
the same returned offer. Graph alone has always provided calendar prices only, so its `airlines`
field is `null`; an airline from a separate search must not be attributed to that calendar price.

## Install
```
git clone https://github.com/afgoes/cheapdates.git && cd cheapdates && uv sync   # see INSTALL.md
uv run python -m playwright install chromium       # for the graph backend
```

## CLI
```
cheapdates JFK LHR 2026-10-01 2026-11-30            # one-way
cheapdates JFK LHR 2026-10-01 2026-11-30 -r 7       # round trip, 7 nights
cheapdates JFK LHR 2026-10-01 2026-11-30 --nonstop --json
cheapdates JFK LHR 2026-10-01 2026-11-30 -b sweep   # force fast-flights
cheapdates JFK SCL 2026-09-23 2026-09-29 -r 7 --include-airlines --json
```

## MCP (Claude Code)
```
claude mcp add cheapdates -- uv run --directory /path/to/cheapdates cheapdates-mcp
```
Tool: `cheapest_dates_tool(origin, destination, start, end, trip_length?, currency?, nonstop?, seat?, backend?, include_airlines?)`.

Example arguments for a seven-night trip with airline names:
```json
{
  "origin": "JFK",
  "destination": "SCL",
  "start": "2026-09-23",
  "end": "2026-09-29",
  "trip_length": 7,
  "include_airlines": true
}
```
For long ranges, use graph to shortlist dates, then request airline searches for that smaller range.
Airline names are returned when Google supplies them; they do not establish flight numbers,
nonstop service, or all return-leg details. Use `nonstop=true` to request nonstop results.

## Results and failures

- Result `status`: `ok`, `no_results`, `partial`, or `error`.
- Day `status`: `priced`, `no_results`, or `error`; `error` contains a diagnostic when applicable.
- A missing fare after a completed search differs from a failed fetch or parser error.
- Partial results retain successful dates and report failed dates individually.
- MCP returns a tool error for invalid inputs or when every requested date fails.
- CLI emits exit code 2 for invalid arguments and 1 for partial/total search failure, including with `--json`.
- Sweep requests have a 20-second timeout, retry only transport errors/HTTP 429/5xx up to three attempts,
  and never retry deterministic parser errors.

The existing result fields are retained. `status` and per-day `error` are additive.

## Update and test

```bash
git pull --ff-only
uv sync --locked
uv run pytest -q
```
Restart the MCP client after updating so it reloads the tool schema. The tests use fixtures and
mocked responses; live flight searches are separate and prices can change.

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
