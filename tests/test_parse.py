import pathlib
import json
import pytest
from cheapdates.graph import parse_calendar

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_oneway():
    rows = parse_calendar((FIX / "calendar_oneway.txt").read_text())
    assert len(rows) == 60
    assert rows[0] == ("2026-10-08", None, 295)
    assert all(r[1] is None for r in rows)


def test_roundtrip():
    rows = parse_calendar((FIX / "calendar_roundtrip.txt").read_text())
    assert len(rows) == 60
    assert rows[0] == ("2026-10-08", "2026-10-15", 596)


def calendar_response(rows):
    return json.dumps([["wrb.fr", "rpc", json.dumps([None, rows])]])


@pytest.mark.parametrize("row", [[], ["bad-date", None], ["2026-10-08", "bad-date"],
                                  ["2026-10-08", None, [[None, "295"]]],
                                  ["2026-10-08", None, [[None, -1]]]])
def test_reject_malformed_calendar_rows(row):
    with pytest.raises(ValueError):
        parse_calendar(calendar_response([row]))


def test_missing_fare_is_a_valid_calendar_row():
    assert parse_calendar(calendar_response([["2026-10-08", None, None]])) == [("2026-10-08", None, None)]
