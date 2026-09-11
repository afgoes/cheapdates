import datetime as dt
import json
from unittest.mock import Mock

import pytest
import playwright.sync_api

from cheapdates.graph import graph_cheapest_dates

DAY = dt.date(2026, 9, 23)


def browser_response(monkeypatch, rows, error=None):
    response = Mock(url="https://www.google.com/GetCalendarGraph")
    response.text.return_value = json.dumps([["wrb.fr", "rpc", json.dumps([None, rows])]])
    page, browser, context = Mock(), Mock(), Mock()
    callback = {}
    page.on.side_effect = lambda event, fn: callback.update({event: fn})
    page.goto.side_effect = error or (lambda *args, **kwargs: callback["response"](response))
    browser.new_context.return_value.new_page.return_value = page
    context.__enter__ = Mock(return_value=Mock(chromium=Mock(launch=Mock(return_value=browser))))
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: context)
    return browser, page


def test_missing_calendar_day_is_a_partial_failure(monkeypatch):
    browser, _ = browser_response(monkeypatch, [["2026-09-23", "2026-09-30", [[None, 858]]]])
    res = graph_cheapest_dates("JFK", "SCL", DAY, DAY + dt.timedelta(days=1), trip_length=7)
    assert res.status == "partial"
    assert res.days[0].price == 858
    assert res.days[1].error
    assert res.days[1].ret == dt.date(2026, 10, 1)
    browser.close.assert_called_once()


def test_wrong_return_date_never_becomes_a_price_match(monkeypatch):
    browser, _ = browser_response(monkeypatch, [["2026-09-23", "2026-09-29", [[None, 858]]]])
    with pytest.raises(ValueError, match="none of the requested"):
        graph_cheapest_dates("JFK", "SCL", DAY, DAY, trip_length=7)
    browser.close.assert_called_once()


def test_browser_is_closed_when_navigation_fails(monkeypatch):
    browser, page = browser_response(monkeypatch, [], error=TimeoutError("navigation timed out"))
    with pytest.raises(TimeoutError):
        graph_cheapest_dates("JFK", "SCL", DAY, DAY)
    browser.close.assert_called_once()
    page.remove_listener.assert_called_once()
