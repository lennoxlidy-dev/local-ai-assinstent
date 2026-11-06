import tomllib

import pytest

from assistant.core.config import ConfigError, ConfigStore, coerce_text, iter_settings


def test_defaults_written_on_first_load(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    assert store.load() == []
    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["general"]["assistant_name"] == "Jarvis"
    assert data["voice"]["silence_timeout_s"] == 1.0


def test_invalid_values_fall_back_with_warning(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text(
        '[general]\nlanguage = "fr"\nassistant_name = "Nova"\n'
        '[voice]\nsilence_timeout_s = 99\nwake_sensitivity = "high"\n'
        '[appearance]\naccent = "red"\n',
        encoding="utf-8",
    )
    store = ConfigStore(f)
    warnings = store.load()
    assert store.data.general.assistant_name == "Nova"      # valid value kept
    assert store.data.general.language == "en"              # invalid choice -> default
    assert store.data.voice.silence_timeout_s == 1.0        # out of range -> default
    assert store.data.voice.wake_sensitivity == 0.5         # wrong type -> default
    assert store.data.appearance.accent == "#7C5CFF"        # bad pattern -> default
    assert len(warnings) == 4


def test_unknown_keys_are_preserved(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('[general]\nfuture_flag = true\n[future_section]\nx = 1\n', encoding="utf-8")
    store = ConfigStore(f)
    warnings = store.load()
    assert any("future_flag" in w for w in warnings)
    store.set("general.user_name", "Sam")
    data = tomllib.loads(f.read_text(encoding="utf-8"))
    assert data["general"]["future_flag"] is True
    assert data["future_section"]["x"] == 1
    assert data["general"]["user_name"] == "Sam"


def test_broken_toml_is_moved_aside(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text("this is [not toml", encoding="utf-8")
    store = ConfigStore(f)
    warnings = store.load()
    assert "not valid TOML" in warnings[0]
    assert list(tmp_path.glob("config.toml.broken-*"))
    assert store.data.general.assistant_name == "Jarvis"
    assert tomllib.loads(f.read_text(encoding="utf-8"))["version"] == 1


def test_utf8_bom_and_umlauts(tmp_path):
    f = tmp_path / "config.toml"
    f.write_bytes('\ufeff[general]\nuser_name = "Jürgen"\n'.encode("utf-8"))
    store = ConfigStore(f)
    assert store.load() == []
    assert store.data.general.user_name == "Jürgen"


def test_set_get_validation_and_notifications(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    store.load()
    seen = []
    store.on_change(seen.append)
    assert store.set("voice.silence_timeout_s", "1,5", from_text=True) == 1.5   # German decimal comma
    assert store.set("voice.tts_enabled", "aus", from_text=True) is False       # German "off"
    assert store.get("voice.silence_timeout_s") == 1.5
    with pytest.raises(ConfigError):
        store.set("voice.silence_timeout_s", 0.0)
    with pytest.raises(ConfigError):
        store.set("general.autostart", "maybe", from_text=True)
    with pytest.raises(KeyError):
        store.set("voice.nope", 1)
    with pytest.raises(KeyError):
        store.get("version")
    store.set("voice.silence_timeout_s", 1.5)  # unchanged -> no notification
    assert seen == [["voice.silence_timeout_s"], ["voice.tts_enabled"]]
    # persisted
    again = ConfigStore(tmp_path / "config.toml")
    again.load()
    assert again.data.voice.silence_timeout_s == 1.5


def test_update_is_all_or_nothing(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    store.load()
    with pytest.raises(ConfigError):
        store.update({"general.user_name": "Ann", "general.language": "xx"})
    assert store.data.general.user_name == ""
    assert store.update({"general.user_name": "Ann", "general.language": "de"}) == [
        "general.user_name", "general.language"]


def test_reload_if_changed_detects_external_edit(tmp_path):
    f = tmp_path / "config.toml"
    store = ConfigStore(f)
    store.load()
    changes = []
    store.on_change(changes.append)
    assert store.reload_if_changed() is False
    text = f.read_text(encoding="utf-8").replace('assistant_name = "Jarvis"', 'assistant_name = "Nova"')
    import os
    import time
    f.write_text(text, encoding="utf-8")
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 10_000_000))
    assert store.reload_if_changed() is True
    assert store.data.general.assistant_name == "Nova"
    assert changes == [["general.assistant_name"]]


def test_reset_and_iter_settings(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    store.load()
    store.set("llm.max_agent_steps", 12)
    assert store.reset("llm.max_agent_steps") == 8
    keys = [k for k, *_ in iter_settings(store.data)]
    assert "general.assistant_name" in keys and "version" not in keys
    assert len(keys) == len(set(keys)) > 40


def test_coerce_text_quotes_and_json():
    assert coerce_text('"  spaced  "', str) == "  spaced  "
    assert coerce_text('["a", "b"]', list[str]) == ["a", "b"]
    with pytest.raises(ConfigError):
        coerce_text("abc", int)
