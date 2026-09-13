# Free flight search and fare research

Version 0.3 combines Google Flights search with ITA Matrix fare research. The default
workflow uses HTTP only: no Chromium, clicking, account, personal API key, subscription,
or paid fallback. The older `graph` calendar backend remains an explicit browser option;
`auto` now uses `sweep` even when Playwright is installed.

## Search and select

Use `cheapest_dates_tool` to find dates, then pass a nested `request` to
`search_flights_tool`:

```json
{
  "request": {
    "origin": "LAX",
    "destination": "AUS",
    "departure_date": "2026-10-14",
    "return_date": "2026-10-21",
    "include_airlines": ["UA"],
    "outbound": {"max_stops": 0, "earliest_departure_hour": 7},
    "inbound": {"max_stops": 0},
    "passengers": {"adults": 1},
    "currency": "USD",
    "limit": 5
  }
}
```

These are example dates and routes. Use explicit IATA airport codes. Omit `return_date`
for one-way travel. A city name does not silently become one airport.

For a round trip:

1. Choose an outbound `offer_id` from the search.
2. Call `select_flight_tool({"offer_id":"..."})` to retrieve return alternatives.
3. Choose an ID from the complete outbound/return offers.
4. Call `compare_fares_tool({"offer_id":"..."})` to research its Matrix fares.

For a one-way search, its offer ID can go directly to `compare_fares_tool`.
Google selection uses encoded flight numbers, airports and dates through HTTP. Initial
round-trip search prices do not identify a return: `itinerary_complete=false`. Return
selection provides a combined party quote; outbound and return prices are never added.

| Search option | Meaning |
|---|---|
| `provider` | Only `google`; optional |
| `include_airlines` / `exclude_airlines` | IATA airline codes; choose one list |
| `alliance` | `ONEWORLD`, `SKYTEAM`, `STAR_ALLIANCE`; use instead of airline lists |
| `airline_scope` | `marketing` by default; `operating` rejects candidates without verified operator codes |
| `seat` | `economy`, `premium_economy`, `business`, `first` |
| `passengers` | Adults, children, infants in seats/on laps; maximum nine total |
| `outbound` / `inbound` | Independent filters for each direction |
| `carry_on_bags` / `checked_bags` | Google preferences; returned allowance and fees are not verified |
| `hide_separate_and_self_transfer` | Google preference; ticket protection remains unverified |
| `max_price` | Maximum Google party price in the requested currency |
| `sort_by` | `price`, `duration`, `departure` |
| `limit` | 1–20 offers, default 5 |

Each direction supports `max_stops` (0–2 or null), `earliest_departure_hour`,
`latest_departure_hour`, `earliest_arrival_hour`, `latest_arrival_hour`,
`max_duration_minutes`, `min_layover_minutes`, `max_layover_minutes`,
`avoid_overnight_layovers`, and `avoid_airport_changes`. Hour bounds include the full
hour (18 means through 18:59); ranges cannot wrap midnight.

Local checks exclude candidates with conflicting routes, dates, airlines, times or
connections. Airline exclusions operate on available Google candidates and can reduce
coverage. Alliance membership is provider-reported. Missing operator codes cause
operating-airline hard filters to return no matching results.

`connections` counts segments minus one. `nonstop_verified` remains null when technical
stops are unknown. A nonstop preference alone is not evidence of zero technical stops.
The price comparison reports the cheapest candidate and the premium for no connections,
before applying the output limit. Initial round-trip comparisons cover outbound options.

## Matrix fares and booking classes

`compare_fares_tool` looks up the exact selected flight sequence in Matrix, checks each
segment's flight number, airports and date, and rechecks timing and airline constraints.
Returned passenger counts/categories and currency must match. Schedule changes are
reported. Separate-ticket itineraries are excluded.

To explore another booking class, optionally pass up to four uppercase letters:

```json
{"offer_id":"...", "booking_codes":["Y"]}
```

The tool researches the default Matrix fare and each requested class separately. A
class applies to all segments in that extra search. Letters are airline-specific;
`Y` is an example, not a universal fare brand or refundability rule. No matching class
produces a warning, not an invented fare. A default query may return only one fare.

Each fare includes its decimal price, fare basis codes, segment booking information,
tax components, ticket restriction notes, and premium over the cheapest Matrix result.
`fare_brand` and seller remain null: a booking class or fare basis does not establish a
consumer label such as Basic, Standard or Flex. Known negative restriction notes are
normalized conservatively. Notes are summaries, not complete fare-rule text.

**Google and Matrix quotes remain separate.** `search_quote` preserves Google's original
price; Matrix's price and conditions belong together. Matching flights does not establish
that both providers priced the same fare or seller, even when amounts happen to match.
`same_fare_as_search_quote_verified` is always false. Matrix may lack an airline or fare
that Google shows. Matrix cannot issue tickets; the output includes a Google itinerary
link for the user to continue manually.

Matrix does not establish baggage allowances here. `total_with_requested_bags` is null
when bags were requested. No guessed fees, mileage earning or refund benefits are added.
Global `exclude_basic_economy=true` is rejected because it cannot be verified across routes.

The adapter discovers Matrix's public application identifier from its own website and
Google-hosted JavaScript. It is cached in memory for one day. No user key is requested or
stored, and there is no paid fallback. Search and detail calls have 55-second timeouts
and at most three attempts for transient failures. Fare research is slower than Google
search: each class requires a search plus detail lookups. Responses can change or be
rejected because these are undocumented interfaces.

Protocol references: [Matrix HTTP client](https://github.com/YogevKr/itamx),
[Google's Matrix routing guide](https://support.google.com/faqs/answer/2736497),
[fast-flights query builder](https://github.com/AWeirdDev/flights).

## References, errors and partner airlines

Offer IDs stay in memory for 15 minutes, with at most 256 entries. They disappear on MCP
restart; expiry does not promise fare availability. Google selection tokens stay in memory.
Transport and parsing failures raise MCP errors. `no_results` means a completed search
found no fares; `partial` signals malformed or excluded fare details.

`airline_partners_tool({"program":"skymiles","airline":"LA"})` looks up curated official
sources. Programs: `aadvantage`, `skymiles`, `latam_pass`. Omit `airline` to list entries.
Unlisted relationships are unknown. After 90 days the snapshot becomes `refresh_required`.
Partnership does not establish booking-class earning eligibility or award availability.

## Validation

Run `uv sync --locked` and `uv run pytest -q`. Tests use sanitized Google/Matrix captures,
synthetic failures, exact-flight/currency/passenger checks, reference expiry and real MCP
stdio exchanges. Live checks are separate; they do not guarantee future inventory or prices.
