# Flight search and fare comparison

Use the existing calendar to find dates, then `search_flights_tool` to inspect flight alternatives.
Version 0.3 preserves the existing CLI and `cheapest_dates_tool` arguments and results.
New prices are decimal strings and cover the requested passenger party.

## Search

`search_flights_tool` takes a nested `request` object:

```json
{
  "request": {
    "origin": "MYJ",
    "destination": "TPE",
    "departure_date": "2026-10-14",
    "return_date": "2026-10-21",
    "provider": "google",
    "seat": "economy",
    "currency": "USD",
    "include_airlines": ["BR"],
    "outbound": {"max_stops": 0, "earliest_departure_hour": 7},
    "inbound": {"max_stops": 0},
    "limit": 5
  }
}
```

Dates and routes above are examples, not current fare offers. Omit `return_date` for one-way travel.
Use explicit airport codes; city names and automatic nearby-airport expansion are not supported.

Search options:

| Field | Meaning |
|---|---|
| `provider` | `google` (default, keyless) or `serpapi` (API key required) |
| `include_airlines` / `exclude_airlines` | Two-character IATA codes; choose one list |
| `alliance` | `ONEWORLD`, `SKYTEAM`, or `STAR_ALLIANCE`; use instead of airline lists |
| `airline_scope` | `marketing` by default; `operating` requires operator data and excludes candidates where it is unknown |
| `outbound` / `inbound` | Separate filter objects for each direction |
| `passengers` | `adults` (default 1), `children`, `infants_in_seat`, `infants_on_lap`; maximum 9 total |
| `carry_on_bags` / `checked_bags` | Requested bag counts; an encoded filter does not establish the returned allowance or fee |
| `hide_separate_and_self_transfer` | Google query preference; returned ticket protection is unverified |
| `max_price` | Maximum party price in the requested currency |
| `sort_by` | `price`, `duration`, or `departure`; default `price` |
| `limit` | 1–20 alternatives; default 5 |

Each direction accepts `max_stops` (0, 1, 2, or null), `earliest_departure_hour`,
`latest_departure_hour`, `earliest_arrival_hour`, `latest_arrival_hour`,
`max_duration_minutes`, `min_layover_minutes`, `max_layover_minutes`,
`avoid_overnight_layovers`, and `avoid_airport_changes`.
Hour bounds are inclusive local hours: an upper bound of 18 includes 18:59.
Ranges cannot wrap midnight. Airport-change layover durations remain unknown because
the two airports' time zones are not established by these responses.

