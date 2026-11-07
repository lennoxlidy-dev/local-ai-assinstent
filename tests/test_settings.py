"""Milestone 6: the settings window - every action of the controller, and every tab loading in QML."""

import json

import pytest

from assistant.permissions.gate import Task
from assistant.permissions.policy import Level, Risk
from assistant.storage.conversations import ConversationStore
from assistant.ui.settings import TABS, SettingsDeps, SettingsWindow


class _Assistant:
    def __init__(self, store):
        self.conversations = store


@pytest.fixture
def sw(ts, tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    system, *_ = ts
    saved: dict[str, str] = {}
    conv = ConversationStore(system.db)

    def save_dialog(title, name, filt):
        saved["path"] = str(tmp_path / name)
        return saved["path"]

    deps = SettingsDeps(ctx=system.ctx, tools=system, assistant=_Assistant(conv), save_dialog=save_dialog,
                        folder_dialog=lambda: "D:\\Projects", open_path=lambda p: saved.setdefault("opened", str(p)))
    w = SettingsWindow(deps)
    w.saved = saved  # type: ignore[attr-defined]
    yield w, system, conv
    w.close()


def call(w, action, **args):
    return w.model.call(action, args)


def test_set_validates_and_coerces(sw):
    w, system, _ = sw
    cfg = system.ctx.config
    assert call(w, "set", key="voice.silence_timeout_s", value=1.4)["ok"]
    assert cfg.data.voice.silence_timeout_s == 1.4
    assert call(w, "set", key="llm.max_agent_steps", value=12.0)["ok"]       # QML sends floats
    assert cfg.data.llm.max_agent_steps == 12 and isinstance(cfg.data.llm.max_agent_steps, int)
    r = call(w, "set", key="voice.silence_timeout_s", value=9.0)
    assert not r["ok"] and "5.0" in r["error"] and w.model.get("statusKind") == "error"
    assert cfg.data.voice.silence_timeout_s == 1.4
    assert w.model.get("cfg")["voice.silence_timeout_s"] == 1.4
    assert not call(w, "set", key="appearance.accent", value="blue")["ok"]
    assert not call(w, "nonsense")["ok"]


def test_renaming_the_assistant_resets_the_calibration(sw):
    w, system, _ = sw
    cfg = system.ctx.config
    cfg.update({"voice.wake_variants": ["Javis"], "voice.wake_sensitivity": 0.7})
    call(w, "set", key="general.assistant_name", value="Nova")
    assert cfg.data.general.assistant_name == "Nova"
    assert cfg.data.voice.wake_variants == [] and cfg.data.voice.wake_sensitivity == 0.5


def test_permissions_apply_immediately(sw):
    w, system, _ = sw
    call(w, "tab", name="permissions")
    caps = {c["id"]: c for c in w.model.get("caps")}
    assert caps["input"]["level"] == "ask" and [o["value"] for o in caps["system.admin"]["options"]] == ["ask", "block"]
    call(w, "setLevel", cap="input", level="allow")
    assert system.policy.policy.level("input") is Level.ALLOW
    call(w, "setRisk", risk="power", on=False)
    assert Risk.POWER not in system.policy.policy.always_confirm
    assert not call(w, "setRisk", risk="admin", on=False)["ok"]                  # locked
    home = system.ctx.paths.config_dir.parent / "user"
    folder = str(home / "Trusted")
    call(w, "setFolder", path=folder, access="full")
    rule = system.gate.rules.rule_for(system.normalizer.normalize(folder + "/x.txt"))
    assert rule is not None and rule.access.value == "full"                     # path checker rebuilt
    call(w, "removeFolder", path=folder)
    assert system.gate.rules.rule_for(system.normalizer.normalize(folder + "/x.txt")) is None
    assert call(w, "addFolder")["path"] == "D:\\Projects"


def test_apps_aliases_and_desktop_rules(sw):
    w, system, _ = sw
    call(w, "tab", name="apps")
    assert "Spotify" in w.model.get("apps")
    call(w, "setAlias", alias="Mail", app="Outlook")
    assert system.ctx.config.data.apps.aliases["mail"] == "Outlook"
    call(w, "setAlias", alias="post", app="Outlook", old="mail")
    assert "mail" not in system.ctx.config.data.apps.aliases
    call(w, "removeAlias", alias="post")
    call(w, "setDesktopRule", app="Discord", rule="assistant")
    assert system.ctx.config.data.desktops.rules["discord"] == "assistant"
    call(w, "removeDesktopRule", app="discord")
    assert "discord" not in system.ctx.config.data.desktops.rules
    assert not call(w, "setAlias", alias="", app="x")["ok"]


def test_routines_editor(sw):
    w, system, _ = sw
    call(w, "tab", name="routines")
    assert "open_app" in w.model.get("toolNames") and "run_routine" not in w.model.get("toolNames")
    r = call(w, "saveRoutine", name="Focus", steps=[{"tool": "open_app", "args": '{"name": "Notepad"}'},
                                                   {"tool": "volume", "args": '{"level": 20}'}])
    assert r["ok"]
    assert w.model.get("routines")[0]["summary"] == "open_app Notepad · volume 20"
    r = call(w, "saveRoutine", name="Bad", steps=[{"tool": "volume", "args": '{"level": 500}'}])
    assert not r["ok"] and "Step 1" in r["error"]
    assert not call(w, "saveRoutine", name="Bad", steps=[{"tool": "volume", "args": "{oops"}])["ok"]
    assert not call(w, "saveRoutine", name="Bad", steps=[{"tool": "nope", "args": "{}"}])["ok"]
    call(w, "saveRoutine", name="Deep focus", old="Focus", steps=[{"tool": "volume", "args": {"level": 10}}])
    names = [x["name"] for x in w.model.get("routines")]
    assert names == ["Deep focus"]
    call(w, "addTemplate", key="night")
    assert any(x["name"] == "Good night" for x in w.model.get("routines"))
    call(w, "deleteRoutine", name="Deep focus")
    assert [x["name"] for x in w.model.get("routines")] == ["Good night"]


def test_history_search_rename_export_delete(sw):
    w, system, conv = sw
    c = conv.create()
    conv.add(c.id, "user", "Open notepad and write a shopping list")
    conv.add(c.id, "assistant", "Done, Notepad is open.")
    other = conv.create()
    conv.add(other.id, "user", "What time is it?")
    call(w, "tab", name="history")
    assert [x["title"] for x in w.model.get("conversations")][:2] == ["What time is it?",
                                                                       "Open notepad and write a shopping list"]
    call(w, "searchHistory", query="shopping")
    rows = w.model.get("conversations")
    assert len(rows) == 1 and "[shopping]" in rows[0]["snippet"]
    call(w, "openConversation", id=c.id)
    conv_vm = w.model.get("conversation")
    assert [m["role"] for m in conv_vm["messages"]] == ["user", "assistant"] and conv_vm["messages"][0]["who"] == "You"
    call(w, "renameConversation", id=c.id, title="Groceries")
    assert w.model.get("conversation")["title"] == "Groceries"
    r = call(w, "exportConversation", id=c.id, fmt="md")
    md = open(r["path"], encoding="utf-8").read()
    assert md.startswith("# Groceries") and "**You:** Open notepad" in md
    r = call(w, "exportConversation", id=c.id, fmt="json")
    data = json.load(open(r["path"], encoding="utf-8"))
    assert data["title"] == "Groceries" and len(data["messages"]) == 2
    call(w, "deleteConversation", id=c.id)
    assert w.model.get("conversation") == {} and conv.get(c.id) is None
    call(w, "deleteAllConversations")
    assert conv.recent() == []


def test_lower_retention_purges_now(sw):
    w, system, conv = sw
    old = conv.create()
    conv.add(old.id, "user", "ancient")
    system.db.execute("UPDATE conversations SET updated=? WHERE id=?", (1.0, old.id))
    call(w, "set", key="privacy.retention_days", value=7)
    assert conv.get(old.id) is None


def test_memory_and_recordings(sw):
    w, system, _ = sw
    call(w, "tab", name="privacy")
    call(w, "addFact", text="I like green tea")
    call(w, "addFact", text="My sister is Lea")
    facts = w.model.get("facts")
    assert {f["text"] for f in facts} == {"I like green tea", "My sister is Lea"}
    call(w, "updateFact", id=facts[0]["id"], text="My sister is Mia")
    assert "My sister is Mia" in {f["text"] for f in w.model.get("facts")}
    call(w, "deleteFact", id=w.model.get("facts")[0]["id"])
    assert len(w.model.get("facts")) == 1
    call(w, "forgetAll")
    assert w.model.get("facts") == []
    rec = system.ctx.paths.local_dir / "recordings"
    rec.mkdir(parents=True)
    (rec / "20261101-120000-wake.wav").write_bytes(b"RIFF....")
    call(w, "tab", name="privacy")
    assert w.model.get("recordings")["count"] == 1
    call(w, "deleteRecordings")
    assert w.model.get("recordings")["count"] == 0


def test_action_log_filters_and_export(sw, ts):
    w, system, _ = sw
    home = ts[3]
    (home / "Desktop").mkdir(exist_ok=True)
    (home / "Desktop" / "x.txt").write_text("x")
    system.invoke("volume", {"level": 30}, Task(source="llm"))
    system.policy.set_level("files.delete", Level.BLOCK)
    system.invoke("delete_path", {"path": "Desktop/x.txt"}, Task(source="llm"))
    system.invoke("volume", {"level": 999}, Task(source="llm"))
    call(w, "tab", name="log")
    rows = w.model.get("actions")
    assert [r["decision"] for r in rows] == ["invalid", "blocked", "allowed"]
    call(w, "filterLog", kind="blocked")
    assert [r["tool"] for r in w.model.get("actions")] == ["delete_path"]
    call(w, "filterLog", kind="errors")
    assert [r["decision"] for r in w.model.get("actions")] == ["invalid"]
    call(w, "filterLog", search="Volume", kind="all")
    assert all(r["tool"] == "volume" for r in w.model.get("actions"))
    csv_text = open(call(w, "exportLog", fmt="csv")["path"], encoding="utf-8-sig").read()
    assert csv_text.splitlines()[0].startswith("time,tool,summary,decision") and "delete_path" in csv_text
    data = json.load(open(call(w, "exportLog", fmt="json")["path"], encoding="utf-8"))
    assert len(data) == 3 and data[0]["decision"] == "invalid"


def test_model_and_voice_tabs_without_models(sw):
    w, system, _ = sw
    call(w, "tab", name="model")
    models = w.model.get("models")
    assert models and all(not m["installed"] for m in models)
    assert w.model.get("llm")["ready"] is False
    call(w, "tab", name="voice")
    v = w.model.get("voice")
    assert v["installed"] is False and v["enrolled"] is False
    assert w.model.get("devicesIn")[0]["value"] == ""
    assert not call(w, "calibrate")["ok"]                     # no speech models -> clear error


def test_strings_follow_the_language(sw):
    w, system, _ = sw
    assert w.model.get("tr")["tab_history"] == "History"
    call(w, "set", key="general.language", value="de")
    w._on_bus(type("E", (), {"topic": "config.changed", "data": {"keys": ["general.language"]}})())
    assert w.model.get("tr")["tab_history"] == "Verlauf"


def test_every_tab_loads_in_qml(sw):
    """Load the real QML window offscreen and open each tab; no QML errors allowed."""
    from PySide6.QtCore import QEventLoop, QTimer, qInstallMessageHandler

    w, system, conv = sw
    c = conv.create()
    conv.add(c.id, "user", "hello")
    messages: list[str] = []
    old = qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
    try:
        assert w.show("general")
        for tab in TABS:
            call(w, "tab", name=tab)
            if tab == "history":
                call(w, "openConversation", id=c.id)
            loop = QEventLoop()
            QTimer.singleShot(60, loop.quit)
            loop.exec()
        w.close()
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec()
    finally:
        qInstallMessageHandler(old)
    bad = [m for m in messages if any(k in m for k in ("qml", "QML", "TypeError", "ReferenceError", "Binding loop"))]
    assert bad == []
    assert w.engine is None and w.window is None


def test_recordings_saved_only_when_enabled_and_purged(tmp_path):
    import os
    import time

    import numpy as np

    from assistant.core.context import bootstrap
    from assistant.voice import recordings

    ctx = bootstrap("cli", home=str(tmp_path / "h"), detect_hardware="off")
    audio = np.zeros(16000, np.float32)
    assert recordings.save(ctx, audio, "wake") is None                  # off by default
    ctx.config.set("privacy.save_audio", True)
    path = recordings.save(ctx, audio, "wake")
    assert path is not None and path.exists() and path.parent == recordings.folder(ctx)
    old = time.time() - 40 * 86400
    os.utime(path, (old, old))
    assert recordings.purge(ctx, 30) == 1 and not path.exists()


def test_closing_the_window_frees_it(sw):
    """The window's own close (X button, Esc) must run the cleanup - not just hide the window."""
    from PySide6.QtCore import QCoreApplication

    w, *_ = sw
    closed = []
    w.closed.connect(lambda: closed.append(1))
    paused = []
    w.d.pause_voice = lambda on: paused.append(on)
    assert w.show()
    w._voice_paused = True                 # e.g. the Voice tab's level meter had paused the wake word
    w.window.close()                       # what the title bar's X does
    for _ in range(5):
        QCoreApplication.processEvents()
    assert w.window is None and w.engine is None and closed == [1]
    assert paused == [False]               # the wake word listens again
