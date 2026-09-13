import copy
import datetime as dt
from pathlib import Path

import pytest

from cheapdates.flight_details import local_time, parse_google_details, parse_serp_details
from cheapdates.search_models import SearchRequest, Segment, journey, money
from cheapdates.flight_search import check_journey


def test_captured_details_use_both_groups_and_preserve_unknown_fields():
    offers, skipped = parse_google_details((Path(__file__).parent / 'fixtures/flight_details.html').read_text())
    assert skipped == 0 and len(offers) == 6
    best = min(offers, key=lambda r: float(r['price']))
    j = best['journey']
    assert best['price'] == '858'
    assert [s.flight_number for s in j.segments] == ['AA1174', 'AA957']
    assert j.layovers[0].minutes == 48
    assert j.duration_minutes == 735
    assert j.segments[-1].arrival_local == dt.datetime(2026, 9, 24, 8)
    assert j.segments[0].operating_carrier is None
    assert j.segments[0].technical_stops is None
    assert j.to_json()['nonstop_verified'] is False


@pytest.mark.parametrize('value,expected', [([8], (8,0)), ([None,31], (0,31)), ([], (0,0))])
def test_sparse_google_times(value, expected):
    result = local_time([2026, 10, 14], value)
    assert (result.hour, result.minute) == expected


@pytest.mark.parametrize('value', [None, '09:00', [25], [1,2,3]])
def test_missing_or_invalid_times_are_not_invented(value):
    with pytest.raises((ValueError, TypeError)):
        local_time([2026, 10, 14], value)


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), '-1', None])
def test_invalid_prices_fail(value):
    with pytest.raises(ValueError):
        money(value)


def test_decimal_prices_do_not_truncate_cents():
    assert money(123.45) == '123.45'


def test_serp_empty_missing_and_malformed_results_are_distinct():
    assert parse_serp_details({'best_flights': []}) == ([], 0)
    for payload in [{}, {'best_flights': None}, {'best_flights': [{'price': 42}]}]:
        with pytest.raises(ValueError):
            parse_serp_details(payload)


def segment(**changes):
    data = dict(origin='MYJ', destination='TPE', departure_local='2026-10-14T18:59:00',
                arrival_local='2026-10-14T20:00:00', duration_minutes=121, marketing_carrier='BR', flight_number='BR109')
    return Segment(**(data | changes))


def test_single_segment_is_not_automatically_nonstop():
    assert journey([segment()]).to_json()['nonstop_verified'] is None
    assert journey([segment(technical_stops=0)]).to_json()['nonstop_verified'] is True
    assert journey([segment(technical_stops=1)]).to_json()['nonstop_verified'] is False
    req = SearchRequest(origin='MYJ', destination='TPE', departure_date='2026-10-14', outbound={'max_stops': 0})
    assert check_journey(journey([segment(technical_stops=1)]), req.outbound, req)[0] == 'max_stops'


def test_hour_bound_includes_the_entire_hour():
    req = SearchRequest(origin='MYJ', destination='TPE', departure_date='2026-10-14', outbound={'latest_departure_hour':18})
    assert check_journey(journey([segment()]), req.outbound, req)[0] is None
    assert check_journey(journey([segment(departure_local='2026-10-14T19:00:00')]), req.outbound, req)[0] == 'time_window'


def test_connections_across_midnight_and_airport_changes():
    first = segment(destination='HND', arrival_local='2026-10-14T23:30:00')
    second = segment(origin='HND', departure_local='2026-10-15T01:00:00', arrival_local='2026-10-15T04:00:00')
    j = journey([first, second])
    assert j.layovers[0].minutes == 90 and j.layovers[0].overnight
    req = SearchRequest(origin='MYJ', destination='TPE', departure_date='2026-10-14', outbound={'avoid_overnight_layovers':True})
    assert check_journey(j, req.outbound, req)[0] == 'overnight_or_unknown_layover'
    second.origin = 'NRT'
    changed = journey([first, second])
    assert changed.layovers[0].minutes is None and changed.layovers[0].airport_change


@pytest.mark.parametrize('fields', [
    {'origin':'New York'}, {'return_date':'2026-10-13'}, {'limit':100},
    {'passengers':{'adults':1,'infants_on_lap':2}}, {'passengers':{'adults':9,'children':1}},
    {'carry_on_bags':2}, {'include_airlines':['American']},
    {'include_airlines':['AA'],'alliance':'ONEWORLD'}, {'outbound':{'max_stops':-1}},
    {'outbound':{'earliest_departure_hour':18,'latest_departure_hour':7}},
    {'exclude_basic_economy':True}, {'provider':'serpapi','checked_bags':1},
    {'inbound':{'max_stops':0}}, {'unexpected':True},
])
def test_invalid_search_inputs_fail_before_transport(fields):
    with pytest.raises(ValueError):
        SearchRequest.model_validate(dict(origin='MYJ',destination='TPE',departure_date='2026-10-14') | fields)
