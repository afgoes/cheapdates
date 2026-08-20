"""'sweep' backend: one fast-flights search per departure date. No browser needed."""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

from .core import DayPrice, Result


def _one(origin, dest, day: dt.date, trip_length, currency, seat, max_stops):
    import fast_flights as ff

    legs = [ff.FlightQuery(date=day.isoformat(), from_airport=origin, to_airport=dest, max_stops=max_stops)]
    ret = None
    if trip_length:
        ret = day + dt.timedelta(days=trip_length)
        legs.append(ff.FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin, max_stops=max_stops))
    err = None
    for attempt in range(3):
        try:
            q = ff.create_query(flights=legs, trip="round-trip" if trip_length else "one-way", currency=currency, seat=seat.replace("_", "-"))
            flights = [f for f in ff.get_flights(q) if f.price]
            if not flights:
                return DayPrice(day, None, ret), None
            best = min(flights, key=lambda f: f.price)
            return DayPrice(day, int(best.price), ret, "/".join(best.airlines)), None
        except Exception as e:  # transient 429s etc.
            err = e
            time.sleep(2 * (attempt + 1))
    return DayPrice(day, None, ret), f"{day}: {type(err).__name__}: {str(err)[:80]}"


def sweep_cheapest_dates(origin, dest, start: dt.date, end: dt.date, *, trip_length=None, currency="USD", seat="economy", max_stops=None, workers=4) -> Result:
    res = Result(origin, dest, start, end, currency, trip_length, "sweep")
    days = [start + dt.timedelta(i) for i in range((end - start).days + 1)]
    with ThreadPoolExecutor(workers) as ex:
        out = list(ex.map(lambda d: _one(origin, dest, d, trip_length, currency, seat, max_stops), days))
    for dp, warn in out:
        res.days.append(dp)
        if warn:
            res.warnings.append(warn)
    return res
