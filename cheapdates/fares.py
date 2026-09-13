"""Compare provider fare options only after checking the selected itinerary."""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal

from .flight_details import parse_serp_journey
from .flight_search import OFFERS, check_journey
from .providers import SerpApi, serp_params
from .search_models import money


def pinned_journey(journey):
    segments = []
    for s in journey.segments:
        if not s.flight_number or not re.fullmatch(r"[A-Z0-9]{2}\d{1,4}", s.flight_number):
            raise ValueError("Fare comparison requires a supported IATA flight number for every segment")
        segments.append(dict(flight_number=s.flight_number, departure_id=s.origin, arrival_id=s.destination,
                             date=s.departure_local.date().isoformat()))
    return segments


def _strings(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError("Malformed fare conditions")
    return value


def conditions(terms):
    normalized = {term.strip().lower() for term in terms}
    return {
        "refunds_allowed": False if "no refunds" in normalized else None,
        "changes_allowed": False if "no ticket changes" in normalized else True if normalized & {"ticket changes for a fee", "free ticket changes"} else None,
        "change_fee": "0" if "free ticket changes" in normalized else None,
        "fare_difference_may_apply": True,
        "raw_terms": terms,
    }


def _bag_total(price, bag_terms, request):
    if not request.carry_on_bags and not request.checked_bags:
        return price
    # A per-person allowance cannot establish group pricing or per-direction fees.
    # Only recognize explicitly free bags for a single-adult one-way itinerary.
    if request.return_date or request.passengers.model_dump() != {"adults": 1, "children": 0, "infants_in_seat": 0, "infants_on_lap": 0}:
        return None
    terms = {s.strip().lower() for s in bag_terms}
    carry = request.carry_on_bags == 0 or request.carry_on_bags == 1 and "1 free carry-on" in terms
    checked = request.checked_bags == 0 or request.checked_bags == 1 and "1 free checked bag" in terms
    return price if carry and checked else None


def compare_fares(offer_id: str):
    entry = OFFERS.get(offer_id)
    request = entry["request"]
    if not entry["offer"]["itinerary_complete"]:
        raise ValueError("Select a return flight before comparing round-trip fares")
    expected = {"outbound": pinned_journey(entry["outbound"])}
    if entry["inbound"]:
        expected["return"] = pinned_journey(entry["inbound"])
    params = serp_params(request, filters=False)
    # Pin the exact flights even when the original quote came from the free provider.
    params["selected_flights_json"] = json.dumps(expected, separators=(",", ":"))
    payload = SerpApi().fetch(params)
    selected = payload.get("selected_flights")
    if not isinstance(selected, list) or len(selected) != len(expected):
        raise ValueError("Provider did not confirm every selected journey; fare conditions cannot be matched safely")
    try:
        journeys = {key: parse_serp_journey(row) for key, row in zip(expected, selected)}
        actual = {key: pinned_journey(j) for key, j in journeys.items()}
    except (ValueError, KeyError, TypeError, IndexError):
        raise ValueError("Provider returned malformed selected-flight details") from None
    if actual != expected:
        raise ValueError("Provider returned different flights; search again before comparing fares")
    for key, j in journeys.items():
        reason, _ = check_journey(j, request.outbound if key == "outbound" else request.inbound,
                                  request, inbound=key == "return")
        if reason:
            raise ValueError(f"Refreshed itinerary no longer passes {reason}; search again")
    schedule_changed = any(
        [(s.departure_local, s.arrival_local) for s in j.segments] !=
        [(s.departure_local, s.arrival_local) for s in entry["outbound" if key == "outbound" else "inbound"].segments]
        for key, j in journeys.items()
    )
    rows = payload.get("booking_options")
    if not isinstance(rows, list):
        raise ValueError("Provider did not return a recognizable booking-options list")
    fares, skipped, separate = [], 0, 0
    for row in rows:
        try:
            if row.get("separate_tickets") or not isinstance(row.get("together"), dict):
                separate += 1
                continue
            option = row["together"]
            price = money(option["price"])
            seller = option["book_with"]
            brand = option.get("option_title")
            if not isinstance(seller, str) or not seller or (brand is not None and not isinstance(brand, str)):
                raise ValueError("Malformed fare seller or brand")
            terms = _strings(option.get("extensions"))
            bags = _strings(option.get("baggage_prices"))
            fares.append(dict(seller=seller, fare_brand=brand, price=price, currency=request.currency,
                              conditions=conditions(terms), baggage_terms=bags,
                              total_with_requested_bags=_bag_total(price, bags, request),
                              booking_handoff_available=isinstance(option.get("booking_request"), dict)))
        except (ValueError, KeyError, TypeError, AttributeError):
            skipped += 1
    if skipped and not fares:
        raise ValueError("All combined-ticket fare options were malformed")
    fares.sort(key=lambda f: Decimal(f["price"]))
    for fare in fares:
        fare["extra_vs_lowest_fare"] = money(Decimal(fare["price"]) - Decimal(fares[0]["price"]))
    known_totals = [f for f in fares if f["total_with_requested_bags"] is not None]
    warnings = ["Fare options are newly retrieved quotes; prices and conditions can change before purchase",
                "Unknown fees and bag allowances remain null. Fare names alone do not establish refundability or mileage earning"]
    if request.carry_on_bags or request.checked_bags:
        warnings.append("Baggage totals are returned only when inclusion and scope can be established; raw fee text is not added as an assumed total")
    if separate:
        warnings.append(f"Excluded {separate} separate-ticket or uncombined options; their prices are not comparable as one complete ticket")
    if skipped:
        warnings.append(f"Skipped {skipped} malformed fare options")
    return dict(status="partial" if skipped else "ok" if fares else "no_results", provider="serpapi",
                retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(), offer_id=offer_id,
                itinerary=actual, flight_details={k: j.to_json() for k, j in journeys.items()},
                schedule_changed=schedule_changed, passengers=request.passengers.model_dump(), currency=request.currency,
                search_quote={"provider": entry["offer"]["provider"], "price": entry["offer"]["price"],
                              "retrieved_at": entry["offer"]["retrieved_at"]},
                fares=fares, lowest_fare=fares[0] if fares else None,
                lowest_known_total_with_bags=min(known_totals, key=lambda f: Decimal(f["total_with_requested_bags"])) if known_totals else None,
                warnings=warnings)
