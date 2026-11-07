import ast
from pathlib import Path

import pytest

import drishti
from drishti.config import ConfigError, load_settings, write_templates
from drishti.profile import Profile, load_profile, parse_profile, seed_watchlist
from drishti.store import Store

RIL = {"isin": "INE002A01018", "symbol": "reliance", "bse_scrip": 500325, "name": "Reliance Industries"}


def test_defaults_when_empty():
    p = parse_profile({})
    assert p == Profile() and p.goals.target_xirr_pct == 25.0 and p.watchlist == ()
    assert p.risk.max_position_pct is None and p.sizing.method is None  # unset = no rule, nothing invented


def test_full_profile():
    p = parse_profile({"name": " Abhi ", "goals": {"target_xirr_pct": 25, "horizon_years": 7},
                       "risk": {"max_position_pct": 10, "max_sector_pct": 30.5}, "sizing": {"method": "conviction"},
                       "watchlist": [RIL, {"isin": "ine040a01034", "bse_scrip": "500180"}]})
    assert p.name == "Abhi" and p.goals.horizon_years == 7 and p.risk.max_sector_pct == 30.5
    assert p.sizing.method == "conviction"
    w = p.watchlist[0]
    assert (w.isin, w.symbol, w.bse_scrip) == ("INE002A01018", "RELIANCE", "500325")
    assert p.watchlist[1].symbol is None and p.watchlist[1].isin == "INE040A01034"  # BSE-only entry is fine
    assert p.watch_isins() == ["INE002A01018", "INE040A01034"]


def test_find_by_any_key():
    p = parse_profile({"watchlist": [RIL]})
    assert p.find(isin="ine002a01018").symbol == "RELIANCE"
    assert p.find(symbol=" reliance ").isin == "INE002A01018"
    assert p.find(bse_scrip=500325).isin == "INE002A01018"
    assert p.find(symbol="TCS") is None and p.find() is None


@pytest.mark.parametrize("raw,needle", [
    ({"nme": "x"}, "unknown key"),
    ({"name": 5}, "name must be a string"),
    ({"goals": {"target_xirr": 25}}, "unknown key"),
    ({"goals": {"target_xirr_pct": "25"}}, "must be a number"),
    ({"goals": {"target_xirr_pct": True}}, "must be a number"),
    ({"goals": {"target_xirr_pct": 0}}, "must be >"),
    ({"goals": {"horizon_years": 5.5}}, "whole number"),
    ({"goals": 5}, "must be a table"),
    ({"risk": {"max_position_pct": 150}}, "<= 100"),
    ({"risk": {"max_position": 10}}, "unknown key"),
    ({"sizing": {"method": "yolo"}}, "must be one of"),
    ({"watchlist": {"isin": "x"}}, "array"),
    ({"watchlist": ["INE002A01018"]}, "must be a table"),
    ({"watchlist": [{"symbol": "RELIANCE"}]}, "isin is required"),
    ({"watchlist": [{"isin": "INE002A01019", "symbol": "RELIANCE"}]}, "not a valid Indian ISIN"),
    ({"watchlist": [{"isin": "INE002A01018"}]}, "symbol and/or"),
    ({"watchlist": [{"isin": "INE002A01018", "symbol": "REL IANCE"}]}, "looks wrong"),
    ({"watchlist": [{"isin": "INE002A01018", "bse_scrip": "RIL"}]}, "5-6 digit"),
    ({"watchlist": [{"isin": "INE002A01018", "symbol": "RELIANCE", "sector": "oil"}]}, "unknown key"),
    ({"watchlist": [RIL, RIL]}, "duplicate ISIN"),
])
def test_invalid_profiles_are_typed_errors(raw, needle):
    with pytest.raises(ConfigError, match=needle):
        parse_profile(raw)


def test_symbols_with_punctuation_ok():
    p = parse_profile({"watchlist": [{"isin": "INE101A01026", "symbol": "m&m"}]})
    assert p.watchlist[0].symbol == "M&M"


def test_load_profile_missing_points_to_init(tmp_path):
    s = load_settings({"DRISHTI_HOME": str(tmp_path)})
    with pytest.raises(ConfigError, match="drishti init"):
        load_profile(s)


def test_load_profile_reports_file_in_error(tmp_path):
    f = tmp_path / "profile.toml"
    f.write_text('[[watchlist]]\nisin = "bad"\nsymbol = "X"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_profile(f)
    assert "profile.toml" in str(ei.value) and "watchlist[0]" in str(ei.value)


def test_init_template_is_a_valid_profile(tmp_path):
    s = load_settings({"DRISHTI_HOME": str(tmp_path)})
    write_templates(s)
    p = load_profile(s)
    assert p.goals.target_xirr_pct == 25.0 and p.watchlist == ()


def test_uncommented_template_watchlist_is_valid(tmp_path):
    """The example entry in the template must be a real, valid one (so users can just uncomment it)."""
    from drishti.config import PROFILE_TEMPLATE
    lines, on = [], False
    for l in PROFILE_TEMPLATE.splitlines():
        on = on or l.startswith("# [[watchlist]]")
        lines.append(l[2:] if on and l.startswith("# ") else l)
    import tomllib
    raw = tomllib.loads("\n".join(lines))
    assert parse_profile(raw).watchlist[0].isin == "INE002A01018"


def test_seed_watchlist_into_security_master_merges(tmp_path):
    p = parse_profile({"watchlist": [RIL, {"isin": "INE040A01034", "bse_scrip": "500180"}]})
    with Store() as st:
        st.upsert_security("INE002A01018", name="RELIANCE INDUSTRIES LTD", source="nse_equity_l")
        assert seed_watchlist(st, p) == 2
        r = st.get_security("INE002A01018")
        assert r["nse_symbol"] == "RELIANCE" and r["bse_scrip"] == "500325" and r["source"] == "profile"
        assert st.get_security("INE040A01034")["nse_symbol"] is None
        assert seed_watchlist(st, p) == 2  # idempotent
        assert st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == 2


def _imports_profile(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[-1] == "profile" or any(a.name == "profile" for a in node.names) and (node.level or mod == "drishti"):
                return True
        elif isinstance(node, ast.Import) and any(a.name.endswith(".profile") or a.name == "drishti.profile" for a in node.names):
            return True
    return False


def test_providers_signals_core_store_never_import_the_profile():
    """Profile-neutral by construction: only decisions/tools/cli may read the investor profile."""
    root = Path(drishti.__file__).parent
    checked = 0
    for pkg in ("core", "providers", "signals", "store", "jobs"):
        for f in (root / pkg).rglob("*.py") if (root / pkg).is_dir() else []:
            checked += 1
            assert not _imports_profile(f), f"{f} imports the profile"
    assert checked >= 10  # core + store modules exist, so this guard is not vacuous


def test_import_guard_catches_a_violation(tmp_path):
    for src in ("from drishti.profile import Profile", "from ..profile import Profile", "from .. import profile",
                "import drishti.profile"):
        f = tmp_path / "bad.py"
        f.write_text(src + "\n", encoding="utf-8")
        assert _imports_profile(f), src
    f.write_text("from drishti.core.fetch import get\n", encoding="utf-8")
    assert not _imports_profile(f)
