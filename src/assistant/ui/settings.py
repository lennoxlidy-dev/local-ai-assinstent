"""The settings window: ten tabs in QML, created when opened and freed when closed.

QML talks to ``settings`` (SettingsModel): read-only properties with the data of the open tab,
and one entry point ``settings.call(action, args)`` that runs ``SettingsWindow.do_<action>``.
Every change is saved immediately (config.toml / permissions.toml / the database).
"""

from __future__ import annotations

import csv
import functools
import gc
import io
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot

from ..core.config import ConfigError, iter_settings
from ..core.context import AppContext
from ..core.events import Event, Topic
from ..permissions.policy import CAPABILITIES, LOCKED_RISKS, RISK_TITLES, Access, Level, Risk
from .qtmodel import model_class
from .settings_text import strings, text, tool_label
from .theme import Look, is_dark

log = logging.getLogger(__name__)

QML = Path(__file__).with_name("qml") / "Settings.qml"
TABS = ("general", "voice", "model", "permissions", "apps", "routines", "history", "privacy", "appearance", "log")
LLM_RESTART_KEYS = ("llm.profile", "llm.model_smart", "llm.model_fast", "llm.context_tokens", "llm.vision",
                    "llm.server_port", "llm.server_path", "llm.extra_args", "llm.idle_unload_min")


@dataclass
class SettingsDeps:
    """What the window needs from the running app (the tray app passes its parts)."""

    ctx: AppContext
    tools: Any                                            # ToolSystem
    assistant: Any = None
    llm: Any = None
    voice: Callable[[], Any] = lambda: None              # the VoiceStack, if running
    popup: Any = None
    downloads: Callable[[], Any] = lambda: None          # SetupManager (created on demand)
    open_setup: Callable[[], None] = lambda: None
    pause_voice: Callable[[bool], None] = lambda on: None
    suspend_hotkeys: Callable[[bool], None] = lambda on: None
    save_dialog: Callable[[str, str, str], str] | None = None   # (title, file name, filter) -> path
    folder_dialog: Callable[[], str] | None = None
    file_dialog: Callable[[str, str], str] | None = None        # (title, filter) -> path of an existing file
    open_path: Callable[[Path], None] | None = None


SettingsModelBase: Any = model_class("SettingsModelBase", {
    "tr": ("QVariantMap", {}), "lang": (str, "en"), "tab": (str, "general"),
    "cfg": ("QVariantMap", {}), "status": (str, ""), "statusKind": (str, "info"), "statusSeq": (int, 0),
    "caps": ("QVariantList", []), "risks": ("QVariantList", []), "folders": ("QVariantList", []),
    "accessOptions": ("QVariantList", []), "apps": ("QVariantList", []), "aliases": ("QVariantList", []),
    "desktopRules": ("QVariantList", []), "routines": ("QVariantList", []), "toolOptions": ("QVariantList", []),
    "facts": ("QVariantList", []), "conversations": ("QVariantList", []), "conversation": ("QVariantMap", {}),
    "actions": ("QVariantList", []), "devicesIn": ("QVariantList", []), "devicesOut": ("QVariantList", []),
    "level": (float, 0.0), "calib": ("QVariantMap", {}), "enroll": ("QVariantMap", {}),
    "voice": ("QVariantMap", {}), "models": ("QVariantList", []), "llm": ("QVariantMap", {}),
    "download": ("QVariantMap", {}), "recordings": ("QVariantMap", {}),
})


class SettingsModel(SettingsModelBase):
    """``settings`` in QML."""

    def __init__(self, handler: Callable[[str, dict[str, Any]], Any]):
        super().__init__()
        self._handler = handler

    @Slot(str, "QVariant", result="QVariant")
    def call(self, action: str, args: Any = None) -> Any:
        return self._handler(action, args if isinstance(args, dict) else {})


def _when(ts: float, lang: str) -> str:
    t = time.localtime(ts)
    if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M", t)
    return time.strftime("%d.%m.%Y %H:%M" if lang == "de" else "%Y-%m-%d %H:%M", t)