The Google query builder sends supported preferences upstream; local checks also reject
conflicting dates, routes, airlines, time windows, duration and connection details.
Google airline exclusions are applied locally to available candidates. Results are not an
exhaustive search of inventory, especially after local filtering.
[Upstream query filters](https://github.com/AWeirdDev/flights#search-filters).

## What the result establishes

Each offer has `offer_id`, provider, retrieval time, party price, explicit selection stage,
outbound details, optional inbound details, and `unverified_properties`.
Segments include flight numbers, local departure/arrival times, duration and aircraft when supplied.
The result's `comparison` summarizes the cheapest matching candidate and the premium for an
alternative without connections, before truncating the displayed offers. For unselected round trips,
that comparison concerns outbound options with round-trip shopping prices; it does not certify the return.

The keyless provider parses outbound shopping options. A round-trip price from that stage
does not identify a selected return flight: `itinerary_complete=false` and `inbound=null`.
Use SerpApi to complete return selection. A one-way itinerary is complete after search,
but its price is still a shopping quote, not a reservation.

`connections` counts plane segments minus one. `nonstop_verified` is false when connections
or known technical stops exist, true only with evidence of zero intermediate stops, and null
when technical-stop data is missing. Neither current adapter infers zero technical stops from
a missing field. A nonstop request can consequently have `technical_stops` in its
`unverified_properties` even when its returned route has one segment.

Operating-carrier codes, fare brands, and baggage allowances remain null when unavailable.
Both adapters currently lack verified operator-code parsing, so an operator-based hard filter
can return no matching results. Marketing-carrier filters use flight numbers.
Alliance membership is provider-reported; it does not establish loyalty eligibility.

Statuses are `ok`, `partial`, `no_results`, or `no_matching_results`. `partial` reports skipped
malformed rows. Transport errors, unrecognizable responses, invalid requests, and entirely malformed
priced rows raise MCP tool errors. Missing optional data does not silently become a known value.

## Return selection and fare types

Set `SERPAPI_API_KEY` in the environment used to launch the MCP server, then restart the client.
Do not put the key in tool arguments, source files, or committed configuration.
No key is needed for the existing calendar or default detailed search. The paid provider is never
selected automatically, and each selection/comparison performs one provider request with a 30-second
timeout. Check the provider's account limits before enabling it.

1. Search with `provider="serpapi"`.
2. Call `select_flight_tool({"offer_id":"..."})` with the desired outbound option.
3. Choose an offer from the returned complete outbound/return combinations.
4. Call `compare_fares_tool({"offer_id":"..."})` using that complete offer's ID.

For one-way searches, call `compare_fares_tool` directly, including for keyless Google results
with flight numbers. The comparison uses SerpApi to retrieve fresh options for those exact flights;
it keeps the earlier provider's quote separately instead of attaching new conditions to that price.

The adapter pins every segment by flight number, airport pair and date, verifies the returned
selection, and rechecks routing/timing/carrier constraints. Schedule changes are reported.
Fare options contain seller, the airline's fare-brand name, price, original condition text,
baggage text, and the difference from the cheapest returned fare. Missing terms remain unknown.
Known “No refunds” and change restrictions are normalized conservatively. A “Flex” label does
not imply a refundable fare; a zero change penalty does not waive a fare difference.

`total_with_requested_bags` remains null unless inclusion and scope can be established.
The initial implementation recognizes explicitly free requested bags for a single-adult one-way
trip; it does not turn fee ranges or ambiguous per-person/per-direction fees into exact totals.
Separate-ticket/uncombined options are excluded rather than compared with complete ticket prices.
No booking or booking-link POST is performed.

Global `exclude_basic_economy=true` is rejected with guidance to compare fare options:
SerpApi documents that filter only for domestic US economy searches, and this adapter does not
silently apply it to international routes. SerpApi also rejects `checked_bags` and
`hide_separate_and_self_transfer` search requests because this adapter cannot enforce them.
[Search and selection parameters](https://serpapi.com/google-flights-api),
[booking-option fields](https://serpapi.com/google-flights-booking-options).

Offer references stay in memory for 15 minutes, with a maximum of 256 stored entries. They disappear
when the MCP process restarts and are not valid in a different process. A reference's lifetime is
not a guarantee of fare availability. API keys and full provider responses are not stored in the cache.

## Partner airlines

```json
{"program":"skymiles","airline":"LA"}
```

Pass this to `airline_partners_tool`. Supported programs are `aadvantage`, `skymiles`, and
`latam_pass`. Omit `airline` to list the curated entries. Each result links an official source and
shows its review date. After 90 days, the snapshot reports `refresh_required` and relationships
become unknown until the bundled source data is reviewed.

This directory is deliberately a subset. An unlisted airline is unknown, not a confirmed
non-partner. Program partnerships do not establish booking-class eligibility, lounge access,
mileage earning, or award inventory. Those fields remain unknown; automated earning calculations
and award search are later work. Source URLs and reviewed relationships are maintained in
`cheapdates/partners.py`.

## Python and validation

```python
from cheapdates import SearchRequest, search_flights, select_flight, compare_fares

result = search_flights(SearchRequest(
    origin="MYJ", destination="TPE", departure_date="2026-10-14",
    outbound={"max_stops": 0}, include_airlines=["BR"],
))
print(result)
```

Run `uv sync --locked` and `uv run pytest -q`. Tests cover a sanitized captured Google response,
synthetic managed-provider responses, transport failures, filter preservation, return selection,
fare matching, unknown conditions, decimal prices, cache expiry and real MCP stdio exchanges.
They do not establish current supplier inventory, paid-account access, or fare availability.

No paid-provider production validation is claimed by the fixture tests. A live account smoke test
should search a representative route, select a return, and compare fares before relying on the
integration for a particular airline. Airport resolution, additional provider adapters, and
program-specific earning calculations remain separate roadmap items.
