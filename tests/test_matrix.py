import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cheapdates import matrix, core
from cheapdates.google_selection import SelectedQuery
from cheapdates.search_models import SearchRequest


@pytest.fixture
def req():
    return SearchRequest(origin='LAX',destination='AUS',departure_date='2026-10-14')


@pytest.fixture
def detail():
    return json.loads((Path(__file__).parent/'fixtures/matrix-detail.json').read_text())


def test_captured_matrix_fare_has_exact_cents_class_and_restriction_notes(detail, req):
    quote=matrix.parse_detail(detail,req)
    assert quote['price']=='158.40'
    assert quote['fare_components'][0]['fare_basis']=='KAG5AKBN'
    assert quote['fare_components'][0]['booking_info'][0]['bookingCode']=='N'
    assert quote['journeys'][0].segments[0].flight_number=='UA1496'
    assert quote['journeys'][0].to_json()['nonstop_verified'] is True
    assert 'This ticket is non-refundable.' in quote['ticket_notes']


@pytest.mark.parametrize('mutation',['currency','count','category','tickets','missing'])
def test_unmatched_currency_passengers_and_separate_tickets_fail(detail,req,mutation):
    d=detail['bookingDetails']
    if mutation=='currency':d['displayTotal']='EUR158.40'
    if mutation=='count':d['passengerCount']=2
    if mutation=='category':d['pricings'][0]['ext']['pax']={'children':1}
    if mutation=='tickets':d['tickets']*=2
    if mutation=='missing':d.pop('itinerary')
    with pytest.raises(ValueError):matrix.parse_detail(detail,req)


def test_matrix_request_pins_flights_and_class_for_both_legs(detail,req):
    j=matrix.parse_detail(detail,req)['journeys'][0]
    r=req.model_copy(update={'passengers':req.passengers.model_copy(update={'adults':2})})
    inp=matrix.matrix_inputs(r,[j],'Y')
    assert inp['pax']['adults']==2 and inp['currency']=='USD'
    assert inp['slices'][0]['routeLanguage']=='UA1496'
    assert inp['slices'][0]['commandLine']=='f bc=Y'
    assert inp['checkAvailability'] is True


def test_selection_url_preserves_passengers_filters_and_pins_segments(detail,req):
    from cheapdates.providers import google_query
    j=matrix.parse_detail(detail,req)['journeys'][0]
    req.outbound.max_stops=0
    q=SelectedQuery(req,[j])
    pb=google_query(req).pb()
    pb.ParseFromString(base64.urlsafe_b64decode(q.params()['tfs']))
    assert pb.data[0].max_stops==0 and len(pb.passengers)==1
    raw=pb.data[0].SerializeToString()
    assert b'UA' in raw and b'1496' in raw and b'2026-10-14' in raw
    assert q.booking_url().startswith('https://www.google.com/travel/flights/booking?')


def test_matrix_retry_is_bounded_and_does_not_expose_request_metadata(monkeypatch):
    client=matrix.MatrixClient()
    monkeypatch.setattr(client,'_public_identifier',lambda:'public-test-identifier')
    raw=Mock(side_effect=matrix.MatrixUnavailable('temporarily unavailable'))
    monkeypatch.setattr(client,'_raw',raw)
    monkeypatch.setattr(matrix.time,'sleep',Mock())
    with pytest.raises(matrix.MatrixUnavailable):client.call('/v1/search',{})
    assert raw.call_count==3


def test_matrix_does_not_retry_deterministic_rejection(monkeypatch):
    client=matrix.MatrixClient()
    monkeypatch.setattr(client,'_public_identifier',lambda:'public-test-identifier')
    raw=Mock(return_value={'error':{'code':400,'message':'bad payload with private context'}})
    monkeypatch.setattr(client,'_raw',raw)
    with pytest.raises(ValueError) as exc:client.call('/v1/search',{})
    assert 'private context' not in str(exc.value) and raw.call_count==1


def test_matrix_transport_failure_omits_public_key_and_query(monkeypatch):
    import primp
    client=Mock();client.return_value.post.side_effect=RuntimeError('sensitive req context')
    monkeypatch.setattr(primp,'Client',client)
    with pytest.raises(matrix.MatrixUnavailable) as exc:matrix.MatrixClient()._raw('POST','/v1/search','fake-key',{})
    assert 'sensitive' not in str(exc.value) and 'fake-key' not in str(exc.value)
    assert client.call_args.kwargs['timeout']==55


def test_default_calendar_never_attempts_browser(monkeypatch):
    from cheapdates import graph,sweep
    g=Mock(side_effect=AssertionError('No browser allowed'))
    s=Mock(return_value='sweep result')
    # A Result is needed for its warning list.
    s.return_value=core.Result('LAX','AUS',__import__('datetime').date(2026,10,14),__import__('datetime').date(2026,10,14),'USD',None,'sweep')
    monkeypatch.setattr(graph,'graph_cheapest_dates',g);monkeypatch.setattr(sweep,'sweep_cheapest_dates',s)
    assert core.cheapest_dates('LAX','AUS',s.return_value.start,s.return_value.end).backend=='sweep'
    g.assert_not_called()

def test_booking_class_filter_is_verified_after_fetch(detail,req,monkeypatch):
    j=matrix.parse_detail(detail,req)['journeys'][0]
    client=Mock()
    search={'solutionList':{'solutions':[{'id':'test'}]},'solutionSet':'set','session':'session'}
    client.call.side_effect=[search,detail,search,detail]
    monkeypatch.setattr(matrix,'MatrixClient',lambda:client)
    result=matrix.fetch_fares(req,j,None,['Y'])
    # The fixture's actual class is N; a Y request must not relabel it.
    assert len(result['quotes'])==1 and result['malformed_rows']==1
    assert result['quotes'][0]['requested_booking_code'] is None
