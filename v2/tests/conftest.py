"""Fixtures are kept out of git (tests/fixtures_tool.py). Without them, the modules that read them are not collected
and pytest says so loudly at the end; set DRISHTI_REQUIRE_FIXTURES=1 to make a missing or changed fixture an error."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures_tool  # noqa: E402

_STATUS = fixtures_tool.status()
_BROKEN = bool(_STATUS["missing"] or _STATUS["changed"]) or _STATUS["want"] == 0
_NEEDS = sorted(p.name for p in Path(__file__).resolve().parent.glob("test_*.py") if '"fixtures"' in p.read_text(encoding="utf-8"))
collect_ignore = _NEEDS if _BROKEN and _STATUS["missing"] else []


def pytest_sessionstart(session):
    if _BROKEN and os.environ.get("DRISHTI_REQUIRE_FIXTURES") == "1":
        raise SystemExit(f"fixtures do not match tests/fixtures.lock: {len(_STATUS['missing'])} missing, "
                         f"{len(_STATUS['changed'])} changed. Run: python tests/fixtures_tool.py check")


def pytest_terminal_summary(terminalreporter):
    if not _BROKEN:
        return
    tr = terminalreporter
    tr.section("FIXTURES", sep="!", red=True, bold=True)
    if _STATUS["missing"]:
        tr.write_line(f"{len(_STATUS['missing'])} of {_STATUS['want']} fixture files are missing, so {len(_NEEDS)} test modules "
                      f"were NOT run: {', '.join(_NEEDS)}", red=True)
        tr.write_line("Get the fixtures zip from the other machine and run: python tests/fixtures_tool.py unpack drishti-fixtures.zip")
    if _STATUS["changed"]:
        tr.write_line(f"{len(_STATUS['changed'])} fixture files differ from the lock (e.g. {_STATUS['changed'][0]}); "
                      "if you changed them on purpose, run: python tests/fixtures_tool.py lock", red=True)
