"""Start at login via ``HKCU\\...\\CurrentVersion\\Run`` (per-user, no admin).

Windows' Task Manager / Settings > Apps > Startup can disable a Run entry
without deleting it, by writing a flag to ``...\\Explorer\\StartupApproved\\Run``.
We report that state honestly, and when the user turns autostart on in *our*
UI we clear that flag so the toggle actually works.
"""

from __future__ import annotations

import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .. import APP_ID

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = APP_ID


class AutostartState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DISABLED_BY_WINDOWS = "disabled in Windows startup settings"
    UNSUPPORTED = "unsupported on this OS"


class RegistryBackend(Protocol):
    def get(self, key: str, name: str) -> object | None: ...
    def set_string(self, key: str, name: str, value: str) -> None: ...
    def delete(self, key: str, name: str) -> None: ...


class WinRegBackend:
    """HKEY_CURRENT_USER access via ``winreg``."""

    def __init__(self) -> None:
        import winreg  # noqa: F401  (fail early on non-Windows)

    def get(self, key: str, name: str) -> object | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return None

    def set_string(self, key: str, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, key: str, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass


class MemoryBackend:
    """In-memory registry for tests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], object] = {}

    def get(self, key: str, name: str) -> object | None:
        return self.values.get((key, name))

    def set_string(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def set_binary(self, key: str, name: str, value: bytes) -> None:
        self.values[(key, name)] = value

    def delete(self, key: str, name: str) -> None:
        self.values.pop((key, name), None)


def launch_command(executable: str | None = None, frozen: bool | None = None) -> str:
    """The command line Windows should run at login."""
    exe = Path(executable or sys.executable)
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:  # packaged .exe
        return f'"{exe}" --autostart'
    # Prefer pythonw.exe next to python.exe (no console window).
    pyw = exe.with_name("pythonw.exe") if exe.name.lower() == "python.exe" else exe
    if not pyw.exists():
        pyw = exe
    # -I (isolated): ignore PYTHON* env vars and never import from the working directory.
    return f'"{pyw}" -I -m assistant --autostart'


class Autostart:
    def __init__(self, backend: RegistryBackend | None = None, command: str | None = None):
        if backend is None and sys.platform == "win32":
            backend = WinRegBackend()
        self._reg = backend
        self.command = command or launch_command()

    @property
    def supported(self) -> bool:
        return self._reg is not None

    def state(self) -> AutostartState:
        if self._reg is None:
            return AutostartState.UNSUPPORTED
        if self._reg.get(RUN_KEY, VALUE_NAME) is None:
            return AutostartState.DISABLED
        flag = self._reg.get(APPROVED_KEY, VALUE_NAME)
        # First byte: 0x02/0x06 = enabled, 0x03/0x07 (odd) = disabled by the user in Windows.
        if isinstance(flag, (bytes, bytearray)) and flag and flag[0] & 0x01:
            return AutostartState.DISABLED_BY_WINDOWS
        return AutostartState.ENABLED

    def enable(self) -> None:
        if self._reg is None:
            return
        self._reg.set_string(RUN_KEY, VALUE_NAME, self.command)
        # Clear a "disabled in Task Manager" flag; with no flag Windows treats the entry as enabled.
        self._reg.delete(APPROVED_KEY, VALUE_NAME)
        log.info("autostart enabled: %s", self.command)

    def disable(self) -> None:
        if self._reg is None:
            return
        self._reg.delete(RUN_KEY, VALUE_NAME)
        self._reg.delete(APPROVED_KEY, VALUE_NAME)
        log.info("autostart disabled")

    def sync(self, wanted: bool) -> AutostartState:
        """Make the registry match the setting; also repairs a stale command (moved install)."""
        if self._reg is None:
            return AutostartState.UNSUPPORTED
        current = self._reg.get(RUN_KEY, VALUE_NAME)
        if wanted:
            if current != self.command:
                # Missing or stale (install moved): rewrite the command but keep a
                # "disabled in Task Manager" choice - only an explicit enable() clears that.
                self._reg.set_string(RUN_KEY, VALUE_NAME, self.command)
                log.info("autostart command updated: %s", self.command)
        elif current is not None:
            self.disable()
        return self.state()
