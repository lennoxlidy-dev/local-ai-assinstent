"""Subprocess helpers.

A tray app runs under ``pythonw.exe`` (no console). Starting a console
program such as PowerShell from there would flash a black window, so every
child process is created with ``CREATE_NO_WINDOW`` on Windows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def hidden_flags(low_priority: bool = False) -> int:
    if sys.platform != "win32":
        return 0
    flags = CREATE_NO_WINDOW
    if low_priority:
        flags |= BELOW_NORMAL_PRIORITY_CLASS
    return flags


def run_hidden(cmd: Sequence[str] | str, *, timeout: float = 30.0, cwd: str | os.PathLike[str] | None = None,
               input: str | None = None, low_priority: bool = False,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command without a console window; capture text output (UTF-8, lenient)."""
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=cwd, input=input, env=env,
        stdin=None if input is not None else subprocess.DEVNULL,
        creationflags=hidden_flags(low_priority),
    )


def trim_memory() -> None:
    """Give memory back to the system after a big window closed (settings, setup wizard).

    Windows: empty the working set (pages come back only if used again). Linux: return freed heap.
    """
    import ctypes
    import gc

    gc.collect()
    try:
        if sys.platform == "win32":
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k32.K32EmptyWorkingSet(k32.GetCurrentProcess())
        elif sys.platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
