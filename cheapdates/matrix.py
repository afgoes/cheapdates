"""Browserless ITA Matrix fare research, using its public web-app protocol.

Protocol reference: https://github.com/YogevKr/itamx (independent implementation).
No account, paid service, personal API key, cookies or browser are used.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import uuid

from .search_models import Segment, journey, money

_ORIGIN = "https://matrix.itasoftware.com"
_BATCH = "https://content-alkalimatrix-pa.googleapis.com/batch"
_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{35}")
_KEY = None
_KEY_UNTIL = 0
_KEY_LOCK = threading.Lock()
_CABINS = {"economy": "COACH", "premium_economy": "PREMIUM-COACH", "business": "BUSINESS", "first": "FIRST"}


class MatrixUnavailable(RuntimeError):
    pass


class MatrixClient:
    def __init__(self):
        from primp import Client
        self.client = Client(timeout=55, headers={"Origin": _ORIGIN, "Referer": _ORIGIN + "/",
                                                   "Accept-Language": "en-US,en;q=0.9"})

    def _raw(self, method, path, key, body=None):
        boundary = "batch" + uuid.uuid4().hex
        lines = ["--" + boundary, "Content-Type: application/http", "Content-Transfer-Encoding: binary",
                 "Content-ID: <" + boundary + "+gapiRequest@googleapis.com>", "",
                 f"{method} {path}{'&' if '?' in path else '?'}key={key}&alt=json",
                 "x-alkali-application-key: applications/matrix", "x-alkali-auth-apps-namespace: alkali_v2",
                 "x-alkali-auth-entities-namespace: alkali_v2", "X-Requested-With: XMLHttpRequest",
                 "Content-Type: application/json", "", json.dumps(body, separators=(",", ":")) if body else "",
                 "--" + boundary + "--", ""]
        try:
            response = self.client.post(_BATCH, params={"$ct": "multipart/mixed; boundary=" + boundary},
                                        headers={"Content-Type": "text/plain; charset=UTF-8"},
                                        content="\r\n".join(lines).encode())
        except Exception:
            raise MatrixUnavailable("ITA Matrix request failed or timed out") from None
        if response.status_code == 429 or response.status_code >= 500:
            raise MatrixUnavailable(f"ITA Matrix returned HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"ITA Matrix returned HTTP {response.status_code}")
        text = response.text
        try:
            data, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("ITA Matrix returned malformed response data") from None
        return data

    def _public_identifier(self):
        global _KEY, _KEY_UNTIL
        with _KEY_LOCK:
            if _KEY and time.monotonic() < _KEY_UNTIL:
                return _KEY
            try:
                response = self.client.get(_ORIGIN + "/search", timeout=15)
                if response.status_code != 200:
                    raise ValueError()
                html = response.text
                candidates = _KEY_RE.findall(html)
                # Only fetch bundles on the public site's fixed Google asset host.
                urls = re.findall(r'src="(//www\.gstatic\.com/alkali/[^"<>]+\.js)"', html)
                for url in urls[:3]:
                    bundle = self.client.get("https:" + url, timeout=15)
                    if bundle.status_code == 200:
                        candidates.extend(_KEY_RE.findall(bundle.text))
                for key in list(dict.fromkeys(candidates))[:8]:
                    result = self._raw("GET", "/v1/locationTypes/CITIES_AND_AIRPORTS/partialNames/LAX/locations?pageSize=1", key)
                    if isinstance(result.get("locations"), list):
                        _KEY, _KEY_UNTIL = key, time.monotonic() + 86400
                        return key
            except Exception:
                raise MatrixUnavailable("Could not initialize ITA Matrix from its public website; retry later") from None
        raise MatrixUnavailable("ITA Matrix public website configuration has changed; no fare request was sent")

    def call(self, path, body):
        global _KEY, _KEY_UNTIL
        for attempt in range(3):
            key = self._public_identifier()
            try:
                result = self._raw("POST", path, key, body)
                error = result.get("error")
                if not error:
                    return result
                code = error.get("code") if isinstance(error, dict) else None
                message = error.get("message", "") if isinstance(error, dict) else ""
                if code in (401, 403) and attempt == 0:
                    with _KEY_LOCK:
                        _KEY, _KEY_UNTIL = None, 0
                    continue
                if code in (429, 500, 502, 503, 504) or re.search(r"currently unavailable|temporarily|internal error", message, re.I):
                    raise MatrixUnavailable("ITA Matrix is temporarily unavailable")
                raise ValueError("ITA Matrix rejected the fare query; its request format or available fares may have changed")
            except MatrixUnavailable:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise MatrixUnavailable("ITA Matrix could not complete the fare query")


def matrix_inputs(request, journeys, booking_code=None):
    slices = []
    for j in journeys:
        numbers = [s.flight_number for s in j.segments]
        if any(not n or not re.fullmatch(r"[A-Z0-9]{2}\d{1,4}", n) for n in numbers):
            raise ValueError("Matrix fare research needs IATA flight numbers for every segment")
        sl = dict(origins=[j.segments[0].origin], destinations=[j.segments[-1].destination],
                  date=j.segments[0].departure_local.date().isoformat(), dateModifier={"minus": 0, "plus": 0},
                  isArrivalDate=False, filter={"warnings": {"values": []}}, selected=False,
                  routeLanguage=" ".join(numbers))
        if booking_code:
            sl["commandLine"] = "f bc=" + booking_code
        slices.append(sl)
    pax = dict(adults=request.passengers.adults, children=request.passengers.children,
               infantsInSeat=request.passengers.infants_in_seat, infantsInLap=request.passengers.infants_on_lap)
    return dict(filter={}, page={"current": 1, "size": 10}, pax=pax, slices=slices, firstDayOfWeek="SUNDAY",
                internalUser=False, sliceIndex=0, sorts="default", cabin=_CABINS[request.seat],
                maxLegsRelativeToMin=2, changeOfAirport=True, checkAvailability=True, currency=request.currency)


def _amount(value, currency):
    if not isinstance(value, str) or not re.fullmatch(re.escape(currency) + r"\d+(?:\.\d+)?", value):
        raise ValueError("ITA Matrix price has missing or mismatched currency")
    return money(value[3:])


def parse_detail(payload, request):
    """Preserve exact Matrix fare components and notes; never infer a brand."""
    try:
        data = payload["bookingDetails"]
        if data["passengerCount"] != sum(request.passengers.model_dump().values()):
            raise ValueError("ITA Matrix returned different passenger counts")
        totals = {"adults": 0, "children": 0, "infantsInSeat": 0, "infantsInLap": 0}
        for pricing in data["pricings"]:
            for key, count in pricing["ext"]["pax"].items():
                if key not in totals or type(count) is not int or count < 0:
                    raise ValueError("Unsupported Matrix passenger category")
                totals[key] += count
        expected = matrix_inputs(request, [])['pax']
        if totals != expected:
            raise ValueError("ITA Matrix returned different passenger categories")
        if not isinstance(data["tickets"], list) or len(data["tickets"]) != 1:
            raise ValueError("Matrix fare is not a single combined ticket")
        journeys, booking_codes = [], []
        for sl in data["itinerary"]["slices"]:
            segments = []
            for s in sl["segments"]:
                code, number = s["carrier"]["code"], str(s["flight"]["number"])
                booking_codes.append([info.get("bookingCode") for info in s.get("bookingInfos", [])])
                segments.append(Segment(origin=s["origin"]["code"], destination=s["destination"]["code"],
                    departure_local=dt.datetime.fromisoformat(s["departure"]).replace(tzinfo=None),
                    arrival_local=dt.datetime.fromisoformat(s["arrival"]).replace(tzinfo=None),
                    duration_minutes=s["duration"], marketing_carrier=code, flight_number=code + number,
                    airline_name=s["carrier"].get("shortName"),
                    cabin=s["bookingInfos"][0].get("cabin") if s.get("bookingInfos") else None,
                    technical_stops=len(s["legs"]) - 1 if isinstance(s.get("legs"), list) and s["legs"] else None))
            journeys.append(journey(segments))
        components, taxes, notes = [], [], []
        for pricing in data["tickets"][0]["pricings"]:
            for fare in pricing["fares"]:
                components.append(dict(carrier=fare["carrier"], fare_basis=fare["code"],
                    origin=fare["originCity"], destination=fare["destinationCity"],
                    price=_amount(fare["displayAdjustedPrice"], request.currency),
                    passenger_types=fare.get("ptcs", []), booking_info=fare.get("bookingInfos", [])))
            for tax in pricing["ext"].get("taxTotals", []):
                taxes.append(dict(code=tax["code"], name=tax["tax"]["name"], price=_amount(tax["totalDisplayPrice"], request.currency)))
            values = pricing.get("notes", [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("Malformed Matrix ticket notes")
            notes.extend(values)
        return dict(price=_amount(data["displayTotal"], request.currency), journeys=journeys,
                    fare_components=components, taxes=taxes, ticket_notes=list(dict.fromkeys(notes)), booking_codes=booking_codes)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError("ITA Matrix returned malformed fare details") from exc


def fetch_fares(request, outbound, inbound, booking_codes):
    client = MatrixClient()
    journeys = [outbound] + ([inbound] if inbound else [])
    quotes, warnings, skipped = [], [], 0
    for code in [None] + booking_codes:
        inputs = matrix_inputs(request, journeys, code)
        search = client.call("/v1/search", dict(summarizers=["solutionList"], inputs=inputs,
                             summarizerSet="wholeTrip", name="specificDatesSlice"))
        try:
            solutions = search["solutionList"]["solutions"]
            if not isinstance(solutions, list):
                raise ValueError("ITA Matrix did not return a solution list")
            for solution in solutions[:10]:
                detail = client.call("/v1/summarize", dict(summarizers=["bookingDetails"],
                    inputs=inputs | {"solution": search["solutionSet"] + "/" + solution["id"]},
                    summarizerSet="viewDetails", solutionSet=search["solutionSet"], session=search["session"]))
                try:
                    quote = parse_detail(detail, request)
                    if code and any(not group or any(actual != code for actual in group) for group in quote["booking_codes"]):
                        raise ValueError("Matrix did not honor the requested booking class")
                    quote['requested_booking_code'] = code
                    quotes.append(quote)
                except ValueError:
                    skipped += 1
            if not solutions:
                warnings.append(f"Matrix found no fare for {'booking class ' + code if code else 'the selected flights'}")
        except (KeyError, TypeError, IndexError):
            raise ValueError("ITA Matrix returned malformed search results") from None
    if skipped and not quotes:
        raise ValueError("All Matrix fare details were malformed or failed currency, passenger or single-ticket checks")
    return {"quotes": quotes, "warnings": warnings, "malformed_rows": skipped}
