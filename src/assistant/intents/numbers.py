"""Spoken numbers -> digits (EN/DE, 0-999 is plenty for volume, timers, desktops)."""

from __future__ import annotations

import re

EN_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
           "eighty": 80, "ninety": 90}
DE_UNITS = {"null": 0, "eins": 1, "ein": 1, "eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
            "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
            "zwoelf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17,
            "achtzehn": 18, "neunzehn": 19}
DE_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50, "fuenfzig": 50,
           "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90}
DE_COMPOUND_UNITS = {"ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,
                     "neun": 9}

_DE_COMPOUND = re.compile(
    r"\b(" + "|".join(DE_COMPOUND_UNITS) + r")und(" + "|".join(DE_TENS) + r")\b")
_EN_COMPOUND = re.compile(r"\b(" + "|".join(EN_TENS) + r")[- ](" + "|".join(k for k in EN_UNITS
                                                                            if EN_UNITS[k] < 10) + r")\b")

# Words that are numbers only in some contexts ("a minute", "ein timer"); handled by the callers.
_AMBIGUOUS = {"ein", "eine", "einen", "one"}


def words_to_digits(text: str) -> str:
    t = text
    t = re.sub(r"\b(one |a |ein)?hundred\b|\b(ein)?hundert\b", "100", t)
    t = _DE_COMPOUND.sub(lambda m: str(DE_TENS[m.group(2)] + DE_COMPOUND_UNITS[m.group(1)]), t)
    t = _EN_COMPOUND.sub(lambda m: str(EN_TENS[m.group(1)] + EN_UNITS[m.group(2)]), t)

    def single(m: re.Match[str]) -> str:
        w = m.group(0)
        if w in _AMBIGUOUS:
            return w
        return str(EN_UNITS.get(w, EN_TENS.get(w, DE_UNITS.get(w, DE_TENS.get(w, w)))))

    words = sorted({*EN_UNITS, *EN_TENS, *DE_UNITS, *DE_TENS}, key=len, reverse=True)
    t = re.sub(r"\b(" + "|".join(map(re.escape, words)) + r")\b", single, t)
    return t


def spoken_duration(text: str) -> str:
    """Normalize duration phrases so tools.system.parse_duration can read them."""
    t = f" {text} "
    t = re.sub(r"\b(half an hour|a half hour|eine halbe stunde|ne halbe stunde|halbe stunde)\b", " 30 min ", t)
    t = re.sub(r"\b(an hour and a half|one and a half hours|anderthalb stunden|eineinhalb stunden)\b", " 90 min ", t)
    t = re.sub(r"\b(a quarter of an hour|quarter of an hour|eine viertelstunde|viertelstunde)\b", " 15 min ", t)
    t = re.sub(r"\b(an|a|one|eine|einer|ein|einen)\s+(hour|stunde|minute|second|sekunde)\b", r" 1 \2", t)
    return t.strip()
