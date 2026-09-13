import copy
import datetime as dt
from dataclasses import replace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from cheapdates import benefits, flight_search, fares, providers
from cheapdates.benefits import assess_benefits, assess_journeys
from cheapdates.matrix import matrix_inputs, parse_detail, _operator_disclosure
from cheapdates.search_models import SearchRequest, Segment, journey
from cheapdates.traveler import TravelerProfile

TODAY = dt.date(2026, 9, 13)


def profile(**kwargs):
    return TravelerProfile(program='Delta SkyMiles', tier='Platinum Medallion',
        required_benefits=['seat_selection', 'extra_baggage', 'complimentary_upgrades'],
        require_non_basic=True).model_copy(update=kwargs)


def segment(**kwargs):
    return dict(origin='JFK', destination='SCL', departure_local='2026-09-23T19:50:00',
        arrival_local='2026-09-24T07:25:00', duration_minutes=635,
        marketing_carrier='LA', flight_number='LA533', operating_airline_name='Latam Airlines Group') | kwargs


def assessment(traveler=None, segments=None, **kwargs):
    return assess_journeys(traveler or profile(), {'outbound': {'segments': segments or [segment()]}},
        complete=True, quote_provider='google', today=TODAY, **kwargs)


def rows(result, index=0):
    return {r['benefit']: r for r in result['segments'][index]['benefits']}


def test_platinum_partner_benefits_are_documented_but_ticket_unknown():
    result = assessment()
    assert rows(result)['seat_selection']['policy_status'] == 'documented'
    assert rows(result)['extra_baggage']['ticket_eligibility'] == 'conditional'
    assert rows(result)['complimentary_upgrades']['policy_status'] == 'unknown'
    assert result['requirements_verified'] is False
    assert result['non_basic_fare']['status'] == 'unknown'
    assert result['segments'][0]['operating_carrier'] is None
    assert result['segments'][0]['operator_evidence'] == 'provider_operator_name'
    assert all(s['checked_on'] == '2026-09-13' for s in result['sources'].values())


def test_different_program_and_tier_have_different_policy_results():
    traveler = profile(program='latam_pass', tier='gold')
    result = assessment(traveler, [segment(operating_airline_name='Delta Air Lines')])
    assert rows(result)['seat_selection']['policy_status'] == 'documented'
    assert rows(result)['extra_baggage']['policy_status'] == 'not_offered'
    assert result['status'] == 'policy_conflict'
    traveler.tier = 'black'
    result = assessment(traveler, [segment(operating_airline_name='Delta Air Lines')])
    assert rows(result)['extra_baggage']['policy_status'] == 'documented'
    assert 'Light' in rows(result)['seat_selection']['conditions'][0]


@pytest.mark.parametrize('changes', [{'program': 'mileageplus'}, {'tier': 'unknown_tier'}])
def test_unreviewed_program_or_tier_is_unknown_not_ineligible(changes):
    result = assessment(profile(**changes))
    assert all(r['policy_status'] == 'unknown' for r in rows(result).values())
    assert result['status'] == 'needs_confirmation'
    assert result['coverage']


@pytest.mark.parametrize('name', [None, 'Unknown Airline', 'Delta Airlines for Latam Airlines Group'])
def test_marketing_and_substrings_never_establish_operator(name):
    result = assessment(segments=[segment(operating_airline_name=name)])
    assert result['segments'][0]['policy_operator'] is None
    assert all(r['policy_status'] == 'unknown' for r in rows(result).values())


def test_operator_code_name_conflict_does_not_choose_convenient_policy():
    result = assessment(segments=[segment(operating_carrier='DL')])
    assert result['segments'][0]['operator_evidence'] == 'conflicting_operator_evidence'
    assert all(r['policy_status'] == 'unknown' for r in rows(result).values())


