import pytest

from drishti.cli import main


@pytest.mark.parametrize("argv", [["mcp"], ["worker"], ["jobs"], ["run", "premarket"]])
def test_stub_commands_report_not_implemented(argv, capsys):
    assert main(argv) == 1
    assert "not implemented" in capsys.readouterr().err


def test_run_requires_job():
    with pytest.raises(SystemExit):
        main(["run"])
