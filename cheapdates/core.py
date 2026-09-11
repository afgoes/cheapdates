"""Shared models and the backend dispatcher."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, asdict
from typing import Literal

Backend = Literal["auto", "graph", "sweep"]
Seat = Literal["economy", "premium_economy", "business", "first"]


@dataclass(frozen=True)
class DayPrice:
    depart: dt.date
    price: int | None
    ret: dt.date | None = None
    airlines: str | None = None  # only populated by the sweep backend
    error: str | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        d["depart"] = self.depart.isoformat()
        d["ret"] = self.ret.isoformat() if self.ret else None
        d["status"] = "error" if self.error else "priced" if self.price is not None else "no_results"
        return d


@dataclass
class Result:
    origin: str
    destination: str
    start: dt.date
    end: dt.date
    currency: str
    trip_length: int | None
    backend: str
    days: list[DayPrice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def priced(self) -> list[DayPrice]:
        return [d for d in self.days if d.price is not None]

    @property
    def cheapest(self) -> DayPrice | None:
        return min(self.priced, key=lambda d: d.price) if self.priced else None

    @property
    def status(self) -> str:
        errors = sum(d.error is not None for d in self.days)
        if errors:
            return "error" if errors == len(self.days) else "partial"
        return "ok" if self.priced else "no_results"

    def to_json(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "currency": self.currency,
            "trip_length": self.trip_length,
            "backend": self.backend,
            "status": self.status,
            "cheapest": self.cheapest.to_json() if self.cheapest else None,
            "days": [d.to_json() for d in self.days],
            "warnings": self.warnings,
        }


def _graph_available() -> bool:
    # Let Playwright resolve platform-specific and custom browser locations.
    # Launch errors still fall back to sweep in the dispatcher.
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _airport(value: str, field_name: str) -> str:
    code = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(
            f"{field_name} must be a three-letter IATA airport code, got {value!r}. "
            "Use SCL for Santiago, Chile. For New York, choose a departure airport "
            "such as JFK, EWR, or LGA; city names are not supported."
        )
    return code


def cheapest_dates(
    origin: str,
    destination: str,
    start: dt.date,
    end: dt.date,
    *,
    trip_length: int | None = None,
    currency: str = "USD",
    backend: Backend = "auto",
    max_stops: int | None = None,
    seat: str = "economy",
    workers: int = 4,
    include_airlines: bool = False,
) -> Result:
    """Return the cheapest fare for every departure date in [start, end].

    trip_length=None → one-way. trip_length=N → round-trip returning N days later.
    backend: "graph" uses Google's own price calendar through a headless browser
    (one request ≈ 60 days); "sweep" runs one fast-flights search per date;
    "auto" tries graph when Playwright is installed, falling back to sweep.
    include_airlines=True uses sweep so prices and airlines describe the same offer.
    Airports must be IATA codes; city names are rejected before any network requests.
    """
    origin, destination = _airport(origin, "origin"), _airport(destination, "destination")
    if origin == destination:
        raise ValueError("origin and destination must be different airports")
    if end < start:
        raise ValueError("end must be >= start")
    if backend not in ("auto", "graph", "sweep"):
        raise ValueError("backend must be auto, graph, or sweep")
    seat = seat.strip().lower().replace("-", "_")
    if seat not in ("economy", "premium_economy", "business", "first"):
        raise ValueError("seat must be economy, premium_economy, business, or first")
    currency = currency.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter currency code, such as USD")
    if trip_length is not None and (type(trip_length) is not int or trip_length <= 0):
        raise ValueError("trip_length must be a positive integer or None for one-way")
    if max_stops is not None and (type(max_stops) is not int or max_stops < 0):
        raise ValueError("max_stops must be a nonnegative integer or None")
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    if type(include_airlines) is not bool:
        raise ValueError("include_airlines must be a boolean")
    if trip_length:
        try:
            end + dt.timedelta(days=trip_length)
        except OverflowError as e:
            raise ValueError("trip_length puts the return date outside the supported date range") from e
    selection_warnings = []
    if include_airlines or max_stops not in (None, 0):
        if backend == "graph":
            selection_warnings.append("Using sweep to honor include_airlines or max_stops; graph provides calendar prices only and supports only nonstop or any stops.")
        backend = "sweep"
    if backend == "auto":
        backend = "graph" if _graph_available() else "sweep"
    if backend == "graph":
        from .graph import graph_cheapest_dates
        try:
            res = graph_cheapest_dates(origin, destination, start, end, trip_length=trip_length, currency=currency, seat=seat, max_stops=max_stops)
            res.warnings.append("Graph calendar prices do not include airlines. Set include_airlines=true for individual flight searches with airline names.")
            return res
        except Exception as e:  # fall through to sweep so the user still gets an answer
            from .sweep import sweep_cheapest_dates
            res = sweep_cheapest_dates(origin, destination, start, end, trip_length=trip_length, currency=currency, seat=seat, max_stops=max_stops, workers=workers)
            res.warnings.insert(0, f"graph backend failed ({type(e).__name__}: {e}); fell back to sweep")
            return res
    from .sweep import sweep_cheapest_dates
    res = sweep_cheapest_dates(origin, destination, start, end, trip_length=trip_length, currency=currency, seat=seat, max_stops=max_stops, workers=workers)
    res.warnings[:0] = selection_warnings
    return res
