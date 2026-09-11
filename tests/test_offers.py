import json
from pathlib import Path

import pytest

from cheapdates.offers import parse_flight_offers


def page(first, second):
    return '<script class="ds:1">AF_initDataCallback({data: ' + json.dumps([None, None, first, second]) + ', sideChannel: {}});</script>'


def offer(price, airlines=None):
    return [[None, airlines], [[None, price]]]


def test_live_fixture_includes_cheaper_offer_skipped_by_fast_flights():
    # Captured 2026-09-11, JFK-SCL 2026-09-23 returning 09-30. Only
    # group boundaries, prices and airline names are retained; no tokens.
    html = (Path(__file__).parent / "fixtures/flights_roundtrip.html").read_text()
    flights = parse_flight_offers(html)
    best = min(flights, key=lambda f: f.price)
    assert len(flights) == 6
    assert (best.price, best.airlines) == (858, ["American"])


def test_cheapest_can_be_in_either_group_and_optional_details_are_not_required():
    flights = parse_flight_offers(page([[offer(977, ["LATAM"])]], [[offer(858, ["American"])]]))
    assert min(flights, key=lambda f: f.price).airlines == ["American"]


def test_empty_search_is_distinct_from_missing_result_payload():
    assert parse_flight_offers(page([None], [[]])) == []
    with pytest.raises(ValueError, match="no recognizable"):
        parse_flight_offers(page(None, None))
    with pytest.raises(ValueError, match="missing"):
        parse_flight_offers("<html>Access denied</html>")


@pytest.mark.parametrize("row", [[], [None], offer(-1), offer("858"), offer(858, [None])])
def test_bad_offer_is_not_silently_skipped(row):
    with pytest.raises(ValueError):
        parse_flight_offers(page([[row]], [[offer(977, ["LATAM"])]]))


def test_unpriced_offer_and_unavailable_airline():
    flights = parse_flight_offers(page([[offer(None, ["LATAM"]), offer(858, None)]], None))
    assert len(flights) == 1
    assert flights[0].price == 858 and flights[0].airlines is None
