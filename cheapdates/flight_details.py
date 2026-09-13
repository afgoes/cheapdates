"""Optional detail parsing; a bad row cannot masquerade as an empty search."""
from __future__ import annotations

import datetime as dt
import json
import re

from selectolax.lexbor import LexborHTMLParser

from .search_models import Segment, journey, money


def google_payload(html):
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ValueError("Google Flights result data is missing")
    js = script.text()
    match = re.search(r"\bdata\s*:", js)
    if not match:
        raise ValueError("Google Flights payload is missing")
    payload, _ = json.JSONDecoder().raw_decode(js[match.end():].lstrip())
    if not isinstance(payload, list) or len(payload) < 4:
        raise ValueError("Unexpected Google Flights payload")
    return payload


def local_time(date, time):
    if not isinstance(date, list) or len(date) != 3 or not isinstance(time, list) or len(time) > 2:
        raise ValueError("Missing or malformed flight time")
    h, m = (time + [None, None])[:2]
    return dt.datetime(*date, h or 0, m or 0)


def parse_google_details(html):
    rows, recognized = [], False
    for group in google_payload(html)[2:4]:
        if group is None:
            continue
        if not isinstance(group, list) or not group:
            raise ValueError("Malformed Google result group")
        recognized = True
        if group[0] is None:
            continue
        if not isinstance(group[0], list):
            raise ValueError("Malformed Google offer list")
        rows.extend(group[0])
    if not recognized:
        raise ValueError("No recognizable Google result groups")
    offers, skipped = [], 0
    for row in rows:
        try:
            if not row[1] or not row[1][0] or row[1][0][1] is None:
                continue
            segments = []
            for s in row[0][2]:
                carrier = s[22] if len(s) > 22 and isinstance(s[22], list) else []
                code = carrier[0] if len(carrier) > 0 else None
                number = carrier[1] if len(carrier) > 1 else None
                segments.append(Segment(
                    origin=s[3], destination=s[6],
                    departure_local=local_time(s[20], s[8]), arrival_local=local_time(s[21], s[10]),
                    duration_minutes=s[11], aircraft=s[17],
                    marketing_carrier=code, flight_number=f"{code}{number}" if code and number else None,
                    airline_name=carrier[3] if len(carrier) > 3 else None,
                    # Operating carrier, cabin and technical stops have no verified indexes.
                ))
            offers.append({"price": money(row[1][0][1]), "journey": journey(segments)})
        except (ValueError, IndexError, TypeError, KeyError):
            skipped += 1
    if skipped and not offers:
        raise ValueError("All priced flight rows had malformed details")
    return offers, skipped


def parse_serp_journey(row):
    segments = []
    for s in row["flights"]:
        number = s.get("flight_number")
        number = re.sub(r"\s+", "", number).upper() if isinstance(number, str) else None
        match = re.fullmatch(r"([A-Z0-9]{2})\d{1,4}[A-Z]?", number or "")
        segments.append(Segment(
            origin=s["departure_airport"]["id"], destination=s["arrival_airport"]["id"],
            departure_local=dt.datetime.fromisoformat(s["departure_airport"]["time"]),
            arrival_local=dt.datetime.fromisoformat(s["arrival_airport"]["time"]),
            duration_minutes=s["duration"], marketing_carrier=match[1] if match else None,
            airline_name=s.get("airline"), flight_number=number,
            aircraft=s.get("airplane"), cabin=s.get("travel_class"),
            # Do not infer an operator or zero technical stops from an airline display name.
        ))
    return journey(segments, row.get("total_duration"))


def parse_serp_details(payload):
    keys = [k for k in ("best_flights", "other_flights") if k in payload]
    if not keys:
        raise ValueError("Provider returned no recognizable flight result groups")
    rows = []
    for key in keys:
        if not isinstance(payload[key], list):
            raise ValueError("Malformed provider result group")
        rows.extend(payload[key])
    offers, skipped = [], 0
    for row in rows:
        if isinstance(row, dict) and row.get("price") is None:
            continue
        try:
            offers.append({"price": money(row["price"]), "journey": parse_serp_journey(row),
                           "departure_token": row.get("departure_token"), "booking_token": row.get("booking_token")})
        except (ValueError, KeyError, TypeError, IndexError):
            skipped += 1
    if skipped and not offers:
        raise ValueError("All priced flight rows had malformed details")
    return offers, skipped
