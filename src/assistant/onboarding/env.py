"""What the wizard needs from the rest of the app (kept small so tests can fake it)."""

from __future__ import annotations

import ctypes
import locale
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.context import AppContext

log = logging.getLogger(__name__)


@dataclass
class WizardEnv:
    ctx: AppContext
    policy: Any = None                              # PolicyStore
    routines: Any = None                            # RoutineStore
    app_names: Callable[[], list[str]] = lambda: []  # installed apps (Start menu + Store)
    vram_mb: int = 0                                 # largest NVIDIA GPU, 0 = none found
    first_run: bool = True
    user_name_guess: Callable[[], str] = field(default=lambda: guess_user_name())
    language_guess: Callable[[], str] = field(default=lambda: guess_language())

    @property
    def lang(self) -> str:
        lang = self.ctx.config.data.general.language
        return lang if lang in ("en", "de") else "en"


def guess_language() -> str:
    """'de' if Windows (or the locale) is German, else 'en'."""
    try:
        if sys.platform == "win32":
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            return "de" if lang_id & 0x3FF == 0x07 else "en"
        loc = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower()
        return "de" if loc.startswith(("de", "german")) else "en"
    except Exception:
        return "en"


def guess_user_name() -> str:
    """First name from the Windows account ('Lennox Lidy' -> 'Lennox'), else the login name."""
    name = ""
    if sys.platform == "win32":
        try:
            size = ctypes.c_ulong(256)
            buf = ctypes.create_unicode_buffer(256)
            # NameDisplay = 3; fails for local accounts without a display name
            if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)):  # type: ignore[attr-defined]
                name = buf.value
        except Exception as e:
            log.debug("display name unavailable: %s", e)
    if not name:
        name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    first = name.replace(".", " ").replace("_", " ").split()
    word = first[0] if first else ""
    if not word or word.lower() in ("root", "admin", "administrator", "user", "benutzer"):
        return ""
    return word[:1].upper() + word[1:]


__all__ = ["WizardEnv", "guess_language", "guess_user_name"]
