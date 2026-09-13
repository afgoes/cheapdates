# cheapdates — Google Flights cheapest-dates calendar for Claude Code

An MCP server (plus CLI) that answers *"when is it cheapest to fly X → Y?"* by reading
Google Flights' own price calendar. Works with Claude Code, Claude Desktop, Cursor, or any
MCP client. No API key, no account, free.

```
> when's cheapest to fly JFK → LHR in October, 7 nights?

Cheapest: Tue Oct 13 → Tue Oct 20, $576. Runners-up: Sun Oct 18 ($592), Thu Oct 15 ($596).
Mid-month is the floor; the weekend of Oct 16–17 jumps to $750+.
```

## Free and browserless by default

`auto` uses `sweep`: one Google Flights HTTP search per date, including airline names.
The detailed search/return-selection tools use Google HTTP requests; fare research uses
ITA Matrix for exact flights, booking classes, fare bases, taxes and restriction notes.
No account, personal API key, subscription, or browser is needed for that workflow.
See [flight search and fare research](docs/flight-search.md).

The legacy `graph` backend is an explicit optional browser workflow. It can cover many
dates with one calendar response but does not include airline details.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- ~300 MB disk for Playwright's Chromium (only needed for the graph backend)

## Install

```bash
git clone https://github.com/afgoes/cheapdates.git
cd cheapdates
uv sync                                  # creates .venv with all deps
uv run pytest -q                         # offline regression tests
```

Quick smoke test (talks to Google):

```bash
uv run cheapdates LAX AUS 2026-10-14 2026-10-15
```

You should see one line per day with a price, the cheapest dates marked `◀`, and a
`cheapest:` summary at the bottom.

## Add to Claude Code

From anywhere:

```bash
claude mcp add cheapdates -- uv run --directory /ABSOLUTE/PATH/TO/cheapdates cheapdates-mcp
```

Add `-s user` to make it available in every project, or leave it off to scope it to the
current project. Restart any open Claude Code session, then just ask in natural language —
Claude will call the `cheapest_dates_tool`.

To confirm it's connected: `claude mcp list` → `cheapdates … ✔ Connected`.

### Other MCP clients (Claude Desktop, Cursor, …)

Add this to the client's MCP config (`claude_desktop_config.json`, `.cursor/mcp.json`, etc.):

```json
{
  "mcpServers": {
    "cheapdates": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/cheapdates", "cheapdates-mcp"]
    }
  }
}
```

If the client can't find `uv`, use its full path (`which uv`).

## The tool

`cheapest_dates_tool(origin, destination, start, end, trip_length?, currency?, nonstop?, seat?, backend?, include_airlines?)`

| arg | meaning | default |
|---|---|---|
| `origin`, `destination` | IATA airport codes (`JFK`, `SCL`); city names rejected with guidance | required |
| `start`, `end` | first / last departure date, `YYYY-MM-DD` | required |
| `trip_length` | nights; omit for one-way, `7` for a 7-night round trip | one-way |
| `currency` | ISO code (`USD`, `EUR`, `GBP`, …) | `USD` |
| `nonstop` | `true` to only consider nonstop fares | `false` |
| `seat` | `economy` · `premium_economy` · `business` · `first` | `economy` |
| `backend` | `auto` · `graph` · `sweep` | `auto` |
| `include_airlines` | Use sweep so each price and airline come from the same returned offer | `false` |

Returns `{status, cheapest, days: [{depart, ret, price, airlines, status, error}], backend, warnings}`.
Graph always returns `airlines: null`. `include_airlines=true` takes precedence over the selected
backend and uses individual searches. Missing airline data remains null with a warning; it is never guessed.
Use `SCL` for Santiago, Chile. For New York, choose the intended airport such as `JFK` or `EWR`.
Do not assume that choosing JFK searches every New York airport.

`status` distinguishes `ok`, `no_results`, `partial`, and `error`. MCP raises a tool error if every
date failed. Partial responses preserve good dates and attach errors to failed ones. A CLI search
with failed dates exits with code 1, including JSON output; invalid inputs exit with code 2.

## CLI

```bash
cheapdates JFK LHR 2026-10-01 2026-11-30            # one-way
cheapdates JFK LHR 2026-10-01 2026-11-30 -r 7       # round trip, 7 nights
cheapdates JFK LHR 2026-10-01 2026-11-30 --nonstop --seat business
cheapdates JFK LHR 2026-10-01 2026-11-30 -b sweep   # force fast-flights (shows airline)
cheapdates JFK SCL 2026-09-23 2026-09-29 -r 7 --include-airlines --json
cheapdates JFK LHR 2026-10-01 2026-11-30 --json     # machine-readable
```

Install the CLI globally with `uv tool install /path/to/cheapdates`.

## Tips

- Sweep makes one search per day; narrow long ranges when possible.
- The graph backend reports the cheapest fare Google puts on its calendar, which may be a
  1-stop itinerary. `--nonstop` filters stops; `--include-airlines` requests airline names.
  An airline name alone does not establish nonstop service or a flight number.
- Chromium is only needed for explicit graph requests. Install it with
  `uv run python -m playwright install chromium` if you choose that backend.
- Avoid tight polling loops; Google and Matrix can reject or throttle searches.

## Troubleshooting

| symptom | fix |
|---|---|
| `playwright._impl._errors.Error: Executable doesn't exist` | `uv run python -m playwright install chromium` |
| `warning: graph backend failed … fell back to sweep` | Usually a transient Google hiccup; rerun. If persistent, Google changed the page — open an issue with the warning text |
| City names cause `IndexError` in an older version | Update, then supply airport codes such as `JFK` and `SCL`. New versions reject city names before searching |
| `airlines` is null with graph | Expected for calendar data; use `include_airlines=true` or `--include-airlines` |
| Every price is `--` | Inspect `status` and warnings: this can mean no fares or a search failure |
| `ModuleNotFoundError: typing_extensions` | Already pinned in `pyproject.toml`; run `uv sync` again |
| Claude Code shows `✘ Failed to connect` | Path after `--directory` must be absolute; run the `uv run … cheapdates-mcp` command by hand to see the error |

To update an existing checkout, run `git pull --ff-only`, `uv sync --locked`, and `uv run pytest -q`
there. Restart the MCP client so it reloads the tool schema. If you use graph and Playwright was upgraded, reinstall
Chromium with `uv run python -m playwright install chromium`. Custom browser locations configured
through `PLAYWRIGHT_BROWSERS_PATH` are honored.

## How it's built

- `cheapdates/graph.py` — Playwright driver + response parser (`parse_calendar`, fixture-tested)
- `cheapdates/sweep.py` — fast-flights queries, bounded requests, per-date sweep
- `cheapdates/offers.py` — prices and airline names from both Google result groups
- `cheapdates/core.py` — models, backend dispatch, fallback
- `cheapdates/cli.py` / `cheapdates/mcp_server.py` — Typer CLI and MCP server (mcp ≥ 2.0; falls back to FastMCP on 1.x)

MIT licensed. Reverse-engineered against an unofficial surface — expect it to need a patch
someday; the parser tests are the canary.
