"""'sweep' backend: one fast-flights search per departure date. No browser needed."""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

from .core import DayPrice, Result
from .offers import parse_flight_offers


class _TransientFetchError(RuntimeError):
    """A transport failure or retryable HTTP response."""


class _FlightFetcher:
    """Fetch fast-flights queries with bounded network timeouts."""

    def fetch_html(self, q, /):
        from primp import Client

        client = Client(
            impersonate="chrome_145", impersonate_os="macos",
            referer=True, cookie_store=True, timeout=20,
        )
        try:
            response = client.get(
                "https://www.google.com/travel/flights",
                params=q.params() if not isinstance(q, str) else {"q": q},
            )
        except Exception as e:
            raise _TransientFetchError(f"Google Flights request failed ({type(e).__name__})") from e
        if response.status_code == 429 or response.status_code >= 500:
            raise _TransientFetchError(f"Google Flights returned HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"Google Flights returned HTTP {response.status_code}")
        return response.text


def _fetch_offers(query):
    return parse_flight_offers(_FlightFetcher().fetch_html(query))


def _one(origin, dest, day: dt.date, trip_length, currency, seat, max_stops):
    import fast_flights as ff

    legs = [ff.FlightQuery(date=day.isoformat(), from_airport=origin, to_airport=dest, max_stops=max_stops)]
    ret = None
    if trip_length:
        ret = day + dt.timedelta(days=trip_length)
        legs.append(ff.FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin, max_stops=max_stops))
    err = None
    q = ff.create_query(flights=legs, trip="round-trip" if trip_length else "one-way", currency=currency, seat=seat.replace("_", "-"))
    for attempt in range(3):
        try:
            flights = _fetch_offers(q)
            if not flights:
                return DayPrice(day, None, ret), None
            best = min(flights, key=lambda f: f.price)
            airlines = "/".join(best.airlines) if best.airlines else None
            warning = None if airlines else f"{day}: Google returned a priced offer without airline names"
            return DayPrice(day, int(best.price), ret, airlines), warning
        except _TransientFetchError as e:
            err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        except (IndexError, KeyError, TypeError, AttributeError, ValueError) as e:
            err = RuntimeError(
                f"Could not parse Google Flights response ({type(e).__name__}); "
                "the response may have changed or the route may have been rejected. "
                "Verify the IATA airport codes."
            )
            break
        except Exception as e:
            err = e
            break
    message = f"{type(err).__name__}: {str(err)[:240]}"
    return DayPrice(day, None, ret, error=message), f"{day}: {message}"


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
