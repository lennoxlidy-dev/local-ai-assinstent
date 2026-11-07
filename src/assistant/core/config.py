"""Typed configuration, stored as TOML in ``%APPDATA%\\Assistant\\config.toml``.

Design goals:

* **Never crash on a bad config.** Every value is validated against its
  dataclass field (type, choices, range, pattern). Invalid values fall back to
  the default and produce a warning; a file that is not valid TOML is moved
  aside (``config.toml.broken-<time>``) and defaults are used.
* **Forward compatible.** Unknown keys are kept and written back on save, so a
  newer version's settings survive a downgrade.
* **Atomic saves** (write temp file, then replace) so a crash mid-save can't
  leave a half-written config.
* Dotted access (``store.get("voice.silence_timeout_s")``) for the CLI, the
  settings UI and the onboarding wizard.

The permission policy lives in its own file (``permissions.toml``, milestone 2)
so it can be protected separately from ordinary settings.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, cast, get_args, get_origin, get_type_hints
from collections.abc import Callable

import tomli_w

log = logging.getLogger(__name__)

CONFIG_VERSION = 1


def opt(default: Any, *, doc: str = "", choices: tuple | None = None,
        min: float | None = None, max: float | None = None, pattern: str | None = None,
        value_choices: tuple | None = None) -> Any:
    """Declare a config field with validation metadata."""
    meta: dict[str, Any] = {"doc": doc}
    if value_choices is not None:
        meta["value_choices"] = tuple(value_choices)
    if choices is not None:
        meta["choices"] = tuple(choices)
    if min is not None:
        meta["min"] = min
    if max is not None:
        meta["max"] = max
    if pattern is not None:
        meta["pattern"] = re.compile(pattern)
    if isinstance(default, (list, dict)):
        return field(default_factory=lambda d=default: copy.deepcopy(d), metadata=meta)
    return field(default=default, metadata=meta)


# --------------------------------------------------------------------------- sections

@dataclass
class GeneralConfig:
    assistant_name: str = opt("Jarvis", doc="The assistant's name; also its wake word.")
    user_name: str = opt("", doc="What the assistant calls you.")
    language: str = opt("en", choices=("en", "de"), doc="Conversation language.")
    personality: str = opt("friendly", choices=("friendly", "professional", "witty", "calm"))
    answer_length: str = opt("short", choices=("short", "normal", "detailed"))
    autostart: bool = opt(False, doc="Start at Windows login.")
    startup_delay_s: float = opt(8.0, min=0, max=120,
                                 doc="When launched at login, wait this long before loading models.")
    onboarding_done: bool = opt(False)


@dataclass
class ProfileConfig:
    about: str = opt("", pattern=r"(?s)^.{0,400}$",
                     doc="A few words about you (work, interests) - the assistant keeps them in mind.")
    location: str = opt("", pattern=r"^.{0,80}$", doc="Where you live (city, country) - for local questions.")


@dataclass
class VoiceConfig:
    wake_word_enabled: bool = opt(True)
    wake_sensitivity: float = opt(0.5, min=0.0, max=1.0, doc="Higher = triggers more easily.")
    wake_double_check: bool = opt(True, doc="Confirm the wake word with a quick STT pass.")
    silence_timeout_s: float = opt(1.0, min=0.3, max=5.0, doc="Silence that ends a request.")
    max_utterance_s: float = opt(20.0, min=3.0, max=120.0, doc="Hard cap for one request.")
    follow_up: bool = opt(True, doc="Keep listening briefly after answering, without the name.")
    follow_up_s: float = opt(5.0, min=1.0, max=30.0)
    push_to_talk_mode: str = opt("press", choices=("press", "hold"),
                                 doc="press: start on key press, stop on silence. hold: talk while held.")
    tts_enabled: bool = opt(True, doc="Speak answers aloud.")
    tts_voice_en: str = opt("F1", choices=("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"),
                            doc="Supertonic voice for English (F = female, M = male).")
    tts_voice_de: str = opt("F1", choices=("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"),
                            doc="Supertonic voice for German.")
    tts_speed: float = opt(1.0, min=0.5, max=2.0)
    chime: bool = opt(True, doc="Play a short sound when the wake word is heard.")
    input_device: str = opt("", doc="Microphone name; empty = Windows default.")
    output_device: str = opt("", doc="Speaker name; empty = Windows default.")
    voice_lock: bool = opt(False, doc="Only react to the enrolled speaker.")
    voice_lock_threshold: float = opt(0.55, min=0.2, max=0.95, doc="Higher = stricter voice lock.")
    wake_variants: list[str] = opt([], doc="Extra spellings of the name learned by calibration (e.g. 'Javis').")
    live_transcript: bool = opt(True, doc="Show what you say while you are still speaking.")
    stt_keep_loaded_min: int = opt(10, min=0, max=1440,
                                   doc="Unload speech recognition + speech output (~1 GB RAM) after N idle "
                                       "minutes; 0 = keep them loaded.")


@dataclass
class HotkeyConfig:
    push_to_talk: str = opt("ctrl+alt+space")
    panic: str = opt("ctrl+alt+escape", doc="Cancels everything and releases mouse/keyboard.")


@dataclass
class LLMConfig:
    profile: str = opt("auto", choices=("auto", "smart", "fast"),
                       doc="auto = smart model if it meets the latency targets, else fast.")
    model_smart: str = opt("qwen3.5-9b")
    model_fast: str = opt("qwen3.5-4b")
    idle_unload_min: int = opt(0, min=0, max=1440, doc="Unload the model after N idle minutes; 0 = keep loaded.")
    context_tokens: int = opt(16384, min=4096, max=131072,
                              doc="Context window; the tool definitions alone use ~6k tokens.")
    max_agent_steps: int = opt(8, min=1, max=40)
    thinking: str = opt("auto", choices=("off", "auto", "on"),
                        doc="auto = thinking only for complex multi-step tasks.")
    server_port: int = opt(8771, min=1024, max=65535)
    vision: bool = opt(True, doc="Load the vision projector (look_at_screen).")
    temperature: float = opt(-1.0, min=-1.0, max=2.0, doc="-1 = the model's recommended value.")
    conversation_timeout_min: int = opt(5, min=0, max=240,
                                        doc="Continue the previous conversation if it is younger than this.")
    server_path: str = opt("", doc="Custom llama-server.exe; empty = the installed one.")
    extra_args: list[str] = opt([], doc="Extra llama-server arguments.")


@dataclass
class AppsConfig:
    music_app: str = opt("Spotify", doc="Used for 'play ...' and 'music'.")
    browser: str = opt("", doc="Empty = Windows default browser.")
    aliases: dict[str, str] = opt({}, doc='Extra names for apps, e.g. {"mail" = "Outlook"}.')


@dataclass
class DesktopsConfig:
    enabled: bool = opt(True, doc="Use a dedicated virtual desktop for background apps.")
    name: str = opt("Assistant", doc="Name of the dedicated virtual desktop.")
    default_rule: str = opt("current", choices=("current", "assistant"),
                            doc="Where apps open unless a rule says otherwise.")
    rules: dict[str, str] = opt({"spotify": "assistant"}, value_choices=("current", "assistant"),
                                doc='Per app: "assistant" (dedicated desktop) or "current".')


@dataclass
class FilesConfig:
    use_everything: bool = opt(True, doc="Use Everything (voidtools) for search when it is installed.")
    index_roots: list[str] = opt([], doc="Extra folders for the fallback index (home folders are always included).")


@dataclass
class PrivacyConfig:
    save_conversations: bool = opt(True)
    retention_days: int = opt(90, min=0, max=3650, doc="Delete conversations older than this; 0 = keep forever.")
    save_audio: bool = opt(False, doc="Keep recordings on disk (off = audio never leaves memory).")
    log_transcripts: bool = opt(False, doc="Write what you say/type into the debug log.")


@dataclass
class AppearanceConfig:
    theme: str = opt("dark", choices=("dark", "light", "system"))
    accent: str = opt("#7C5CFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    popup_position: str = opt("top-center", choices=(
        "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"))
    popup_timeout_s: float = opt(6.0, min=1.0, max=60.0, doc="Fade out the popup after N seconds.")
    popup_blur: bool = opt(True, doc="Acrylic (blurred) popup background on Windows 10/11.")


@dataclass
class PerformanceConfig:
    game_mode_auto: bool = opt(True, doc="Enter game mode when a fullscreen game is detected.")
    games: list[str] = opt([], doc="Programs that are always games (exe names, e.g. 'eldenring.exe').")
    not_games: list[str] = opt([], doc="Fullscreen programs that are never games (exe names).")
    eco_qos: bool = opt(True, doc="Run the idle listening loop as an efficiency (EcoQoS) thread.")


@dataclass
class DebugConfig:
    log_level: str = opt("INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    debug_log: bool = opt(True, doc="Write a verbose debug log incl. per-stage timings.")


@dataclass
class Config:
    version: int = CONFIG_VERSION
    general: GeneralConfig = field(default_factory=GeneralConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    apps: AppsConfig = field(default_factory=AppsConfig)
    desktops: DesktopsConfig = field(default_factory=DesktopsConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)


# --------------------------------------------------------------------------- validation

class ConfigError(ValueError):
    pass


_TYPE_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _TYPE_CACHE:
        _TYPE_CACHE[cls] = get_type_hints(cls)
    return _TYPE_CACHE[cls]


def _describe(tp: Any) -> str:
    return getattr(tp, "__name__", str(tp))


def validate_value(value: Any, tp: Any, meta: dict[str, Any] | None = None) -> Any:
    """Return ``value`` (normalized) if it fits ``tp``/``meta``, else raise ConfigError."""
    meta = meta or {}
    origin = get_origin(tp)
    if tp is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"expected true/false, got {value!r}")
    elif tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"expected an integer, got {value!r}")
    elif tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"expected a number, got {value!r}")
        value = float(value)
    elif tp is str:
        if not isinstance(value, str):
            raise ConfigError(f"expected text, got {value!r}")
    elif origin is list:
        (item_tp,) = get_args(tp)
        if not isinstance(value, list):
            raise ConfigError(f"expected a list, got {value!r}")
        value = [validate_value(v, item_tp) for v in value]
    elif origin is dict:
        key_tp, val_tp = get_args(tp)
        if not isinstance(value, dict):
            raise ConfigError(f"expected a table, got {value!r}")
        value = {validate_value(k, key_tp): validate_value(v, val_tp) for k, v in value.items()}
    else:  # pragma: no cover - programming error
        raise ConfigError(f"unsupported config type {_describe(tp)}")

    if "value_choices" in meta and isinstance(value, dict):
        bad = [f"{k}={v}" for k, v in value.items() if v not in meta["value_choices"]]
        if bad:
            raise ConfigError(f"values must be one of {', '.join(meta['value_choices'])}; got {', '.join(bad)}")
    if "choices" in meta and value not in meta["choices"]:
        raise ConfigError(f"must be one of {', '.join(map(str, meta['choices']))}; got {value!r}")
    if "min" in meta and value < meta["min"]:
        raise ConfigError(f"must be >= {meta['min']}; got {value!r}")
    if "max" in meta and value > meta["max"]:
        raise ConfigError(f"must be <= {meta['max']}; got {value!r}")
    if "pattern" in meta and not meta["pattern"].match(value):
        raise ConfigError(f"has an invalid format: {value!r}")
    return value


def coerce_text(text: str, tp: Any) -> Any:
    """Parse a value typed by a human (CLI) into ``tp``."""
    s = text.strip()
    if tp is bool:
        low = s.lower()
        if low in ("1", "true", "yes", "on", "ja", "an"):
            return True
        if low in ("0", "false", "no", "off", "nein", "aus"):
            return False
        raise ConfigError(f"expected on/off, got {text!r}")
    if tp is int:
        try:
            return int(s)
        except ValueError:
            raise ConfigError(f"expected an integer, got {text!r}") from None
    if tp is float:
        try:
            return float(s.replace(",", "."))
        except ValueError:
            raise ConfigError(f"expected a number, got {text!r}") from None
    if tp is str:
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            return s[1:-1]
        return s
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        raise ConfigError(f"expected JSON for {_describe(tp)}, got {text!r}") from None


def _section_from_dict(cls: type, data: dict[str, Any], prefix: str,
                       warnings: list[str], unknown: dict[str, Any]) -> Any:
    obj = cls()
    hints = _hints(cls)
    known = {f.name: f for f in fields(cls)}
    for key, raw in data.items():
        dotted = f"{prefix}{key}"
        f = known.get(key)
        if f is None:
            unknown[dotted] = raw
            warnings.append(f"unknown setting '{dotted}' (kept as-is)")
            continue
        tp = hints[key]
        if is_dataclass(tp):
            if not isinstance(raw, dict):
                warnings.append(f"'{dotted}' must be a section; using defaults")
                continue
            setattr(obj, key, _section_from_dict(cast(type, tp), raw, dotted + ".", warnings, unknown))
            continue
        try:
            setattr(obj, key, validate_value(raw, tp, dict(f.metadata)))
        except ConfigError as e:
            warnings.append(f"'{dotted}' {e}; using default {getattr(obj, key)!r}")
    return obj


def _to_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(obj):
        v = getattr(obj, f.name)
        out[f.name] = _to_dict(v) if is_dataclass(v) else copy.deepcopy(v)
    return out


def _merge_unknown(data: dict[str, Any], unknown: dict[str, Any]) -> None:
    for dotted, value in unknown.items():
        node = data
        *parents, leaf = dotted.split(".")
        ok = True
        for p in parents:
            nxt = node.setdefault(p, {})
            if not isinstance(nxt, dict):
                ok = False
                break
            node = nxt
        if ok and leaf not in node:
            node[leaf] = value


def iter_settings(obj: Any = None, prefix: str = ""):
    """Yield ``(dotted_key, value, type, metadata)`` for every leaf setting."""
    obj = Config() if obj is None else obj
    hints = _hints(type(obj))
    for f in fields(obj):
        if f.name == "version":
            continue
        v = getattr(obj, f.name)
        if is_dataclass(v):
            yield from iter_settings(v, f"{prefix}{f.name}.")
        else:
            yield f"{prefix}{f.name}", v, hints[f.name], dict(f.metadata)


# Migrations: CONFIG_VERSION n -> n+1, operating on the raw dict.
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


# --------------------------------------------------------------------------- store

Listener = Callable[[list[str]], None]


class ConfigStore:
    """Thread-safe owner of the current :class:`Config` and its file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = Config()
        self.warnings: list[str] = []
        self._unknown: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._mtime_ns: int | None = None

    # ---- listeners ----------------------------------------------------------
    def on_change(self, fn: Listener) -> Callable[[], None]:
        """Call ``fn(changed_dotted_keys)`` after every change; returns an unsubscribe function."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _notify(self, changed: list[str]) -> None:
        if not changed:
            return
        for fn in list(self._listeners):
            try:
                fn(changed)
            except Exception:  # a broken listener must not break config handling
                log.exception("config listener failed")

    # ---- load / save --------------------------------------------------------
    def load(self) -> list[str]:
        """(Re)load from disk. Creates the file with defaults if missing. Returns warnings."""
        with self._lock:
            old = _flat(self.data)
            warnings: list[str] = []
            unknown: dict[str, Any] = {}
            raw: dict[str, Any] = {}
            if self.path.exists():
                try:
                    raw = tomllib.loads(self.path.read_text(encoding="utf-8-sig"))
                except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
                    backup = self.path.with_name(f"{self.path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
                    try:
                        os.replace(self.path, backup)
                        warnings.append(f"config file was not valid TOML ({e}); moved to {backup.name}, using defaults")
                    except OSError as e2:
                        warnings.append(f"config file was not valid TOML ({e}) and could not be moved aside ({e2})")
                    raw = {}
            raw = self._migrate(raw, warnings)
            raw.pop("version", None)
            self.data = _section_from_dict(Config, raw, "", warnings, unknown)
            self.data.version = CONFIG_VERSION
            self._unknown = unknown
            self.warnings = warnings
            for w in warnings:
                log.warning("config: %s", w)
            if not self.path.exists():
                self._write()
            self._mtime_ns = self._stat_mtime()
            new = _flat(self.data)
            changed = [k for k in new if old.get(k) != new[k]]
        self._notify(changed)
        return warnings

    def _migrate(self, raw: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        version = raw.get("version", CONFIG_VERSION)
        if not isinstance(version, int):
            version = CONFIG_VERSION
        if version > CONFIG_VERSION:
            warnings.append(f"config was written by a newer version (v{version}); unknown settings are kept")
        while version < CONFIG_VERSION:
            step = MIGRATIONS.get(version)
            if step is None:
                break
            raw = step(raw)
            version += 1
        return raw

    def save(self) -> None:
        with self._lock:
            self._write()
            self._mtime_ns = self._stat_mtime()

    def _write(self) -> None:
        data = _to_dict(self.data)
        _merge_unknown(data, self._unknown)
        text = (
            "# Assistant configuration. Edit while the app runs; changes are picked up automatically.\n"
            "# Invalid values fall back to defaults (see the log for warnings).\n\n"
            + tomli_w.dumps(data)
        )
        atomic_write_text(self.path, text)

    def _stat_mtime(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def reload_if_changed(self) -> bool:
        """Reload if the file changed on disk since we last read/wrote it."""
        mtime = self._stat_mtime()
        if mtime is not None and mtime != self._mtime_ns:
            log.info("config file changed on disk; reloading")
            self.load()
            return True
        return False

    # ---- dotted access ------------------------------------------------------
    def _resolve(self, dotted: str) -> tuple[Any, str, Any, dict[str, Any]]:
        parts = dotted.split(".")
        node: Any = self.data
        for p in parts[:-1]:
            if not is_dataclass(node) or p not in {f.name for f in fields(node)}:
                raise KeyError(dotted)
            node = getattr(node, p)
        leaf = parts[-1]
        if not is_dataclass(node):
            raise KeyError(dotted)
        flds = {f.name: f for f in fields(node)}
        if leaf not in flds or leaf == "version":
            raise KeyError(dotted)
        tp = _hints(type(node))[leaf]
        return node, leaf, tp, dict(flds[leaf].metadata)

    def get(self, dotted: str) -> Any:
        with self._lock:
            node, leaf, _, _ = self._resolve(dotted)
            v = getattr(node, leaf)
            return _to_dict(v) if is_dataclass(v) else copy.deepcopy(v)

    def type_of(self, dotted: str) -> Any:
        return self._resolve(dotted)[2]

    def set(self, dotted: str, value: Any, *, save: bool = True, from_text: bool = False) -> Any:
        """Validate and set one setting. Raises KeyError / ConfigError."""
        with self._lock:
            node, leaf, tp, meta = self._resolve(dotted)
            if is_dataclass(tp):
                raise ConfigError(f"'{dotted}' is a section, not a setting")
            if from_text and isinstance(value, str):
                value = coerce_text(value, tp)
            value = validate_value(value, tp, meta)
            if getattr(node, leaf) == value:
                return value
            setattr(node, leaf, value)
            if save:
                self.save()
        self._notify([dotted])
        return value

    def update(self, values: dict[str, Any]) -> list[str]:
        """Set several settings at once (one save, one notification). All-or-nothing."""
        with self._lock:
            staged = []
            for dotted, value in values.items():
                node, leaf, tp, meta = self._resolve(dotted)
                staged.append((dotted, node, leaf, validate_value(value, tp, meta)))
            changed = []
            for dotted, node, leaf, value in staged:
                if getattr(node, leaf) != value:
                    setattr(node, leaf, value)
                    changed.append(dotted)
            if changed:
                self.save()
        self._notify(changed)
        return changed

    def reset(self, dotted: str) -> Any:
        node, leaf, _, _ = self._resolve(dotted)
        default_obj = type(node)()
        return self.set(dotted, copy.deepcopy(getattr(default_obj, leaf)))

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return _to_dict(self.data)


def _flat(obj: Any) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v, _, _ in iter_settings(obj)}


def atomic_write_text(path: Path, text: str, *, retries: int = 5) -> None:
    """Write ``text`` to ``path`` atomically (temp file + replace).

    On Windows ``os.replace`` fails while another process (an editor, an
    antivirus scanner) briefly holds the target open, so we retry a few times.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))
