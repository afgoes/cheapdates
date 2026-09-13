"""Small, reviewed policy registry. Adding a program does not change the engine.

Only explicit published benefits/exclusions belong here. Missing rows are unknown.
These are tier policies, never evidence of eligibility for a particular ticket.
"""
import datetime as dt
from dataclasses import dataclass

CHECKED_ON = dt.date(2026, 9, 13)
REVIEW_INTERVAL = dt.timedelta(days=90)
DELTA_SOURCE = "https://www.delta.com/us/en/skymiles/medallion-program/international-partner-skyteam-benefits"
LATAM_SOURCE = "https://latampass.latam.com/en_un/associated-airlines/delta-air-lines"

# Exact provider disclosures only. A name match identifies a policy group, not
# an independently verified operating-carrier IATA code. Never use substrings.
OPERATORS = {
    "LA": ("latam airlines group", "latam airlines peru", "latam airlines brasil"),
    "DL": ("delta air lines", "delta airlines", "delta"),
    "VS": ("virgin atlantic", "virgin atlantic airways"),
}


@dataclass(frozen=True)
class Rule:
    benefit: str
    description: str
    conditions: tuple[str, ...] = ()
    offered: bool = True


@dataclass(frozen=True)
class Policy:
    program: str
    tiers: tuple[str, ...]
    operator: str
    source: str
    rules: tuple[Rule, ...]
    checked_on: dt.date = CHECKED_ON


POLICIES = (
    Policy("skymiles", ("gold", "platinum", "diamond"), "LA", DELTA_SOURCE, (
        Rule("seat_selection", "Regular seat assignment fee waived.", ("Confirm seat availability and any Basic fare exclusions.",)),
        Rule("extra_baggage", "One additional piece above the fare/route allowance.", ("Confirm weight, size and route allowance with LATAM.",)),
        Rule("priority_boarding", "Priority boarding is listed."),
        Rule("priority_check_in", "Priority check-in is listed.", ("Facilities vary by airport; Diamond/Platinum exclusive counters are limited to BOG, GRU, LIM, MIA and SCL where available.",)),
        Rule("priority_baggage_handling", "Priority baggage handling is listed."),
    )),
    Policy("skymiles", ("silver", "gold", "platinum", "diamond"), "VS", DELTA_SOURCE, (
        Rule("complimentary_upgrades", "Medallion complimentary cabin upgrades are not offered on Virgin Atlantic-operated flights.", offered=False),
    )),
    Policy("latam_pass", ("gold", "platinum", "black", "black_signature"), "DL", LATAM_SOURCE, (
        Rule("seat_selection", "Seat selection is listed after purchase.", ("Light fares allow selection only at the airport; confirm the exact fare and seat category.",)),
        Rule("priority_baggage_handling", "Priority baggage handling is listed."),
    )),
    Policy("latam_pass", ("platinum", "black", "black_signature"), "DL", LATAM_SOURCE, (
        Rule("priority_boarding", "Premium boarding in Zone 4 is listed."),
        Rule("extra_baggage", "Additional checked baggage is listed.", ("Confirm the allowance and bag limits for this fare and route.",)),
    )),
    Policy("latam_pass", ("gold",), "DL", LATAM_SOURCE, (
        Rule("extra_baggage", "This tier does not include the additional checked-bag partner benefit.", offered=False),
        Rule("priority_boarding", "This tier does not include the premium boarding partner benefit.", offered=False),
    )),
)
