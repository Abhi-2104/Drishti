import pytest

from drishti import config
from drishti.config import ConfigError, Settings, load_settings, write_templates


def test_home_env_puts_everything_under_one_dir(tmp_path):
    s = load_settings({"DRISHTI_HOME": str(tmp_path)})
    assert s.config_dir == tmp_path
    assert s.data_dir == tmp_path / "data"
    assert s.settings_path == tmp_path / "settings.toml"
    assert s.profile_path == tmp_path / "profile.toml"
    assert s.db_path == tmp_path / "data" / "drishti.db"
    assert s.sec_user_agent is None


def test_platform_defaults_via_platformdirs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "user_config_dir", lambda *a, **k: (calls.append(("cfg", a, k)), str(tmp_path / "cfg"))[1])
    monkeypatch.setattr(config, "user_data_dir", lambda *a, **k: (calls.append(("data", a, k)), str(tmp_path / "data"))[1])
    s = load_settings({})
    assert s.config_dir == tmp_path / "cfg" and s.data_dir == tmp_path / "data"
    assert all(c[1] == ("drishti",) and c[2] == {"appauthor": False} for c in calls)


def test_blank_home_env_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "user_config_dir", lambda *a, **k: str(tmp_path / "cfg"))
    monkeypatch.setattr(config, "user_data_dir", lambda *a, **k: str(tmp_path / "data"))
    assert load_settings({"DRISHTI_HOME": "  "}).config_dir == tmp_path / "cfg"


def test_settings_file_overrides_data_dir_and_sets_ua(tmp_path):
    (tmp_path / "settings.toml").write_text('data_dir = "' + (tmp_path / "elsewhere").as_posix() + '"\n'
                                            'sec_user_agent = "  Abhi abhi@example.com "\n', encoding="utf-8")
    s = load_settings({"DRISHTI_HOME": str(tmp_path)})
    assert s.data_dir == tmp_path / "elsewhere"
    assert s.db_path == tmp_path / "elsewhere" / "drishti.db"
    assert s.sec_user_agent == "Abhi abhi@example.com"


def test_empty_values_mean_default(tmp_path):
    (tmp_path / "settings.toml").write_text('data_dir = ""\nsec_user_agent = ""\n', encoding="utf-8")
    s = load_settings({"DRISHTI_HOME": str(tmp_path)})
    assert s.data_dir == tmp_path / "data" and s.sec_user_agent is None


def test_bom_is_tolerated(tmp_path):
    (tmp_path / "settings.toml").write_bytes(b"\xef\xbb\xbf" + b'sec_user_agent = "A b@c.d"\n')
    assert load_settings({"DRISHTI_HOME": str(tmp_path)}).sec_user_agent == "A b@c.d"


@pytest.mark.parametrize("body,needle", [
    ("data_dir = [", "invalid TOML"),
    ('data_dri = "x"', "unknown key"),
    ("data_dir = 5", "data_dir must be a string"),
    ("sec_user_agent = 5", "sec_user_agent must be a string"),
])
def test_bad_settings_raise_config_error_naming_the_file(tmp_path, body, needle):
    (tmp_path / "settings.toml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=needle) as ei:
        load_settings({"DRISHTI_HOME": str(tmp_path)})
    assert "settings.toml" in str(ei.value)


def test_templates_created_once_never_overwritten(tmp_path):
    s = load_settings({"DRISHTI_HOME": str(tmp_path / "home")})
    made = write_templates(s)
    assert {p.name for p in made} == {"settings.toml", "profile.toml"}
    s.profile_path.write_text('name = "mine"\n', encoding="utf-8")
    assert write_templates(s) == []
    assert s.profile_path.read_text(encoding="utf-8") == 'name = "mine"\n'
    assert s.data_dir.is_dir()


def test_settings_template_loads_clean(tmp_path):
    write_templates(load_settings({"DRISHTI_HOME": str(tmp_path)}))
    s = load_settings({"DRISHTI_HOME": str(tmp_path)})
    assert s.sec_user_agent is None and s.data_dir == tmp_path / "data"


def test_open_store_creates_dir_and_db(tmp_path):
    s = Settings(config_dir=tmp_path, data_dir=tmp_path / "a" / "b")
    with s.open_store() as st:
        st.set_signal("x", "2026-11-06", 1)
    assert s.db_path.exists()
    with s.open_store() as st:
        assert st.get_signal("x")["value"] == 1
