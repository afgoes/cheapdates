"""Validated search inputs and provider-independent flight details."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .core import Seat, _airport

Count = Annotated[int, Field(strict=True, ge=0, le=9)]
Hour = Annotated[int, Field(strict=True, ge=0, le=23)]
Minutes = Annotated[int, Field(strict=True, ge=0, le=10080)]
Stops = Annotated[int, Field(strict=True, ge=0, le=2)]
Alliance = Literal["ONEWORLD", "SKYTEAM", "STAR_ALLIANCE"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Passengers(Model):
    adults: Annotated[int, Field(strict=True, ge=1, le=9)] = 1
    children: Count = 0
    infants_in_seat: Count = 0
    infants_on_lap: Count = 0

    @model_validator(mode="after")
    def valid_party(self):
        if sum(self.model_dump().values()) > 9:
            raise ValueError("At most nine passengers are supported")
        if self.infants_on_lap > self.adults:
            raise ValueError("Each infant on lap needs an adult")
        return self


class LegFilters(Model):
    max_stops: Stops | None = None
    earliest_departure_hour: Hour | None = None
    latest_departure_hour: Hour | None = None
    earliest_arrival_hour: Hour | None = None
    latest_arrival_hour: Hour | None = None
    max_duration_minutes: Annotated[int, Field(strict=True, gt=0, le=10080)] | None = None
    min_layover_minutes: Minutes | None = None
    max_layover_minutes: Minutes | None = None
    avoid_overnight_layovers: bool = False
    avoid_airport_changes: bool = False

    @model_validator(mode="after")
    def ordered_bounds(self):
        for lo, hi in [(self.earliest_departure_hour, self.latest_departure_hour),
                       (self.earliest_arrival_hour, self.latest_arrival_hour),
                       (self.min_layover_minutes, self.max_layover_minutes)]:
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("Lower time/layover bound must not exceed upper bound")
        return self


class SearchRequest(Model):
    origin: str
    destination: str
    departure_date: dt.date
    return_date: dt.date | None = None
    currency: str = "USD"
    seat: Seat = "economy"
    passengers: Passengers = Field(default_factory=Passengers)
    provider: Literal["google", "serpapi"] = "google"
    include_airlines: list[str] = Field(default_factory=list, max_length=20)
    exclude_airlines: list[str] = Field(default_factory=list, max_length=20)
    alliance: Alliance | None = None
    airline_scope: Literal["marketing", "operating"] = "marketing"
    outbound: LegFilters = Field(default_factory=LegFilters)
    inbound: LegFilters = Field(default_factory=LegFilters)
    carry_on_bags: Count = 0
    checked_bags: Count = 0
    exclude_basic_economy: bool = False
    hide_separate_and_self_transfer: bool = False
    max_price: Annotated[int, Field(strict=True, gt=0)] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 5
    sort_by: Literal["price", "duration", "departure"] = "price"

    @field_validator("origin", "destination")
    @classmethod
    def airport(cls, value, info):
        return _airport(value, info.field_name)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be a three-letter code")
        return value

    @field_validator("include_airlines", "exclude_airlines")
    @classmethod
    def carrier_codes(cls, values):
        codes = list(dict.fromkeys(v.strip().upper() for v in values))
        if any(not re.fullmatch(r"[A-Z0-9]{2}", v) or v.isdigit() for v in codes):
            raise ValueError("Airlines must be two-character IATA codes; use alliance for alliances")
        return codes

    @model_validator(mode="after")
    def compatible(self):
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.return_date and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        if self.return_date is None and self.inbound != LegFilters():
            raise ValueError("inbound filters require return_date")
        if sum(bool(x) for x in (self.include_airlines, self.exclude_airlines, self.alliance)) > 1:
            raise ValueError("Choose include_airlines, exclude_airlines, or alliance, not a combination")
        if self.alliance and self.airline_scope == "operating":
            raise ValueError("Operating-carrier alliance filtering is not supported")
        if self.carry_on_bags > self.passengers.adults + self.passengers.children + self.passengers.infants_in_seat:
            raise ValueError("carry_on_bags cannot exceed passengers with seats")
        if self.exclude_basic_economy:
            raise ValueError("Global basic-economy exclusion cannot be verified. Use compare_fares_tool to inspect fare types for selected flights.")
        if self.provider == "serpapi" and (self.checked_bags or self.hide_separate_and_self_transfer):
            raise ValueError("SerpApi cannot enforce checked_bags or hide_separate_and_self_transfer in this adapter; inspect fare options instead")
        return self


def money(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("Invalid offer price")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid offer price") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid offer price")
    return format(amount, "f")


class Segment(Model):
    origin: str
    destination: str
    departure_local: dt.datetime
    arrival_local: dt.datetime
    duration_minutes: Annotated[int, Field(strict=True, ge=0)]
    marketing_carrier: str | None = None
    operating_carrier: str | None = None
    airline_name: str | None = None
    flight_number: str | None = None
    aircraft: str | None = None
    cabin: str | None = None
    technical_stops: Annotated[int, Field(strict=True, ge=0)] | None = None

    @field_validator("origin", "destination")
    @classmethod
    def airport_code(cls, value, info):
        return _airport(value, info.field_name)


class Layover(Model):
    arrival_airport: str
    departure_airport: str
    minutes: int | None
    overnight: bool | None
    airport_change: bool


class Journey(Model):
    segments: list[Segment] = Field(min_length=1)
    duration_minutes: Annotated[int, Field(strict=True, ge=0)] | None = None
    layovers: list[Layover] = Field(default_factory=list)

    def to_json(self):
        data = self.model_dump(mode="json")
        data["connections"] = len(self.segments) - 1
        if len(self.segments) > 1 or any(s.technical_stops for s in self.segments):
            data["nonstop_verified"] = False
        elif all(s.technical_stops == 0 for s in self.segments):
            data["nonstop_verified"] = True
        else:
            data["nonstop_verified"] = None
        data["time_basis"] = "local airport time; UTC offsets not supplied"
        return data


def journey(segments: list[Segment], duration: int | None = None) -> Journey:
    layovers = []
    for a, b in zip(segments, segments[1:]):
        same = a.destination == b.origin
        minutes = int((b.departure_local - a.arrival_local).total_seconds() / 60) if same else None
        if minutes is not None and minutes < 0:
            raise ValueError("Flight connection has reversed local times")
        layovers.append(Layover(arrival_airport=a.destination, departure_airport=b.origin,
                                minutes=minutes, airport_change=not same,
                                overnight=b.departure_local.date() > a.arrival_local.date() if same else None))
    if duration is None and all(l.minutes is not None for l in layovers):
        duration = sum(s.duration_minutes for s in segments) + sum(l.minutes for l in layovers)
    return Journey(segments=segments, duration_minutes=duration, layovers=layovers)
