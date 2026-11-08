"""Settings and paths. Platform-neutral: pathlib, platformdirs, explicit UTF-8, no OS-specific branches.

Providers and signals may import this module (for the data dir, the SEC contact UA). The investor's
goals/risk/watchlist live in drishti.profile instead, which providers and signals must never import.

Layout (override everything with DRISHTI_HOME=<dir>, e.g. for tests or a second install):
    config dir : settings.toml, profile.toml      (platformdirs user_config_dir, or $DRISHTI_HOME)
    data dir   : drishti.db                       (platformdirs user_data_dir, or $DRISHTI_HOME/data,
                                                   or `data_dir` in settings.toml)
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from platformdirs import user_config_dir, user_data_dir

APP = "drishti"
HOME_ENV = "DRISHTI_HOME"
SETTINGS_FILE = "settings.toml"
PROFILE_FILE = "profile.toml"
SIGNAL_SETTINGS_FILE = "signal-settings.toml"
SIGNAL_SETTINGS_TEMPLATE = Path(__file__).parent / "signals" / "signal-settings.template.toml"
DB_FILE = "drishti.db"


class ConfigError(ValueError):
    """A settings/profile file is missing, unreadable or invalid. The message names the file and field."""

    def __init__(self, message: str, path: Path | None = None):
        super().__init__(f"{path}: {message}" if path else message)
        self.path = path


def read_toml(path: Path) -> dict[str, Any] | None:
    """Parse a TOML file (UTF-8, BOM tolerated: PowerShell likes to write one). None if it does not exist."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read file ({type(e).__name__}: {e})", path) from e
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML: {e}", path) from e


def reject_unknown(table: Mapping[str, Any], allowed: set[str], where: str, path: Path | None) -> None:
    """Typos in a config file must fail, not silently do nothing."""
    extra = sorted(set(table) - allowed)
    if extra:
        raise ConfigError(f"unknown key(s) {extra} in {where}; allowed: {sorted(allowed)}", path)


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    data_dir: Path
    sec_user_agent: str | None = None  # EDGAR requires contact info in the UA; never baked into code

    @property
    def settings_path(self) -> Path:
        return self.config_dir / SETTINGS_FILE

    @property
    def profile_path(self) -> Path:
        return self.config_dir / PROFILE_FILE

    @property
    def signal_settings_path(self) -> Path:
        return self.config_dir / SIGNAL_SETTINGS_FILE

    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_FILE

    def ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def open_store(self):
        """Open (creating if needed) this install's SQLite store."""
        from .store import Store  # local import: config stays importable without the store

        self.data_dir.mkdir(parents=True, exist_ok=True)
        return Store(self.db_path)


_SETTINGS_KEYS = {"data_dir", "sec_user_agent"}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    home = env.get(HOME_ENV, "").strip()
    config_dir = Path(home).expanduser() if home else Path(user_config_dir(APP, appauthor=False))
    default_data = (Path(home).expanduser() / "data") if home else Path(user_data_dir(APP, appauthor=False))

    path = config_dir / SETTINGS_FILE
    raw = read_toml(path) or {}
    reject_unknown(raw, _SETTINGS_KEYS, SETTINGS_FILE, path)

    data_dir = default_data
    if "data_dir" in raw:
        v = raw["data_dir"]
        if not isinstance(v, str):
            raise ConfigError("data_dir must be a string path", path)
        if v.strip():
            data_dir = Path(v).expanduser()
    ua = raw.get("sec_user_agent")
    if ua is not None and not isinstance(ua, str):
        raise ConfigError("sec_user_agent must be a string", path)
    return Settings(config_dir=config_dir, data_dir=data_dir, sec_user_agent=(ua.strip() or None) if ua else None)


SETTINGS_TEMPLATE = """\
# Drishti settings. Everything is optional.

# Where drishti.db lives. Empty = the platform default (see `drishti paths`).
data_dir = ""

# SEC EDGAR requires contact info in the User-Agent, e.g. "Your Name you@example.com".
# Only needed for US filings; leave empty until then.
sec_user_agent = ""
"""

PROFILE_TEMPLATE = """\
# Your investor profile. Read by decisions and tools only; data providers and signals never see it.
# Unknown keys are rejected so typos do not silently do nothing. Unset values mean "no rule".

name = ""

[goals]
target_xirr_pct = 25.0
# horizon_years = 5

[risk]
# max_position_pct = 10     # largest single holding, % of portfolio
# max_sector_pct = 30       # largest sector, % of portfolio

[sizing]
# method = "equal"          # "equal" or "conviction"

# Watchlist: ISIN is required (it is the join key); give the NSE symbol and/or the BSE scrip code.
# [[watchlist]]
# isin = "INE002A01018"
# symbol = "RELIANCE"
# bse_scrip = "500325"
# name = "Reliance Industries"
"""


def write_templates(settings: Settings) -> list[Path]:
    """Create the config files that do not exist yet. Never overwrites. Returns the files created."""
    settings.ensure_dirs()
    made: list[Path] = []
    for path, text in ((settings.settings_path, SETTINGS_TEMPLATE), (settings.profile_path, PROFILE_TEMPLATE),
                       (settings.signal_settings_path, SIGNAL_SETTINGS_TEMPLATE.read_text(encoding="utf-8"))):
        if not path.exists():
            path.write_text(text, encoding="utf-8", newline="\n")
            made.append(path)
    return made
