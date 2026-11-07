import pytest

from drishti.cli import main


@pytest.mark.parametrize("argv", [["mcp"], ["worker"], ["jobs"], ["run", "premarket"]])
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
