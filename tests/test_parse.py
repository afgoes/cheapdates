import pathlib
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
