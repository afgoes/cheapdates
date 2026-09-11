"""Read prices and airlines from both Google Flights result groups.

fast-flights 3.1 reads only payload[3], omitting offers in payload[2]. We use
its query builder, but parse the small set of offer fields this tool needs.
The observed response shape is covered by a sanitized live-response fixture.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser


@dataclass(frozen=True)
class FlightOffer:
    price: int
    airlines: list[str] | None


def parse_flight_offers(html: str) -> list[FlightOffer]:
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ValueError("Google Flights result data is missing; the request may have been blocked")
    js = script.text()
    match = re.search(r"\bdata\s*:", js)
    if not match:
        raise ValueError("Google Flights result payload is missing")
    payload, _ = json.JSONDecoder().raw_decode(js[match.end():].lstrip())
    if not isinstance(payload, list) or len(payload) < 4:
        raise ValueError("Google Flights result payload has an unexpected format")

    offers = []
    recognized_group = False
    for group in payload[2:4]:
        if group is None:
            continue
        if not isinstance(group, list) or not group:
            raise ValueError("Malformed Google Flights result group")
        recognized_group = True
        rows = group[0]
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError("Malformed Google Flights offer list")
        for row in rows:
            try:
                airlines = row[0][1]
                price_data = row[1]
                price = price_data[0][1] if price_data and price_data[0] else None
            except (IndexError, TypeError) as e:
                raise ValueError("Malformed Google Flights offer") from e
            if price is None:
                continue
            if type(price) is not int or price < 0:
                raise ValueError("Invalid Google Flights offer price")
            if airlines is not None and (
                not isinstance(airlines, list) or any(not isinstance(a, str) for a in airlines)
            ):
                raise ValueError("Invalid Google Flights airline names")
            names = [a.strip() for a in (airlines or []) if a.strip()]
            offers.append(FlightOffer(price, names or None))
    if not recognized_group:
        raise ValueError("Google Flights returned no recognizable result groups")
    return offers
