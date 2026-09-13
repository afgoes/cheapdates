"""Detailed shopping, explicit return selection, and short-lived offer references."""
from __future__ import annotations

import copy
import datetime as dt
import threading
import time
import uuid
from collections import OrderedDict
from decimal import Decimal

from .providers import fetch_google
from .search_models import Journey, LegFilters, SearchRequest, money


class OfferStore:
    def __init__(self, ttl=900, capacity=256, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def put(self, entry):
        with self.lock:
            now = self.clock()
            self.entries = OrderedDict((k, v) for k, v in self.entries.items() if v[0] > now)
            while len(self.entries) >= self.capacity:
                self.entries.popitem(last=False)
            key = uuid.uuid4().hex
            self.entries[key] = (now + self.ttl, copy.deepcopy(entry))
            return key

    def get(self, key):
        with self.lock:
            item = self.entries.get(key)
            if item is None or item[0] <= self.clock():
                self.entries.pop(key, None)
                raise ValueError("Offer reference expired or belongs to another MCP session; search again")
            return copy.deepcopy(item[1])


OFFERS = OfferStore()


def check_journey(j: Journey, filters: LegFilters, request: SearchRequest, *, inbound=False):
    """Return a rejection reason, plus properties that only the provider can assert."""
    first, last = j.segments[0], j.segments[-1]
    origin, destination = (request.destination, request.origin) if inbound else (request.origin, request.destination)
    date = request.return_date if inbound else request.departure_date
    if (first.origin, last.destination, first.departure_local.date()) != (origin, destination, date):
        return "route_or_date", []
    unknown = []
    if filters.max_stops is not None:
        observed = len(j.segments) - 1 + sum(s.technical_stops or 0 for s in j.segments)
        if observed > filters.max_stops:
            return "max_stops", []
        if any(s.technical_stops is None for s in j.segments):
            unknown.append("technical_stops")
    for lo, hi, hour in [
        (filters.earliest_departure_hour, filters.latest_departure_hour, first.departure_local.hour),
        (filters.earliest_arrival_hour, filters.latest_arrival_hour, last.arrival_local.hour),
    ]:
        if (lo is not None and hour < lo) or (hi is not None and hour > hi):
            return "time_window", []
    if filters.max_duration_minutes is not None:
        if j.duration_minutes is None or j.duration_minutes > filters.max_duration_minutes:
            return "max_duration", []
    for layover in j.layovers:
        if filters.avoid_airport_changes and layover.airport_change:
            return "airport_change", []
        if filters.avoid_overnight_layovers and layover.overnight is not False:
            return "overnight_or_unknown_layover", []
        if filters.min_layover_minutes is not None and (layover.minutes is None or layover.minutes < filters.min_layover_minutes):
            return "min_layover", []
        if filters.max_layover_minutes is not None and (layover.minutes is None or layover.minutes > filters.max_layover_minutes):
            return "max_layover", []
    if request.include_airlines or request.exclude_airlines:
        carriers = [getattr(s, request.airline_scope + "_carrier") for s in j.segments]
        if any(c is None for c in carriers):
            return "unknown_" + request.airline_scope + "_carrier", []
        if request.include_airlines and any(c not in request.include_airlines for c in carriers):
            return "include_airlines", []
        if request.exclude_airlines and any(c in request.exclude_airlines for c in carriers):
            return "exclude_airlines", []
    if request.alliance:
        unknown.append("alliance_membership")
    return None, unknown


def _results(request, rows, skipped, selected=None):
    valid, rejected = [], {}
    for row in rows:
        reason, unverified = check_journey(row["journey"], request.inbound if selected else request.outbound,
                                           request, inbound=bool(selected))
        if request.max_price is not None and Decimal(row["price"]) > request.max_price:
            reason = "max_price"
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        if request.carry_on_bags or request.checked_bags:
            unverified.append("baggage_allowances_and_total")
        if request.hide_separate_and_self_transfer:
            unverified.append("separate_tickets_and_self_transfer")
        valid.append((row, unverified))
    def sort_key(item):
        row = item[0]
        if request.sort_by == "duration":
            return (row["journey"].duration_minutes if row["journey"].duration_minutes is not None else float("inf"), Decimal(row["price"]))
        if request.sort_by == "departure":
            return (row["journey"].segments[0].departure_local, Decimal(row["price"]))
        return (Decimal(row["price"]), row["journey"].segments[0].departure_local)
    valid.sort(key=sort_key)
    cheapest = min((row for row, _ in valid), key=lambda r: Decimal(r["price"]), default=None)
    no_connections = min(
        (row for row, _ in valid if len(row["journey"].segments) == 1 and
         (selected is None or len(selected["outbound"].segments) == 1)),
        key=lambda r: Decimal(r["price"]), default=None,
    )
    comparison = dict(
        scope="complete_itinerary" if selected or request.return_date is None else "outbound_options_with_round_trip_prices",
        cheapest_price=cheapest["price"] if cheapest else None,
        cheapest_without_connections=no_connections["price"] if no_connections else None,
        extra_without_connections=money(Decimal(no_connections["price"]) - Decimal(cheapest["price"])) if no_connections and cheapest else None,
        note="No connections does not verify absence of technical stops. Comparisons use available matching candidates before the output limit.",
    )
    fetched = dt.datetime.now(dt.timezone.utc).isoformat()
    warnings = []
    if skipped:
        warnings.append(f"Skipped {skipped} malformed priced rows; results may be incomplete")
    if request.return_date and not selected:
        warnings.append("Prices are round-trip shopping quotes; the return flight has not been selected")
    if any(unknown for _, unknown in valid):
        warnings.append("Some requested properties are provider-reported only; see each offer's unverified_properties")
    if rejected:
        warnings.append("Candidates failing local checks or missing required carrier/connection data were excluded")
    output, seen = [], set()
    for row, unverified in valid:
        signature = (row["price"], row["journey"].model_dump_json())
        if signature in seen:
            continue
        seen.add(signature)
        outbound = selected["outbound"] if selected else row["journey"]
        inbound = row["journey"] if selected else None
        complete = request.return_date is None or inbound is not None
        if selected:
            unverified = sorted(set(unverified + selected["unverified_properties"]))
        offer = dict(provider=request.provider, retrieved_at=fetched, price=row["price"], currency=request.currency,
                     price_scope="round_trip_party" if request.return_date else "one_way_party",
                     passengers=request.passengers.model_dump(), itinerary_complete=complete,
                     selection_stage="complete_itinerary" if complete else "outbound_option",
                     outbound=outbound.to_json(), inbound=inbound.to_json() if inbound else None,
                     unverified_properties=unverified, fare_brand=None, baggage=None,
                     reference_expires_in_seconds=OFFERS.ttl)
        entry = dict(request=request, outbound=outbound, inbound=inbound, offer=offer,
                     selection_token=row.get("selection_token"),
                     unverified_properties=unverified)
        offer["offer_id"] = OFFERS.put(entry)
        output.append(offer)
        if len(output) >= request.limit:
            break
    return dict(status="partial" if skipped else "ok" if output else "no_matching_results" if rows else "no_results",
                provider=request.provider, retrieved_at=fetched, request=request.model_dump(mode="json"),
                offers=output, comparison=comparison, rejected=rejected, malformed_rows=skipped, warnings=warnings,
                scope="Available provider candidates; not an exhaustive inventory search")


def search_flights(request: SearchRequest | dict):
    request = SearchRequest.model_validate(request)
    rows, skipped = fetch_google(request)
    return _results(request, rows, skipped)


def select_flight(offer_id: str):
    entry = OFFERS.get(offer_id)
    if entry["offer"]["itinerary_complete"]:
        return {"status": "ok", "offer": {**entry["offer"], "offer_id": offer_id}, "next_tool": "compare_fares_tool"}
    rows, skipped = fetch_google(entry["request"], entry["outbound"])
    return _results(entry["request"], rows, skipped, selected=entry)
