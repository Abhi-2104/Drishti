from datetime import date, datetime

import pytest

from drishti.core.canary import check_fresh, parse_date
from drishti.core.envelope import DrishtiError, ErrorKind, Result, guard

TODAY = date(2026, 11, 7)  # Saturday after the 6 Nov close


def kind_of(fn):
    with pytest.raises(DrishtiError) as e:
        fn()
    return e.value


@pytest.mark.parametrize("rows", [[], None, ()])
def test_no_rows_is_empty(rows):
    assert kind_of(lambda: check_fresh(rows, "d", 5, "x", today=TODAY)).kind is ErrorKind.EMPTY


def test_frozen_endpoint_is_stale_even_with_rows():
    """The old NSE PIT endpoint: 20 rows, newest 25 May 2022. Row count looks alive; it is not."""
    rows = [{"date": "25-May-2022"} for _ in range(20)]
    e = kind_of(lambda: check_fresh(rows, "date", 120, "corporates-pit TCS", today=TODAY))
    assert e.kind is ErrorKind.STALE
    assert "2022-05-25" in e.detail and "20 rows" in e.detail and "corporates-pit TCS" in e.detail


def test_fresh_rows_return_newest_date_across_mixed_formats():
    rows = [
        {"d": "03-Nov-2026 10:15:00"},
        {"d": "06-NOV-2026 15:40:00"},  # NSE shouts month names
        {"d": "2026-11-05"},
        {"d": ""},                      # unreadable rows are skipped, not fatal
    ]
    assert check_fresh(rows, "d", 5, "x", today=TODAY) == date(2026, 11, 6)


def test_age_boundary():
    rows = [{"d": "02-Nov-2026"}]  # 5 days before TODAY
    assert check_fresh(rows, "d", 5, "x", today=TODAY) == date(2026, 11, 2)
    assert kind_of(lambda: check_fresh(rows, "d", 4, "x", today=TODAY)).kind is ErrorKind.STALE


@pytest.mark.parametrize("rows", [[{"d": "n/a"}], [{"other": "06-Nov-2026"}], [{"d": None}], [object()]])
def test_unreadable_dates_are_parse_fail(rows):
    assert kind_of(lambda: check_fresh(rows, "d", 5, "x", today=TODAY)).kind is ErrorKind.PARSE_FAIL


def test_callable_key_and_date_objects():
    rows = [("a", date(2026, 11, 6)), ("b", datetime(2026, 11, 4, 9, 0))]
    assert check_fresh(rows, lambda r: r[1], 5, "x", today=TODAY) == date(2026, 11, 6)


def test_parse_date_formats():
    assert parse_date("06-11-2026") == date(2026, 11, 6)
    assert parse_date("20261106") == date(2026, 11, 6)
    assert parse_date("2026-11-06T15:40:00+05:30") == date(2026, 11, 6)
    assert parse_date("") is None and parse_date(None) is None and parse_date("garbage") is None


def test_default_today_is_used_when_not_given():
    # A record dated today (IST) is always fresh, whatever the clock says.
    from drishti.core.canary import today_ist
    assert check_fresh([{"d": today_ist()}], "d", 0, "x") == today_ist()


def test_stale_surfaces_as_typed_error_result_through_guard():
    @guard
    def provider():
        check_fresh([{"d": "25-May-2022"}], "d", 120, "pit", today=TODAY)
        return Result(data="should never be returned")

    r = provider()
    assert not r.ok and r.data is None and r.error.kind is ErrorKind.STALE
    assert r.to_dict()["error"]["kind"] == "STALE"
