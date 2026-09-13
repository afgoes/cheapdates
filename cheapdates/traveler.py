"""Optional, per-request preferences. No account numbers or personal defaults."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Benefit = Literal[
    "seat_selection", "extra_baggage", "priority_boarding", "priority_check_in",
    "priority_baggage_handling", "lounge_access", "complimentary_upgrades",
    "upgrade_certificates", "mileage_earning",
]


def identifier(value: str) -> str:
    return re.sub(r"[\s-]+", "_", value.strip().lower())


class TravelerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    program: str = Field(min_length=1, max_length=80, description="Loyalty program name, not an account number. Any program is accepted; policy coverage is limited and explicit.")
    tier: str = Field(min_length=1, max_length=80, description="Status tier in this program. This is self-reported and applies to one traveler only.")
    required_benefits: list[Benefit] = Field(default_factory=list, max_length=9,
        description="Only benefits explicitly requested by this traveler. Empty by default; status alone must not create requirements.")
    require_non_basic: bool = Field(default=False, description="Record a requirement for review; this does not filter search results or verify a fare brand.")

    @field_validator("program", "tier", mode="before")
    @classmethod
    def normalize(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use a program or tier name")
        normalized = identifier(value)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized):
            raise ValueError("Use a program or tier name, without personal/account details")
        return normalized

    @model_validator(mode="after")
    def canonical_names(self):
        aliases = {"delta_skymiles": "skymiles", "latampass": "latam_pass",
                   "american_aadvantage": "aadvantage"}
        object.__setattr__(self, "program", aliases.get(self.program, self.program))
        if self.program == "skymiles":
            object.__setattr__(self, "tier", self.tier.removesuffix("_medallion"))
        object.__setattr__(self, "required_benefits", list(dict.fromkeys(self.required_benefits)))
        return self
