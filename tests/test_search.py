import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from cheapdates import core, graph, sweep
from cheapdates.cli import app
from cheapdates.mcp_server import cheapest_dates_tool

DAY = dt.date(2026, 9, 23)


def result(backend="sweep", days=None):
    return core.Result("JFK", "SCL", DAY, DAY, "USD", 7, backend,
                       days if days is not None else [core.DayPrice(DAY, 977, DAY + dt.timedelta(days=7), "LATAM")])


@pytest.fixture
def backends(monkeypatch):
    graph_mock, sweep_mock = Mock(return_value=result("graph")), Mock(return_value=result())
    monkeypatch.setattr(graph, "graph_cheapest_dates", graph_mock)
    monkeypatch.setattr(sweep, "sweep_cheapest_dates", sweep_mock)
    return graph_mock, sweep_mock


@pytest.mark.parametrize("origin,destination,field", [
    ("New York", "Santiago, Chile", "origin"),
    ("JFK", "Santiago, Chile", "destination"),
    ("", "SCL", "origin"), ("J1K", "SCL", "origin"),
])
def test_reject_city_names_before_network(backends, origin, destination, field):
    with pytest.raises(ValueError, match=field + " must be a three-letter IATA"):
        core.cheapest_dates(origin, destination, DAY, DAY)
    assert not any(b.called for b in backends)


@pytest.mark.parametrize("kwargs", [
    {"backend": "typo"}, {"seat": "cargo"}, {"trip_length": 0},
    {"trip_length": -7}, {"trip_length": True}, {"workers": 0},
    {"max_stops": -1}, {"currency": "dollars"}, {"include_airlines": "yes"},
])
def test_invalid_options_do_not_search(backends, kwargs):
    with pytest.raises(ValueError):
        core.cheapest_dates("JFK", "SCL", DAY, DAY, **kwargs)
    assert not any(b.called for b in backends)


def test_normalize_codes_and_cabin(backends):
    core.cheapest_dates(" jfk ", " scl ", DAY, DAY, backend="sweep", seat="premium-economy", currency=" usd ")
    args, kwargs = backends[1].call_args
    assert args[:2] == ("JFK", "SCL")
    assert kwargs["currency"] == "USD"
    assert kwargs["seat"] == "premium_economy"


@pytest.mark.parametrize("backend", ["auto", "graph", "sweep"])
def test_airlines_use_same_offer_search(backends, backend):
    res = core.cheapest_dates("JFK", "SCL", DAY, DAY, include_airlines=True, backend=backend)
    backends[0].assert_not_called()
    assert res.backend == "sweep"
    assert (res.cheapest.price, res.cheapest.airlines) == (977, "LATAM")


def test_graph_does_not_claim_airlines(backends):
    backends[0].return_value = result("graph", [core.DayPrice(DAY, 858)])
    res = core.cheapest_dates("JFK", "SCL", DAY, DAY, backend="graph")
    assert res.cheapest.airlines is None
    assert "include_airlines=true" in res.warnings[0]
    backends[1].assert_not_called()


def test_stop_limit_is_not_silently_ignored(backends):
    core.cheapest_dates("JFK", "SCL", DAY, DAY, backend="graph", max_stops=1)
    backends[0].assert_not_called()
    assert backends[1].call_args.kwargs["max_stops"] == 1


def test_graph_failure_falls_back_with_reason(backends):
    backends[0].side_effect = RuntimeError("browser missing")
    res = core.cheapest_dates("JFK", "SCL", DAY, DAY, backend="graph")
    assert res.backend == "sweep"
    assert "browser missing" in res.warnings[0]


@pytest.mark.parametrize("prices,expected", [
    ([core.DayPrice(DAY, 1)], "ok"),
    ([core.DayPrice(DAY, None)], "no_results"),
    ([core.DayPrice(DAY, None, error="failed")], "error"),
    ([core.DayPrice(DAY, 1), core.DayPrice(DAY, None, error="failed")], "partial"),
])
def test_outcomes_distinguish_no_fares_from_failures(prices, expected):
    data = result(days=prices).to_json()
    assert data["status"] == expected
    assert all("status" in d and "error" in d for d in data["days"])


