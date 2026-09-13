import copy
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from cheapdates import flight_search, providers, fares
from cheapdates.flight_search import OfferStore, search_flights, select_flight
from cheapdates.search_models import Segment
from cheapdates.search_models import SearchRequest, journey


def flight(origin='MYJ', destination='TPE', date='2026-10-14', number='BR 109', price=300):
    num=number.replace(' ', '') if number else None
    return {'price':str(price), 'journey':journey([Segment(origin=origin, destination=destination,
        departure_local=date+'T09:00', arrival_local=date+'T11:00', duration_minutes=120,
        marketing_carrier=num[:2] if num else None, flight_number=num)])}


def request(**kwargs):
    return SearchRequest.model_validate(dict(origin='MYJ',destination='TPE',departure_date='2026-10-14') | kwargs)


def parsed(row, **kwargs):
    return row | kwargs


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(flight_search, 'fetch_google', Mock(side_effect=AssertionError('Unexpected Google call')))
    monkeypatch.setattr(fares, 'fetch_fares', Mock(side_effect=AssertionError('Unexpected Matrix call')))


def test_airline_filter_excludes_missing_or_mismatched_carriers(monkeypatch):
    rows = [parsed(flight(number='BR 109',price=400)), parsed(flight(number='JL 109',price=200)), parsed(flight(number=None,price=100))]
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=(rows,0)))
    result = search_flights(request(include_airlines=[' br ']))
    assert [o['price'] for o in result['offers']] == ['400']
    assert result['rejected'] == {'include_airlines':1,'unknown_marketing_carrier':1}


def test_roundtrip_selection_does_not_add_two_roundtrip_prices(monkeypatch):
    outbound = parsed(flight(price=300), departure_token='out-token')
    returning = parsed(flight('TPE','MYJ','2026-10-21','BR 110',350), booking_token='booking-token')
    fetch = Mock(side_effect=[([outbound],0),([returning],0)])
    monkeypatch.setattr(flight_search,'fetch_google',fetch)
    initial = search_flights(request(return_date='2026-10-21'))['offers'][0]
    assert initial['inbound'] is None and not initial['itinerary_complete']
    assert initial['airline_filter_scope'] == 'marketing'
    result = select_flight(initial['offer_id'])['offers'][0]
    assert result['price'] == '350' and result['itinerary_complete']
    assert result['outbound']['segments'][0]['origin'] == 'MYJ'
    assert result['inbound']['segments'][0]['origin'] == 'TPE'
    assert fetch.call_args.args[1] == outbound['journey']


def test_incomplete_roundtrip_cannot_compare_fares(monkeypatch):
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=([flight()],0)))
    offer = search_flights(request(return_date='2026-10-21'))['offers'][0]
    with pytest.raises(ValueError, match='Select a return'):
        fares.compare_fares(offer['offer_id'])


def test_return_filters_are_applied_to_actual_return(monkeypatch):
    outbound = parsed(flight(),departure_token='out-token')
    returning = parsed(flight('TPE','MYJ','2026-10-21','BR 110'))
    fetch = Mock(side_effect=[([outbound],0),([returning],0)])
    monkeypatch.setattr(flight_search,'fetch_google',fetch)
    initial = search_flights(request(return_date='2026-10-21',inbound={'earliest_departure_hour':12}))
    result = select_flight(initial['offers'][0]['offer_id'])
    assert not result['offers'] and result['rejected'] == {'time_window':1}
    assert any('does not establish that no matching itinerary exists' in w for w in result['warnings'])


def test_provider_filters_and_passenger_counts_are_encoded():
    req = request(return_date='2026-10-21',alliance='ONEWORLD',passengers={'adults':2},
                  outbound={'max_stops':0,'earliest_departure_hour':8},inbound={'max_stops':1},carry_on_bags=2)
    q = providers.google_query(req)
    assert q.flight_data[0].max_stops == 0 and q.flight_data[1].max_stops == 1
    assert q.flight_data[0].airlines == ['ONEWORLD']
    assert len(q.passengers) == 2


def test_sort_limit_partial_and_duplicate_candidates(monkeypatch):
    rows = [parsed(flight(price=p)) for p in [300,200,100,100]]
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=(rows,1)))
    result = search_flights(request(limit=2))
    assert result['status'] == 'partial'
    assert [o['price'] for o in result['offers']] == ['100','200']


def test_connection_price_premium_is_calculated_before_output_limit(monkeypatch):
    a=flight(destination='NRT',price=100)['journey'].segments[0]
    b=flight(origin='NRT',price=100)['journey'].segments[0]
    b.departure_local=b.departure_local.replace(hour=12)
    b.arrival_local=b.arrival_local.replace(hour=14)
    rows=[{'price':'100.25','journey':journey([a,b])},parsed(flight(price=200.50))]
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=(rows,0)))
    result=search_flights(request(limit=1))
    assert len(result['offers']) == 1
    assert result['comparison']['extra_without_connections'] == '100.25'


