import copy
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from cheapdates import flight_search, providers, fares
from cheapdates.flight_search import OfferStore, search_flights, select_flight
from cheapdates.flight_details import parse_serp_journey
from cheapdates.search_models import SearchRequest, journey


def flight(origin='MYJ', destination='TPE', date='2026-10-14', number='BR 109', price=300):
    return dict(price=price, total_duration=120, flights=[dict(
        departure_airport={'id':origin,'time':date+' 09:00'}, arrival_airport={'id':destination,'time':date+' 11:00'},
        duration=120, airline='EVA Air', flight_number=number, travel_class='Economy')])


def request(**kwargs):
    return SearchRequest.model_validate(dict(origin='MYJ',destination='TPE',departure_date='2026-10-14') | kwargs)


def parsed(row, **kwargs):
    return {'price':str(row['price']), 'journey':parse_serp_journey(row)} | kwargs


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(providers.SerpApi, 'fetch', Mock(side_effect=AssertionError('Unexpected network call')))
    monkeypatch.setattr(flight_search, 'fetch_google', Mock(side_effect=AssertionError('Unexpected Google call')))
    monkeypatch.setattr(flight_search, 'fetch_serp', Mock(side_effect=AssertionError('Unexpected SerpApi call')))


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
    monkeypatch.setattr(flight_search,'fetch_serp',fetch)
    initial = search_flights(request(provider='serpapi',return_date='2026-10-21'))['offers'][0]
    assert initial['inbound'] is None and not initial['itinerary_complete']
    result = select_flight(initial['offer_id'])['offers'][0]
    assert result['price'] == '350' and result['itinerary_complete']
    assert result['outbound']['segments'][0]['origin'] == 'MYJ'
    assert result['inbound']['segments'][0]['origin'] == 'TPE'
    assert fetch.call_args.args[1] == 'out-token'


def test_keyless_roundtrip_cannot_claim_a_selected_return(monkeypatch):
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=([parsed(flight())],0)))
    offer = search_flights(request(return_date='2026-10-21'))['offers'][0]
    with pytest.raises(ValueError, match='provider=serpapi'):
        select_flight(offer['offer_id'])
    with pytest.raises(ValueError, match='Select a return'):
        fares.compare_fares(offer['offer_id'])


def test_return_filters_are_applied_to_actual_return(monkeypatch):
    outbound = parsed(flight(),departure_token='out-token')
    returning = parsed(flight('TPE','MYJ','2026-10-21','BR 110'))
    fetch = Mock(side_effect=[([outbound],0),([returning],0)])
    monkeypatch.setattr(flight_search,'fetch_serp',fetch)
    initial = search_flights(request(provider='serpapi',return_date='2026-10-21',inbound={'earliest_departure_hour':12}))
    result = select_flight(initial['offers'][0]['offer_id'])
    assert not result['offers'] and result['rejected'] == {'time_window':1}


def test_provider_filters_and_passenger_counts_are_encoded():
    req = request(provider='serpapi',return_date='2026-10-21',alliance='ONEWORLD',passengers={'adults':2},
                  outbound={'max_stops':0,'earliest_departure_hour':8},inbound={'max_stops':1},carry_on_bags=2)
    params = providers.serp_params(req)
    assert params['stops'] == 2 and params['include_airlines'] == 'ONEWORLD'
    assert params['adults'] == 2 and params['bags'] == 2 and params['outbound_times'] == '8,23,0,23'
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
    a=parse_serp_journey(flight(destination='NRT',price=100)).segments[0]
    b=parse_serp_journey(flight(origin='NRT',price=100)).segments[0]
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


def booking_payload():
    return dict(selected_flights=[flight()],booking_options=[
        {'together':{'book_with':'EVA Air','option_title':'Basic','price':300.25,'extensions':['No refunds','Ticket changes for a fee'],'baggage_prices':['1 free carry-on']}},
        {'together':{'book_with':'EVA Air','option_title':'Flex','price':350.50,'extensions':[],'baggage_prices':[]}},
    ])


def stored_oneway(monkeypatch, **options):
    monkeypatch.setattr(flight_search,'fetch_google',Mock(return_value=([parsed(flight())],0)))
    return search_flights(request(**options))['offers'][0]['offer_id']


