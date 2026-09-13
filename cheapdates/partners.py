"""Small sourced partner directory, deliberately separate from fare eligibility."""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal

Program = Literal["aadvantage", "skymiles", "latam_pass"]
CHECKED_ON = dt.date(2026, 9, 13)
# Curated entries, not a complete alliance or program membership database.
PROGRAMS = {
    "aadvantage": {
        "name": "American AAdvantage",
        "source": "https://www.aa.com/pubcontent/en_US/aadvantage-program/miles/partners/partner-airlines.html",
        "airlines": {"AS": "Alaska Airlines", "BA": "British Airways", "CX": "Cathay Pacific",
                     "AY": "Finnair", "IB": "Iberia", "JL": "Japan Airlines", "QF": "Qantas",
                     "QR": "Qatar Airways", "EY": "Etihad Airways", "G3": "GOL", "JA": "JetSMART"},
    },
    "skymiles": {
        "name": "Delta SkyMiles",
        "source": "https://www.delta.com/us/en/skymiles/how-to-earn-miles/airline-partners",
        "airlines": {"LA": "LATAM", "AF": "Air France", "KL": "KLM", "AM": "Aeromexico",
                     "KE": "Korean Air", "VS": "Virgin Atlantic"},
    },
    "latam_pass": {
        "name": "LATAM Pass",
        "source": "https://web.latampass.latam.com/en_un/associated-airlines",
        "airlines": {"DL": "Delta Air Lines", "AM": "Aeromexico"},
    },
}


def airline_partners(program: Program, airline: str | None = None):
    if program not in PROGRAMS:
        raise ValueError("Supported programs: aadvantage, skymiles, latam_pass")
    if airline is not None:
        airline = airline.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2}", airline) or airline.isdigit():
            raise ValueError("airline must be a two-character IATA code")
    record = PROGRAMS[program]
    stale = dt.date.today() > CHECKED_ON + dt.timedelta(days=90)
    codes = [airline] if airline else list(record["airlines"])
    return dict(program=program, name=record["name"], source=record["source"],
                checked_on=str(CHECKED_ON), refresh_due=str(CHECKED_ON + dt.timedelta(days=90)),
                status="refresh_required" if stale else "ok", coverage="curated_subset",
                airlines=[dict(code=c, name=record["airlines"].get(c),
                               relationship="documented_partner" if c in record["airlines"] and not stale else "unknown",
                               earning_eligibility="unknown", award_availability="unknown") for c in codes],
                warnings=["Directory membership is not fare eligibility. Check booking class, marketing and operating carriers, ticket rules and travel dates against the source",
                          "An unlisted airline is unknown, not necessarily a non-partner. Award inventory is not searched"])
