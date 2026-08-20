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
            depart, ret = day[0], day[1]
            price = None
            if len(day) > 2 and day[2] and day[2][0] and len(day[2][0]) > 1:
                price = day[2][0][1]
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
        res.warnings.append("graph backend only supports max_stops=0 (nonstop) or any; ignoring max_stops")
    centers: list[dt.date] = []
    c = start + dt.timedelta(days=WINDOW_BEFORE)
    while c - dt.timedelta(days=WINDOW_BEFORE) <= end:
        centers.append(c)
        c += dt.timedelta(days=WINDOW)
    seen: dict[str, tuple[str | None, int | None]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
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
                    seen.setdefault(depart, (ret, price))
            finally:
                page.remove_listener("response", on_response)
        browser.close()
    d = start
    while d <= end:
        ret, price = seen.get(d.isoformat(), (None, None))
        res.days.append(DayPrice(depart=d, price=price, ret=dt.date.fromisoformat(ret) if ret else None))
        d += dt.timedelta(days=1)
    return res
