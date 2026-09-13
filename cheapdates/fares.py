"""Compare fresh ITA Matrix fares for the exact Google-selected flights."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from .flight_search import OFFERS, check_journey
from .google_selection import SelectedQuery
from .matrix import fetch_fares
from .search_models import money


def pinned_journey(journey):
    result = []
    for s in journey.segments:
        if not s.flight_number or not re.fullmatch(r"[A-Z0-9]{2}\d{1,4}", s.flight_number):
            raise ValueError("Fare comparison requires an IATA flight number for every segment")
        result.append(dict(flight_number=s.flight_number, departure_id=s.origin, arrival_id=s.destination,
                           date=s.departure_local.date().isoformat()))
    return result


def conditions(terms):
    normalized = {term.strip().lower() for term in terms}
    return {
        "refunds_allowed": False if "this ticket is non-refundable." in normalized else None,
        "changes_allowed": None,
        "change_fee": None,
        "change_penalty_applies": True if "changes to this ticket will incur a penalty fee." in normalized else None,
        "changes_after_departure_allowed": False if "no changes may be made to this ticket after departure." in normalized else None,
        "cancellation_penalty_applies": True if "cancellation of this ticket will incur a penalty fee." in normalized else None,
        "raw_terms": terms,
    }


def compare_fares(offer_id: str, booking_codes: list[str] | None = None):
    """Default fare plus optional alternatives in up to four booking classes."""
    if booking_codes is None:
        booking_codes = []
    if not isinstance(booking_codes, list) or len(booking_codes) > 4 or any(
        not isinstance(c, str) or not re.fullmatch(r"[A-Z]", c) for c in booking_codes
    ):
        raise ValueError("booking_codes must contain at most four uppercase booking-class letters, such as ['Y']; these are airline-specific")
    booking_codes = list(dict.fromkeys(booking_codes))
    entry = OFFERS.get(offer_id)
    request = entry["request"]
    if not entry["offer"]["itinerary_complete"]:
        raise ValueError("Select a return flight before comparing round-trip fares")
    expected = {"outbound": pinned_journey(entry["outbound"])}
    if entry["inbound"]:
        expected["return"] = pinned_journey(entry["inbound"])
    payload = fetch_fares(request, entry["outbound"], entry["inbound"], booking_codes)
    fares, rejected, seen = [], 0, set()
    for quote in payload["quotes"]:
        selected = quote["journeys"]
        actual = {key: pinned_journey(j) for key, j in zip(expected, selected)}
        if len(selected) != len(expected) or actual != expected:
            rejected += 1
            continue
        if any(check_journey(j, request.outbound if key == "outbound" else request.inbound, request,
                             inbound=key == "return")[0] for key, j in zip(expected, selected)):
            rejected += 1
            continue
        price = money(quote["price"])
        signature = (price, tuple((f["carrier"], f["origin"], f["destination"], f["fare_basis"]) for f in quote["fare_components"]))
        if signature in seen:
            continue
        seen.add(signature)
        changed = any([(s.departure_local, s.arrival_local) for s in j.segments] !=
                      [(s.departure_local, s.arrival_local) for s in entry[key].segments]
                      for key, j in zip(("outbound", "inbound"), selected))
        fares.append(dict(provider="ita_matrix", price=price, currency=request.currency, seller=None, fare_brand=None,
                          requested_booking_code=quote['requested_booking_code'], fare_components=quote["fare_components"],
                          taxes=quote["taxes"], conditions=conditions(quote["ticket_notes"]),
                          baggage_terms=[], total_with_requested_bags=None if request.carry_on_bags or request.checked_bags else price,
                          flight_details={key: j.to_json() for key, j in zip(expected, selected)}, schedule_changed=changed))
    if rejected and not fares:
        raise ValueError("Matrix returned different flights or flights failing the requested constraints; no fare conditions were attached")
    fares.sort(key=lambda fare: Decimal(fare["price"]))
    for fare in fares:
        fare["extra_vs_lowest_fare"] = money(Decimal(fare["price"]) - Decimal(fares[0]["price"]))
    warnings = payload["warnings"] + [
        "Matrix fares are separate fresh quotes for the selected flights. They are not verified as the same fare or seller as the Google search price",
        "Booking classes and fare basis codes are airline-specific; they do not establish a branded fare, mileage earning, or award eligibility",
        "Ticket notes are a summary, not complete fare rules. Unknown change/refund terms and baggage charges remain null",
        "Matrix cannot issue tickets. Recheck the price and fare rules with the airline or seller before purchase",
    ]
    skipped = payload["malformed_rows"] + rejected
    if skipped:
        warnings.append(f"Excluded {skipped} malformed or mismatched Matrix fares")
    known = [f for f in fares if f["total_with_requested_bags"] is not None]
    journeys = [entry["outbound"]] + ([entry["inbound"]] if entry["inbound"] else [])
    return dict(status="partial" if skipped else "ok" if fares else "no_results", provider="ita_matrix",
                retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(), offer_id=offer_id, itinerary=expected,
                passengers=request.passengers.model_dump(), currency=request.currency,
                search_quote={"provider": "google", "price": entry["offer"]["price"], "retrieved_at": entry["offer"]["retrieved_at"]},
                same_fare_as_search_quote_verified=False, fares=fares, lowest_fare=fares[0] if fares else None,
                lowest_known_total_with_bags=known[0] if known else None,
                google_flights_url=SelectedQuery(request, journeys, entry["selection_token"]).booking_url(),
                matrix_url="https://matrix.itasoftware.com/search", warnings=warnings,
                scope="Available Matrix fares for exact flights, with optional booking-class searches; not all branded fare bundles or sellers")