def test_mixed_operators_do_not_inherit_outbound_benefits():
    result = assessment(segments=[segment(), segment(operating_airline_name=None, operating_carrier='VS')])
    assert rows(result)['seat_selection']['policy_status'] == 'documented'
    assert rows(result, 1)['seat_selection']['policy_status'] == 'unknown'
    assert rows(result, 1)['complimentary_upgrades']['policy_status'] == 'not_offered'
    assert result['status'] == 'policy_conflict'


def test_stale_or_future_review_never_confirms_policies(monkeypatch):
    for date in [TODAY - dt.timedelta(days=91), TODAY + dt.timedelta(days=1)]:
        monkeypatch.setattr(benefits, 'POLICIES', tuple(replace(p, checked_on=date) for p in benefits.POLICIES))
        result = assessment()
        assert rows(result)['seat_selection']['policy_status'] == 'unknown'
        assert rows(result)['seat_selection']['reason'] == 'policy_review_required'
        assert all(s['stale'] for s in result['sources'].values())


def test_travel_beyond_review_window_requires_refresh():
    result = assessment(segments=[segment(departure_local='2027-02-10T19:50:00')])
    assert any('Refresh' in c for c in rows(result)['seat_selection']['conditions'])
    assert not result['requirements_verified']


def test_profile_validation_aliases_and_no_account_fields():
    assert profile().program == 'skymiles' and profile().tier == 'platinum'
    assert TravelerProfile(program='LATAM Pass', tier='Black Signature').tier == 'black_signature'
    for data in [dict(program='', tier='gold'), dict(program='123456789', tier='gold'),
                 dict(program='skymiles', tier='gold', account_number='123'),
                 dict(program='skymiles', tier='gold', required_benefits=['imaginary'])]:
        with pytest.raises(ValidationError):
            TravelerProfile.model_validate(data)


def test_policy_lookup_no_offer_or_network_needed():
    result = assess_benefits(traveler=profile(), operating_airline=' la ')
    assert result['quote_provider'] is None and not result['itinerary_complete']
    assert result['segments'][0]['operator_evidence'] == 'user_supplied_for_policy_lookup'
    with pytest.raises(ValueError, match='exactly one'):
        assess_benefits('id', profile(), 'LA')
    with pytest.raises(ValueError, match='Provide traveler'):
        assess_benefits(operating_airline='LA')


def test_profile_survives_return_selection_without_leaking_or_filtering(monkeypatch):
    req = SearchRequest(origin='JFK', destination='SCL', departure_date='2026-09-23', return_date='2026-09-30', traveler=profile())
    outbound = journey([Segment(**segment())])
    inbound = journey([Segment(**segment(origin='SCL', destination='JFK', departure_local='2026-09-30T23:55:00',
        arrival_local='2026-10-01T09:40:00', flight_number='LA532'))])
    fetch = Mock(side_effect=[([{'price':'1132', 'journey':outbound}], 0), ([{'price':'1132', 'journey':inbound}], 0)])
    monkeypatch.setattr(flight_search, 'fetch_google', fetch)
    offer = flight_search.search_flights(req)['offers'][0]
    assert not offer['benefit_review']['requirements_verified']
    assert not assess_benefits(offer['offer_id'])['itinerary_complete']
    selected = flight_search.select_flight(offer['offer_id'])['offers'][0]
    result = assess_benefits(selected['offer_id'])
    assert result['itinerary_complete'] and len(result['segments']) == 2
    assert result['traveler']['tier'] == 'platinum'
    plain = req.model_copy(update={'traveler': None})
    assert providers.google_query(req).params() == providers.google_query(plain).params()
    assert matrix_inputs(req, [outbound, inbound]) == matrix_inputs(plain, [outbound, inbound])
    assert not assess_benefits(selected['offer_id'], profile(program='other'))['sources']
    assert assess_benefits(selected['offer_id'])['traveler']['program'] == 'skymiles'


