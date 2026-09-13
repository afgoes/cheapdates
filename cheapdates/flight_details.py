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
            offers.append({"price": money(row[1][0][1]), "journey": parse_google_journey(row[0]),
                           "selection_token": row[1][1] if len(row[1]) > 1 and isinstance(row[1][1], str) else None})
        except (ValueError, IndexError, TypeError, KeyError):
            skipped += 1
    if skipped and not offers:
        raise ValueError("All priced flight rows had malformed details")
    return offers, skipped



def parse_google_journey(data):
    segments = []
    for s in data[2]:
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
    return journey(segments, data[9] if len(data) > 9 else None)
