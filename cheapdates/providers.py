"""Small, explicit provider adapters. No automatic paid-provider fallback."""
from __future__ import annotations

import os
import time

from .flight_details import parse_google_details, parse_serp_details
from .search_models import SearchRequest
from .sweep import _FlightFetcher, _TransientFetchError


def google_query(request: SearchRequest):
    import fast_flights as ff

    legs = []
    for origin, destination, date, filters in [
        (request.origin, request.destination, request.departure_date, request.outbound),
        (request.destination, request.origin, request.return_date, request.inbound),
    ]:
        if date is None:
            continue
        values = filters.model_dump(exclude={"avoid_overnight_layovers", "avoid_airport_changes"})
        legs.append(ff.FlightQuery(date=date.isoformat(), from_airport=origin, to_airport=destination,
                                  airlines=[request.alliance] if request.alliance else request.include_airlines or None,
                                  **values))
    return ff.create_query(flights=legs, trip="round-trip" if request.return_date else "one-way",
                           currency=request.currency, language="en", seat=request.seat.replace("_", "-"),
                           passengers=ff.Passengers(**request.passengers.model_dump()),
                           max_price=request.max_price, carry_on_bags=request.carry_on_bags,
                           checked_bags=request.checked_bags,
                           hide_separate_and_self_transfer=request.hide_separate_and_self_transfer)


def fetch_google(request):
    query = google_query(request)
    for attempt in range(3):
        try:
            html = _FlightFetcher().fetch_html(query)
            return parse_google_details(html)
        except _TransientFetchError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def serp_params(request: SearchRequest, *, filters=True):
    params = dict(engine="google_flights", type=1 if request.return_date else 2,
                  departure_id=request.origin, arrival_id=request.destination,
                  outbound_date=str(request.departure_date), currency=request.currency, hl="en", gl="us",
                  travel_class={"economy": 1, "premium_economy": 2, "business": 3, "first": 4}[request.seat],
                  **request.passengers.model_dump())
    if request.return_date:
        params["return_date"] = str(request.return_date)
    if not filters:
        return params
    if request.include_airlines or request.alliance:
        params["include_airlines"] = request.alliance or ",".join(request.include_airlines)
    if request.exclude_airlines:
        params["exclude_airlines"] = ",".join(request.exclude_airlines)
    params["sort_by"] = {"price": 2, "duration": 5, "departure": 3}[request.sort_by]
    if request.max_price:
        params["max_price"] = request.max_price
    if request.carry_on_bags:
        params["bags"] = request.carry_on_bags
    legs = [request.outbound] + ([request.inbound] if request.return_date else [])
    # Provider has one stop limit; use the least restrictive and verify each leg locally.
    if all(l.max_stops is not None for l in legs):
        params["stops"] = max(l.max_stops for l in legs) + 1
    if all(l.max_duration_minutes is not None for l in legs):
        params["max_duration"] = max(l.max_duration_minutes for l in legs)
    if all(l.min_layover_minutes is not None and l.max_layover_minutes is not None for l in legs):
        params["layover_duration"] = f"{min(l.min_layover_minutes for l in legs)},{max(l.max_layover_minutes for l in legs)}"
    for key, leg in zip(("outbound_times", "return_times"), legs):
        values = [leg.earliest_departure_hour, leg.latest_departure_hour,
                  leg.earliest_arrival_hour, leg.latest_arrival_hour]
        if any(v is not None for v in values):
            params[key] = ",".join(str(default if v is None else v) for v, default in zip(values, [0, 23, 0, 23]))
    return params


class SerpApi:
    def fetch(self, params):
        from primp import Client

        key = os.environ.get("SERPAPI_API_KEY", "").strip()
        if not key:
            raise ValueError("Set SERPAPI_API_KEY in the MCP server environment to use fare comparison or the serpapi provider")
        # Errors deliberately omit request URLs, response bodies and credentials.
        query = {k: str(v).lower() if isinstance(v, bool) else str(v)
                 for k, v in params.items() if v is not None}
        try:
            response = Client(timeout=30).get("https://serpapi.com/search.json", params={**query, "api_key": key})
        except Exception:
            raise RuntimeError("SerpApi transport failed; retry the request") from None
        if response.status_code != 200:
            raise RuntimeError(f"SerpApi returned HTTP {response.status_code}; check account access or retry later")
        try:
            payload = response.json()
        except Exception:
            raise ValueError("SerpApi returned invalid JSON") from None
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError("SerpApi reported an upstream error; check account access and search parameters")
        metadata = payload.get("search_metadata", {})
        echoed = payload.get("search_parameters", {})
        if not isinstance(metadata, dict) or not isinstance(echoed, dict):
            raise ValueError("SerpApi returned malformed search metadata")
        if metadata.get("status") not in (None, "Success"):
            raise RuntimeError("SerpApi search did not complete successfully")
        if echoed.get("currency", params["currency"]) != params["currency"]:
            raise ValueError("Provider returned a different currency")
        return payload


def fetch_serp(request, departure_token=None):
    params = serp_params(request)
    if departure_token:
        params["departure_token"] = departure_token
    return parse_serp_details(SerpApi().fetch(params))
