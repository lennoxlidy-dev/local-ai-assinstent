"""Fast path: anchored EN/DE patterns for the most common commands, no LLM involved.

Precision over recall: every pattern matches the *whole* (normalized) utterance,
and slot values are validated (e.g. the app must exist) - anything uncertain
falls through to the LLM, which can ask back. Target: < 1 s end to end.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..tools.base import ToolResult
from ..tools.system import parse_duration, parse_time_of_day
from .numbers import spoken_duration, words_to_digits

Reply = Callable[[ToolResult, str], str]   # (result, language) -> spoken text


@dataclass
class FastIntent:
    intent: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    reply: Reply | None = None
    answer: str | None = None         # answered locally (time/date)
    action: str | None = None         # "cancel"
    fallback: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # tried if the tool fails


# ----------------------------------------------------------------------------- normalization

_FILLER_START = re.compile(
    r"^((hey|hi|ok|okay|hallo|so|and|und|also|äh|ähm|uh|um|please|bitte|now|jetzt|mal|doch|"
    r"can you|could you|would you|will you|would you please|can you please|could you please|"
    r"kannst du|könntest du|würdest du|kannst du bitte|könntest du bitte|i want you to|ich möchte dass du|"
    r"i'd like you to|go ahead and)\s+)+")
_FILLER_END = re.compile(r"(\s+(please|bitte|for me|für mich|mal|now|jetzt|thanks|thank you|danke))+$")


def normalize(text: str, names: list[str]) -> str:
    t = text.lower().replace("’", "'").replace("´", "'")  # lower(), not casefold(): keeps "ß"
    t = re.sub(r"[“”\"„!?¿¡.,;…]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for n in sorted({x.lower() for x in names if x}, key=len, reverse=True):
        t = re.sub(rf"^((hey|hi|ok|okay|hallo)\s+)?{re.escape(n)}\b\s*", "", t)
        t = re.sub(rf"\s*\b{re.escape(n)}$", "", t)
    for _ in range(3):
        t2 = _FILLER_END.sub("", _FILLER_START.sub("", t)).strip()
        if t2 == t:
            break
        t = t2
    return words_to_digits(t)


# ----------------------------------------------------------------------------- replies

def _r(en: str, de: str) -> Reply:
    return lambda res, lang: (de if lang == "de" else en).format(**(res.data or {}))


def _volume_reply(res: ToolResult, lang: str) -> str:
    d = res.data or {}
    if d.get("muted"):
        return "Ton aus." if lang == "de" else "Muted."
    return f"Lautstärke {d.get('volume')} Prozent." if lang == "de" else f"Volume {d.get('volume')} percent."


MEDIA_REPLY = {
    "play": _r("Playing.", "Läuft."), "pause": _r("Paused.", "Pausiert."), "toggle": _r("OK.", "OK."),
    "next": _r("Next track.", "Nächster Titel."), "previous": _r("Previous track.", "Vorheriger Titel."),
    "stop": _r("Stopped.", "Gestoppt."),
}


def failure_text(res: ToolResult, lang: str) -> str:
    return (f"Das hat nicht geklappt: {res.text}" if lang == "de" else res.text) if res.text else (
        "Das hat nicht geklappt." if lang == "de" else "That didn't work.")


# ----------------------------------------------------------------------------- patterns

P = re.compile

STOP = P(r"(stop|cancel|never ?mind|abort|quiet|be quiet|shut up|stopp|halt|abbrechen|abbruch|vergiss es|"
         r"lass es|sei still|ruhe|schluss)")
PAUSE = P(r"(pause|pause (the )?(music|song|playback|it|this|video)|stop (the )?(music|song|playback)|"
          r"(die )?musik (pausieren|anhalten|stoppen|aus|ausmachen|ausschalten)|pausier(e|en)?|"
          r"pausier(e|en)? (die|das) (musik|lied|wiedergabe|video)|halte? (die )?musik an|mach (die )?musik aus)")
RESUME = P(r"(play|resume|resume (the )?(music|playback|song)|continue( playing| the music)?|unpause|"
           r"play (the |some )?music|weiter|weiter ?spielen|weiter abspielen|fortsetzen|musik (an|weiter|fortsetzen)|"
           r"mach (die )?musik an|spiel(e)? weiter|spiel(e)? (etwas |ein bisschen )?musik|wiedergabe fortsetzen)")
NEXT = P(r"(next|skip|next (song|track|one|title)|skip (this )?(song|track|one|it)|"
         r"(das )?(nächste[rsn]?|nächstes) (lied|titel|song|track)|überspring(e|en)?( (das|dieses) (lied|stück))?|"
         r"(lied|titel|song) überspringen|nächster)")
PREV = P(r"(previous|previous (song|track|one|title)|last (song|track)|go back a (song|track)|"
         r"(das )?(vorherige[rsn]?|letzte[rsn]?) (lied|titel|song|track)|(ein )?(lied|titel) zurück)")
STATUS = P(r"(what('s| is) (playing|this song|the song|this track)( right now)?|which song is this|"
           r"was (läuft|spielt) (da|gerade|hier)?|welches lied (ist das|läuft)( gerade)?|wer singt das)")

VOL_SET = [P(r"(set |change |turn )?(the )?(volume|sound) (up |down )?(to |at )?(?P<n>\d{1,3})( ?%| percent)?"),
           P(r"(stell(e)? |setz(e)? |mach |dreh(e)? )?(die )?(lautstärke|ton) (auf |zu )?(?P<n>\d{1,3})( ?%| prozent)?"
             r"( stellen| setzen| drehen)?"),
           P(r"(?P<n>\d{1,3}) ?(%|percent|prozent) (volume|lautstärke)")]
VOL_UP = P(r"(turn (it |the volume |the sound |the music )?up|volume up|louder|increase (the )?volume|"
           r"raise (the )?volume|make it louder|(etwas |ein bisschen |ein wenig )?lauter|mach (es |das |die musik )?lauter|"
           r"lautstärke (hoch|rauf|erhöhen)|dreh (es |die musik |den ton )?(lauter|auf))( a (bit|little))?"
           r"( by (?P<n>\d{1,3})( ?%| percent)?)?")
VOL_DOWN = P(r"(turn (it |the volume |the sound |the music )?down|volume down|quieter|softer|lower (the )?volume|"
             r"decrease (the )?volume|make it quieter|(etwas |ein bisschen |ein wenig )?leiser|"
             r"mach (es |das |die musik )?leiser|lautstärke (runter|senken|verringern)|dreh (es |die musik |den ton )?"
             r"(leiser|runter))( a (bit|little))?( by (?P<n>\d{1,3})( ?%| percent)?)?")
VOL_BY = P(r"(lautstärke|ton) um (?P<n>\d{1,3})( ?%| prozent)? (?P<dir>erhöhen|hoch|rauf|lauter|verringern|senken|"
           r"runter|leiser)")
MUTE = P(r"(mute|mute (the )?(sound|audio|volume|it|pc|computer|everything)|stumm|stumm ?schalten|ton aus|"
         r"ton stumm|mach den ton aus|lautlos)")
UNMUTE = P(r"(unmute|unmute (the )?(sound|audio|volume|it)|ton an|ton wieder an|stumm aus|"
           r"stummschaltung aufheben|mach den ton (wieder )?an)")

BRIGHT_SET = [P(r"(set |change )?(the )?(screen |display )?brightness (to |at )?(?P<n>\d{1,3})( ?%| percent)?"),
              P(r"(stell(e)? |setz(e)? )?(die )?(bildschirm)?helligkeit (auf )?(?P<n>\d{1,3})( ?%| prozent)?( stellen)?")]
BRIGHTER = P(r"(brighter|increase (the )?brightness|make (it|the screen) brighter|(bildschirm )?heller|mach (es |den bildschirm )?heller)")
DIMMER = P(r"(dimmer|darker|dim the screen|decrease (the )?brightness|make (it|the screen) darker|"
           r"(bildschirm )?dunkler|mach (es |den bildschirm )?dunkler)")

TIME = P(r"(what('s| is) the time|what time is it|whats the time|time|tell me the time|current time|"
         r"do you know what time it is|wie spät ist es|wie viel uhr ist es|wieviel uhr ist es|wie viel uhr|"
         r"wieviel uhr|uhrzeit|wie spät|sag mir die uhrzeit)( (right )?now| gerade| jetzt)?")
DATE = P(r"(what('s| is) (the date|today's date|the day)( today)?|what day is (it|today)|which day is (it|today)|"
         r"today's date|what's today|what is today|welcher tag ist heute|welches datum (ist|haben wir)( heute)?|"
         r"der wievielte ist heute|was ist heute für ein tag|welchen tag haben wir( heute)?|datum)")

TIMER_SET = [P(r"(set |start |create |make )?(a |an )?timer (for |of |to |at )?(?P<d>.+)"),
             P(r"(set |start )?(a |an )?(?P<d>.+?) timer"),
             P(r"(stell(e)? |setz(e)? |start(e)? |mach )?(einen |ein |nen )?(timer|wecker) (auf |für |von |über )?(?P<d>.+?)"
               r"( stellen| setzen| starten)?"),
             P(r"(stell(e)? |setz(e)? |start(e)? )?(einen |ein )?(?P<d>.+?) timer( stellen| setzen| starten)?"),
             P(r"weck(e)? mich in (?P<d>.+)")]
TIMER_CANCEL = P(r"((cancel|stop|delete|remove|clear) (the |my |all |all the )?(timers?|alarms?|reminders?)|"
                 r"(den |die |alle |alle die )?(timer|wecker|erinnerungen?) (abbrechen|stoppen|löschen|aus(schalten)?)|"
                 r"(brich|stopp(e)?|lösch(e)?) (den |die |alle )?(timer|wecker)( ab)?)")
TIMER_LIST = P(r"(how much time is left|how long is left|how long left|time left( on (the|my) timer)?|"
               r"what timers are (running|set)|(list|show) (the |my )?timers|wie lange (noch|läuft der timer( noch)?)|"
               r"wie viel zeit (ist )?noch|welche timer laufen|timer anzeigen)")
REMIND = [P(r"remind me (in (?P<in>.+?)|at (?P<at>.+?)) (to|that|about) (?P<what>.+)"),
          P(r"remind me to (?P<what>.+?) (in (?P<in>.+)|at (?P<at>.+))"),
          P(r"erinnere? mich (in (?P<in>.+?)|um (?P<at>.+?)) (daran,? dass|daran,? |daran|an|dass) (?P<what>.+)")]

LOCK = P(r"(lock( (the|my))?( (pc|computer|screen|laptop|workstation|windows))?|"
         r"(den |die |meinen )?(pc|computer|bildschirm|laptop|rechner) sperren|"
         r"sperr(e)? (den |die |meinen )?(pc|computer|bildschirm|laptop|rechner)|sperren|bildschirmsperre)")

_DESK_WORDS = r"next|previous|left|right|assistant|first|second|third|fourth|\d+"
DESKTOP = [P(rf"(switch|go|move|change|jump) to (the )?(?P<t>{_DESK_WORDS})(st|nd|rd|th)? (virtual )?desktop"),
           P(rf"((switch|go) to )?(virtual )?desktop (?P<t>{_DESK_WORDS})"),
           P(r"(?P<t>next|previous) desktop"),
           P(r"(wechsle|wechsel|geh(e)?|spring(e)?) (zum |zu |auf )?(den |dem |das )?(?P<t>nächsten|vorherigen|ersten|"
             r"zweiten|dritten|vierten|assistenten|assistant|\d+) desktop"),
           P(r"(zu |auf |zum )?desktop (?P<t>\d+|nächster|vorheriger|assistent|assistant)"),
           P(r"(?P<t>nächster|vorheriger) desktop")]
_DESK_MAP = {"nächsten": "next", "nächster": "next", "vorherigen": "previous", "vorheriger": "previous",
             "ersten": "1", "first": "1", "zweiten": "2", "second": "2", "dritten": "3", "third": "3",
             "vierten": "4", "fourth": "4", "assistenten": "assistant", "assistent": "assistant", "left": "previous",
             "right": "next"}

CLOSE = [P(r"(close|quit|exit) (the |my )?(?P<app>.+?)( app| application| window| program)?"),
         P(r"(schließe?|schließ|beende|beend) (das |den |die |mein |meinen )?(?P<app>.+?)( fenster| programm| app)?"),
         P(r"(das |den |die )?(?P<app>.+?) (schließen|beenden|zumachen)"),
         P(r"mach (das |den |die )?(?P<app>.+?) zu")]
OPEN = [P(r"(open|start|launch|run|fire up|bring up|show me|switch to|go to) (the |my )?(?P<app>.+?)( app| application| program)?"),
        P(r"(öffne?|öffnen|starte?|starten|lade|wechsle zu|wechsel zu|geh(e)? zu) (das |den |die |mir |mein |meinen )?"
          r"(?P<app>.+?)( app| programm)?"),
        P(r"mach (das |den |die |mir )?(?P<app>.+?) (auf|an)"),
        P(r"(das |den |die )?(?P<app>.+?) (öffnen|starten|aufmachen)")]
PLAY = [P(r"play (?P<q>.+?)( on (?P<app>spotify|youtube music|youtube))?"),
        P(r"spiel(e)? (mal )?(?P<q>.+?)( ab)?( (auf|bei|in) (?P<app>spotify|youtube music|youtube))?( ab)?"),
        P(r"(ich (will|möchte)|lass uns|i want to|i wanna|let's) (?P<q>.+?) (hören|listen to)"),
        P(r"(ich (will|möchte)|i want to|i wanna|let's) (listen to|hear) (?P<q>.+)")]
_GENERIC_PLAY = {"music", "some music", "something", "a song", "songs", "musik", "etwas musik", "was", "etwas",
                 "ein lied", "irgendwas", "anything"}
_ACTIVE = {"this", "it", "that", "this window", "the window", "that window", "current window", "das", "es",
           "dieses fenster", "das fenster", "fenster", "das hier"}
_NOT_APPS = re.compile(r"(\\|/|\.[a-z0-9]{2,4}$|\b(file|files|folder|website|web ?site|page|link|url|document|"
                       r"datei|dateien|ordner|webseite|seite|dokument|tab|email|e-mail|mail an)\b|https?|www)")


#: nothing is playing -> open the music app ("music" is an alias for the configured app), then press play
PLAY_FALLBACK = [("open_app", {"name": "music"}), ("wait", {"seconds": 3}), ("media", {"action": "play"})]


def _full(p: re.Pattern[str], t: str) -> re.Match[str] | None:
    return p.fullmatch(t)


def _first(ps: list[re.Pattern[str]], t: str) -> re.Match[str] | None:
    for p in ps:
        m = p.fullmatch(t)
        if m:
            return m
    return None


class FastPath:
    """``match(text)`` -> FastIntent or None. ``resolve_app(name)`` must return a display name or None."""

    def __init__(self, resolve_app: Callable[[str], str | None], names: Callable[[], list[str]],
                 language: Callable[[], str]):
        self.resolve_app = resolve_app
        self.names = names
        self.language = language

    def match(self, text: str) -> FastIntent | None:
        t = normalize(text, self.names())
        if not t or len(t) > 120:
            return None
        lang = self.language()
        if _full(STOP, t):
            return FastIntent("stop", action="cancel")
        if _full(TIME, t):
            now = dt.datetime.now()
            return FastIntent("time", answer=(f"Es ist {now:%H:%M} Uhr." if lang == "de"
                                              else f"It's {now:%I:%M %p}.".replace(" 0", " ").lstrip("0")))
        if _full(DATE, t):
            return FastIntent("date", answer=spoken_date(dt.date.today(), lang))
        for intent, pattern in (("pause", PAUSE), ("next", NEXT), ("previous", PREV)):
            if _full(pattern, t):
                return FastIntent(f"media.{intent}", "media", {"action": intent}, MEDIA_REPLY[intent])
        if _full(STATUS, t):
            return FastIntent("media.status", "media", {"action": "status"},
                              lambda res, lang: res.text)
        if (m := _first(VOL_SET, t)) is not None:
            n = int(m.group("n"))
            if n <= 100:
                return FastIntent("volume.set", "volume", {"level": n}, _volume_reply)
        for pattern, sign in ((VOL_UP, 1), (VOL_DOWN, -1)):
            if (m := _full(pattern, t)) is not None:
                n = int(m.group("n")) if m.group("n") else 10
                return FastIntent("volume.change", "volume", {"change": sign * min(n, 100)}, _volume_reply)
        if (m := _full(VOL_BY, t)) is not None:
            sign = 1 if m.group("dir") in ("erhöhen", "hoch", "rauf", "lauter") else -1
            return FastIntent("volume.change", "volume", {"change": sign * min(int(m.group("n")), 100)},
                              _volume_reply)
        if _full(MUTE, t):
            return FastIntent("volume.mute", "volume", {"mute": True}, _volume_reply)
        if _full(UNMUTE, t):
            return FastIntent("volume.unmute", "volume", {"mute": False}, _volume_reply)
        if (m := _first(BRIGHT_SET, t)) is not None and int(m.group("n")) <= 100:
            return FastIntent("brightness.set", "brightness", {"level": int(m.group("n"))},
                              _r("Brightness {brightness} percent.", "Helligkeit {brightness} Prozent."))
        if _full(BRIGHTER, t) or _full(DIMMER, t):
            return FastIntent("brightness.change", "brightness", {"change": 15 if _full(BRIGHTER, t) else -15},
                              _r("Brightness {brightness} percent.", "Helligkeit {brightness} Prozent."))
        if _full(TIMER_CANCEL, t):
            return FastIntent("timer.cancel", "timer", {"action": "cancel", "all": True},
                              _r("Timer cancelled.", "Timer abgebrochen."))
        if _full(TIMER_LIST, t):
            return FastIntent("timer.list", "timer", {"action": "list"}, lambda res, lang: res.text)
        if (m := _first(REMIND, t)) is not None:
            fi = self._reminder(m, lang)
            if fi is not None:
                return fi
        if (m := _first(TIMER_SET, t)) is not None:
            secs = parse_duration(spoken_duration(m.group("d")))
            if secs:
                return FastIntent("timer.set", "timer", {"action": "set_timer", "seconds": secs},
                                  lambda res, lang: timer_reply(secs, lang))
        if _full(LOCK, t):
            return FastIntent("lock", "power", {"action": "lock"}, _r("Locked.", "Gesperrt."))
        if (m := _first(DESKTOP, t)) is not None:
            target = _DESK_MAP.get(m.group("t"), m.group("t"))
            return FastIntent("desktop.switch", "desktop", {"action": "switch", "target": target},
                              _r("Switched desktop.", "Desktop gewechselt."))
        if _full(RESUME, t):
            return FastIntent("media.play", "media", {"action": "play"}, MEDIA_REPLY["play"],
                              fallback=PLAY_FALLBACK)
        if (m := _first(PLAY, t)) is not None:
            q = re.sub(r"^(something|some songs|songs|music|etwas|was|lieder|musik|ein lied) (by|from|von) ", "",
                       m.group("q").strip())
            if q in _GENERIC_PLAY:
                return FastIntent("media.play", "media", {"action": "play"}, MEDIA_REPLY["play"],
                                  fallback=PLAY_FALLBACK)
            args = {"query": q}
            if m.groupdict().get("app"):
                args["app"] = m.group("app")
            return FastIntent("music.play", "play_music", args,
                              lambda res, lang: (f"Ich spiele {q}." if lang == "de" else f"Playing {q}.")
                              if res.ok else res.text)
        if (m := _first(CLOSE, t)) is not None:
            target = m.group("app").strip()
            if target in _ACTIVE:
                return FastIntent("app.close", "window", {"action": "close"},
                                  _r("Closed.", "Geschlossen."))
            name = None if _NOT_APPS.search(target) else self.resolve_app(target)
            if name:
                return FastIntent("app.close", "window", {"action": "close", "target": name},
                                  lambda res, lang, n=name: f"{n} wird geschlossen." if lang == "de"
                                  else f"Closing {n}.")
        if (m := _first(OPEN, t)) is not None:
            target = m.group("app").strip()
            name = None if _NOT_APPS.search(target) else self.resolve_app(target)
            if name:
                return FastIntent("app.open", "open_app", {"name": name},
                                  lambda res, lang, n=name: (f"{n} ist offen." if lang == "de" else f"{n} is open.")
                                  if "Switched" in res.text or "running" in res.text else
                                  (f"Ich öffne {n}." if lang == "de" else f"Opening {n}."))
        return None

    def _reminder(self, m: re.Match[str], lang: str) -> FastIntent | None:
        what = m.group("what").strip()
        when_in, when_at = m.groupdict().get("in"), m.groupdict().get("at")
        if when_in:
            secs = parse_duration(spoken_duration(when_in))
            if not secs:
                return None
            at = (dt.datetime.now() + dt.timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%S")
        else:
            parsed = parse_time_of_day((when_at or "").replace(" uhr", "").strip())
            if parsed is None:
                return None
            at = parsed.strftime("%Y-%m-%dT%H:%M:%S")
        return FastIntent("reminder.set", "timer", {"action": "set_reminder", "at": at, "label": what},
                          lambda res, lang: res.text if lang != "de" else
                          f"Ich erinnere dich um {at[11:16]} Uhr: {what}.")


def spoken_date(d: dt.date, lang: str) -> str:
    if lang == "de":
        days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                  "November", "Dezember"]
        return f"Heute ist {days[d.weekday()]}, der {d.day}. {months[d.month - 1]}."
    suffix = "th" if 11 <= d.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"Today is {d:%A}, {d:%B} {d.day}{suffix}."


def timer_reply(secs: int, lang: str) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if lang == "de":
        parts = [f"{h} Stunde{'n' if h != 1 else ''}" if h else "", f"{m} Minute{'n' if m != 1 else ''}" if m else "",
                 f"{s} Sekunde{'n' if s != 1 else ''}" if s else ""]
        return "Timer für " + " ".join(p for p in parts if p) + " gestellt."
    parts = [f"{h} hour{'s' if h != 1 else ''}" if h else "", f"{m} minute{'s' if m != 1 else ''}" if m else "",
             f"{s} second{'s' if s != 1 else ''}" if s else ""]
    return "Timer set for " + " ".join(p for p in parts if p) + "."