def _json_value(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _json_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    return v


@functools.lru_cache(maxsize=64)
def _sound_seconds(path: str, size: int, mtime_ns: int) -> float:
    """(cached per file version: the editor asks again whenever its rows are rebuilt)"""
    import soundfile as sf

    return round(float(sf.info(path).duration), 1)


class SettingsWindow(QObject):
    closed = Signal()
    _bus_event = Signal(object)
    _voice_event = Signal(str, object)

    def __init__(self, deps: SettingsDeps):
        super().__init__()
        self.d = deps
        self.ctx = deps.ctx
        self.model = SettingsModel(self._dispatch)
        self.engine: Any = None
        self.window: Any = None
        self.look: Any = None
        self._sv: Any = None              # SetupVoice for calibration / enrollment / previews / mic meter
        self._voice_paused = False
        self._history_query = ""
        self._open_conv: int | None = None
        self._log_filter = {"search": "", "kind": "all"}
        self._types: dict[str, Any] = {}
        self._bus_event.connect(self._on_bus, Qt.ConnectionType.QueuedConnection)
        self._voice_event.connect(self._on_voice, Qt.ConnectionType.QueuedConnection)
        self._log_timer = QTimer(self, singleShot=True, interval=700)
        self._log_timer.timeout.connect(self._refresh_actions)
        self._unsubs = [self.ctx.bus.subscribe(t, self._bus_event.emit) for t in (
            Topic.CONFIG_CHANGED, "llm.state", "setup.progress", "setup.job_done", "setup.finished",
            "setup.failed", "tool.done", "tool.refused", "permissions.changed", "assistant.reply")]
        self._refresh_strings()
        self._refresh_cfg()

    # ------------------------------------------------------------------ window
    def show(self, tab: str = "") -> bool:
        if self.window is not None:
            if tab:
                self.do_tab(tab)
            self.window.show()
            self.window.raise_()
            self.window.requestActivate()
            return True
        from PySide6.QtQml import QQmlApplicationEngine

        engine = QQmlApplicationEngine()
        self.look = Look(self.ctx)
        self.look.setParent(engine)          # freed together with the engine, after the QML items
        engine.rootContext().setContextProperty("look", self.look)
        engine.rootContext().setContextProperty("settings", self.model)
        self.do_tab(tab or self.model.get("tab"))
        engine.load(QUrl.fromLocalFile(str(QML)))
        roots = engine.rootObjects()
        if not roots:
            log.error("settings window QML failed to load")
            engine.deleteLater()
            return False
        self.engine, self.window = engine, roots[0]
        try:
            from ..win.dwm import set_dark_titlebar

            set_dark_titlebar(int(self.window.winId()), is_dark(self.ctx.config.data.appearance.theme))
        except Exception as e:
            log.debug("title bar styling: %s", e)
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()
        return True

    def close(self) -> None:
        """Free everything: the QML engine, the tab data, borrowed audio."""
        if self._sv is not None:
            self._sv.close()
            self._sv = None
        if self._voice_paused:
            self.d.pause_voice(False)
            self._voice_paused = False
        self.d.suspend_hotkeys(False)
        for u in self._unsubs:
            u()
        self._unsubs = []
        if self.window is not None:
            self.window.hide()
            self.window.deleteLater()
            self.window = None
        if self.engine is not None:
            self.engine.deleteLater()
            self.engine = None
        if self.look is not None:
            self.look.close()                # stops listening for changes; deleted with the engine
            self.look = None
        self.model.set(conversations=[], conversation={}, actions=[], facts=[], routines=[], apps=[])
        gc.collect()
        self.closed.emit()

    # ------------------------------------------------------------------ plumbing
    def _dispatch(self, action: str, args: dict[str, Any]) -> Any:
        fn = getattr(self, "do_" + action, None)
        if fn is None:
            log.warning("settings: unknown action %s", action)
            return {"ok": False, "error": f"unknown action {action}"}
        try:
            res = fn(**args)
        except (ConfigError, ValueError, KeyError, OSError) as e:
            msg = str(e).strip("'\"")
            self._status(msg, "error")
            if isinstance(e, ConfigError):
                self._refresh_cfg()                  # put the control back to the saved value
            return {"ok": False, "error": msg}
        except Exception as e:
            log.exception("settings action %s failed", action)
            self._status(f"{type(e).__name__}: {e}", "error")
            return {"ok": False, "error": str(e)}
        return res if res is not None else {"ok": True}

    def t(self, key: str, **kw: Any) -> str:
        return text(key, self._lang(), **kw)

    def _lang(self) -> str:
        return "de" if self.ctx.config.data.general.language == "de" else "en"

    def _status(self, msg: str, kind: str = "info") -> None:
        self.model.set(status=msg, statusKind=kind, statusSeq=self.model.get("statusSeq") + 1)

    def _saved(self) -> None:
        self._status(self.t("saved"), "ok")

    # ------------------------------------------------------------------ refreshers
    def _refresh_strings(self) -> None:
        lang = self._lang()
        self.model.set(tr=strings(lang), lang=lang, accessOptions=[
            {"value": a.value, "label": self.t("acc_" + a.value)} for a in (Access.FULL, Access.READ, Access.ASK,
                                                                             Access.NONE)])

    def _refresh_cfg(self) -> None:
        cfg: dict[str, Any] = {}
        for key, value, tp, _meta in iter_settings(self.ctx.config.data):
            cfg[key] = _json_value(value)
            self._types[key] = tp
        self.model.set(cfg=cfg)

    def _refresh_perms(self) -> None:
        pol = self.d.tools.policy.policy
        de = self._lang() == "de"
        from ..onboarding.questions import CAP_DE, RISK_DE

        self.model.set(
            caps=[{"id": c.id, "title": CAP_DE.get(c.id, c.title) if de else c.title, "hint": "" if de else c.description,
                   "level": pol.level(c.id).value,
                   "options": [{"value": lv.value, "label": self.t(lv.value)} for lv in c.choices]}
                  for c in CAPABILITIES],
            risks=[{"id": r.value, "title": RISK_DE[r] if de else RISK_TITLES[r], "on": pol.needs_confirm(r),
                    "locked": r in LOCKED_RISKS} for r in Risk],
            folders=[{"path": f.path, "access": f.access.value, "note": f.note} for f in pol.folders])

    def _app_names(self) -> list[str]:
        try:
            return sorted({a.name for a in self.d.tools.services["apps"].apps()}, key=str.casefold)
        except Exception as e:
            log.debug("app list unavailable: %s", e)
            return []

    def _refresh_apps(self) -> None:
        c = self.ctx.config.data
        self.model.set(apps=self._app_names(),
                       aliases=[{"alias": k, "app": v} for k, v in sorted(c.apps.aliases.items())],
                       desktopRules=[{"app": k, "rule": v} for k, v in sorted(c.desktops.rules.items())])

    def _refresh_routines(self) -> None:
        from ..tools.routines import NOT_IN_ROUTINES

        store = self.d.tools.services["routines"]
        first = ["say", "play_sound", "wait", "open_app", "volume", "media", "play_music", "desktop", "driver_updates"]
        names = first + sorted(n for n in self.d.tools.registry.names() if n not in NOT_IN_ROUTINES and n not in first)
        lang = self._lang()
        self.model.set(routines=[{"name": r.name, "description": r.description,
                                  "steps": [{"tool": s["tool"], "args": json.dumps(s.get("args", {}), ensure_ascii=False)}
                                            for s in r.steps], "summary": self._steps_summary(r.steps)}
                                 for r in store.all()],
                       toolOptions=[{"value": n, "label": tool_label(n, lang)} for n in names])

    @staticmethod
    def _steps_summary(steps: list[dict[str, Any]]) -> str:
        parts = []
        for s in steps[:6]:
            a = s.get("args", {})
            if "path" in a:
                detail = str(a["path"]).replace("\\", "/").rsplit("/", 1)[-1]
            else:
                detail = next((str(a[k]) for k in ("name", "level", "action", "query", "text", "seconds") if k in a), "")
            parts.append(f"{s['tool']} {detail}".strip())
        return " · ".join(parts) + (" …" if len(steps) > 6 else "")

    def _refresh_facts(self) -> None:
        lang = self._lang()
        self.model.set(facts=[{"id": f.id, "text": f.text, "when": _when(f.updated, lang)}
                              for f in self.d.tools.services["memory"].all()])

    def _conversations(self) -> Any:
        if self.d.assistant is not None:
            return self.d.assistant.conversations
        from ..storage.conversations import ConversationStore

        return ConversationStore(self.d.tools.db)

    def _refresh_history(self) -> None:
        store, lang = self._conversations(), self._lang()
        q = self._history_query.strip()
        if q:
            rows = [{"id": c.id, "title": c.title or "…", "when": _when(c.updated, lang), "snippet": snip}
                    for c, snip in store.search(q, 100)]
        else:
            rows = [{"id": c.id, "title": c.title or "…", "when": _when(c.updated, lang), "snippet": ""}
                    for c in store.recent(200)]
        self.model.set(conversations=rows)
        if self._open_conv is not None:
            self.do_openConversation(self._open_conv)

    def _refresh_actions(self) -> None:
        lang = self._lang()
        f = self._log_filter
        rows = self.d.tools.action_log.recent(500, search=f["search"] or None)
        kind = f["kind"]
        if kind == "asked":
            rows = [r for r in rows if r.decision in ("approved", "denied")]
        elif kind == "denied":
            rows = [r for r in rows if r.decision == "denied"]
        elif kind == "blocked":
            rows = [r for r in rows if r.decision == "blocked"]
        elif kind == "errors":
            rows = [r for r in rows if r.outcome == "error" or r.decision == "invalid"]
        self.model.set(actions=[{
            "id": r.id, "when": _when(r.ts, lang), "tool": r.tool, "summary": r.summary or r.tool,
            "decision": r.decision, "decisionText": self.t("d_" + r.decision) if f"d_{r.decision}" in strings(lang) else r.decision,
            "outcome": r.outcome or "", "result": (r.result or "")[:400], "source": r.source or "",
            "risks": ", ".join(r.risks), "ms": round(r.duration_ms) if r.duration_ms else None,
            "args": json.dumps(r.args, ensure_ascii=False)[:600]} for r in rows[:300]])

    def _refresh_voice(self) -> None:
        from ..audio.sources import list_devices
        from ..voice.models import installed

        md = self.ctx.paths.models_dir
        self.model.set(
            devicesIn=[{"value": "", "label": self.t("default_device")}] + [{"value": d, "label": d}
                                                                            for d in list_devices("input")],
            devicesOut=[{"value": "", "label": self.t("default_device")}] + [{"value": d, "label": d}
                                                                             for d in list_devices("output")],
            voice={"installed": all(installed(m, md) for m in ("kws", "vad", "stt", "tts")),
                   "lockInstalled": installed("speaker", md),
                   "enrolled": (self.ctx.paths.config_dir / "voiceprint.npy").exists(),
                   "running": bool(self.d.voice() is not None and self.d.voice().pipeline.running)})

    def _refresh_model(self) -> None:
        from ..llm.models import CATALOG, is_installed
        from ..llm.service import bench_result

        md = self.ctx.paths.models_dir
        self.model.set(models=[{"value": s.id, "label": f"{s.title}  ·  {s.download_bytes / 2**30:.1f} GB  ·  "
                                + (self.t("installed") if is_installed(s, md, vision=False) else self.t("not_installed")),
                                "installed": is_installed(s, md, vision=False)}
                               for s in CATALOG.values() if not s.hidden])
        llm = self.d.llm
        bench = bench_result(self.ctx)
        info: dict[str, Any] = {"ready": bool(llm is not None and llm.ready),
                                "status": llm.status() if llm is not None else "-",
                                "model": llm.spec.title if llm is not None and llm.spec is not None else "",
                                "reason": (llm.reason or "") if llm is not None else ""}
        if bench:
            info["bench"] = (f"{bench.get('recommended', '?')} ({bench.get('date', '?')}): {bench.get('reason', '')}")
        self.model.set(llm=info)
        self._refresh_download()

    def _refresh_download(self) -> None:
        dl = self.d.downloads()
        if dl is None:
            self.model.set(download={})
            return
        from ..onboarding.downloads import describe

        st = dl.status
        self.model.set(download={"active": bool(st.running), "text": describe(st, self._lang()), "pct": st.percent})

    def _refresh_privacy(self) -> None:
        self._refresh_facts()
        self._refresh_recordings()

    def _recordings_dir(self) -> Path:
        return self.ctx.paths.local_dir / "recordings"

    def _refresh_recordings(self) -> None:
        from ..onboarding.downloads import human_size

        files = list(self._recordings_dir().glob("*.wav")) if self._recordings_dir().exists() else []
        size = sum(f.stat().st_size for f in files)
        self.model.set(recordings={"count": len(files), "text": self.t("recordings", n=len(files), size=human_size(size))})

    # ------------------------------------------------------------------ events
    @Slot(object)
    def _on_bus(self, e: Event) -> None:
        tab = self.model.get("tab")
        if e.topic == Topic.CONFIG_CHANGED:
            keys = e.data.get("keys", [])
            self._refresh_cfg()
            if "general.language" in keys:
                self._refresh_strings()
                self.do_tab(tab)
        elif e.topic in ("llm.state",) and tab == "model":
            self._refresh_model()
        elif e.topic.startswith("setup.") and tab in ("model", "voice"):
            self._refresh_download()
            if e.topic in ("setup.job_done", "setup.finished"):
                if tab == "model":
                    self._refresh_model()
                else:
                    self._refresh_voice()
        elif e.topic in ("tool.done", "tool.refused") and tab == "log":
            self._log_timer.start()
        elif e.topic == "permissions.changed" and tab == "permissions":
            self._refresh_perms()
        elif e.topic == "assistant.reply" and tab == "history":
            self._refresh_history()

    # ------------------------------------------------------------------ actions: general
    def do_tab(self, name: str) -> dict[str, Any]:
        if name not in TABS:
            raise ValueError(f"unknown tab {name}")
        prev = self.model.get("tab")
        if prev == "voice" and name != "voice" and self._sv is not None:
            self._sv.stop_monitor()
        refresh = {"permissions": self._refresh_perms, "apps": self._refresh_apps, "routines": self._refresh_routines,
                   "history": self._refresh_history, "privacy": self._refresh_privacy,
                   "log": self._refresh_actions, "voice": self._refresh_voice, "model": self._refresh_model}.get(name)
        if refresh is not None:
            refresh()                         # data first: the page reads it when it is created
        self.model.set(tab=name)
        if name == "voice" and self.window is not None:
            self.do_monitor(True)
        return {"ok": True}

    def do_set(self, key: str, value: Any) -> dict[str, Any]:
        tp = self._types.get(key)
        if tp is int and isinstance(value, float) and value.is_integer():
            value = int(value)
        if tp is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        old = self.ctx.config.get(key)
        if old == value:
            return {"ok": True}
        if key == "general.assistant_name" and str(value).strip().casefold() != str(old).casefold():
            # a calibration for the old name is useless for the new one
            self.ctx.config.update({key: value, "voice.wake_variants": [], "voice.wake_sensitivity": 0.5})
        else:
            self.ctx.config.set(key, value)
        if key == "privacy.retention_days" and value and self.d.assistant is not None:
            n = self.d.assistant.conversations.purge(int(value))
            if n:
                log.info("retention lowered: deleted %d conversations", n)
        if key == "voice.input_device" and self.model.get("tab") == "voice":
            self.do_monitor(True)
        if key in LLM_RESTART_KEYS:
            self._status(self.t("apply_restart"), "info")
        else:
            self._saved()
        return {"ok": True}

    def do_reset(self, key: str) -> dict[str, Any]:
        self.ctx.config.reset(key)
        self._saved()
        return {"ok": True}

    def do_open(self, what: str) -> dict[str, Any]:
        p = self.ctx.paths
        target = {"config": p.config_file, "data": p.config_dir, "logs": p.logs_dir, "models": p.models_dir,
                  "recordings": self._recordings_dir()}[what]
        if what == "recordings":
            target.mkdir(parents=True, exist_ok=True)
        opener = self.d.open_path
        if opener is None:
            from ..win.shell import open_path

            open_path(target, edit=what == "config")
        else:
            opener(target)
        return {"ok": True}

    def do_runSetup(self) -> dict[str, Any]:  # noqa: N802
        QTimer.singleShot(0, self.d.open_setup)
        if self.window is not None:
            self.window.close()
        return {"ok": True}

    def do_suspendHotkeys(self, on: bool) -> dict[str, Any]:  # noqa: N802
        self.d.suspend_hotkeys(bool(on))
        return {"ok": True}

    # ------------------------------------------------------------------ actions: permissions
    def _policy_changed(self) -> None:
        self.d.tools.reload_policy()             # folder rules are compiled into the path checker
        self._refresh_perms()
        self._saved()

    def do_setLevel(self, cap: str, level: str) -> dict[str, Any]:  # noqa: N802
        self.d.tools.policy.set_level(cap, Level(level))
        self._policy_changed()
        return {"ok": True}

    def do_setRisk(self, risk: str, on: bool) -> dict[str, Any]:  # noqa: N802
        self.d.tools.policy.set_always_confirm(Risk(risk), bool(on))
        self._policy_changed()
        return {"ok": True}

    def do_setFolder(self, path: str, access: str, note: str = "") -> dict[str, Any]:  # noqa: N802
        path = path.strip()
        if not path:
            raise ValueError("empty folder")
        self.d.tools.policy.set_folder(path, Access(access), note)
        self._policy_changed()
        return {"ok": True}

    def do_removeFolder(self, path: str) -> dict[str, Any]:  # noqa: N802
        self.d.tools.policy.set_folder(path, None)
        self._policy_changed()
        return {"ok": True}

    def do_addFolder(self) -> dict[str, Any]:  # noqa: N802
        path = self._pick_folder()
        if path:
            self.do_setFolder(path, Access.FULL.value)
        return {"ok": True, "path": path}

    def _pick_folder(self) -> str:
        if self.d.folder_dialog is not None:
            return self.d.folder_dialog()
        from PySide6.QtWidgets import QFileDialog

        p = QFileDialog.getExistingDirectory(None, "")
        return p.replace("/", "\\") if p and re.match(r"^[A-Za-z]:/", p) else p

    # ------------------------------------------------------------------ actions: apps
    def do_setAlias(self, alias: str, app: str, old: str = "") -> dict[str, Any]:  # noqa: N802
        alias, app = alias.strip().casefold(), app.strip()
        if not alias or not app:
            raise ValueError("alias and app are needed")
        aliases = dict(self.ctx.config.data.apps.aliases)
        if old and old != alias:
            aliases.pop(old, None)
        aliases[alias] = app
        self.ctx.config.set("apps.aliases", aliases)
        self._refresh_apps()
        self._saved()
        return {"ok": True}

    def do_removeAlias(self, alias: str) -> dict[str, Any]:  # noqa: N802
        aliases = dict(self.ctx.config.data.apps.aliases)
        aliases.pop(alias, None)
        self.ctx.config.set("apps.aliases", aliases)
        self._refresh_apps()
        self._saved()
        return {"ok": True}

    def do_setDesktopRule(self, app: str, rule: str) -> dict[str, Any]:  # noqa: N802
        app = app.strip().casefold()
        if not app:
            raise ValueError("app name needed")
        rules = dict(self.ctx.config.data.desktops.rules)
        rules[app] = rule
        self.ctx.config.set("desktops.rules", rules)
        self._refresh_apps()
        self._saved()
        return {"ok": True}

    def do_removeDesktopRule(self, app: str) -> dict[str, Any]:  # noqa: N802
        rules = dict(self.ctx.config.data.desktops.rules)
        rules.pop(app, None)
        self.ctx.config.set("desktops.rules", rules)
        self._refresh_apps()
        self._saved()
        return {"ok": True}

    def do_addIndexRoot(self) -> dict[str, Any]:  # noqa: N802
        path = self._pick_folder()
        if path:
            roots = [r for r in self.ctx.config.data.files.index_roots if r.casefold() != path.casefold()]
            self.ctx.config.set("files.index_roots", [*roots, path])
            self._saved()
        return {"ok": True, "path": path}

    def do_removeIndexRoot(self, path: str) -> dict[str, Any]:  # noqa: N802
        self.ctx.config.set("files.index_roots", [r for r in self.ctx.config.data.files.index_roots if r != path])
        self._saved()
        return {"ok": True}

    def do_refreshApps(self) -> dict[str, Any]:  # noqa: N802
        try:
            self.d.tools.services["apps"].apps(refresh=True)
        except Exception as e:
            raise ValueError(str(e)) from None
        self._refresh_apps()
        self._status(self.t("apps_count", n=len(self.model.get("apps"))), "ok")
        return {"ok": True}

    # ------------------------------------------------------------------ actions: routines
    def do_saveRoutine(self, name: str, steps: list[Any], description: str = "",  # noqa: N802
                       old: str = "", quiet: bool = False) -> dict[str, Any]:
        """``quiet``: the editor's auto-save - a routine that isn't valid yet (an empty step while you type) is reported
        in the editor's status line instead of an error pop-up, and a successful save shows no toast."""
        try:
            return self._save_routine(name, steps, description, old, quiet)
        except ValueError as e:
            if not quiet:
                raise
            return {"ok": False, "error": str(e).strip("'\"")}

    def _save_routine(self, name: str, steps: list[Any], description: str, old: str, quiet: bool) -> dict[str, Any]:
        from ..storage.routines import Routine
        from ..tools.base import ToolError
        from ..tools.routines import validate_steps

        name = " ".join(name.split())
        if not name:
            raise ValueError(self.t("routine_name") + "?")
        parsed = []
        for i, s in enumerate(steps or [], 1):
            args = s.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError as e:
                    raise ValueError(f"{i}. {s.get('tool')}: JSON - {e.msg}") from None
            parsed.append({"tool": str(s.get("tool", "")), "args": args})
        try:
            clean = validate_steps(self.d.tools.registry, parsed)
        except ToolError as e:
            raise ValueError(str(e)) from None
        store = self.d.tools.services["routines"]
        other = store.get(name)
        if other is not None and other.name.casefold() != old.casefold():
            # (the store would overwrite that routine - and the editor saves while you type a new name)
            raise ValueError(self.t("routine_exists", name=other.name))
        if old and old.casefold() != name.casefold():
            store.delete(old)
        store.save(Routine(name=name, steps=clean, description=description.strip()))
        self._refresh_routines()
        if not quiet:
            self._saved()
        log.info("routine saved: %s (%d steps)", name, len(clean))
        return {"ok": True}

    def do_soundInfo(self, path: str) -> dict[str, Any]:  # noqa: N802
        """Length of a sound file (shown next to its name in the routine editor; 0 = unknown)."""
        try:
            st = Path(path).stat()
            return {"ok": True, "seconds": _sound_seconds(path, st.st_size, st.st_mtime_ns)}
        except Exception:
            return {"ok": True, "seconds": 0}

    def do_deleteRoutine(self, name: str) -> dict[str, Any]:  # noqa: N802
        self.d.tools.services["routines"].delete(name)
        self._refresh_routines()
        self._saved()
        return {"ok": True}

    def do_addTemplate(self, key: str) -> dict[str, Any]:  # noqa: N802
        from ..onboarding.questions import ROUTINE_TEMPLATES, _installed, routine_steps
        from ..onboarding.env import WizardEnv

        tpl = next(t for t in ROUTINE_TEMPLATES if t["key"] == key)
        env = WizardEnv(ctx=self.ctx, app_names=self._app_names)
        apps = _installed(env, tpl["apps"]) or tpl["apps"]
        steps = routine_steps({"key": key, "apps": ", ".join(apps), "volume": tpl["volume"]}, self._lang())
        store, base = self.d.tools.services["routines"], tpl["name"][self._lang()]
        name, n = base, 2
        while store.get(name) is not None:          # added again: "Gaming mode 2" - never overwrite your version
            name, n = f"{base} {n}", n + 1
        res = self.do_saveRoutine(name, steps, "")
        return {**res, "name": name}

    def do_pickSound(self) -> dict[str, Any]:  # noqa: N802
        title, filt = self.t("choose_sound"), "Sounds (*.mp3 *.wav *.flac *.ogg *.wma *.m4a *.aac)"
        if self.d.file_dialog is not None:
            path = self.d.file_dialog(title, filt)
        else:
            from PySide6.QtWidgets import QFileDialog

            path, _ = QFileDialog.getOpenFileName(None, title, str(Path.home() / "Music"), filt)
            path = path.replace("/", "\\") if path and re.match(r"^[A-Za-z]:/", path) else path
        return {"ok": True, "path": path or ""}

    def do_runRoutine(self, name: str) -> dict[str, Any]:  # noqa: N802
        from ..core.cancel import CancelToken
        from ..permissions.gate import Task

        def speak(text: str) -> None:            # "say" steps out loud, like when you ask by voice
            stack, v = self.d.voice(), self.ctx.config.data.voice
            if stack is not None and v.tts_enabled and text.strip():
                lang = self._lang()
                stack.speaker.say(text.strip(), lang, v.tts_voice_de if lang == "de" else v.tts_voice_en,
                                  v.tts_speed, CancelToken())

        def run() -> None:
            res = self.d.tools.invoke("run_routine", {"name": name}, Task(source="user", say=speak))
            self.ctx.bus.publish(Topic.NOTIFY, title=name, message=res.text.splitlines()[0] if res.text else "",
                                 level="info" if res.ok else "warning")

        threading.Thread(target=run, name="routine-run", daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ actions: history
    def do_searchHistory(self, query: str) -> dict[str, Any]:  # noqa: N802
        self._history_query = query
        self._refresh_history()
        return {"ok": True}

    def do_openConversation(self, id: int) -> dict[str, Any]:  # noqa: N802, A002
        store, lang = self._conversations(), self._lang()
        conv = store.get(int(id))
        if conv is None:
            self._open_conv = None
            self.model.set(conversation={})
            return {"ok": False}
        self._open_conv = conv.id
        name = self.ctx.config.data.general.assistant_name
        msgs = [{"role": m.role, "who": self.t("you") if m.role == "user" else name, "text": m.content,
                 "when": _when(m.ts, lang)} for m in store.messages(conv.id) if m.role in ("user", "assistant")]
        self.model.set(conversation={"id": conv.id, "title": conv.title or "…", "when": _when(conv.started, lang),
                                     "messages": msgs})
        return {"ok": True}

    def do_renameConversation(self, id: int, title: str) -> dict[str, Any]:  # noqa: N802, A002
        if not title.strip():
            raise ValueError("empty title")
        self._conversations().rename(int(id), title)
        self._refresh_history()
        self._saved()
        return {"ok": True}

    def do_deleteConversation(self, id: int) -> dict[str, Any]:  # noqa: N802, A002
        self._conversations().delete(int(id))
        if self._open_conv == int(id):
            self._open_conv = None
            self.model.set(conversation={})
        self._refresh_history()
        self._saved()
        return {"ok": True}

    def do_deleteAllConversations(self) -> dict[str, Any]:  # noqa: N802
        store = self._conversations()
        for c in store.recent(100000):
            store.delete(c.id)
        self._open_conv = None
        self.model.set(conversation={})
        self._refresh_history()
        self._saved()
        return {"ok": True}

    def do_exportConversation(self, id: int, fmt: str = "md") -> dict[str, Any]:  # noqa: N802, A002
        store = self._conversations()
        conv = store.get(int(id))
        if conv is None:
            raise KeyError(id)
        data = store.export(conv.id, "json" if fmt == "json" else "md")
        safe = re.sub(r"[^\w\- ]+", "", conv.title or "conversation").strip()[:60] or "conversation"
        ext = "json" if fmt == "json" else "md"
        path = self._save_path(self.t("export"), f"{safe}.{ext}",
                               "JSON (*.json)" if ext == "json" else "Markdown (*.md)")
        if not path:
            return {"ok": False, "cancelled": True}
        Path(path).write_text(data, encoding="utf-8")
        self._status(f"{self.t('saved')}: {path}", "ok")
        return {"ok": True, "path": path}

    def _save_path(self, title: str, name: str, filt: str) -> str:
        if self.d.save_dialog is not None:
            return self.d.save_dialog(title, name, filt)
        from PySide6.QtWidgets import QFileDialog

        docs = Path.home() / "Documents"
        start = str((docs if docs.exists() else Path.home()) / name)
        path, _ = QFileDialog.getSaveFileName(None, title, start, filt)
        return path

    # ------------------------------------------------------------------ actions: privacy / memory
    def do_addFact(self, text: str) -> dict[str, Any]:  # noqa: N802
        if not text.strip():
            return {"ok": False}
        self.d.tools.services["memory"].add(text, source="settings")
        self._refresh_facts()
        self._saved()
        return {"ok": True}

    def do_updateFact(self, id: int, text: str) -> dict[str, Any]:  # noqa: N802, A002
        mem = self.d.tools.services["memory"]
        if text.strip():
            mem.update(int(id), text)
        else:
            mem.delete(int(id))
        self._refresh_facts()
        self._saved()
        return {"ok": True}

    def do_deleteFact(self, id: int) -> dict[str, Any]:  # noqa: N802, A002
        self.d.tools.services["memory"].delete(int(id))
        self._refresh_facts()
        self._saved()
        return {"ok": True}

    def do_forgetAll(self) -> dict[str, Any]:  # noqa: N802
        mem = self.d.tools.services["memory"]
        for f in mem.all():
            mem.delete(f.id)
        self._refresh_facts()
        self._saved()
        return {"ok": True}

    def do_deleteRecordings(self) -> dict[str, Any]:  # noqa: N802
        d = self._recordings_dir()
        if d.exists():
            for f in d.glob("*.wav"):
                f.unlink(missing_ok=True)
        self._refresh_recordings()
        self._saved()
        return {"ok": True}

    # ------------------------------------------------------------------ actions: action log
    def do_filterLog(self, search: str = "", kind: str = "all") -> dict[str, Any]:  # noqa: N802
        self._log_filter = {"search": search.strip(), "kind": kind}
        self._refresh_actions()
        return {"ok": True}

    def do_exportLog(self, fmt: str = "csv") -> dict[str, Any]:  # noqa: N802
        rows = self.d.tools.action_log.recent(100000)
        stamp = time.strftime("%Y%m%d-%H%M")
        if fmt == "json":
            data = json.dumps([{"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.ts)), "tool": r.tool,
                                "capability": r.capability, "summary": r.summary, "args": r.args, "risks": r.risks,
                                "decision": r.decision, "outcome": r.outcome, "result": r.result,
                                "duration_ms": r.duration_ms, "source": r.source, "task": r.task_id} for r in rows],
                              ensure_ascii=False, indent=1)
            path = self._save_path(self.t("export"), f"action-log-{stamp}.json", "JSON (*.json)")
        else:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["time", "tool", "summary", "decision", "outcome", "result", "risks", "duration_ms", "source",
                        "args"])
            for r in rows:
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.ts)), r.tool, r.summary, r.decision,
                            r.outcome, r.result, ";".join(r.risks), r.duration_ms, r.source,
                            json.dumps(r.args, ensure_ascii=False)])
            data = buf.getvalue()
            path = self._save_path(self.t("export"), f"action-log-{stamp}.csv", "CSV (*.csv)")
        if not path:
            return {"ok": False, "cancelled": True}
        Path(path).write_text(data, encoding="utf-8-sig" if fmt != "json" else "utf-8")
        self._status(f"{self.t('saved')}: {path}", "ok")
        return {"ok": True, "path": path}

    # ------------------------------------------------------------------ actions: voice
    def _setup_voice(self) -> Any:
        if self._sv is None:
            from ..onboarding.voice import SetupVoice

            stack = self.d.voice()
            self._sv = SetupVoice(self.ctx, lambda k, data: self._voice_event.emit(k, data),
                                  stt=stack.stt if stack is not None else None,
                                  speaker=stack.speaker if stack is not None else None)
        return self._sv

    def _pause_voice(self, on: bool) -> None:
        if on != self._voice_paused:
            self._voice_paused = on
            self.d.pause_voice(on)

    def do_monitor(self, on: bool) -> dict[str, Any]:
        from ..voice.models import installed

        if on and installed("vad", self.ctx.paths.models_dir):
            self._setup_voice().start_monitor(self.ctx.config.data.voice.input_device)
        elif self._sv is not None:
            self._sv.stop_monitor()
        return {"ok": True}

    def do_calibrate(self) -> dict[str, Any]:
        sv = self._setup_voice()
        if not sv.available()["wake"]:
            raise ValueError(self.t("voice_off"))
        sv.stop_monitor()
        self._pause_voice(True)              # the running wake word must not react to the three takes
        self.model.set(calib={"step": "starting"})
        sv.record_name(self.ctx.config.data.general.assistant_name)
        return {"ok": True}

    def do_enroll(self) -> dict[str, Any]:
        sv = self._setup_voice()
        if not sv.available()["lock"]:
            raise ValueError(self.t("voice_off"))
        sv.stop_monitor()
        self._pause_voice(True)
        self.model.set(enroll={"step": "starting"})
        sv.enroll(self._lang())
        return {"ok": True}

    def do_forgetVoice(self) -> dict[str, Any]:  # noqa: N802
        (self.ctx.paths.config_dir / "voiceprint.npy").unlink(missing_ok=True)
        self.ctx.config.set("voice.voice_lock", False)
        self.model.set(enroll={})
        self._refresh_voice()
        self._saved()
        return {"ok": True}

    def do_removeVariant(self, variant: str) -> dict[str, Any]:  # noqa: N802
        self.ctx.config.set("voice.wake_variants", [v for v in self.ctx.config.data.voice.wake_variants if v != variant])
        self._saved()
        return {"ok": True}

    def do_preview(self, voice: str, lang: str = "") -> dict[str, Any]:
        lang = lang or self._lang()
        g = self.ctx.config.data.general
        sample = (f"Hallo{' ' + g.user_name if g.user_name else ''}, ich bin {g.assistant_name}. So klinge ich."
                  if lang == "de" else
                  f"Hi{' ' + g.user_name if g.user_name else ''}, I'm {g.assistant_name}. This is how I sound.")
        self._setup_voice().say(sample, voice=voice, lang=lang)
        return {"ok": True}

    def do_testSound(self) -> dict[str, Any]:  # noqa: N802
        from ..audio.earcons import sound

        self._setup_voice().player().play(sound("wake"))
        return {"ok": True}

    @Slot(str, object)
    def _on_voice(self, kind: str, data: dict[str, Any]) -> None:
        m = self.model
        enrolling = m.get("enroll").get("step") in ("starting", "recording", "processing")
        if kind == "level":
            m.set(level=float(data.get("level", 0.0)))
        elif kind in ("take", "takes_done"):
            state = ({"step": "recording", "n": data["n"], "total": data["total"], "prompt": data.get("prompt", "")}
                     if kind == "take" else {"step": "processing"})
            if enrolling:
                m.set(enroll=state)
            else:
                m.set(calib=state)
        elif kind == "calibrated":
            r = data["result"]
            self.ctx.config.update({"voice.wake_sensitivity": float(r["sensitivity"]),
                                    "voice.wake_variants": [str(v) for v in r.get("variants", [])][:3]})
            m.set(calib={"step": "done", "ok": bool(r.get("ok")),
                         "text": self.t("calib_done", detected=r.get("detected"), total=r.get("total"))}, level=0.0)
            self._pause_voice(False)
            if m.get("tab") == "voice":
                self.do_monitor(True)
        elif kind == "enrolled":
            self.ctx.config.set("voice.voice_lock", True)
            m.set(enroll={"step": "done"}, level=0.0)
            self._pause_voice(False)
            self._refresh_voice()
            if m.get("tab") == "voice":
                self.do_monitor(True)
        elif kind == "error":
            self._status(str(data.get("message", "")), "error")
            m.set(calib={}, enroll={}, level=0.0)
            self._pause_voice(False)

    # ------------------------------------------------------------------ actions: model
    def do_restartModel(self) -> dict[str, Any]:  # noqa: N802
        llm = self.d.llm
        if llm is None:
            raise ValueError("the language model service is not running")

        def go() -> None:
            llm.stop()
            llm.spec = None
            llm.start_background()

        threading.Thread(target=go, name="llm-restart", daemon=True).start()
        self._status(self.t("restart_model") + " …", "info")
        return {"ok": True}

    def do_windowClosed(self) -> dict[str, Any]:  # noqa: N802 - X button / Esc (QML onClosing)
        # (not window.closing.connect(...): PySide6 can't hand QQuickCloseEvent to Python, so such a slot
        # never ran and the window was only hidden - with the wake word possibly still paused)
        QTimer.singleShot(0, self.close)         # not inside the window's own close handler
        return {"ok": True}

    def do_downloadModels(self) -> dict[str, Any]:  # noqa: N802
        dl = self.d.downloads()
        if dl is not None:
            dl.start()
        self._refresh_download()
        return {"ok": True}

    # ------------------------------------------------------------------ actions: appearance
    def do_testPopup(self) -> dict[str, Any]:  # noqa: N802
        if self.d.popup is not None:
            self.d.popup.demo(self.t("test_popup_text"))
        return {"ok": True}


__all__ = ["SettingsDeps", "SettingsModel", "SettingsWindow", "TABS"]
