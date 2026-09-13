# cheapdates

Flight calendars, detailed itineraries, airline/alliance filters, and free ITA Matrix fare research. CLI + MCP server.

The default workflow uses HTTP only: no Chromium, clicking, account, personal API key or subscription.

The MCP includes `cheapest_dates_tool`, `search_flights_tool`, `select_flight_tool`,
`compare_fares_tool`, `airline_partners_tool`, and `assess_benefits_tool`.
Optional traveler profiles record any loyalty program, status tier and requested benefits.
Benefit assessments distinguish sourced tier policies, explicit exclusions and missing evidence
for each operator. Initial reviewed coverage includes SkyMiles on LATAM/Virgin Atlantic and
LATAM Pass on Delta; other combinations remain unknown. No personal defaults or account numbers
are needed. This does not verify fare brands or guarantee ticket eligibility.
See [detailed search and fare comparison](docs/flight-search.md) for examples, booking-class comparisons,
and the distinction between requested filters and verified itinerary details.

`cheapdates` has two calendar backends:

- **sweep** (default) — one Google flight search per date, with airline names and no browser.
- **graph** (explicit opt-in) — headless Chromium opens Google's price graph. One response
  covers roughly 60 dates, but provides no airline details.

`auto` always selects sweep. Sweep reads both Google result groups so a cheaper offer in
one group is not overlooked. Detailed search and return selection also use HTTP.
Fare research uses ITA Matrix, with its quote kept separate from Google's price.

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

# Optional, only if you explicitly use --backend graph:
uv run python -m playwright install chromium
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
For long ranges, narrow the date window to reduce HTTP requests. Graph is an optional browser alternative.
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