def test_fare_comparison_pins_flights_and_keeps_unknown_conditions(monkeypatch):
    offer_id = stored_oneway(monkeypatch,carry_on_bags=1)
    fetch = Mock(return_value=booking_payload())
    monkeypatch.setattr(providers.SerpApi,'fetch',fetch)
    result = fares.compare_fares(offer_id)
    selected = json.loads(fetch.call_args.args[0]['selected_flights_json'])
    assert selected['outbound'][0] == {'flight_number':'BR109','departure_id':'MYJ','arrival_id':'TPE','date':'2026-10-14'}
    basic, flex = result['fares']
    assert basic['conditions']['refunds_allowed'] is False
    assert basic['conditions']['changes_allowed'] is True and basic['conditions']['change_fee'] is None
    assert flex['conditions']['refunds_allowed'] is None
    assert basic['total_with_requested_bags'] == '300.25' and flex['total_with_requested_bags'] is None
    assert flex['extra_vs_lowest_fare'] == '50.25'
    assert result['search_quote']['provider'] == 'google' and result['provider'] == 'serpapi'


@pytest.mark.parametrize('mutation', ['wrong_flight','missing_selection','wrong_date'])
def test_fare_details_cannot_attach_to_a_different_itinerary(monkeypatch,mutation):
    offer_id = stored_oneway(monkeypatch)
    data=booking_payload()
    if mutation == 'wrong_flight': data['selected_flights'][0]['flights'][0]['flight_number']='BR 999'
    if mutation == 'missing_selection': data['selected_flights']=[]
    if mutation == 'wrong_date': data['selected_flights'][0]['flights'][0]['departure_airport']['time']='2026-10-15 09:00'
    monkeypatch.setattr(providers.SerpApi,'fetch',Mock(return_value=data))
    with pytest.raises(ValueError): fares.compare_fares(offer_id)


def test_retimed_flights_are_visible_and_constraints_are_rechecked(monkeypatch):
    offer_id = stored_oneway(monkeypatch,outbound={'latest_departure_hour':10})
    data=booking_payload()
    data['selected_flights'][0]['flights'][0]['departure_airport']['time']='2026-10-14 10:00'
    monkeypatch.setattr(providers.SerpApi,'fetch',Mock(return_value=data))
    result=fares.compare_fares(offer_id)
    assert result['schedule_changed'] is True
    data['selected_flights'][0]['flights'][0]['departure_airport']['time']='2026-10-14 12:00'
    with pytest.raises(ValueError,match='time_window'): fares.compare_fares(offer_id)


def test_separate_ticket_prices_are_not_ranked_as_complete_fares(monkeypatch):
    offer_id=stored_oneway(monkeypatch,checked_bags=1)
    data=booking_payload()
    data['booking_options'].append({'separate_tickets':True,'departing':{'price':10}})
    data['booking_options'][0]['together']['baggage_prices']=['1st checked bag: 99-187']
    monkeypatch.setattr(providers.SerpApi,'fetch',Mock(return_value=data))
    result=fares.compare_fares(offer_id)
    assert len(result['fares']) == 2 and result['fares'][0]['price'] == '300.25'
    assert result['lowest_known_total_with_bags'] is None


def test_complete_managed_workflow_keeps_both_legs_and_party_price(monkeypatch):
    outbound=flight(price=300)
    outbound['departure_token']='return-selection-token'
    inbound=flight('TPE','MYJ','2026-10-21','BR 110',price=350)
    booking=booking_payload()
    booking['selected_flights']=[outbound,inbound]
    booking['booking_options'][0]['together']['price']=351.25
    booking['booking_options'][1]['together']['price']=400
    fetch=Mock(side_effect=[{'best_flights':[outbound]},{'other_flights':[inbound]},booking])
    monkeypatch.setattr(providers.SerpApi,'fetch',fetch)
    monkeypatch.setattr(flight_search,'fetch_serp',providers.fetch_serp)
    initial=search_flights(request(provider='serpapi',return_date='2026-10-21',passengers={'adults':2}))
    selected=select_flight(initial['offers'][0]['offer_id'])
    result=fares.compare_fares(selected['offers'][0]['offer_id'])
    assert result['lowest_fare']['price']=='351.25'
    assert result['search_quote']['price']=='350'
    assert result['passengers']['adults']==2
    assert set(result['itinerary'])=={'outbound','return'}
    assert fetch.call_args_list[1].args[0]['departure_token']=='return-selection-token'
    assert 'return' in json.loads(fetch.call_args_list[2].args[0]['selected_flights_json'])
    assert all(c.args[0]['adults']==2 for c in fetch.call_args_list)