def test_offer_references_expire_and_storage_is_bounded():
    now = [0]
    store = OfferStore(ttl=10,capacity=2,clock=lambda:now[0])
    first = store.put({'a':[]})
    store.get(first)['a'].append(1)
    assert store.get(first)['a'] == []
    second = store.put({})
    store.put({})
    with pytest.raises(ValueError,match='expired'):
        store.get(first)
    now[0] = 11
    with pytest.raises(ValueError,match='expired'):
        store.get(second)


def quote(**kwargs):
    return dict(price='300.25',journeys=[flight()['journey']],fare_components=[
        dict(carrier='BR',origin='MYJ',destination='TPE',fare_basis='TEST',price='250')],
        taxes=[],ticket_notes=['This ticket is non-refundable.'],requested_booking_code=None) | kwargs


def stored_oneway(monkeypatch, **options):
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=([flight()],0)))
    return search_flights(request(**options))['offers'][0]['offer_id']


def test_matrix_quote_never_inherits_google_price_or_claims_same_fare(monkeypatch):
    offer_id=stored_oneway(monkeypatch,checked_bags=1)
    fetch=Mock(return_value={'quotes':[quote(),quote(price='400',fare_components=[dict(carrier='BR',origin='MYJ',destination='TPE',fare_basis='FLEX')])], 'warnings':[], 'malformed_rows':0})
    monkeypatch.setattr(fares,'fetch_fares',fetch)
    result=fares.compare_fares(offer_id,['Y'])
    assert fetch.call_args.args[-1] == ['Y']
    assert result['provider']=='ita_matrix' and result['search_quote']['price']=='300'
    assert result['same_fare_as_search_quote_verified'] is False
    assert result['lowest_fare']['price']=='300.25'
    assert result['lowest_fare']['fare_brand'] is None
    assert result['lowest_fare']['basic_economy'] is None
    assert result['fare_brand_verification'] == 'unavailable'
    assert all(v == 'unknown' for v in result['benefits_eligibility'].values())
    assert result['lowest_fare']['conditions']['refunds_allowed'] is False
    assert result['lowest_known_total_with_bags'] is None
    assert result['fares'][1]['extra_vs_lowest_fare']=='99.75'


@pytest.mark.parametrize('mutation',['flight','date','missing','time'])
def test_matrix_fares_must_match_selected_flights_and_constraints(monkeypatch,mutation):
    offer_id=stored_oneway(monkeypatch,outbound={'latest_departure_hour':10})
    q=quote()
    if mutation=='flight': q['journeys']=[flight(number='BR999')['journey']]
    if mutation=='date': q['journeys']=[flight(date='2026-10-15')['journey']]
    if mutation=='missing': q['journeys']=[]
    if mutation=='time': q['journeys'][0].segments[0].departure_local=q['journeys'][0].segments[0].departure_local.replace(hour=12)
    monkeypatch.setattr(fares,'fetch_fares',Mock(return_value={'quotes':[q],'warnings':[],'malformed_rows':0}))
    with pytest.raises(ValueError,match='different flights'): fares.compare_fares(offer_id)


def test_same_day_schedule_changes_are_reported(monkeypatch):
    offer_id=stored_oneway(monkeypatch)
    q=quote();q['journeys'][0].segments[0].departure_local=q['journeys'][0].segments[0].departure_local.replace(minute=10)
    monkeypatch.setattr(fares,'fetch_fares',Mock(return_value={'quotes':[q],'warnings':[],'malformed_rows':0}))
    assert fares.compare_fares(offer_id)['fares'][0]['schedule_changed'] is True


@pytest.mark.parametrize('codes',[['Economy'],['y'],['Y;anything'],list('ABCDE'),'Y'])
def test_booking_class_validation_precedes_network(monkeypatch,codes):
    with pytest.raises(ValueError,match='booking_codes'): fares.compare_fares('none',codes)


def test_unknown_matrix_restrictions_remain_unknown():
    assert fares.conditions([])['refunds_allowed'] is None
    result=fares.conditions(['Changes to this ticket will incur a penalty fee.'])
    assert result['change_penalty_applies'] is True and result['change_fee'] is None


def test_matrix_no_fares_retains_google_quote_without_claiming_unavailability(monkeypatch):
    offer_id = stored_oneway(monkeypatch)
    monkeypatch.setattr(fares, 'fetch_fares', Mock(return_value={'quotes': [], 'warnings': [], 'malformed_rows': 0}))
    result = fares.compare_fares(offer_id)
    assert result['status'] == 'no_results'
    assert result['no_fares_reason'] == 'no_matrix_fares_for_selected_flights'
    assert result['search_quote']['price'] == '300'
    assert result['fares'] == [] and result['lowest_fare'] is None
    assert result['fare_brand_verification'] == 'unavailable'
