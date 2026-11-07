"""The setup questions (spec: >= 15). Each has a default, can be skipped, and is applied right away.

Kinds and their value types:

* ``text`` -> str            * ``choice`` -> option value      * ``bool`` -> bool
* ``number`` -> float        * ``multi`` -> list of values     * ``form`` -> {field: str}
* ``matrix`` -> {row: value} * ``page`` -> special page (wake, mic, folders, hotkeys, voicelock, routines)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..permissions.policy import CAPABILITIES, LOCKED_RISKS, RISK_TITLES, Access, FolderRule, Level, Risk
from .env import WizardEnv

log = logging.getLogger(__name__)

T = dict[str, str]


def t(en: str, de: str) -> T:
    return {"en": en, "de": de}


@dataclass
class Option:
    value: Any
    label: T
    hint: T | None = None
    words: tuple[str, ...] = ()          # extra spoken forms (any language)


@dataclass
class Field:
    key: str
    label: T
    placeholder: T | None = None
    suggestions: list[str] = field(default_factory=list)
    multiline: bool = False
    max_len: int = 200


@dataclass
class Question:
    id: str
    kind: str
    title: T
    help: T = field(default_factory=lambda: t("", ""))
    say: T | None = None                                     # spoken version (default: title + options)
    default: Callable[[WizardEnv], Any] = lambda env: None
    apply: Callable[[WizardEnv, Any], object] = lambda env, value: None
    options: Callable[[WizardEnv], list[Option]] | None = None
    validate: Callable[[WizardEnv, Any], T | None] | None = None
    when: Callable[[WizardEnv, Callable[[str], Any]], bool] | None = None   # (env, value_of) -> show?
    page: str = ""
    preview: bool = False                                    # choice: a "play" button per option (voices)
    allow_other: bool = False                                # choice: free text allowed too
    number: tuple[float, float, float, T] | None = None      # min, max, step, unit
    fields: Callable[[WizardEnv], list[Field]] | None = None
    rows: Callable[[WizardEnv], list[Option]] | None = None  # matrix rows (value = row key)
    columns: Callable[[WizardEnv, str], list[Option]] | None = None  # matrix: options for a row
    name_answer: bool = False                                # text answer is a name ("call me X" -> "X")
    needs: tuple[str, ...] = ()                              # mic | kws | stt | tts | speaker


# ----------------------------------------------------------------------------- helpers

def _cfg(env: WizardEnv, key: str) -> Any:
    return env.ctx.config.get(key)


def _set(*keys: str) -> Callable[[WizardEnv, Any], None]:
    """apply = write the answer to these config keys."""
    def apply(env: WizardEnv, value: Any) -> None:
        env.ctx.config.update({k: value for k in keys})
    return apply


def _installed(env: WizardEnv, candidates: list[str]) -> list[str]:
    """Which of ``candidates`` are installed (matched against the app index by name)."""
    try:
        names = [n.casefold() for n in env.app_names()]
    except Exception as e:
        log.debug("app list unavailable: %s", e)
        names = []
    found = []
    for c in candidates:
        key = c.casefold()
        if any(n == key or n.startswith(key + " ") or key in n for n in names):
            found.append(c)
    return found


MUSIC_APPS = ["Spotify", "Apple Music", "iTunes", "Amazon Music", "Deezer", "TIDAL", "YouTube Music", "foobar2000",
              "MusicBee", "AIMP", "Winamp", "VLC media player", "Windows Media Player", "Media Player"]
BROWSERS = ["Microsoft Edge", "Google Chrome", "Firefox", "Brave", "Opera GX", "Opera", "Vivaldi", "Arc",
            "Zen Browser", "LibreWolf", "Chromium"]
DESKTOP_APPS = ["Spotify", "Discord", "Steam", "Microsoft Teams", "Slack", "WhatsApp", "Telegram", "Signal",
                "Outlook", "Thunderbird", "OBS Studio", "Epic Games Launcher", "Battle.net", "EA app",
                "Ubisoft Connect", "Zoom"]
APP_SLOTS: list[tuple[str, T, list[str], tuple[str, ...]]] = [
    ("mail", t("E-mail", "E-Mail"), ["Outlook", "Thunderbird", "Mail"], ("mail", "email", "e-mail", "mails")),
    ("notes", t("Notes", "Notizen"), ["OneNote", "Obsidian", "Notion", "Evernote", "Notepad++", "Notepad"],
     ("notes", "notizen", "notiz app", "notes app")),
    ("code", t("Code editor", "Code-Editor"),
     ["Visual Studio Code", "Visual Studio", "PyCharm", "IntelliJ IDEA", "Cursor", "Sublime Text", "Notepad++"],
     ("code", "code editor", "ide", "programmieren")),
    ("chat", t("Chat", "Chat"), ["Discord", "WhatsApp", "Telegram", "Signal", "Slack", "Microsoft Teams"],
     ("chat", "messenger")),
    ("calls", t("Video calls", "Videoanrufe"), ["Zoom", "Microsoft Teams", "Skype", "Webex", "Discord"],
     ("video call", "videocall", "meeting", "videoanruf", "konferenz")),
]

PERSONALITIES = [
    Option("friendly", t("Friendly", "Freundlich"), t("Warm and helpful", "Warm und hilfsbereit"), ("nice", "nett")),
    Option("professional", t("Professional", "Professionell"), t("Precise, no small talk", "Präzise, kein Smalltalk"),
           ("formal", "sachlich", "business")),
    Option("witty", t("Witty", "Witzig"), t("Friendly with a bit of humour", "Freundlich mit etwas Humor"),
           ("funny", "humorous", "lustig", "humorvoll")),
    Option("calm", t("Calm", "Ruhig"), t("Relaxed and reassuring", "Entspannt und gelassen"),
           ("relaxed", "gelassen", "entspannt")),
]
LENGTHS = [
    Option("short", t("Short", "Kurz"), t("One or two sentences", "Ein, zwei Sätze"), ("brief", "knapp")),
    Option("normal", t("Normal", "Normal"), t("A few sentences", "Ein paar Sätze"), ("medium", "mittel")),
    Option("detailed", t("Detailed", "Ausführlich"), t("Complete answers when useful", "Vollständig, wenn sinnvoll"),
           ("long", "lang", "detailliert")),
]
VOICES = [Option(v, t(f"Voice {v[1]} ({'female' if v[0] == 'F' else 'male'})",
                      f"Stimme {v[1]} ({'weiblich' if v[0] == 'F' else 'männlich'})"),
                 words=(f"{'female' if v[0] == 'F' else 'male'} {v[1]}", f"{'weiblich' if v[0] == 'F' else 'männlich'} {v[1]}",
                        f"{v[0].lower()} {v[1]}", f"{v[0].lower()}{v[1]}"))
          for v in ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5")]
ACCESS_OPTIONS = [
    Option(Access.FULL.value, t("Full access", "Vollzugriff"), t("Read + change without asking", "Lesen + ändern ohne Nachfrage")),
    Option(Access.READ.value, t("Read only", "Nur lesen"), t("Never changed", "Wird nie verändert")),
    Option(Access.ASK.value, t("Always ask", "Immer fragen"), t("Even for reading", "Auch beim Lesen")),
    Option(Access.NONE.value, t("Blocked", "Gesperrt"), t("Never touched", "Wird nie angefasst")),
]
LEVEL_LABELS = {Level.ALLOW: t("Allow", "Erlauben"), Level.ASK: t("Ask", "Fragen"), Level.BLOCK: t("Block", "Sperren")}

#: German titles for capabilities and risks (the English ones live in permissions/policy.py)
CAP_DE = {
    "apps.open": "Apps, Dateien & Webseiten öffnen", "apps.windows": "Fenster verwalten", "apps.close": "Apps schließen",
    "desktops": "Virtuelle Desktops", "media": "Mediensteuerung", "system.volume": "Lautstärke",
    "system.display": "Helligkeit", "system.lock": "PC sperren", "system.power": "Ein/Aus (Ruhezustand, Neustart …)",
    "system.clipboard": "Zwischenablage", "system.notify": "Timer & Erinnerungen", "screen.capture": "Bildschirm sehen",
    "input": "Maus & Tastatur", "files.read": "Dateien lesen", "files.write": "Dateien erstellen",
    "files.modify": "Dateien ändern (kopieren, verschieben, umbenennen)", "files.delete": "Dateien löschen (Papierkorb)",
    "terminal.read": "Nur-Lese-Befehle", "terminal.run": "Terminal-Befehle", "software.install": "Software installieren",
    "system.admin": "Administrator-Rechte", "memory": "Gedächtnis (Fakten merken)", "routines": "Routinen",
}
RISK_DE = {
    Risk.DELETE: "Dateien löschen (in den Papierkorb)", Risk.MOVE: "Dateien verschieben oder umbenennen",
    Risk.OVERWRITE: "Vorhandene Dateien überschreiben", Risk.TERMINAL_CHANGE: "Terminal-Befehle, die etwas ändern können",
    Risk.INSTALL: "Software installieren oder deinstallieren", Risk.CLOSE_UNSAVED: "Apps mit ungespeicherter Arbeit schließen",
    Risk.FORCE_CLOSE: "Apps zwangsweise beenden", Risk.ADMIN: "Alles mit Administrator-Rechten",
    Risk.POWER: "Herunterfahren, Neustart, Ruhezustand, Abmelden",
}


def _validate_name(env: WizardEnv, value: Any) -> T | None:
    from ..voice.wakeword import ascii_upper

    name = str(value or "").strip()
    letters = ascii_upper(name).replace(" ", "")
    if not 2 <= len(name) <= 24 or len(letters) < 2:
        return t("Please choose a name with 2 to 24 letters.", "Bitte wähle einen Namen mit 2 bis 24 Buchstaben.")
    if len(name.split()) > 2:
        return t("One or two words, please.", "Bitte ein oder zwei Wörter.")
    return None


def _apply_name(env: WizardEnv, value: Any) -> None:
    name = " ".join(str(value).split())
    old = env.ctx.config.data.general.assistant_name
    values: dict[str, Any] = {"general.assistant_name": name}
    if name.casefold() != old.casefold():   # a calibration for another name is useless
        values.update({"voice.wake_variants": [], "voice.wake_sensitivity": 0.5})
    env.ctx.config.update(values)


def _apply_calibration(env: WizardEnv, value: Any) -> None:
    if isinstance(value, dict) and "sensitivity" in value:
        env.ctx.config.update({"voice.wake_sensitivity": float(value["sensitivity"]),
                               "voice.wake_variants": [str(v) for v in value.get("variants", [])][:3]})


def _apply_voice(env: WizardEnv, value: Any) -> None:
    env.ctx.config.update({"voice.tts_voice_en": value, "voice.tts_voice_de": value})


def _music_options(env: WizardEnv) -> list[Option]:
    found = _installed(env, MUSIC_APPS)
    current = _cfg(env, "apps.music_app")
    names = found + ([current] if current and current not in found else [])
    if not names:
        names = ["Spotify"]
    return [Option(n, t(n, n)) for n in names]


def _browser_options(env: WizardEnv) -> list[Option]:
    found = _installed(env, BROWSERS)
    current = _cfg(env, "apps.browser")
    opts = [Option("", t("Windows default browser", "Standardbrowser von Windows"), words=("default", "standard"))]
    for n in found + ([current] if current and current not in found else []):
        opts.append(Option(n, t(n, n)))
    return opts


def _app_fields(env: WizardEnv) -> list[Field]:
    out = []
    for key, label, candidates, _words in APP_SLOTS:
        found = _installed(env, candidates)
        out.append(Field(key, label, t("e.g. " + (found or candidates)[0], "z. B. " + (found or candidates)[0]),
                         suggestions=found or candidates))
    return out


def _app_defaults(env: WizardEnv) -> dict[str, str]:
    aliases = {k.casefold(): v for k, v in (_cfg(env, "apps.aliases") or {}).items()}
    return {key: aliases.get(words[0], "") for key, _label, _c, words in APP_SLOTS}


def _apply_apps(env: WizardEnv, value: Any) -> None:
    aliases = dict(_cfg(env, "apps.aliases") or {})
    for key, _label, _cands, words in APP_SLOTS:
        app = str((value or {}).get(key, "")).strip()
        for w in words:
            if app:
                aliases[w] = app
            elif w in aliases:
                del aliases[w]
    env.ctx.config.set("apps.aliases", aliases)


def _folder_defaults(env: WizardEnv) -> list[dict[str, str]]:
    if env.policy is None:
        return []
    return [{"path": f.path, "access": str(f.access), "note": f.note} for f in env.policy.policy.folders]


def _apply_folders(env: WizardEnv, value: Any) -> None:
    if env.policy is None:
        return
    rules = []
    for item in value or []:
        try:
            rules.append(FolderRule(str(item["path"]).strip(), Access(str(item["access"])), str(item.get("note", ""))))
        except (KeyError, ValueError):
            continue
    env.policy.policy.folders = [r for r in rules if r.path]
    env.policy.save()
    env.policy_changed()


def _cap_rows(env: WizardEnv) -> list[Option]:
    return [Option(c.id, t(c.title, CAP_DE.get(c.id, c.title)), t(c.description, "")) for c in CAPABILITIES]


def _cap_columns(env: WizardEnv, cap_id: str) -> list[Option]:
    cap = next(c for c in CAPABILITIES if c.id == cap_id)
    return [Option(lvl.value, LEVEL_LABELS[lvl]) for lvl in cap.choices]


def _cap_defaults(env: WizardEnv) -> dict[str, str]:
    if env.policy is None:
        return {c.id: c.default.value for c in CAPABILITIES}
    return {c.id: env.policy.policy.level(c.id).value for c in CAPABILITIES}


def _apply_caps(env: WizardEnv, value: Any) -> None:
    if env.policy is None:
        return
    for cap in CAPABILITIES:
        v = (value or {}).get(cap.id)
        if v is None:
            continue
        try:
            lvl = Level(v)
        except ValueError:
            continue
        if lvl in cap.choices and env.policy.policy.level(cap.id) is not lvl:
            env.policy.set_level(cap.id, lvl)


def _risk_options(env: WizardEnv) -> list[Option]:
    return [Option(r.value, t(RISK_TITLES[r], RISK_DE[r])) for r in Risk if r not in LOCKED_RISKS]


def _risk_defaults(env: WizardEnv) -> list[str]:
    if env.policy is None:
        return [r.value for r in Risk if r not in LOCKED_RISKS]
    return [r.value for r in Risk if r not in LOCKED_RISKS and r in env.policy.policy.always_confirm]


def _apply_risks(env: WizardEnv, value: Any) -> None:
    if env.policy is None:
        return
    chosen = set(value or [])
    for r in Risk:
        if r in LOCKED_RISKS:
            continue
        on = r.value in chosen
        if (r in env.policy.policy.always_confirm) != on:
            env.policy.set_always_confirm(r, on)


def _desktop_rows(env: WizardEnv) -> list[Option]:
    rules = _cfg(env, "desktops.rules") or {}
    names = _installed(env, DESKTOP_APPS)
    for k in rules:
        if not any(k.casefold() == n.casefold() for n in names):
            names.append(k[:1].upper() + k[1:])
    return [Option(n.casefold(), t(n, n)) for n in names]


def _desktop_columns(env: WizardEnv, row: str) -> list[Option]:
    return [Option("current", t("This desktop", "Aktueller Desktop")),
            Option("assistant", t("Assistant desktop", "Assistenten-Desktop"))]


def _desktop_defaults(env: WizardEnv) -> dict[str, str]:
    rules = {k.casefold(): v for k, v in (_cfg(env, "desktops.rules") or {}).items()}
    default = _cfg(env, "desktops.default_rule") or "current"
    return {o.value: rules.get(o.value, default) for o in _desktop_rows(env)}


def _apply_desktop_rules(env: WizardEnv, value: Any) -> None:
    rules = {k: v for k, v in (value or {}).items() if v in ("current", "assistant")}
    env.ctx.config.set("desktops.rules", rules)


def _hotkey_defaults(env: WizardEnv) -> dict[str, str]:
    return {"push_to_talk": _cfg(env, "hotkeys.push_to_talk"), "panic": _cfg(env, "hotkeys.panic"),
            "mode": _cfg(env, "voice.push_to_talk_mode")}


def _validate_hotkeys(env: WizardEnv, value: Any) -> T | None:
    from ..win.hotkeys import parse_hotkey

    v = value or {}
    seen = {}
    for key in ("push_to_talk", "panic"):
        spec = str(v.get(key, "")).strip()
        if not spec:
            continue
        try:
            parsed = parse_hotkey(spec)
        except ValueError as e:
            return t(f"{spec}: {e}", f"{spec}: {e}")
        if parsed in seen.values():
            return t("Push-to-talk and panic need different keys.", "Push-to-Talk und Notaus brauchen verschiedene Tasten.")
        seen[key] = parsed
    if not str(v.get("panic", "")).strip():
        return t("The panic hotkey can't be empty - it is your emergency stop.",
                 "Die Notaus-Taste darf nicht leer sein - sie ist dein Notstopp.")
    return None


def _apply_hotkeys(env: WizardEnv, value: Any) -> None:
    v = value or {}
    env.ctx.config.update({"hotkeys.push_to_talk": str(v.get("push_to_talk", "")).strip().lower(),
                           "hotkeys.panic": str(v.get("panic", "")).strip().lower() or "ctrl+alt+escape",
                           "voice.push_to_talk_mode": "hold" if v.get("mode") == "hold" else "press"})


def _model_options(env: WizardEnv) -> list[Option]:
    from ..llm.models import get_model

    def size(mid: str) -> str:
        try:
            return f"{get_model(mid).download_bytes / 2**30:.1f} GB"
        except Exception:
            return "?"

    smart, fast = _cfg(env, "llm.model_smart"), _cfg(env, "llm.model_fast")
    vram = f"{env.vram_mb / 1024:.0f} GB" if env.vram_mb else "-"
    return [
        Option("auto", t("Automatic (recommended)", "Automatisch (empfohlen)"),
               t(f"Best model your GPU ({vram} VRAM) runs fast enough", f"Bestes Modell, das deine GPU ({vram} VRAM) "
                 "schnell genug schafft"), ("automatic", "automatisch", "auto", "recommended", "empfohlen")),
        Option("smart", t("Smart", "Schlau"), t(f"Better answers, a bit slower ({size(smart)})",
                                                f"Bessere Antworten, etwas langsamer ({size(smart)})"),
               ("smarter", "schlauer", "intelligent", "quality", "qualität", "9b", "nine b")),
        Option("fast", t("Fast", "Schnell"), t(f"Quicker replies, less VRAM ({size(fast)})",
                                               f"Schnellere Antworten, weniger VRAM ({size(fast)})"),
               ("speed", "faster", "quick", "schneller", "tempo", "4b", "four b")),
    ]


def _retention_options(env: WizardEnv) -> list[Option]:
    return [Option(7, t("1 week", "1 Woche"), words=("week", "woche", "seven days", "sieben tage")),
            Option(30, t("1 month", "1 Monat"), words=("month", "monat", "thirty days", "30 days")),
            Option(90, t("3 months", "3 Monate"), words=("three months", "drei monate", "90 days")),
            Option(365, t("1 year", "1 Jahr"), words=("year", "jahr", "twelve months")),
            Option(0, t("Forever", "Für immer"), words=("never delete", "keep forever", "nie löschen", "immer"))]


ROUTINE_TEMPLATES: list[dict[str, Any]] = [
    {"key": "gaming", "name": t("Gaming mode", "Spielmodus"), "apps": ["Steam", "Discord"], "volume": 40,
     "extra": []},
    {"key": "work", "name": t("Work mode", "Arbeitsmodus"), "apps": ["Outlook", "Microsoft Teams"], "volume": 30,
     "extra": []},
    {"key": "music", "name": t("Music time", "Musikzeit"), "apps": [], "volume": 35,
     "extra": [{"tool": "media", "args": {"action": "play"}}]},
    {"key": "night", "name": t("Good night", "Gute Nacht"), "apps": [], "volume": None,
     "extra": [{"tool": "media", "args": {"action": "pause"}}, {"tool": "power", "args": {"action": "lock"}}]},
]


def _routine_defaults(env: WizardEnv) -> list[dict[str, Any]]:
    lang = env.lang
    existing = {r.name.casefold() for r in env.routines.all()} if env.routines is not None else set()
    out = []
    for tpl in ROUTINE_TEMPLATES:
        name = tpl["name"][lang]
        installed = _installed(env, tpl["apps"]) if tpl["apps"] else []
        out.append({"key": tpl["key"], "name": name, "enabled": name.casefold() in existing,
                    "apps": ", ".join(installed or tpl["apps"]), "volume": tpl["volume"]})
    return out


def routine_steps(item: dict[str, Any]) -> list[dict[str, Any]]:
    tpl = next((x for x in ROUTINE_TEMPLATES if x["key"] == item.get("key")), None)
    steps: list[dict[str, Any]] = []
    for app in [a.strip() for a in str(item.get("apps", "")).split(",") if a.strip()][:6]:
        steps.append({"tool": "open_app", "args": {"name": app}})
    vol = item.get("volume")
    if vol is not None and str(vol) != "":
        steps.append({"tool": "volume", "args": {"level": max(0, min(100, int(float(vol))))}})
    steps += list(tpl["extra"]) if tpl else []
    return steps


def _apply_routines(env: WizardEnv, value: Any) -> None:
    if env.routines is None:
        return
    from ..storage.routines import Routine

    for item in value or []:
        name = str(item.get("name", "")).strip()
        if not item.get("enabled") or not name:
            continue
        steps = routine_steps(item)
        if steps:
            env.routines.save(Routine(name=name, steps=steps, description="created in setup"))


def _not(qid: str) -> Callable[[WizardEnv, Callable[[str], Any]], bool]:
    """Show only if question ``qid`` was not answered/defaulted with False."""
    return lambda env, value_of: value_of(qid) is not False


# ----------------------------------------------------------------------------- the questions

QUESTIONS: list[Question] = [
    Question("language", "choice", t("Which language should I speak? / Welche Sprache?",
                                     "Welche Sprache soll ich sprechen? / Language?"),
             t("You can switch later in the settings.", "Du kannst das später in den Einstellungen ändern."),
             say=t("Which language should I speak? English or German?", "Welche Sprache soll ich sprechen? Deutsch oder Englisch?"),
             default=lambda env: env.ctx.config.data.general.language if not env.first_run else env.language_guess(),
             apply=_set("general.language"),
             options=lambda env: [Option("en", t("English", "Englisch"), words=("english", "englisch")),
                                  Option("de", t("German (Deutsch)", "Deutsch"), words=("german", "deutsch"))]),
    Question("assistant_name", "text", t("What should my name be?", "Wie soll ich heißen?"),
             t("This is also the wake word - you say it to talk to me. Names with 2-3 syllables work best "
               "(Jarvis, Nova, Friday).", "Das ist auch das Aktivierungswort - du sagst es, um mit mir zu sprechen. "
               "Namen mit 2-3 Silben funktionieren am besten (Jarvis, Nova, Friday)."),
             default=lambda env: env.ctx.config.data.general.assistant_name,
             apply=_apply_name, validate=_validate_name, name_answer=True),
    Question("wake_test", "page", t("Say my name three times", "Sag dreimal meinen Namen"),
             t("I'll learn how you say it, so I react reliably - and not to similar words.",
               "Ich lerne, wie du ihn aussprichst - damit ich zuverlässig reagiere und nicht auf ähnliche Wörter."),
             page="wake", apply=_apply_calibration, needs=("mic", "kws")),
    Question("user_name", "text", t("What should I call you?", "Wie soll ich dich nennen?"),
             default=lambda env: env.ctx.config.data.general.user_name or env.user_name_guess(),
             apply=_set("general.user_name"), name_answer=True),
    Question("profile", "form", t("Tell me a bit about yourself (optional)", "Erzähl mir etwas über dich (optional)"),
             t("I keep this in mind when I help you. It stays on this PC.",
               "Das behalte ich im Hinterkopf, wenn ich dir helfe. Es bleibt auf diesem PC."),
             say=t("Tell me a bit about yourself, for example your job or hobbies. Or say skip.",
                   "Erzähl mir etwas über dich, zum Beispiel deinen Beruf oder deine Hobbys. Oder sag überspringen."),
             fields=lambda env: [Field("about", t("About you", "Über dich"),
                                       t("e.g. I'm a student, I like football and jazz",
                                         "z. B. Ich studiere Informatik, mag Fußball und Jazz"), multiline=True, max_len=400),
                                 Field("location", t("Where you live", "Wohnort"),
                                       t("e.g. Hamburg, Germany", "z. B. Hamburg"), max_len=80)],
             default=lambda env: {"about": env.ctx.config.data.profile.about,
                                  "location": env.ctx.config.data.profile.location},
             apply=lambda env, v: env.ctx.config.update({"profile.about": str((v or {}).get("about", "")).strip()[:400],
                                                         "profile.location": str((v or {}).get("location", "")).strip()[:80]})),
    Question("voice_replies", "bool", t("Should I answer out loud?", "Soll ich laut antworten?"),
             t("Answers are always shown in the popup too.", "Antworten stehen außerdem immer im Popup."),
             default=lambda env: env.ctx.config.data.voice.tts_enabled, apply=_set("voice.tts_enabled")),
    Question("voice", "choice", t("Pick my voice", "Such mir eine Stimme aus"),
             t("Press ▶ to hear each one.", "Drück ▶, um sie anzuhören."),
             say=t("Pick my voice. Press play to hear each one.", "Such mir eine Stimme aus. Drück auf Abspielen, um sie zu hören."),
             options=lambda env: VOICES, preview=True, needs=("tts",),
             default=lambda env: env.ctx.config.data.voice.tts_voice_de if env.lang == "de"
             else env.ctx.config.data.voice.tts_voice_en,
             apply=_apply_voice, when=_not("voice_replies")),
    Question("personality", "choice", t("What personality should I have?", "Welche Persönlichkeit soll ich haben?"),
             options=lambda env: PERSONALITIES, default=lambda env: env.ctx.config.data.general.personality,
             apply=_set("general.personality")),
    Question("answer_length", "choice", t("How long should my answers be?", "Wie lang sollen meine Antworten sein?"),
             options=lambda env: LENGTHS, default=lambda env: env.ctx.config.data.general.answer_length,
             apply=_set("general.answer_length")),
    Question("microphone", "page", t("Which microphone should I use?", "Welches Mikrofon soll ich benutzen?"),
             t("Say something - the bar shows how well I hear you.", "Sag etwas - der Balken zeigt, wie gut ich dich höre."),
             page="mic", default=lambda env: env.ctx.config.data.voice.input_device,
             apply=_set("voice.input_device"), needs=("mic",)),
    Question("music_app", "choice", t("Which app do you use for music?", "Mit welcher App hörst du Musik?"),
             t("For “play …” requests.", "Für „spiel …“."), options=_music_options, allow_other=True,
             default=lambda env: (_installed(env, MUSIC_APPS) or [env.ctx.config.data.apps.music_app])[0]
             if env.first_run else env.ctx.config.data.apps.music_app,
             apply=_set("apps.music_app")),
    Question("browser", "choice", t("Which browser should I use?", "Welchen Browser soll ich benutzen?"),
             options=_browser_options, allow_other=True, default=lambda env: env.ctx.config.data.apps.browser,
             apply=_set("apps.browser")),
    Question("default_apps", "form", t("Your apps for everyday things", "Deine Apps für den Alltag"),
             t("So “open my mail” or “start a video call” opens the right app. Leave empty if you don't use it.",
               "Damit „öffne meine Mails“ oder „starte einen Videoanruf“ die richtige App öffnet. Leer lassen, "
               "wenn du es nicht nutzt."),
             say=t("Which apps do you use for mail, notes, code, chat and video calls? You can type them or say skip.",
                   "Welche Apps nutzt du für Mails, Notizen, Code, Chat und Videoanrufe? Tipp sie ein oder sag überspringen."),
             fields=_app_fields, default=_app_defaults, apply=_apply_apps),
    Question("folders", "page", t("Which folders may I use?", "Welche Ordner darf ich benutzen?"),
             t("By default I can read all your drives; changes always need your OK. Windows and program folders "
               "are read-only, password and key stores are blocked. Add folders you want to trust or block.",
               "Standard: Ich darf alle Laufwerke lesen; Änderungen brauchen immer dein OK. Windows- und Programmordner "
               "sind schreibgeschützt, Passwort- und Schlüsselspeicher gesperrt. Füge Ordner hinzu, denen du vertraust "
               "oder die gesperrt sein sollen."),
             say=t("Which folders may I use? The defaults are safe - you can add folders to trust or block, or say next.",
                   "Welche Ordner darf ich benutzen? Die Standards sind sicher - du kannst Ordner freigeben oder sperren, "
                   "oder sag weiter."),
             page="folders", default=_folder_defaults, apply=_apply_folders),
    Question("permissions", "matrix", t("What may I do on my own?", "Was darf ich selbst entscheiden?"),
             t("Allow = just do it, Ask = ask you first, Block = never.",
               "Erlauben = einfach machen, Fragen = vorher fragen, Sperren = nie."),
             say=t("What may I do on my own? The defaults ask before anything that changes your files or system. "
                   "Say next to keep them.", "Was darf ich selbst entscheiden? Standardmäßig frage ich vor allem, was "
                   "Dateien oder das System ändert. Sag weiter, um das zu behalten."),
             rows=_cap_rows, columns=_cap_columns, default=_cap_defaults, apply=_apply_caps),
    Question("confirm_actions", "multi", t("What should I always ask about?", "Wobei soll ich immer nachfragen?"),
             t("Even if the capability is allowed. Administrator actions always ask (via the Windows UAC prompt).",
               "Auch wenn die Fähigkeit erlaubt ist. Administrator-Aktionen fragen immer (über die Windows-UAC-Abfrage)."),
             say=t("Which actions should always need your OK? By default all risky ones do. Say next to keep that.",
                   "Welche Aktionen sollen immer dein OK brauchen? Standardmäßig alle riskanten. Sag weiter, um das zu behalten."),
             options=_risk_options, default=_risk_defaults, apply=_apply_risks),
    Question("desktops_enabled", "bool", t("Use a separate “Assistant” desktop?", "Einen eigenen „Assistent“-Desktop nutzen?"),
             t("Apps like Spotify can open on their own virtual desktop, so your current desktop isn't disturbed.",
               "Apps wie Spotify können auf einem eigenen virtuellen Desktop starten, damit dein aktueller Desktop "
               "nicht gestört wird."),
             default=lambda env: env.ctx.config.data.desktops.enabled, apply=_set("desktops.enabled")),
    Question("desktop_rules", "matrix", t("Where should these apps open?", "Wo sollen diese Apps starten?"),
             say=t("Where should these apps open? Say next to keep the suggestions.",
                   "Wo sollen diese Apps starten? Sag weiter, um die Vorschläge zu behalten."),
             rows=_desktop_rows, columns=_desktop_columns, default=_desktop_defaults, apply=_apply_desktop_rules,
             when=_not("desktops_enabled")),
    Question("autostart", "bool", t("Start me automatically when you sign in?", "Soll ich beim Anmelden automatisch starten?"),
             t("I only listen for my name in the background (tiny CPU use).",
               "Im Hintergrund höre ich nur auf meinen Namen (kaum CPU-Last)."),
             default=lambda env: True if env.first_run else env.ctx.config.data.general.autostart,
             apply=lambda env, v: (env.ctx.config.set("general.autostart", bool(v)), env.ctx.apply_autostart())),
    Question("hotkeys", "page", t("Keyboard shortcuts", "Tastenkürzel"),
             t("Push-to-talk: talk without saying my name. Panic: stops everything at once and releases mouse and "
               "keyboard.", "Push-to-Talk: sprechen, ohne meinen Namen zu sagen. Notaus: stoppt sofort alles und "
               "gibt Maus und Tastatur frei."),
             say=t("These are my keyboard shortcuts for push to talk and for an emergency stop. Say next to keep them.",
                   "Das sind meine Tastenkürzel für Push-to-Talk und den Notaus. Sag weiter, um sie zu behalten."),
             page="hotkeys", default=_hotkey_defaults, validate=_validate_hotkeys, apply=_apply_hotkeys),
    Question("silence_timeout", "number", t("How long should I wait when you pause?", "Wie lange soll ich bei Pausen warten?"),
             t("When you're quiet this long, I start working. Longer = more time to think while talking.",
               "Wenn du so lange still bist, lege ich los. Länger = mehr Zeit zum Nachdenken beim Sprechen."),
             number=(0.5, 3.0, 0.1, t("seconds", "Sekunden")),
             default=lambda env: env.ctx.config.data.voice.silence_timeout_s, apply=_set("voice.silence_timeout_s")),
    Question("game_mode", "bool", t("Free the graphics card when you play a game?", "Grafikkarte beim Spielen freigeben?"),
             t("Game mode unloads the language model when a fullscreen game runs and reloads it afterwards.",
               "Der Spielmodus entlädt das Sprachmodell, solange ein Vollbild-Spiel läuft, und lädt es danach wieder."),
             default=lambda env: env.ctx.config.data.performance.game_mode_auto, apply=_set("performance.game_mode_auto")),
    Question("model", "choice", t("Speed or smarts?", "Tempo oder Köpfchen?"),
             t("Which language model to use. It's downloaded in the background.",
               "Welches Sprachmodell ich nutze. Es wird im Hintergrund heruntergeladen."),
             say=t("Should I be fast, smart, or decide automatically?", "Soll ich schnell sein, schlau, oder automatisch entscheiden?"),
             options=_model_options, default=lambda env: env.ctx.config.data.llm.profile, apply=_set("llm.profile")),
    Question("save_conversations", "bool", t("Save our conversations?", "Unsere Gespräche speichern?"),
             t("As text on this PC, searchable in the history. Audio is never saved.",
               "Als Text auf diesem PC, durchsuchbar im Verlauf. Audio wird nie gespeichert."),
             default=lambda env: env.ctx.config.data.privacy.save_conversations,
             apply=_set("privacy.save_conversations")),
    Question("retention", "choice", t("How long should I keep them?", "Wie lange soll ich sie aufbewahren?"),
             options=_retention_options, default=lambda env: env.ctx.config.data.privacy.retention_days,
             apply=_set("privacy.retention_days"), when=_not("save_conversations")),
    Question("voice_lock", "page", t("Voice lock (optional)", "Stimmsperre (optional)"),
             t("I'll only react to your voice - not to videos or other people. You read three short sentences.",
               "Ich reagiere dann nur auf deine Stimme - nicht auf Videos oder andere Leute. Du liest drei kurze Sätze vor."),
             say=t("Do you want a voice lock, so I only react to your voice? Say yes to set it up, or no.",
                   "Möchtest du eine Stimmsperre, damit ich nur auf deine Stimme reagiere? Sag ja zum Einrichten, oder nein."),
             page="voicelock", default=lambda env: env.ctx.config.data.voice.voice_lock,
             apply=_set("voice.voice_lock"), needs=("mic", "speaker")),
    Question("routines", "page", t("Routines (optional)", "Routinen (optional)"),
             t("One sentence starts several actions, e.g. “gaming mode”. Pick some to start with - you can create "
               "more any time: “create a routine called …”.",
               "Ein Satz startet mehrere Aktionen, z. B. „Spielmodus“. Wähl ein paar für den Anfang - weitere kannst "
               "du jederzeit anlegen: „erstelle eine Routine namens …“."),
             say=t("Would you like some routines? Pick the ones you like, or say next.",
                   "Möchtest du ein paar Routinen? Wähl die aus, die dir gefallen, oder sag weiter."),
             page="routines", default=_routine_defaults, apply=_apply_routines),
]

BY_ID = {q.id: q for q in QUESTIONS}


def label(text: T | None, lang: str) -> str:
    if not text:
        return ""
    return text.get(lang) or text.get("en", "")


def spoken(q: Question, env: WizardEnv) -> str:
    """What the assistant says for this question (title + short choice list)."""
    lang = env.lang
    if q.say:
        return label(q.say, lang)
    text = label(q.title, lang)
    if q.kind == "choice" and q.options is not None and not q.preview:
        opts = [label(o.label, lang) for o in q.options(env)][:5]
        if len(opts) > 1:
            joiner = " oder " if lang == "de" else " or "
            text += " " + ", ".join(opts[:-1]) + joiner + opts[-1] + "?"
    return text


__all__ = ["BY_ID", "Field", "Option", "QUESTIONS", "Question", "ROUTINE_TEMPLATES", "label", "routine_steps",
           "spoken", "t"]
