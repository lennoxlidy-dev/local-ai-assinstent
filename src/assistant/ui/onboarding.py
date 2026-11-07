"""The setup wizard window (QML) and its controller.

One question at a time; the assistant speaks each question (when speech output is installed and
voice replies are on) and listens for the answer (when speech recognition is installed and
"answer by voice" is on). Typing/clicking always works. Models download in the background and
the window shows the progress.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot

from ..audio.sources import list_devices
from ..core.context import AppContext
from ..core.events import Event
from ..onboarding.downloads import SetupManager, describe
from ..onboarding.env import WizardEnv
from ..onboarding.questions import ACCESS_OPTIONS, Question, label, spoken
from ..onboarding.voice import ENROLL_SENTENCES, SetupVoice, level_verdict
from ..onboarding.wizard import Wizard
from .qtmodel import model_class

log = logging.getLogger(__name__)

QML = Path(__file__).with_name("qml") / "Setup.qml"

UI = {
    "en": {"next": "Next", "back": "Back", "skip": "Skip", "finish": "Finish", "listen": "Answer by voice",
           "speak": "Read questions aloud", "listening": "Listening…", "speaking": "Speaking…",
           "thinking": "One moment…", "heard": "I heard: “{text}”", "unclear": "Sorry, I didn't get that. "
           "Try again, or type/click.", "nothing": "I didn't hear anything - say it again or click.",
           "repeat": "Repeat", "of": "of", "setup": "Setup", "other": "Other…", "start": "Start",
           "again": "Try again", "add_folder": "Add folder…", "remove": "Remove", "default_mic": "Windows default",
           "press_keys": "Press the keys…", "press": "Press once, then talk", "hold": "Hold while talking",
           "ptt": "Push-to-talk", "panic": "Panic (stop everything)", "change": "Change", "clear": "Clear",
           "set_up": "Set up voice lock", "not_now": "Not now", "read_aloud": "Read aloud:",
           "apps": "Apps to open", "volume": "Volume", "name": "Name", "done_title": "All set!",
           "close_title": "Finish setup later?", "close_text": "The remaining questions keep their suggested "
           "answers. You can run the setup again from the tray menu any time.",
           "close_yes": "Use the suggestions", "close_no": "Continue setup", "preview": "Play",
           "voice_wait": "Voice answers work as soon as speech recognition is downloaded.",
           "wake_wait": "The wake-word model is still downloading…", "lock_wait": "The voice-lock model is still downloading…",
           "recording": "Recording {n} of {total}", "processing": "Checking…", "say_name": "Say “{name}”",
           "detected": "Recognized {detected} of {total}", "variants": "Also listening for: {variants}",
           "wake_ok": "Great - I'll react to my name reliably.", "wake_meh": "Okay - mostly recognized. You "
           "can try again, or pick another name with 2-3 syllables.", "wake_bad": "I didn't recognize it well. "
           "Try again a bit louder and clearer, or pick another name.", "enrolled": "Done - I'll only react to your voice.",
           "level": "Microphone level", "downloads": "Downloads", "yes": "Yes", "no": "No"},
    "de": {"next": "Weiter", "back": "Zurück", "skip": "Überspringen", "finish": "Fertig",
           "listen": "Per Stimme antworten", "speak": "Fragen vorlesen", "listening": "Ich höre zu…",
           "speaking": "Ich spreche…", "thinking": "Einen Moment…", "heard": "Verstanden: „{text}“",
           "unclear": "Das habe ich nicht verstanden. Versuch es nochmal, oder tippe/klicke.",
           "nothing": "Ich habe nichts gehört - sag es nochmal oder klicke.", "repeat": "Wiederholen", "of": "von",
           "setup": "Einrichtung", "other": "Andere…", "start": "Start", "again": "Nochmal",
           "add_folder": "Ordner hinzufügen…", "remove": "Entfernen", "default_mic": "Windows-Standard",
           "press_keys": "Tasten drücken…", "press": "Einmal drücken, dann sprechen", "hold": "Gedrückt halten beim Sprechen",
           "ptt": "Push-to-Talk", "panic": "Notaus (alles stoppen)", "change": "Ändern", "clear": "Leeren",
           "set_up": "Stimmsperre einrichten", "not_now": "Jetzt nicht", "read_aloud": "Lies vor:",
           "apps": "Apps öffnen", "volume": "Lautstärke", "name": "Name", "done_title": "Alles bereit!",
           "close_title": "Einrichtung später beenden?", "close_text": "Die restlichen Fragen behalten ihre "
           "Vorschläge. Du kannst die Einrichtung jederzeit über das Tray-Menü erneut starten.",
           "close_yes": "Vorschläge übernehmen", "close_no": "Weiter einrichten", "preview": "Anhören",
           "voice_wait": "Antworten per Stimme gehen, sobald die Spracherkennung geladen ist.",
           "wake_wait": "Das Aktivierungswort-Modell wird noch geladen…",
           "lock_wait": "Das Modell für die Stimmsperre wird noch geladen…",
           "recording": "Aufnahme {n} von {total}", "processing": "Prüfe…", "say_name": "Sag „{name}“",
           "detected": "{detected} von {total} erkannt", "variants": "Höre auch auf: {variants}",
           "wake_ok": "Super - ich reagiere zuverlässig auf meinen Namen.", "wake_meh": "Okay - meistens erkannt. "
           "Du kannst es nochmal versuchen oder einen Namen mit 2-3 Silben wählen.",
           "wake_bad": "Das hat noch nicht gut geklappt. Versuch es lauter und deutlicher, oder wähl einen anderen Namen.",
           "enrolled": "Fertig - ich reagiere nur noch auf deine Stimme.", "level": "Mikrofonpegel",
           "downloads": "Downloads", "yes": "Ja", "no": "Nein"},
}

DARK = {"bg": "#16161A", "card": "#1F1F25", "card2": "#26262E", "field": "#1A1A20", "fg": "#F2F3F5",
        "dim": "#A3A7B0", "border": "#33343C", "warn": "#F5B44A", "ok": "#3DD68C", "danger": "#F2555A"}
LIGHT = {"bg": "#F4F5F8", "card": "#FFFFFF", "card2": "#ECEDF2", "field": "#FFFFFF", "fg": "#1B1C20",
         "dim": "#5E626B", "border": "#D9DBE2", "warn": "#B26B00", "ok": "#178A52", "danger": "#C9333A"}

SetupModelBase: Any = model_class("SetupModelBase", {
    "q": ("QVariantMap", {}), "ui": ("QVariantMap", {}), "theme": ("QVariantMap", {}), "atEnd": (bool, False),
    "status": (str, ""), "statusKind": (str, "info"), "orbMode": (str, "idle"), "level": (float, 0.0),
    "voiceMode": (bool, True), "speakMode": (bool, True), "canListen": (bool, False), "canSpeak": (bool, False),
    "download": (str, ""), "downloadPct": (float, 0.0), "downloadActive": (bool, False),
    "pageState": ("QVariantMap", {}), "finish": ("QVariantMap", {}), "busy": (bool, False),
})


class SetupModel(SetupModelBase):
    """The ``setup`` object in QML."""

    submitted = Signal(object)
    valueSet = Signal(object)
    skipped = Signal()
    wentBack = Signal()
    repeated = Signal()
    voiceToggled = Signal(bool)
    speakToggled = Signal(bool)
    previewed = Signal(str)
    wakeStarted = Signal()
    enrollStarted = Signal()
    micSelected = Signal(str)
    finished = Signal()
    closeRequested = Signal()
    hotkeysSuspended = Signal(bool)

    @Slot("QVariant")
    def submit(self, value: Any) -> None:
        self.submitted.emit(value)

    @Slot("QVariant")
    def setValue(self, value: Any) -> None:  # noqa: N802
        self.valueSet.emit(value)

    @Slot()
    def skip(self) -> None:
        self.skipped.emit()

    @Slot()
    def back(self) -> None:
        self.wentBack.emit()

    @Slot()
    def repeat(self) -> None:
        self.repeated.emit()

    @Slot(bool)
    def setVoiceMode(self, on: bool) -> None:  # noqa: N802
        self.voiceToggled.emit(on)

    @Slot(bool)
    def setSpeakMode(self, on: bool) -> None:  # noqa: N802
        self.speakToggled.emit(on)

    @Slot(str)
    def preview(self, voice: str) -> None:
        self.previewed.emit(voice)

    @Slot()
    def startWake(self) -> None:  # noqa: N802
        self.wakeStarted.emit()

    @Slot()
    def startEnroll(self) -> None:  # noqa: N802
        self.enrollStarted.emit()

    @Slot(str)
    def selectMic(self, device: str) -> None:  # noqa: N802
        self.micSelected.emit(device)

    @Slot(result=str)
    def browseFolder(self) -> str:  # noqa: N802
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getExistingDirectory(None, "") or ""

    @Slot()
    def done(self) -> None:
        self.finished.emit()

    @Slot()
    def requestClose(self) -> None:  # noqa: N802
        self.closeRequested.emit()

    @Slot(bool)
    def suspendHotkeys(self, on: bool) -> None:  # noqa: N802
        self.hotkeysSuspended.emit(on)


def pretty_hotkey(spec: str) -> str:
    """'ctrl+alt+space' -> 'Ctrl + Alt + Space'."""
    return " + ".join(p.upper() if len(p) <= 1 or p.startswith("f") and p[1:].isdigit() else p.capitalize()
                      for p in spec.split("+") if p) or "-"


def _qml_value(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _qml_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_qml_value(x) for x in v]
    if hasattr(v, "value") and not isinstance(v, (str, int, float, bool)):   # enums
        return v.value
    return v


class SetupWindow(QObject):
    """Owns the wizard, its voice helper and the QML window. Emits ``completed`` when done."""

    completed = Signal()
    _voice_event = Signal(str, object)
    _bus_event = Signal(object)

    MAX_UNCLEAR = 2

    def __init__(self, ctx: AppContext, env: WizardEnv, downloads: SetupManager | None = None, *,
                 suspend_hotkeys: Any = None, voice: SetupVoice | None = None):
        super().__init__()
        self.ctx = ctx
        self.env = env
        self.wizard = Wizard(env)
        self.downloads = downloads
        self.suspend_hotkeys = suspend_hotkeys
        self.model = SetupModel()
        self.engine: Any = None
        self.window: Any = None
        self._unclear = 0
        self._ack = ""
        self._closing = False
        self._voice_event.connect(self._on_voice, Qt.ConnectionType.QueuedConnection)
        self._bus_event.connect(self._on_bus)
        self.voice = voice or SetupVoice(ctx, lambda kind, data: self._voice_event.emit(kind, data))
        m = self.model
        m.submitted.connect(self._submit)
        m.valueSet.connect(self._set_value)
        m.skipped.connect(self._skip)
        m.wentBack.connect(self._back)
        m.repeated.connect(self._ask)
        m.voiceToggled.connect(self._toggle_voice)
        m.speakToggled.connect(self._toggle_speak)
        m.previewed.connect(self._preview)
        m.wakeStarted.connect(self._start_wake)
        m.enrollStarted.connect(self._start_enroll)
        m.micSelected.connect(self._select_mic)
        m.finished.connect(self.finish)
        m.closeRequested.connect(self._close_requested)
        m.hotkeysSuspended.connect(self._suspend_hotkeys)
        self._unsubs = [ctx.bus.subscribe(t, self._bus_event.emit)
                        for t in ("setup.progress", "setup.job_done", "setup.job_started", "setup.failed",
                                  "setup.finished", "setup.retry")]
        self._update_availability()
        m.set(theme=self._theme(), voiceMode=True, speakMode=bool(ctx.config.data.voice.tts_enabled))
        self._refresh()
        self._update_download()

    # ------------------------------------------------------------------ window
    def show(self) -> bool:
        from PySide6.QtQml import QQmlApplicationEngine

        if self.window is not None:
            self.window.show()
            self.window.raise_()
            self.window.requestActivate()
            return True
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("setup", self.model)
        engine.load(QUrl.fromLocalFile(str(QML)))
        roots = engine.rootObjects()
        if not roots:
            log.error("setup window QML failed to load")
            return False
        self.engine, self.window = engine, roots[0]
        try:
            from ..win.dwm import set_dark_titlebar

            set_dark_titlebar(int(self.window.winId()), self._dark())
        except Exception as e:
            log.debug("title bar styling: %s", e)
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()
        self.voice.preload()
        QTimer.singleShot(400, self._ask)
        return True

    def _dark(self) -> bool:
        return self.ctx.config.data.appearance.theme != "light"

    def _theme(self) -> dict[str, str]:
        t = dict(DARK if self._dark() else LIGHT)
        t["accent"] = self.ctx.config.data.appearance.accent
        t["font"] = "Segoe UI Variable Text" if self._is_win11() else "Segoe UI"
        return t

    @staticmethod
    def _is_win11() -> bool:
        from ..win.dwm import windows_build

        return windows_build() >= 22000

    # ------------------------------------------------------------------ view model
    def t(self, key: str, **kw: Any) -> str:
        s = UI.get(self.env.lang, UI["en"]).get(key) or UI["en"].get(key, key)
        return s.format(**kw) if kw else s

    def _refresh(self) -> None:
        m, w, lang = self.model, self.wizard, self.env.lang
        m.set(ui=UI.get(lang, UI["en"]), theme=self._theme())
        q = w.current
        if q is None:
            m.set(atEnd=True, q={"id": "finish", "kind": "finish", "page": "finish"}, finish=self._finish_info(),
                  pageState={})
            return
        n, total = w.progress()
        vm: dict[str, Any] = {"id": q.id, "kind": q.kind, "page": q.page or q.kind, "title": label(q.title, lang),
                              "help": label(q.help, lang), "value": _qml_value(w.value()), "index": n, "total": total,
                              "preview": q.preview, "allowOther": q.allow_other, "canBack": bool(w.history)}
        env = self.env
        if q.options is not None:
            vm["options"] = [{"value": _qml_value(o.value), "label": label(o.label, lang), "hint": label(o.hint, lang)}
                             for o in q.options(env)]
        if q.number is not None:
            lo, hi, step, unit = q.number
            vm.update(min=lo, max=hi, step=step, unit=label(unit, lang))
        if q.fields is not None:
            vm["fields"] = [{"key": f.key, "label": label(f.label, lang), "placeholder": label(f.placeholder, lang),
                             "suggestions": f.suggestions, "multiline": f.multiline, "maxLen": f.max_len}
                            for f in q.fields(env)]
        if q.rows is not None and q.columns is not None:
            vm["rows"] = [{"key": r.value, "label": label(r.label, lang), "hint": label(r.hint, lang),
                           "options": [{"value": c.value, "label": label(c.label, lang)} for c in q.columns(env, r.value)]}
                          for r in q.rows(env)]
        if q.page == "wake":
            vm["name"] = self.ctx.config.data.general.assistant_name
        elif q.page == "mic":
            vm["devices"] = [""] + list_devices("input")
        elif q.page == "folders":
            vm["access"] = [{"value": o.value, "label": label(o.label, lang), "hint": label(o.hint, lang)}
                            for o in ACCESS_OPTIONS]
        elif q.page == "voicelock":
            vm["sentences"] = ENROLL_SENTENCES["de" if lang == "de" else "en"]
            vm["enrolled"] = (self.ctx.paths.config_dir / "voiceprint.npy").exists()
        m.set(atEnd=False, q=vm, pageState={})
        self._page_entered(q)

    def _finish_info(self) -> dict[str, Any]:
        c, lang = self.ctx.config.data, self.env.lang
        name = c.general.assistant_name
        de = lang == "de"
        ptt, panic = pretty_hotkey(c.hotkeys.push_to_talk), pretty_hotkey(c.hotkeys.panic)
        tips = ([f"Sag „{name}, wie spät ist es?“", f"„{name}, öffne den Editor“",
                 f"„{name}, merk dir, dass ich Tee mag“", f"Push-to-Talk: {ptt}",
                 f"Notaus: {panic} oder „{name}, stopp“"] if de else
                [f"Say “{name}, what time is it?”", f"“{name}, open Notepad”", f"“{name}, remember that I like tea”",
                 f"Push-to-talk: {ptt}", f"Panic: {panic} or “{name}, stop”"])
        if not c.hotkeys.push_to_talk:
            tips.pop(3)
        return {"title": self.t("done_title"), "tips": tips,
                "text": (f"Ich bin {name} und warte im Tray auf dich." if de else
                         f"I'm {name} and I'll wait in the tray for you.")}

    def _page_entered(self, q: Question) -> None:
        if q.page == "mic":
            self.voice.start_monitor(None)
        else:
            self.voice.stop_monitor()
        if self.suspend_hotkeys is not None:
            self.suspend_hotkeys(q.page == "hotkeys")

    # ------------------------------------------------------------------ voice
    def _update_availability(self) -> None:
        av = self.voice.available()
        self.model.set(canSpeak=av["speak"], canListen=av["listen"])

    def _ask(self) -> None:
        q = self.wizard.current
        m = self.model
        if self.window is None or self._closing:
            return
        if q is None:
            text = self._finish_info()["text"]
            self.voice.ask(self._take_ack() + text, speak=self._speaking(), listen=False)
            return
        listen = m.get("voiceMode") and m.get("canListen") and q.page not in ("wake", "mic", "voicelock")
        text = self._take_ack() + spoken(q, self.env)
        if q.page == "wake" and m.get("voiceMode") and self.voice.available()["wake"]:
            self._start_wake(intro=text)
            return
        self.voice.ask(text, speak=self._speaking(), listen=bool(listen))

    def _take_ack(self) -> str:
        ack, self._ack = self._ack, ""
        return ack

    def _speaking(self) -> bool:
        return bool(self.model.get("speakMode")) and bool(self.model.get("canSpeak")) and \
            bool(self.ctx.config.data.voice.tts_enabled)

    @Slot(str, object)
    def _on_voice(self, kind: str, data: dict[str, Any]) -> None:
        m = self.model
        if kind == "speaking":
            m.set(orbMode="speaking", status="", statusKind="info")
        elif kind == "listening":
            m.set(orbMode="listening", status=self.t("listening"), statusKind="info")
        elif kind == "thinking":
            m.set(orbMode="thinking", status=self.t("thinking"), statusKind="info", level=0.0)
        elif kind == "level":
            m.set(level=float(data.get("level", 0.0)))
            if self.wizard.current is not None and self.wizard.current.page == "mic":
                peak = float(data.get("peak", data.get("level", 0.0)))
                verdict, text = level_verdict(peak, float(data.get("level", 0.0)), self.env.lang)
                m.set(pageState={"peak": peak, "verdict": verdict, "text": text})
        elif kind == "idle":
            m.set(orbMode="idle", level=0.0)
            if m.get("status") == self.t("listening"):
                m.set(status="")
        elif kind == "nothing":
            m.set(orbMode="idle", status=self.t("nothing"), statusKind="info", level=0.0)
        elif kind == "heard":
            self._heard(str(data.get("text", "")))
        elif kind == "take":
            m.set(orbMode="listening", pageState={"step": "recording", "n": data["n"], "total": data["total"],
                                                  "prompt": data.get("prompt", "")})
        elif kind == "takes_done":
            m.set(orbMode="thinking", pageState={"step": "processing"}, level=0.0)
        elif kind == "calibrated":
            self._calibrated(data["result"])
        elif kind == "enrolled":
            self.wizard.set_value(True)
            m.set(orbMode="done", pageState={"step": "done", "text": self.t("enrolled")})
            self.voice.say(self.t("enrolled"))
        elif kind == "error":
            m.set(orbMode="error", status=str(data.get("message", "")), statusKind="error", busy=False)

    def _heard(self, text: str) -> None:
        m, w = self.model, self.wizard
        q = w.current
        if q is None:
            return
        m.set(status=self.t("heard", text=text), statusKind="info", orbMode="thinking")
        before = q.id
        it = w.handle_text(text)
        if it.action == "repeat":
            self._ask()
            return
        if it.action == "start":
            if q.page == "wake":
                self._start_wake()
            elif q.page == "voicelock":
                self._start_enroll()
            return
        if it.action in ("unclear", "invalid"):
            self._unclear += 1
            msg = label(it.value, self.env.lang) if it.action == "invalid" else self.t("unclear")
            m.set(status=msg, statusKind="error", orbMode="idle")
            again = self._unclear <= self.MAX_UNCLEAR and m.get("voiceMode")
            self.voice.ask(msg, speak=self._speaking(), listen=bool(again))
            return
        self._unclear = 0
        if it.action == "answer" and w.current is not None and w.current.id == before:
            self._refresh()        # stayed (e.g. set_value) - nothing to announce
            return
        self._ack = ("Okay. " if self.env.lang != "de" else "Alles klar. ") if it.action == "answer" else ""
        self._refresh()
        self._ask()

    def _calibrated(self, r: dict[str, Any]) -> None:
        self.wizard.set_value(r)
        key = "wake_ok" if r.get("detected") == r.get("total") else "wake_meh" if r.get("ok") else "wake_bad"
        text = self.t(key)
        variants = ", ".join(r.get("variants") or [])
        self.model.set(orbMode="done" if r.get("ok") else "error",
                       pageState={"step": "done", "detected": r.get("detected"), "total": r.get("total"),
                                  "ok": bool(r.get("ok")), "text": text,
                                  "variants": self.t("variants", variants=variants) if variants else ""})
        self.voice.ask(self.t("detected", detected=r.get("detected"), total=r.get("total")) + ". " + text,
                       speak=self._speaking(), listen=bool(self.model.get("voiceMode") and self.model.get("canListen")))

    # ------------------------------------------------------------------ actions from QML
    def _coerce(self, q: Question, value: Any) -> Any:
        """QML numbers come back as floats; map choice values back to the option's own value."""
        if q.options is not None and q.kind in ("choice", "multi"):
            opts = [o.value for o in q.options(self.env)]

            def back(v: Any) -> Any:
                return next((o for o in opts if o == v and type(o) is not bool), v)
            return [back(v) for v in value] if isinstance(value, list) else back(value)
        return value

    def _submit(self, value: Any) -> None:
        q = self.wizard.current
        if q is None:
            self.finish()
            return
        self.voice.cancel()
        err = self.wizard.answer(self._coerce(q, value))
        if err is not None:
            self.model.set(status=label(err, self.env.lang), statusKind="error")
            return
        self._unclear = 0
        self.model.set(status="", statusKind="info")
        self._refresh()
        self._ask()

    def _set_value(self, value: Any) -> None:
        q = self.wizard.current
        if q is None:
            return
        err = self.wizard.set_value(self._coerce(q, value))
        if err is not None:
            self.model.set(status=label(err, self.env.lang), statusKind="error")
        elif q.id == "language":
            self.model.set(ui=UI.get(self.env.lang, UI["en"]))

    def _skip(self) -> None:
        self.voice.cancel()
        self.wizard.skip()
        self._unclear = 0
        self.model.set(status="")
        self._refresh()
        self._ask()

    def _back(self) -> None:
        self.voice.cancel()
        if self.wizard.back():
            self.model.set(status="")
            self._refresh()
            self._ask()

    def _toggle_voice(self, on: bool) -> None:
        self.model.set(voiceMode=on)
        if not on:
            self.voice.cancel()
            self.model.set(orbMode="idle", status="")

    def _toggle_speak(self, on: bool) -> None:
        self.model.set(speakMode=on)
        if not on:
            self.voice.cancel()

    def _preview(self, voice_id: str) -> None:
        name = self.ctx.config.data.general.assistant_name
        user = self.ctx.config.data.general.user_name
        text = (f"Hallo{' ' + user if user else ''}, ich bin {name}. So klinge ich." if self.env.lang == "de"
                else f"Hi{' ' + user if user else ''}, I'm {name}. This is how I sound.")
        self.voice.say(text, voice=voice_id)

    def _start_wake(self, intro: str = "") -> None:
        if not self.voice.available()["wake"]:
            self.model.set(status=self.t("wake_wait"), statusKind="info")
            return
        self.voice.stop_monitor()
        self.model.set(pageState={"step": "starting"})
        self.voice.record_name(self.ctx.config.data.general.assistant_name, intro=intro if self._speaking() else "")

    def _start_enroll(self) -> None:
        if not self.voice.available()["lock"]:
            self.model.set(status=self.t("lock_wait"), statusKind="info")
            return
        self.model.set(pageState={"step": "starting"})
        self.voice.enroll(self.env.lang)

    def _select_mic(self, device: str) -> None:
        self.wizard.set_value(device)
        self.voice.start_monitor(device)

    def _suspend_hotkeys(self, on: bool) -> None:
        if self.suspend_hotkeys is not None:
            self.suspend_hotkeys(on)

    # ------------------------------------------------------------------ downloads
    @Slot(object)
    def _on_bus(self, e: Event) -> None:
        if e.topic == "setup.job_done" and str(e.data.get("job", "")).startswith("voice:"):
            had = (self.model.get("canSpeak"), self.model.get("canListen"))
            self._update_availability()
            self.voice.preload()
            if not had[0] and self.model.get("canSpeak") and self.model.get("orbMode") == "idle":
                self._ask()                     # speech output just arrived: read the current question
        self._update_download()

    def _update_download(self) -> None:
        if self.downloads is None:
            return
        st = self.downloads.status
        self.model.set(download=describe(st, self.env.lang), downloadPct=float(st.percent),
                       downloadActive=bool(st.running))

    # ------------------------------------------------------------------ the end
    def _close_requested(self) -> None:
        if self.wizard.at_end or self.wizard.done:
            self.finish()
            return
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setWindowTitle(self.t("close_title"))
        box.setText(self.t("close_title"))
        box.setInformativeText(self.t("close_text"))
        yes = box.addButton(self.t("close_yes"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.t("close_no"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.finish()

    def finish(self) -> None:
        if self._closing:
            return
        self._closing = True
        if not self.wizard.done:
            self.wizard.finish(apply_remaining=True)
        if self.suspend_hotkeys is not None:
            self.suspend_hotkeys(False)
        self.voice.close()
        for u in self._unsubs:
            u()
        self._unsubs = []
        if self.window is not None:
            self.window.setProperty("allowClose", True)
            self.window.close()
            self.window.deleteLater()
            self.window = None
        if self.engine is not None:
            self.engine.deleteLater()
            self.engine = None
        self.completed.emit()


__all__ = ["SetupModel", "SetupWindow", "UI"]
