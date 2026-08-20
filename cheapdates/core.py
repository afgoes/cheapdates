"""Shared models and the backend dispatcher."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Literal

Backend = Literal["auto", "graph", "sweep"]


@dataclass(frozen=True)
class DayPrice:
    depart: dt.date
    price: int | None
    ret: dt.date | None = None
    airlines: str | None = None  # only populated by the sweep backend

    def to_json(self) -> dict:
        d = asdict(self)
        d["depart"] = self.depart.isoformat()
        d["ret"] = self.ret.isoformat() if self.ret else None
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

    def to_json(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "currency": self.currency,
            "trip_length": self.trip_length,
            "backend": self.backend,
            "cheapest": self.cheapest.to_json() if self.cheapest else None,
            "days": [d.to_json() for d in self.days],
            "warnings": self.warnings,
        }


def _graph_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    import glob, os
    cache = os.path.expanduser("~/Library/Caches/ms-playwright") if os.uname().sysname == "Darwin" else os.path.expanduser("~/.cache/ms-playwright")
    return bool(glob.glob(os.path.join(cache, "chromium*")))


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
) -> Result:
    """Return the cheapest fare for every departure date in [start, end].

    trip_length=None → one-way. trip_length=N → round-trip returning N days later.
    backend: "graph" uses Google's own price calendar through a headless browser
    (one request ≈ 60 days); "sweep" runs one fast-flights search per date;
    "auto" prefers graph when Playwright+Chromium are installed.
    """
    origin, destination = origin.upper(), destination.upper()
    if end < start:
        raise ValueError("end must be >= start")
    if backend == "auto":
        backend = "graph" if _graph_available() else "sweep"
    if backend == "graph":
        from .graph import graph_cheapest_dates
        try:
            return graph_cheapest_dates(origin, destination, start, end, trip_length=trip_length, currency=currency, seat=seat, max_stops=max_stops)
        except Exception as e:  # fall through to sweep so the user still gets an answer
            from .sweep import sweep_cheapest_dates
            res = sweep_cheapest_dates(origin, destination, start, end, trip_length=trip_length, currency=currency, seat=seat, max_stops=max_stops, workers=workers)
            res.warnings.insert(0, f"graph backend failed ({type(e).__name__}: {e}); fell back to sweep")
            return res
    from .sweep import sweep_cheapest_dates
    return sweep_cheapest_dates(origin, destination, start, end, trip_length=trip_length, currency=currency, seat=seat, max_stops=max_stops, workers=workers)