def test_sweep_chooses_airline_from_cheapest_offer(monkeypatch):
    fetch = Mock(return_value=[SimpleNamespace(price=1100, airlines=["American"]),
                              SimpleNamespace(price=977, airlines=["LATAM"])])
    monkeypatch.setattr(sweep, "_fetch_offers", fetch)
    day, warning = sweep._one("JFK", "SCL", DAY, 7, "USD", "economy", 0)
    assert (day.price, day.airlines, day.ret, warning) == (977, "LATAM", dt.date(2026, 9, 30), None)
    query = fetch.call_args.args[0]
    assert query.flight_data[0].max_stops == 0
    assert query.flight_data[1].date == "2026-09-30"


def test_parser_failure_is_not_retried(monkeypatch):
    fetch, sleep = Mock(side_effect=IndexError("list index out of range")), Mock()
    monkeypatch.setattr(sweep, "_fetch_offers", fetch)
    monkeypatch.setattr(sweep.time, "sleep", sleep)
    day, warning = sweep._one("JFK", "SCL", DAY, 7, "USD", "economy", None)
    assert fetch.call_count == 1
    sleep.assert_not_called()
    assert day.error and "Could not parse" in warning


def test_transient_retries_only_sleep_before_next_attempt(monkeypatch):
    fetch, sleep = Mock(side_effect=sweep._TransientFetchError("HTTP 429")), Mock()
    monkeypatch.setattr(sweep, "_fetch_offers", fetch)
    monkeypatch.setattr(sweep.time, "sleep", sleep)
    day, _ = sweep._one("JFK", "SCL", DAY, None, "USD", "economy", None)
    assert day.error
    assert fetch.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [2, 4]


def test_empty_results_and_missing_airline_are_distinct(monkeypatch):
    monkeypatch.setattr(sweep, "_fetch_offers", Mock(return_value=[]))
    day, warning = sweep._one("JFK", "SCL", DAY, None, "USD", "economy", None)
    assert day.to_json()["status"] == "no_results"
    assert warning is None
    monkeypatch.setattr(sweep, "_fetch_offers", Mock(return_value=[SimpleNamespace(price=977, airlines=None)]))
    day, warning = sweep._one("JFK", "SCL", DAY, None, "USD", "economy", None)
    assert day.price == 977 and day.airlines is None
    assert "without airline names" in warning


@pytest.mark.parametrize("status,exception", [(429, sweep._TransientFetchError), (503, sweep._TransientFetchError), (403, RuntimeError)])
def test_fetch_timeout_and_http_status(monkeypatch, status, exception):
    import primp
    client = Mock()
    client.return_value.get.return_value = SimpleNamespace(status_code=status, text="body")
    monkeypatch.setattr(primp, "Client", client)
    with pytest.raises(exception, match=f"HTTP {status}"):
        sweep._FlightFetcher().fetch_html("test")
    assert client.call_args.kwargs["timeout"] == 20


def test_mcp_reports_total_failure_but_preserves_partial_results(backends):
    backends[1].return_value = result(days=[core.DayPrice(DAY, None, error="parse failure")])
    backends[1].return_value.warnings = ["parse failure"]
    with pytest.raises(RuntimeError, match="every requested date"):
        asyncio.run(cheapest_dates_tool("JFK", "SCL", str(DAY), str(DAY), include_airlines=True))
    backends[1].return_value.days.append(core.DayPrice(DAY + dt.timedelta(days=1), 977, airlines="LATAM"))
    data = asyncio.run(cheapest_dates_tool("JFK", "SCL", str(DAY), str(DAY), include_airlines=True))
    assert data["status"] == "partial"
    assert data["cheapest"]["airlines"] == "LATAM"


def test_cli_invalid_city_and_failed_json(backends):
    runner = CliRunner()
    response = runner.invoke(app, ["New York", "SCL", str(DAY), str(DAY)])
    assert response.exit_code == 2
    assert "IATA" in response.output
    backends[1].return_value = result(days=[core.DayPrice(DAY, None, error="parse failure")])
    response = runner.invoke(app, ["JFK", "SCL", str(DAY), str(DAY), "--include-airlines", "--json"])
    assert response.exit_code == 1
    assert '"status": "error"' in response.stdout
