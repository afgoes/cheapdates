"""'graph' backend: Google Flights' own price calendar (GetCalendarGraph).

Google signs every FlightsFrontendService request with a per-body BotGuard token
(x-goog-batchexecute-bgr), so the RPC can't be replayed from plain HTTP. Instead we
load the route page in headless Chromium, click "Price graph", and read the response
the page itself receives. One response covers ~60 departure dates.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse

from .core import DayPrice, Result

WINDOW_BEFORE = 7   # Google returns [date-7, date+52]
WINDOW = 60
SEAT_WORDS = {"economy": "", "premium_economy": "premium economy ", "business": "business class ", "first": "first class "}


def parse_calendar(text: str) -> list[tuple[str, str | None, int | None]]:
    """Parse a GetCalendarGraph response (chunked 'rt=c' framing) into (depart, return, price)."""
    dec = json.JSONDecoder()
    rows: list[tuple[str, str | None, int | None]] = []
    for m in re.finditer(r'\[\["wrb\.fr"', text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        payload = obj[0][2] if len(obj[0]) > 2 else None
        if not payload:
            continue
        inner = json.loads(payload)
        if not (isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list)):
            continue
        for day in inner[1]:
            if not isinstance(day, list) or len(day) < 2:
                raise ValueError("malformed calendar row from Google Flights")
            depart, ret = day[0], day[1]
            try:
                dt.date.fromisoformat(depart)
                if ret is not None:
                    dt.date.fromisoformat(ret)
            except (TypeError, ValueError) as e:
                raise ValueError("malformed calendar dates from Google Flights") from e
            price = None
            try:
                if len(day) > 2 and day[2] and day[2][0] and len(day[2][0]) > 1:
                    price = day[2][0][1]
            except (TypeError, IndexError) as e:
                raise ValueError("malformed calendar price from Google Flights") from e
            if price is not None and (type(price) is not int or price < 0):
                raise ValueError("invalid calendar price from Google Flights")
            rows.append((depart, ret, price))
    if not rows:
        raise ValueError("no calendar rows in response (Google may have changed the format or rejected the request)")
    return rows


def _query_url(origin: str, dest: str, date: dt.date, trip_length: int | None, currency: str, seat: str, max_stops: int | None) -> str:
    q = f"{SEAT_WORDS.get(seat, '')}Flights to {dest} from {origin} on {date.isoformat()}"
    q += f" through {(date + dt.timedelta(days=trip_length)).isoformat()}" if trip_length else " one way"
    if max_stops == 0:
        q += " nonstop"
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"hl": "en", "curr": currency, "q": q})


def graph_cheapest_dates(origin, dest, start: dt.date, end: dt.date, *, trip_length=None, currency="USD", seat="economy", max_stops=None, timeout_s=30) -> Result:
    from playwright.sync_api import sync_playwright

    res = Result(origin, dest, start, end, currency, trip_length, "graph")
    if max_stops not in (None, 0):
        raise ValueError("graph supports only max_stops=0 (nonstop) or None; use sweep for other stop limits")
    centers: list[dt.date] = []
    c = start + dt.timedelta(days=WINDOW_BEFORE)
    while c - dt.timedelta(days=WINDOW_BEFORE) <= end:
        centers.append(c)
        c += dt.timedelta(days=WINDOW)
    seen: dict[str, tuple[str | None, int | None]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(locale="en-US")
            page = ctx.new_page()
            for center in centers:
                captured: dict[str, str] = {}

                def on_response(r):
                    if "GetCalendarGraph" in r.url and "text" not in captured:
                        try:
                            captured["text"] = r.text()
                        except Exception:
                            pass

                page.on("response", on_response)
                try:
                    page.goto(_query_url(origin, dest, center, trip_length, currency, seat, max_stops), wait_until="networkidle", timeout=timeout_s * 1000)
                    if "text" not in captured:
                        page.click("button:has-text('Price graph')", timeout=8000)
                        for _ in range(timeout_s * 4):
                            if "text" in captured:
                                break
                            page.wait_for_timeout(250)
                    if "text" not in captured:
                        raise TimeoutError("price graph never loaded")
                    for depart, ret, price in parse_calendar(captured["text"]):
                        expected_return = (dt.date.fromisoformat(depart) + dt.timedelta(days=trip_length)).isoformat() if trip_length else None
                        if ret == expected_return:
                            seen.setdefault(depart, (ret, price))
                finally:
                    page.remove_listener("response", on_response)
        finally:
            browser.close()
    d = start
    while d <= end:
        ret, price = seen.get(d.isoformat(), (None, None))
        error = None
        if d.isoformat() not in seen:
            error = "Google calendar omitted this departure/return date pair"
            res.warnings.append(f"{d}: {error}")
        res.days.append(DayPrice(
            depart=d, price=price,
            ret=dt.date.fromisoformat(ret) if ret else d + dt.timedelta(days=trip_length) if trip_length else None,
            error=error,
        ))
        d += dt.timedelta(days=1)
    if res.status == "error":
        raise ValueError("Google calendar contained none of the requested departure/return date pairs")
    return res
