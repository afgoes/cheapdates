# cheapdates — Google Flights cheapest-dates calendar for Claude Code

An MCP server (plus CLI) that answers *"when is it cheapest to fly X → Y?"* by reading
Google Flights' own price calendar. Works with Claude Code, Claude Desktop, Cursor, or any
MCP client. No API key, no account, free.

```
> when's cheapest to fly JFK → LHR in October, 7 nights?

Cheapest: Tue Oct 13 → Tue Oct 20, $576. Runners-up: Sun Oct 18 ($592), Thu Oct 15 ($596).
Mid-month is the floor; the weekend of Oct 16–17 jumps to $750+.
```

## Why this exists

Google never shipped a Flights API. The open-source libraries that reverse-engineered the
price calendar (`fli`, `google-flights-mcp`, …) all stopped returning results in mid-2026:
Google now signs every calendar request with a per-request BotGuard token, so plain HTTP
replays get an empty response.

`cheapdates` gets around that with two backends:

| backend | how it works | speed | notes |
|---|---|---|---|
| **graph** (default) | Headless Chromium loads the route page, opens *Price graph*, and we read the calendar the page itself receives | ~3 s per 60 days | One-way **and** round-trip (fixed trip length), nonstop filter, cabin class |
| **sweep** (fallback) | One [fast-flights](https://github.com/AWeirdDev/flights) search per date, no browser | ~0.5 s per date | Also reports the airline |

`auto` uses graph when Playwright + Chromium are installed and falls back to sweep if graph
ever fails, so you always get an answer.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- ~300 MB disk for Playwright's Chromium (only needed for the graph backend)

## Install

```bash
git clone <repo-url> cheapdates
cd cheapdates
uv sync                                  # creates .venv with all deps
uv run python -m playwright install chromium   # one-time browser download
uv run pytest -q                         # 2 parser tests should pass
```

Quick smoke test (talks to Google, ~5 s):

```bash
uv run cheapdates JFK LHR 2026-10-01 2026-10-31
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

`cheapest_dates_tool(origin, destination, start, end, trip_length?, currency?, nonstop?, seat?, backend?)`

| arg | meaning | default |
|---|---|---|
| `origin`, `destination` | IATA airport codes (`JFK`, `LHR`) | required |
| `start`, `end` | first / last departure date, `YYYY-MM-DD` | required |
| `trip_length` | nights; omit for one-way, `7` for a 7-night round trip | one-way |
| `currency` | ISO code (`USD`, `EUR`, `GBP`, …) | `USD` |
| `nonstop` | `true` to only consider nonstop fares | `false` |
| `seat` | `economy` · `premium_economy` · `business` · `first` | `economy` |
| `backend` | `auto` · `graph` · `sweep` | `auto` |

Returns `{cheapest, days: [{depart, ret, price, airlines}], backend, warnings}`.

## CLI

```bash
cheapdates JFK LHR 2026-10-01 2026-11-30            # one-way
cheapdates JFK LHR 2026-10-01 2026-11-30 -r 7       # round trip, 7 nights
cheapdates JFK LHR 2026-10-01 2026-11-30 --nonstop --seat business
cheapdates JFK LHR 2026-10-01 2026-11-30 -b sweep   # force fast-flights (shows airline)
cheapdates JFK LHR 2026-10-01 2026-11-30 --json     # machine-readable
```

Install the CLI globally with `uv tool install /path/to/cheapdates`.

## Tips

- Long ranges are fine — 90 days is two graph calls (~7 s).
- The graph backend reports the cheapest fare Google puts on its calendar, which may be a
  1-stop itinerary. Use `--nonstop` or `-b sweep` if you want to see the airline.
- Headless Chromium ran fine against Google from a normal home connection with no stealth
  tricks. If you're behind a datacenter IP or VPN you may get CAPTCHAs; the sweep backend is
  less sensitive to that.
- Be a good citizen: this hits Google's public site. A few dozen queries a day is nothing;
  don't put it in a tight loop.

## Troubleshooting

| symptom | fix |
|---|---|
| `playwright._impl._errors.Error: Executable doesn't exist` | `uv run python -m playwright install chromium` |
| `warning: graph backend failed … fell back to sweep` | Usually a transient Google hiccup; rerun. If persistent, Google changed the page — open an issue with the warning text |
| Every price is `--` | The route/date has no fares (tiny airports, dates > ~11 months out) |
| `ModuleNotFoundError: typing_extensions` | Already pinned in `pyproject.toml`; run `uv sync` again |
| Claude Code shows `✘ Failed to connect` | Path after `--directory` must be absolute; run the `uv run … cheapdates-mcp` command by hand to see the error |

## How it's built

- `cheapdates/graph.py` — Playwright driver + response parser (`parse_calendar`, fixture-tested)
- `cheapdates/sweep.py` — fast-flights per-date sweep
- `cheapdates/core.py` — models, backend dispatch, fallback
- `cheapdates/cli.py` / `cheapdates/mcp_server.py` — Typer CLI and MCP server (mcp ≥ 2.0; falls back to FastMCP on 1.x)

MIT licensed. Reverse-engineered against an unofficial surface — expect it to need a patch
someday; the parser tests are the canary.
