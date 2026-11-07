"""Understand an answer to a setup question - typed or spoken, English or German.

Besides answers, every question understands "skip / next / keep it", "back" and "repeat"
(and the German equivalents). Anything unclear is reported as such, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..intents.numbers import words_to_digits
from ..permissions.gate import ConfirmChoice
from ..voice.yesno import parse_answer
from .env import WizardEnv
from .questions import Option, Question, label


@dataclass
class Interpretation:
    action: str            # answer | skip | back | repeat | start | unclear
    value: Any = None
    heard: str = ""


_NAV = {
    "skip": ("skip", "skip it", "skip this", "next", "next question", "continue", "go on", "keep", "keep it",
             "keep that", "keep them", "keep this", "keep the default", "keep the defaults", "default", "defaults",
             "use the default", "use the defaults", "doesn't matter", "does not matter", "don't care", "whatever",
             "that's fine", "thats fine", "fine", "sounds good", "looks good", "later", "not now", "maybe later",
             "überspringen", "überspring", "weiter", "nächste", "nächste frage", "standard", "standardwert",
             "behalten", "beibehalten", "passt", "passt so", "egal", "ist mir egal", "lass so", "so lassen",
             "alles gut", "später", "nicht jetzt", "vielleicht später", "so lassen bitte"),
    "back": ("back", "go back", "previous", "previous question", "one back", "zurück", "geh zurück",
             "gehe zurück", "vorherige", "vorherige frage", "eins zurück"),
    "repeat": ("repeat", "repeat that", "repeat please", "again", "say that again", "say it again", "what",
               "pardon", "sorry", "come again", "wiederholen", "wiederhol das", "nochmal", "noch mal",
               "noch einmal", "wie bitte", "was", "bitte wiederholen"),
    "start": ("start", "ready", "go", "let's go", "lets go", "i'm ready", "im ready", "begin", "los", "los geht's",
              "los gehts", "bereit", "ich bin bereit", "anfangen", "starten", "fang an"),
}
_FILLER = re.compile(r"^(?:(?:um|uh|hmm|ähm|äh|so|well|also|na|ok|okay|please|bitte|yes|ja)[\s,]+)+|"
                     r"(?:[\s,]+(?:please|bitte|thanks|thank you|danke))+$")
_ORDINALS = {
    0: ("first", "the first", "first one", "the first one", "number one", "one", "1", "erste", "die erste",
        "der erste", "das erste", "ersten", "nummer eins", "eins"),
    1: ("second", "the second", "second one", "the second one", "number two", "two", "2", "zweite", "die zweite",
        "der zweite", "das zweite", "nummer zwei", "zwei"),
    2: ("third", "the third", "third one", "the third one", "number three", "three", "3", "dritte", "die dritte",
        "der dritte", "das dritte", "nummer drei", "drei"),
    3: ("fourth", "the fourth", "fourth one", "number four", "four", "4", "vierte", "die vierte", "der vierte",
        "nummer vier", "vier"),
    4: ("fifth", "the fifth", "number five", "five", "5", "fünfte", "die fünfte", "nummer fünf", "fünf"),
}
_ALL = ("all", "all of them", "everything", "every one", "alle", "alles", "jede", "alle davon")
_NONE = ("none", "none of them", "nothing", "no one", "keine", "keins", "keines", "nichts", "gar nichts")
_NAME_LEAD = re.compile(
    r"^(?:(?:you can |just |please )?call (?:me|yourself)|my name is|i'm|i am|it's|its|it is|your name is|"
    r"your name should be|you are|you're|you should be called|name yourself|let's go with|lets go with|"
    r"how about|i'd like|i would like|i want|it should be|"
    r"(?:du kannst mich |bitte )?nenn(?:e)? (?:mich|dich)|ich heiße|ich heisse|ich bin|mein name ist|du heißt|"
    r"du heisst|dein name ist|du sollst|du sollst heißen|heiß|heiße|wie wäre es mit|ich möchte|ich will|"
    r"es soll|nimm)\s+", re.I)
_APP_LEAD = re.compile(r"^(?:i use|i'm using|i am using|mostly|usually|use|ich nutze|ich benutze|ich verwende|"
                       r"meistens|normalerweise|nimm)\s+", re.I)


def norm(text: str) -> str:
    t = text.casefold().replace("’", "'").replace("´", "'")
    t = re.sub(r"[“”\"„!?¿¡.,;:…()]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for _ in range(2):
        t = _FILLER.sub("", t).strip()
    return t


def nav_action(text: str) -> str | None:
    n = norm(text)
    if not n:
        return None
    for action, phrases in _NAV.items():
        if n in phrases:
            return action
    return None


def interpret(q: Question, text: str, env: WizardEnv) -> Interpretation:
    heard = text.strip()
    if not heard:
        return Interpretation("unclear", heard=heard)
    if q.kind == "bool":                 # yes/no first: "okay" must mean yes here, not "skip"
        b = parse_bool(heard)
        if b is not None:
            return Interpretation("answer", b, heard)
    if q.page == "voicelock":
        b = parse_bool(heard)
        if b is True:
            return Interpretation("start", None, heard)
        if b is False:
            return Interpretation("answer", False, heard)
    action = nav_action(heard)
    if action == "start" and q.page not in ("wake", "voicelock"):
        action = "skip" if norm(heard) in ("go", "go on", "los", "weiter") else None
    if action is not None:
        return Interpretation(action, heard=heard)
    if q.page == "wake" and parse_bool(heard) is True:
        return Interpretation("start", heard=heard)
    if q.kind in ("bool", "page", "matrix"):
        if q.kind == "page" and parse_bool(heard) is True:
            return Interpretation("skip", heard=heard)       # "yes/okay" on a settings page = keep it
        return Interpretation("unclear", heard=heard)
    if q.kind == "text":
        value = clean_name(heard) if q.name_answer else _sentence(heard)
        return Interpretation("answer", value, heard) if value else Interpretation("unclear", heard=heard)
    if q.kind == "number":
        assert q.number is not None
        lo, hi, step, _unit = q.number
        n = parse_amount(heard)
        if n is None:
            return Interpretation("unclear", heard=heard)
        n = min(hi, max(lo, round(round(n / step) * step, 3)))
        return Interpretation("answer", n, heard)
    options = q.options(env) if q.options is not None else []
    if q.kind == "choice":
        opt = match_option(heard, options, env.lang)
        if opt is not None:
            return Interpretation("answer", opt.value, heard)
        if q.allow_other:
            other = _APP_LEAD.sub("", norm(heard)).strip()
            if other and len(other.split()) <= 3:
                return Interpretation("answer", _title(other), heard)
        if parse_bool(heard) is True:
            return Interpretation("skip", heard=heard)       # "yes"/"okay" = keep the suggestion
        return Interpretation("unclear", heard=heard)
    if q.kind == "multi":
        n = norm(heard)
        if n in _ALL:
            return Interpretation("answer", [o.value for o in options], heard)
        if n in _NONE:
            return Interpretation("answer", [], heard)
        found = mentioned_options(heard, options, env.lang)
        if found:
            return Interpretation("answer", [o.value for o in found], heard)
        if parse_bool(heard) is True:
            return Interpretation("skip", heard=heard)
        return Interpretation("unclear", heard=heard)
    if q.kind == "form":
        fields = q.fields(env) if q.fields is not None else []
        values: dict[str, str] = {}
        for f in fields:
            for s in f.suggestions:
                if _contains(norm(heard), norm(s)):
                    values[f.key] = s
                    break
        if values:
            return Interpretation("answer", values, heard)
        text_field = next((f for f in fields if f.multiline), None)
        if text_field is not None and parse_bool(heard) is None:
            return Interpretation("answer", {text_field.key: _sentence(heard)[: text_field.max_len]}, heard)
        if parse_bool(heard) is True:
            return Interpretation("skip", heard=heard)
        return Interpretation("unclear", heard=heard)
    return Interpretation("unclear", heard=heard)


# ----------------------------------------------------------------------------- pieces

def parse_bool(text: str) -> bool | None:
    n = norm(text)
    if n in ("on", "turn it on", "switch it on", "enable", "enabled", "an", "ein", "einschalten", "anschalten",
             "aktivieren", "gerne", "sure", "of course", "natürlich", "klar", "jo"):
        return True
    if n in ("off", "turn it off", "switch it off", "disable", "disabled", "aus", "ausschalten", "deaktivieren",
             "nope", "nö", "nee", "no thanks", "nein danke", "lieber nicht"):
        return False
    a = parse_answer(text)
    if a in (ConfirmChoice.ONCE, ConfirmChoice.ALWAYS, ConfirmChoice.TASK):
        return True
    if a == ConfirmChoice.DENY or a == "stop":
        return False
    return None


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _candidates(o: Option, lang: str) -> list[str]:
    names = [label(o.label, lang), label(o.label, "en"), label(o.label, "de"), str(o.value), *o.words]
    out = []
    for n in names:
        k = norm(n)
        if k and k not in out:
            out.append(k)
        short = norm(re.sub(r"\(.*?\)", "", n))      # "German (Deutsch)" -> "german"
        if short and short not in out:
            out.append(short)
    return out


def match_option(text: str, options: list[Option], lang: str) -> Option | None:
    n = norm(text)
    if not n:
        return None
    digits = norm(words_to_digits(n))
    if digits != n:                                  # "male two" -> "male 2"
        found = match_option(digits, options, lang)
        if found is not None:
            return found
    for idx, words in _ORDINALS.items():
        if n in words and idx < len(options):
            return options[idx]
    best: tuple[float, Option] | None = None
    tie = False
    for o in options:
        score = 0.0
        for c in _candidates(o, lang):
            if n == c:
                score = max(score, 1000.0)
            elif _contains(n, c):
                score = max(score, 100.0 + len(c))
            elif len(n) >= 4 and _contains(c, n):          # "chrome" -> "Google Chrome"
                score = max(score, 50.0 + len(n))
        if score and (best is None or score > best[0]):
            best, tie = (score, o), False
        elif score and best is not None and score == best[0] and o is not best[1]:
            tie = True
    if best is not None and not tie:
        return best[1]
    from rapidfuzz import fuzz

    scored = sorted(((max(fuzz.ratio(n, c) for c in _candidates(o, lang)), o) for o in options),
                    key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= 85 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 8):
        return scored[0][1]
    return None


def mentioned_options(text: str, options: list[Option], lang: str) -> list[Option]:
    n = norm(text)
    return [o for o in options if any(_contains(n, c) for c in _candidates(o, lang) if len(c) > 2)]


_HALF = {"half": 0.5, "a half": 0.5, "halb": 0.5, "halbe": 0.5, "eine halbe": 0.5, "anderthalb": 1.5,
         "eineinhalb": 1.5, "one and a half": 1.5, "zweieinhalb": 2.5, "two and a half": 2.5,
         "dreieinhalb": 3.5, "three and a half": 3.5}


def parse_amount(text: str) -> float | None:
    """'1.5', '1,5', 'one and a half', 'eineinhalb', 'zwei Komma fünf', 'two seconds' -> float."""
    n = text.casefold().strip()
    n = re.sub(r"(?<=\d),(?=\d)", ".", n)                      # 1,5 -> 1.5
    n = re.sub(r"[^\w\s.]|(?<!\d)\.|\.(?!\d)", " ", n)          # keep only decimal points
    n = re.sub(r"\b(seconds?|secs?|sekunden?|sek|s)\b", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    for words, value in sorted(_HALF.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<!\w){re.escape(words)}(?!\w)", n):
            return value
    d = words_to_digits(n)
    m = re.search(r"(\d+)\s*(?:komma|point|punkt|[.,])\s*(\d+)", d)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.search(r"(\d+)(?:\s+and a half|\s*einhalb)", d)
    if m:
        return int(m.group(1)) + 0.5
    m = re.search(r"\d+(?:\.\d+)?", d)
    return float(m.group(0)) if m else None


def clean_name(text: str) -> str:
    """'Call me Lennox.' -> 'Lennox'; 'Ich heiße anna-lena' -> 'Anna-Lena'."""
    t = text.strip().strip(" .!?,;:\"'„“”")
    for _ in range(2):
        t = _NAME_LEAD.sub("", t.strip()).strip(" .!?,;:\"'„“”")
    t = re.sub(r"[\s,]+(?:please|bitte|thanks|thank you|danke)$", "", t, flags=re.I)
    words = [w for w in re.split(r"\s+", t) if w]
    if not words:
        return ""
    return " ".join(_title(w) for w in words)


def _title(word: str) -> str:
    return "-".join(p[:1].upper() + p[1:] for p in word.split("-")) if word.islower() else word


def _sentence(text: str) -> str:
    t = " ".join(text.split())
    return t[:1].upper() + t[1:] if t else t


__all__ = ["Interpretation", "clean_name", "interpret", "match_option", "nav_action", "parse_amount", "parse_bool"]
