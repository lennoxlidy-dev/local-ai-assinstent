"""The setup wizard in text form (debug CLI ``:setup``): same questions, same answers, typed.

Enter keeps the suggestion, ``back`` goes back, ``quit`` stops (the rest keeps its suggestions).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .questions import Question, label
from .wizard import Wizard

ReadLine = Callable[[str], str]
Print = Callable[..., None]


def _show_value(q: Question, value: Any, lang: str) -> str:
    if q.kind == "bool":
        return ("ja" if value else "nein") if lang == "de" else ("yes" if value else "no")
    if q.kind in ("choice", "multi") and q.options is not None:
        return ""
    if value in (None, "", [], {}):
        return "-"
    return str(value)


def run_text_wizard(w: Wizard, read_line: ReadLine, out: Print) -> bool:
    """Returns True when finished (all questions seen or quit)."""
    lang = w.env.lang
    while w.current is not None:
        q = w.current
        lang = w.env.lang
        n, total = w.progress()
        out(f"\n[{n}/{total}] {label(q.title, lang)}")
        if label(q.help, lang):
            out("  " + label(q.help, lang))
        value = w.value()
        options = q.options(w.env) if q.options is not None else []
        if q.kind in ("choice", "multi"):
            chosen = value if isinstance(value, list) else [value]
            for i, o in enumerate(options, 1):
                mark = "*" if o.value in chosen else " "
                out(f"  {mark} {i}. {label(o.label, lang)}" + (f" - {label(o.hint, lang)}" if o.hint else ""))
        elif q.kind == "number" and q.number is not None:
            lo, hi, _step, unit = q.number
            out(f"  ({lo}-{hi} {label(unit, lang)}) now: {value}")
        elif q.kind == "form" and q.fields is not None:
            ans = {}
            for f in q.fields(w.env):
                cur = (value or {}).get(f.key, "")
                line = read_line(f"  {label(f.label, lang)} [{cur}]: ").strip()
                if line.lower() in ("back", "zurück"):
                    w.back()
                    break
                ans[f.key] = line if line else cur
            else:
                w.answer(ans)
            continue
        elif q.kind == "matrix" and q.rows is not None and q.columns is not None:
            for r in q.rows(w.env):
                out(f"    {r.value:<18} {(value or {}).get(r.value)}")
            out("  Enter = keep, or e.g. 'input=allow files.delete=block'")
        elif q.page == "folders":
            for i, f in enumerate(value or [], 1):
                out(f"    {i}. {f['path']}  [{f['access']}]")
            out("  Enter = keep, 'add <folder> full|read|ask|none', 'remove <n>'")
        elif q.page == "hotkeys":
            out(f"  push-to-talk: {value.get('push_to_talk')}   panic: {value.get('panic')}   mode: {value.get('mode')}")
            out("  Enter = keep, or 'ptt=ctrl+alt+x panic=ctrl+alt+escape mode=hold'")
        elif q.page == "wake":
            out("  (Enter = skip; use :calibrate to say the name 3x with your microphone)")
        elif q.page == "voicelock":
            out("  (Enter = skip; use :enroll to record your voice)")
        elif q.page == "routines":
            for i, r in enumerate(value or [], 1):
                out(f"  {'*' if r.get('enabled') else ' '} {i}. {r['name']}  ({r.get('apps') or '-'})")
            out("  Enter = keep, or the numbers to create, e.g. '1 4'")
        elif q.page == "mic":
            from ..audio.sources import list_devices

            devs = list_devices("input")
            for i, d in enumerate(devs, 1):
                out(f"    {i}. {d}")
            out(f"  now: {value or 'Windows default'}   (Enter = keep, or a number)")
        else:
            out(f"  [{_show_value(q, value, lang)}]")
        line = read_line("> ").strip()
        low = line.lower()
        if low in ("quit", "exit", "q", "beenden"):
            break
        if not line:
            w.skip()
            continue
        if low in ("back", "zurück"):
            if not w.back():
                out("  (this is the first question)")
            continue
        err = _special(w, q, line, value)
        if err is not None:
            if err:
                out("  ! " + err)
            continue
        it = w.handle_text(line)
        if it.action == "unclear":
            out("  ? " + ("Das habe ich nicht verstanden." if lang == "de" else "Sorry, I didn't get that."))
        elif it.action == "invalid":
            out("  ! " + label(it.value, lang))
        elif it.action == "repeat":
            continue
    w.finish(apply_remaining=True)
    out("\n" + ("Einrichtung gespeichert." if lang == "de" else "Setup saved."))
    return True


def _special(w: Wizard, q: Question, line: str, value: Any) -> str | None:
    """Typed edits for the pages; returns '' (handled), an error text, or None (not special)."""
    if q.kind == "matrix":
        new = dict(value or {})
        for part in line.replace(",", " ").split():
            if "=" not in part:
                return "use key=value"
            k, v = part.split("=", 1)
            if k not in new:
                return f"unknown row {k}"
            new[k] = v
        err = w.answer(new)
        return label(err, w.env.lang) if err else ""
    if q.page == "folders":
        rules = list(value or [])
        parts = line.split()
        if parts[0].lower() == "add" and len(parts) >= 3:
            rules.insert(0, {"path": " ".join(parts[1:-1]), "access": parts[-1], "note": ""})
        elif parts[0].lower() == "remove" and len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(rules):
                rules.pop(idx)
        else:
            return "add <folder> full|read|ask|none  or  remove <n>"
        err = w.set_value(rules)
        return label(err, w.env.lang) if err else ""
    if q.page == "hotkeys":
        new = dict(value or {})
        keys = {"ptt": "push_to_talk", "panic": "panic", "mode": "mode"}
        for part in line.split():
            if "=" not in part or part.split("=", 1)[0] not in keys:
                return "use ptt=... panic=... mode=press|hold"
            k, v = part.split("=", 1)
            new[keys[k]] = v
        err = w.answer(new)
        return label(err, w.env.lang) if err else ""
    if q.page == "routines":
        items = [dict(r) for r in value or []]
        try:
            picks = {int(x) - 1 for x in line.replace(",", " ").split()}
        except ValueError:
            return "numbers, e.g. 1 4"
        for i, r in enumerate(items):
            r["enabled"] = i in picks
        w.answer(items)
        return ""
    if q.page == "mic" and line.isdigit():
        from ..audio.sources import list_devices

        devs = list_devices("input")
        idx = int(line) - 1
        if not 0 <= idx < len(devs):
            return "no such device"
        w.answer(devs[idx])
        return ""
    if q.page in ("wake", "voicelock", "mic"):
        return None if q.page == "voicelock" else "press Enter to continue"
    return None


__all__ = ["run_text_wizard"]
