import pytest

from drishti.core.isin import is_valid_isin, normalize_isin

REAL = ["INE002A01018", "INE040A01034", "INE009A01021", "INE467B01029", "INE062A01020", "INE238A01034"]


@pytest.mark.parametrize("isin", REAL)
def test_real_isins_pass(isin):
    assert is_valid_isin(isin)


def test_normalises_case_and_space():
    assert normalize_isin(" ine002a01018 ") == "INE002A01018"
    assert is_valid_isin(" ine002a01018 ")
    assert normalize_isin(None) == ""


@pytest.mark.parametrize("bad", [
    "INE002A01019",   # wrong check digit
    "INE002A0101",    # too short
    "INE002A010188",  # too long
    "US0378331005",   # Apple: valid ISIN, but not Indian
    "XYZ002A01018", "", None, 123, "INE002A0101-",
])
def test_bad_isins_fail(bad):
    assert not is_valid_isin(bad)


def test_single_digit_typo_always_caught():
    """Luhn guarantees this for a digit replaced by another digit (letters expand to two digits, so not for them)."""
    base = "INE002A01018"
    for i in (3, 4, 5, 7, 8, 9, 10):
        for d in "0123456789":
            if d != base[i]:
                assert not is_valid_isin(base[:i] + d + base[i + 1:]), (i, d)
