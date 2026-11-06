"""Understand a spoken answer to "May I ...?" (English and German, either language at any time).

Conservative on purpose: anything negative or unclear is *not* a yes.
"""

from __future__ import annotations

import re

from ..permissions.gate import ConfirmChoice

_NO = ("no", "nope", "nah", "dont", "do not", "not", "never", "deny", "negative", "wait", "abort",
       "nein", "nee", "noe", "nö", "nicht", "nichts", "niemals", "lieber nicht", "auf keinen fall", "warte",
       "lass das", "lass es", "bloss nicht", "bloß nicht")
_STOP = ("stop", "cancel", "stopp", "halt", "abbrechen", "abbruch")
_YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "go ahead", "go", "allow", "allowed",
        "confirm", "confirmed", "please do", "correct", "affirmative", "of course", "alright", "all right",
        "ja", "jawohl", "jo", "jep", "klar", "sicher", "mach", "machs", "mach das", "mach es", "los", "erlauben",
        "erlaubt", "erlaube", "bestätigt", "bestätige", "in ordnung", "genau", "gerne", "natürlich", "passt",
        "einverstanden", "richtig")
_ALWAYS = ("always", "immer")
_TASK = ("for this task", "for now", "this time", "für diese aufgabe", "für jetzt", "diesmal")


def _norm(text: str) -> str:
    t = text.casefold().replace("’", "").replace("'", "")
    return " " + " ".join(re.findall(r"[^\W_]+", t)) + " "


def _has(norm: str, phrases: tuple[str, ...]) -> bool:
    return any(f" {p} " in norm for p in phrases)


def parse_answer(text: str) -> ConfirmChoice | str | None:
    """-> ConfirmChoice (ONCE / TASK / ALWAYS / DENY), "stop" (cancel the whole request), or None (unclear)."""
    n = _norm(text)
    if not n.strip():
        return None
    if _has(n, _STOP):
        return "stop"
    if _has(n, _NO):
        return ConfirmChoice.DENY
    yes = _has(n, _YES)
    if _has(n, _ALWAYS) and (yes or len(n.split()) <= 3):
        return ConfirmChoice.ALWAYS
    if _has(n, _TASK):
        return ConfirmChoice.TASK
    if yes:
        return ConfirmChoice.ONCE
    return None


__all__ = ["parse_answer"]
