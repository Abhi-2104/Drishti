import pytest

from drishti.cli import main


@pytest.mark.parametrize("argv", [["worker"]])
def test_stub_commands_report_not_implemented(argv, capsys):
    assert main(argv) == 1
    assert "not implemented" in capsys.readouterr().err


def test_run_requires_job():
    with pytest.raises(SystemExit):
        main(["run"])


def test_init_creates_templates_then_is_a_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path / "h"))
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "created" in out and "profile.toml" in out and "drishti.db" in out
    assert (tmp_path / "h" / "profile.toml").exists()
    assert main(["init"]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_paths_does_not_create_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path / "h"))
    assert main(["paths"]) == 0
    assert "settings" in capsys.readouterr().out
    assert not (tmp_path / "h").exists()


def test_bad_settings_give_exit_2_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    (tmp_path / "settings.toml").write_text("data_dir = [", encoding="utf-8")
    assert main(["paths"]) == 2
    assert "invalid TOML" in capsys.readouterr().err


def test_resolve_command(tmp_path, monkeypatch, capsys):
    from drishti.config import load_settings
    from drishti.store import Store
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    with Store(load_settings().db_path) as st:
        st.upsert_security("INE002A01018", name="Reliance Industries Limited", nse_symbol="RELIANCE", bse_scrip="500325", bse_symbol="RELIANCE",
                           instrument_class="equity", source="t")
        st.upsert_security("INE111A01011", name="First", nse_symbol="CLASH", source="t")
        st.upsert_security("INE222A01010", name="Second", bse_symbol="CLASH", bse_scrip="511111", source="t")
    assert main(["resolve", "reliance"]) == 0
    assert "INE002A01018  Reliance Industries Limited  NSE:RELIANCE" in capsys.readouterr().out
    assert main(["resolve", "500325"]) == 0
    assert main(["resolve", "nosuchthing"]) == 1
    assert "NOT_FOUND" in capsys.readouterr().err
    assert main(["resolve", "clash"]) == 3 and capsys.readouterr().out.count("INE") == 2
