"""Local, source-backed policy assessments, separate from ticket verification."""
from __future__ import annotations

import datetime as dt
import re

from .benefit_policies import OPERATORS, POLICIES, REVIEW_INTERVAL
from .traveler import TravelerProfile

FOLLOW_UP = {
    "mileage_earning": "Confirm the marketing carrier, ticketing carrier, paid/award status and booking class against this program's earning rules.",
    "upgrade_certificates": "Confirm the certificate type actually held, permitted operators/routes/fares and upgrade inventory; tier alone does not prove certificate ownership.",
    "complimentary_upgrades": "Check this program's rules for the operating airline, route, cabin, fare and upgrade availability.",
    "lounge_access": "Check the specific lounge, airport, cabin, international connection and guest rules; airline partnership alone does not grant entry.",
}


def _operator(segment):
    code = segment.get("operating_carrier")
    name = segment.get("operating_airline_name")
    named = next((key for key, names in OPERATORS.items()
                  if isinstance(name, str) and name.strip().casefold() in names), None)
    if code:
        code = code.upper()
        if named and named != code:
            return None, "conflicting_operator_evidence"
        return code, "provider_operator_code"
    return (named, "provider_operator_name") if named else (None, "operator_unknown")


def assess_journeys(traveler, journeys, *, complete, quote_provider, today=None):
    """Assess each segment independently; never transfer fare facts between quotes."""
    traveler = TravelerProfile.model_validate(traveler)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    segments, sources = [], {}
    for direction, j in journeys.items():
        for index, segment in enumerate(j["segments"]):
            operator, evidence = _operator(segment)
            assessments = []
            departure = segment.get("departure_local")
            travel_date = dt.date.fromisoformat(str(departure)[:10]) if departure else None
            for benefit in traveler.required_benefits:
                matches = [(p, r) for p in POLICIES if
                           (p.program, p.operator) == (traveler.program, operator) and traveler.tier in p.tiers
                           for r in p.rules if r.benefit == benefit]
                row = dict(benefit=benefit, policy_status="unknown", ticket_eligibility="unknown",
                           reason="operator_unknown" if operator is None else "policy_not_covered",
                           description=None, conditions=[], source=None)
                if matches:
                    policy, rule = matches[0]
                    review_due = policy.checked_on + REVIEW_INTERVAL
                    fresh = policy.checked_on <= today <= review_due
                    source_key = f"{policy.source}|{policy.checked_on}"
                    sources[source_key] = dict(url=policy.source, checked_on=policy.checked_on.isoformat(),
                                               review_due=review_due.isoformat(), stale=not fresh)
                    row.update(source=source_key, description=rule.description,
                               reason="policy_requires_confirmation" if fresh else "policy_review_required")
                    if fresh:
                        row["policy_status"] = "documented" if rule.offered else "not_offered"
                        row["ticket_eligibility"] = "conditional" if rule.offered else "not_offered_by_tier_policy"
                    row["conditions"] = list(rule.conditions)
                    if travel_date and travel_date > review_due:
                        row["conditions"].append("Refresh the policy before travel; the travel date exceeds the source review window.")
                if row["policy_status"] != "not_offered":
                    row["conditions"].extend([
                        "Confirm this traveler's status is valid on the travel date and recognized on the reservation.",
                        "Confirm the seller's exact fare brand and applicable benefit exclusions for this quote.",
                    ])
                if row["policy_status"] == "unknown":
                    row["conditions"].append(FOLLOW_UP.get(benefit, "Consult this program's current policy for this tier and operator."))
                assessments.append(row)
            segments.append(dict(direction=direction, segment_index=index,
                flight_number=segment.get("flight_number"), origin=segment.get("origin"), destination=segment.get("destination"),
                marketing_carrier=segment.get("marketing_carrier"),
                operating_carrier=segment.get("operating_carrier"), operating_airline_name=segment.get("operating_airline_name"),
                policy_operator=operator, operator_evidence=evidence, benefits=assessments))
    unsupported = any(b["policy_status"] == "not_offered" for s in segments for b in s["benefits"])
    has_requirements = bool(traveler.required_benefits or traveler.require_non_basic)
    return dict(
        status="no_requirements" if not has_requirements else "policy_conflict" if unsupported else "needs_confirmation",
        traveler=traveler.model_dump(), traveler_scope="One self-reported traveler; no companion or party-wide eligibility inferred",
        itinerary_complete=complete, quote_provider=quote_provider,
        requirements_verified=False if has_requirements else None,
        non_basic_fare={"required": traveler.require_non_basic,
                        "status": "unknown" if traveler.require_non_basic else "not_requested", "fare_brand": None,
                        "action": "Verify the exact seller fare brand. A cabin, booking letter or fare basis alone does not establish non-Basic." if traveler.require_non_basic else None},
        segments=segments, sources=sources,
        coverage=[dict(program=p.program, tiers=list(p.tiers), operating_airline=p.operator,
                       benefits=[r.benefit for r in p.rules]) for p in POLICIES],
        warnings=["Policy support is not confirmed ticket eligibility. Unlisted programs, tiers, operators and benefits remain unknown.",
                  "Benefits and non-Basic requirements annotate offers; they do not filter or rank prices."] +
                 ([] if complete else ["Only the supplied segments were assessed; a return or complete itinerary has not been verified."]) +
                 ([] if has_requirements else ["No benefit or non-Basic requirement was supplied; none was inferred from loyalty status."]),
    )


def assess_benefits(offer_id=None, traveler=None, operating_airline=None):
    """Assess an offer, or consult a policy for a user-specified operator."""
    if bool(offer_id) == bool(operating_airline):
        raise ValueError("Provide exactly one of offer_id or operating_airline")
    if offer_id:
        from .flight_search import OFFERS
        entry = OFFERS.get(offer_id)
        profile = traveler if traveler is not None else entry["request"].traveler
        if profile is None:
            raise ValueError("Provide traveler with a loyalty program and tier; no personal defaults are stored")
        journeys = {"outbound": entry["outbound"].to_json()}
        if entry["inbound"]:
            journeys["return"] = entry["inbound"].to_json()
        result = assess_journeys(profile, journeys, complete=entry["offer"]["itinerary_complete"], quote_provider=entry["offer"]["provider"])
        return result | {"offer_id": offer_id}
    if traveler is None:
        raise ValueError("Provide traveler with a loyalty program and tier")
    if not isinstance(operating_airline, str) or not re.fullmatch(r"[A-Z0-9]{2}", operating_airline.strip().upper()) or operating_airline.strip().isdigit():
        raise ValueError("operating_airline must be a two-character IATA code")
    result = assess_journeys(traveler, {"policy_lookup": {"segments": [
        {"operating_carrier": operating_airline.strip().upper()}]}}, complete=False, quote_provider=None)
    result["segments"][0]["operator_evidence"] = "user_supplied_for_policy_lookup"
    return result
