"""Free Google Flights search adapter."""
from __future__ import annotations

import time

from .flight_details import parse_google_details
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
                           hide_separate_and_self_transfer=request.hide_separate_and_self_transfer,
                           exclude_basic_economy=request.exclude_basic_economy)


def fetch_google(request, outbound=None):
    query = google_query(request)
    if outbound is not None:
        from .google_selection import SelectedQuery
        query = SelectedQuery(request, [outbound])
    for attempt in range(3):
        try:
            html = _FlightFetcher().fetch_html(query)
            return parse_google_details(html)
        except _TransientFetchError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
