"""Free Google Flights selection URLs.

FlightData field 4 was checked against Google selection URLs on 2026-09-13.
The encoded query works over ordinary HTTP; no browser is launched.
"""
from __future__ import annotations

import base64
import re
from urllib.parse import urlencode


def _varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def _field(number, value):
    if isinstance(value, str):
        value = value.encode()
    return _varint(number * 8 + 2) + _varint(len(value)) + value


class SelectedQuery:
    """Extend fast-flights' query while preserving every existing filter."""
    def __init__(self, request, journeys, token=None):
        from .providers import google_query

        pb = google_query(request).pb()
        if not journeys or len(journeys) > len(pb.data):
            raise ValueError("Selected journeys do not match the search type")
        for leg, journey in zip(pb.data, journeys):
            for segment in journey.segments:
                number = segment.flight_number or ""
                if not re.fullmatch(r"[A-Z0-9]{2}\d{1,4}", number):
                    raise ValueError("Selection requires an IATA flight number for every segment")
                selected = b"".join(_field(key, value) for key, value in (
                    (1, segment.origin), (2, segment.departure_local.date().isoformat()),
                    (3, segment.destination), (5, number[:2]), (6, number[2:]),
                ))
                # Unknown protobuf fields are retained by the upstream runtime.
                leg.MergeFromString(_field(4, selected))
        self.values = {"tfs": base64.urlsafe_b64encode(pb.SerializeToString()).decode(),
                       "hl": "en", "curr": request.currency}
        if token:
            self.values["tfu"] = base64.urlsafe_b64encode(_field(1, token) + _field(2, b"\x08\x00")).decode()

    def params(self):
        return dict(self.values)

    def booking_url(self):
        return "https://www.google.com/travel/flights/booking?" + urlencode(self.values)
