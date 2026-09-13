import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cheapdates import partners, providers


def test_missing_api_key_fails_without_network(monkeypatch):
    import primp
    monkeypatch.delenv('SERPAPI_API_KEY',raising=False)
    client=Mock()
    monkeypatch.setattr(primp,'Client',client)
    with pytest.raises(ValueError,match='SERPAPI_API_KEY'):
        providers.SerpApi().fetch({'currency':'USD'})
    client.assert_not_called()


@pytest.mark.parametrize('failure', ['transport','http','json','upstream','currency'])
def test_provider_errors_never_expose_credentials(monkeypatch,failure):
    import primp
    secret='unit-test-secret-not-a-real-key'
    monkeypatch.setenv('SERPAPI_API_KEY',secret)
    response=SimpleNamespace(status_code=200,json=lambda:{'best_flights':[]})
    client=Mock()
    client.return_value.get.return_value=response
    if failure == 'transport': client.return_value.get.side_effect=RuntimeError('URL?api_key='+secret)
    if failure == 'http': response.status_code=401
    if failure == 'json': response.json=Mock(side_effect=ValueError(secret))
    if failure == 'upstream': response.json=lambda:{'error':secret}
    if failure == 'currency': response.json=lambda:{'search_parameters':{'currency':'EUR'}}
    monkeypatch.setattr(primp,'Client',client)
    with pytest.raises((ValueError,RuntimeError)) as exc:
        providers.SerpApi().fetch({'currency':'USD'})
    assert secret not in str(exc.value)
    assert client.call_args.kwargs['timeout'] == 30


def test_partner_relationship_is_not_earning_eligibility(monkeypatch):
    monkeypatch.setattr(partners,'CHECKED_ON',dt.date.today())
    data=partners.airline_partners('skymiles','la')
    assert data['airlines'][0]['relationship'] == 'documented_partner'
    assert data['airlines'][0]['earning_eligibility'] == 'unknown'
    assert data['airlines'][0]['award_availability'] == 'unknown'
    assert partners.airline_partners('skymiles','ZZ')['airlines'][0]['relationship'] == 'unknown'


def test_stale_partner_snapshot_does_not_assert_current_relationship(monkeypatch):
    monkeypatch.setattr(partners,'CHECKED_ON',dt.date(2000,1,1))
    result=partners.airline_partners('skymiles','LA')
    assert result['status'] == 'refresh_required'
    assert result['airlines'][0]['relationship'] == 'unknown'


def test_provider_query_uses_strings_and_retains_zero_passenger_counts(monkeypatch):
    import primp
    monkeypatch.setenv('SERPAPI_API_KEY','fake-key')
    client=Mock()
    client.return_value.get.return_value=SimpleNamespace(status_code=200,json=lambda:{'best_flights':[]})
    monkeypatch.setattr(primp,'Client',client)
    providers.SerpApi().fetch({'currency':'USD','type':1,'adults':2,'infants_on_lap':0})
    params=client.return_value.get.call_args.kwargs['params']
    assert params['type']=='1' and params['adults']=='2' and params['infants_on_lap']=='0'