def test_no_profile_is_not_replaced_with_personal_default(monkeypatch):
    monkeypatch.setattr(flight_search, 'fetch_google', lambda req: ([{'price':'100', 'journey':journey([Segment(**segment())])}], 0))
    offer = flight_search.search_flights(dict(origin='JFK', destination='SCL', departure_date='2026-09-23'))['offers'][0]
    assert 'benefit_review' not in offer
    with pytest.raises(ValueError, match='no personal defaults'):
        assess_benefits(offer['offer_id'])


def test_matrix_operator_evidence_is_preserved_without_iata_inference():
    import json
    from pathlib import Path
    payload = json.loads(Path('tests/fixtures/matrix-detail.json').read_text())
    raw = payload['bookingDetails']['itinerary']['slices'][0]['segments'][0]
    raw.setdefault('ext', {})['operationalDisclosure'] = 'OPERATED BY LATAM AIRLINES GROUP'
    req = SearchRequest(origin='LAX', destination='AUS', departure_date='2026-10-14')
    s = parse_detail(payload, req)['journeys'][0].segments[0]
    assert s.operating_airline_name == 'LATAM AIRLINES GROUP' and s.operating_carrier is None
    for value in [None, [], 'LATAM AIRLINES GROUP', 'OPERATED BY ']:
        assert _operator_disclosure({'ext': {'operationalDisclosure': value}}) is None


def test_matrix_assessment_uses_its_own_operator_evidence(monkeypatch):
    out = journey([Segment(**segment())])
    req = SearchRequest(origin='JFK', destination='SCL', departure_date='2026-09-23', traveler=profile())
    monkeypatch.setattr(flight_search, 'fetch_google', lambda req: ([{'price':'200', 'journey':out}], 0))
    offer = flight_search.search_flights(req)['offers'][0]
    matrix_journey = copy.deepcopy(out)
    matrix_journey.segments[0].operating_airline_name = None
    quote = dict(price='210', journeys=[matrix_journey], fare_components=[], taxes=[], ticket_notes=[], requested_booking_code=None)
    monkeypatch.setattr(fares, 'fetch_fares', lambda *a: dict(quotes=[quote], warnings=[], malformed_rows=0))
    result = fares.compare_fares(offer['offer_id'])['fares'][0]['benefit_assessment']
    assert result['quote_provider'] == 'ita_matrix'
    assert rows(result)['seat_selection']['policy_status'] == 'unknown'
    assert result['non_basic_fare']['status'] == 'unknown'


def test_new_program_is_data_only_and_does_not_require_engine_changes(monkeypatch):
    from cheapdates.benefit_policies import Policy, Rule
    monkeypatch.setattr(benefits, 'POLICIES', (
        Policy('example_program', ('example_tier',), 'XY', 'https://example.org/policy',
               (Rule('priority_boarding', 'Synthetic test policy.'),)),))
    result = assessment(profile(program='example_program', tier='example_tier', required_benefits=['priority_boarding']),
                        [segment(operating_carrier='XY', operating_airline_name=None)])
    assert rows(result)['priority_boarding']['policy_status'] == 'documented'
    assert result['requirements_verified'] is False


def test_status_alone_creates_no_benefit_or_fare_requirements():
    traveler = TravelerProfile(program='skymiles', tier='platinum')
    assert traveler.required_benefits == [] and traveler.require_non_basic is False
    result = assessment(traveler)
    assert result['status'] == 'no_requirements'
    assert result['requirements_verified'] is None
    assert result['segments'][0]['benefits'] == []
    assert result['non_basic_fare']['status'] == 'not_requested'
    assert result['non_basic_fare']['action'] is None


def test_non_basic_review_requirement_does_not_switch_on_google_filter():
    req = SearchRequest(origin='JFK', destination='SCL', departure_date='2026-09-23', traveler=profile())
    assert req.traveler.require_non_basic is True
    assert providers.google_query(req).exclude_basic_economy is False
    assert not providers.google_query(req).pb().HasField('exclude_basic_economy')
