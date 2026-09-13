import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cheapdates import partners, providers


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
