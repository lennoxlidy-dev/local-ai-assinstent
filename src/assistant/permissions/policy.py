"""Permission policy: per-capability Allow/Ask/Block, risks that always need confirmation, folder rules.

Stored in ``%APPDATA%\\Assistant\\permissions.toml``, separate from config.toml.
The assistant's own data folders are protected in code (see ``gate.py``), so
no tool - and no prompt-injected instruction - can rewrite this file.
"""

from __future__ import annotations

import logging
import os
import threading
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import tomli_w

from ..core.config import atomic_write_text

log = logging.getLogger(__name__)


class Level(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


LEVEL_ORDER = {Level.ALLOW: 0, Level.ASK: 1, Level.BLOCK: 2}


def stricter(a: Level, b: Level) -> Level:
    return a if LEVEL_ORDER[a] >= LEVEL_ORDER[b] else b


class Risk(StrEnum):
    DELETE = "delete"
    MOVE = "move"
    OVERWRITE = "overwrite"
    TERMINAL_CHANGE = "terminal_change"
    INSTALL = "install"
    CLOSE_UNSAVED = "close_unsaved"
    FORCE_CLOSE = "force_close"
    ADMIN = "admin"
    POWER = "power"


RISK_TITLES = {
    Risk.DELETE: "Deleting files (to the Recycle Bin)",
    Risk.MOVE: "Moving or renaming files",
    Risk.OVERWRITE: "Overwriting existing files",
    Risk.TERMINAL_CHANGE: "Terminal commands that may change something",
    Risk.INSTALL: "Installing or uninstalling software",
    Risk.CLOSE_UNSAVED: "Closing apps that may have unsaved work",
    Risk.FORCE_CLOSE: "Force-closing (killing) apps",
    Risk.ADMIN: "Anything that needs administrator rights",
    Risk.POWER: "Shutdown, restart, sleep, sign out",
}

#: Risks the user can never stop being asked about.
LOCKED_RISKS = frozenset({Risk.ADMIN})


class Access(StrEnum):
    FULL = "full"   # read + write without asking (trusted folder)
    READ = "read"   # read only; writes blocked (protected)
    ASK = "ask"     # every access asks
    NONE = "none"   # blocked entirely


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    description: str
    default: Level
    #: levels the user may choose (e.g. admin can never be "allow")
    choices: tuple[Level, ...] = (Level.ALLOW, Level.ASK, Level.BLOCK)


CAPABILITIES: tuple[Capability, ...] = (
    Capability("apps.open", "Open apps, files & websites", "Launch programs, open files/URLs.", Level.ALLOW),
    Capability("apps.windows", "Manage windows", "Focus, minimize, maximize and list windows.", Level.ALLOW),
    Capability("apps.close", "Close apps", "Close windows normally (apps can still ask to save).", Level.ALLOW),
    Capability("desktops", "Virtual desktops", "Switch/create desktops and move windows between them.",
               Level.ALLOW),
    Capability("media", "Media control", "Play, pause, skip, play songs.", Level.ALLOW),
    Capability("system.volume", "Volume", "Change volume and mute.", Level.ALLOW),
    Capability("system.display", "Brightness", "Change screen brightness.", Level.ALLOW),
    Capability("system.lock", "Lock PC", "Lock the workstation.", Level.ALLOW),
    Capability("system.power", "Power", "Sleep, shutdown, restart, sign out.", Level.ASK),
    Capability("system.clipboard", "Clipboard", "Read and write the clipboard.", Level.ALLOW),
    Capability("system.notify", "Timers & reminders", "Timers, reminders and notifications.", Level.ALLOW),
    Capability("screen.capture", "See the screen", "Screenshots (kept in memory), OCR, UI element lists.",
               Level.ALLOW),
    Capability("input", "Mouse & keyboard", "Move/click the mouse, type text, press hotkeys.", Level.ASK),
    Capability("files.read", "Read files", "Search, list and read files.", Level.ALLOW),
    Capability("files.write", "Create files", "Create new files and folders.", Level.ASK),
    Capability("files.modify", "Change files", "Copy, move, rename and overwrite files.", Level.ASK),
    Capability("files.delete", "Delete files", "Move files to the Recycle Bin (never permanent).", Level.ASK),
    Capability("terminal.read", "Read-only commands", "Harmless information commands (e.g. ipconfig).",
               Level.ALLOW),
    Capability("terminal.run", "Terminal commands", "PowerShell/cmd commands that may change things.",
               Level.ASK),
    Capability("software.install", "Install software", "Install/uninstall apps with winget.", Level.ASK,
               (Level.ASK, Level.BLOCK)),
    Capability("system.admin", "Administrator", "Run something elevated (always via the UAC prompt).",
               Level.ASK, (Level.ASK, Level.BLOCK)),
    Capability("memory", "Memory", "Remember and forget facts about you.", Level.ALLOW),
    Capability("routines", "Routines", "Create and run routines (each step is checked separately).",
               Level.ALLOW),
)

CAPS_BY_ID = {c.id: c for c in CAPABILITIES}


@dataclass
class FolderRule:
    path: str
    access: Access
    note: str = ""


def default_folder_rules(env: dict[str, str] | None = None) -> list[FolderRule]:
    """Windows defaults: system folders read-only, credential stores blocked."""
    env = {k.upper(): v for k, v in (env if env is not None else os.environ).items()}
    sysdrive = env.get("SYSTEMDRIVE", "C:")
    windir = env.get("WINDIR", sysdrive + "\\Windows")
    pf = env.get("PROGRAMFILES", sysdrive + "\\Program Files")
    pf86 = env.get("PROGRAMFILES(X86)", sysdrive + "\\Program Files (x86)")
    pdata = env.get("PROGRAMDATA", sysdrive + "\\ProgramData")
    home = env.get("USERPROFILE", sysdrive + "\\Users\\Default")
    appdata = env.get("APPDATA", home + "\\AppData\\Roaming")
    local = env.get("LOCALAPPDATA", home + "\\AppData\\Local")
    rules = [
        FolderRule(windir, Access.READ, "Windows"),
        FolderRule(pf, Access.READ, "Programs"),
        FolderRule(pf86, Access.READ, "Programs (x86)"),
        FolderRule(pdata, Access.READ, "Shared app data"),
        FolderRule(home + "\\.ssh", Access.NONE, "SSH keys"),
        FolderRule(home + "\\.gnupg", Access.NONE, "GPG keys"),
        FolderRule(appdata + "\\Microsoft\\Credentials", Access.NONE, "Windows credentials"),
        FolderRule(local + "\\Microsoft\\Credentials", Access.NONE, "Windows credentials"),
        FolderRule(appdata + "\\Microsoft\\Protect", Access.NONE, "Windows key store"),
        FolderRule(appdata + "\\Microsoft\\Crypto", Access.NONE, "Windows key store"),
        FolderRule(local + "\\Google\\Chrome\\User Data", Access.NONE, "Browser passwords/cookies"),
        FolderRule(local + "\\Microsoft\\Edge\\User Data", Access.NONE, "Browser passwords/cookies"),
        FolderRule(local + "\\BraveSoftware", Access.NONE, "Browser passwords/cookies"),
        FolderRule(appdata + "\\Mozilla\\Firefox\\Profiles", Access.NONE, "Browser passwords/cookies"),
        FolderRule(appdata + "\\Opera Software", Access.NONE, "Browser passwords/cookies"),
    ]
    return rules


@dataclass
class Policy:
    levels: dict[str, Level] = field(default_factory=lambda: {c.id: c.default for c in CAPABILITIES})
    always_confirm: set[Risk] = field(default_factory=lambda: set(Risk))
    folders: list[FolderRule] = field(default_factory=list)

    def level(self, capability: str) -> Level:
        cap = CAPS_BY_ID.get(capability)
        if cap is None:
            return Level.BLOCK  # unknown capability: deny by default
        lvl = self.levels.get(capability, cap.default)
        return lvl if lvl in cap.choices else cap.default

    def needs_confirm(self, risk: Risk) -> bool:
        return risk in self.always_confirm or risk in LOCKED_RISKS


class PolicyStore:
    """Load/save ``permissions.toml``; invalid entries fall back to defaults with a warning."""

    def __init__(self, path: Path | None, *, defaults_env: dict[str, str] | None = None):
        self.path = path
        self._env = defaults_env
        self.policy = Policy(folders=default_folder_rules(defaults_env) if _is_windows_env(defaults_env) else [])
        self.warnings: list[str] = []
        self._lock = threading.RLock()

    def load(self) -> Policy:
        with self._lock:
            warnings: list[str] = []
            if self.path is None or not self.path.exists():
                self.save()
                return self.policy
            try:
                raw = tomllib.loads(self.path.read_text(encoding="utf-8-sig"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
                warnings.append(f"permissions.toml is invalid ({e}); using safe defaults")
                raw = {}
            pol = Policy(folders=[])
            for cap_id, lvl in (raw.get("capabilities") or {}).items():
                cap = CAPS_BY_ID.get(cap_id)
                if cap is None:
                    warnings.append(f"unknown capability '{cap_id}' ignored")
                    continue
                try:
                    level = Level(str(lvl).lower())
                except ValueError:
                    warnings.append(f"'{cap_id}': invalid level {lvl!r}")
                    continue
                if level not in cap.choices:
                    warnings.append(f"'{cap_id}' cannot be '{level}'; using '{cap.default}'")
                    continue
                pol.levels[cap_id] = level
            if "always_confirm" in raw:
                risks = set()
                for r in raw.get("always_confirm") or []:
                    try:
                        risks.add(Risk(r))
                    except ValueError:
                        warnings.append(f"unknown risk '{r}' ignored")
                pol.always_confirm = risks | set(LOCKED_RISKS)
            if "folders" in raw:
                for item in raw.get("folders") or []:
                    try:
                        pol.folders.append(FolderRule(str(item["path"]), Access(str(item["access"]).lower()),
                                                      str(item.get("note", ""))))
                    except (KeyError, ValueError, TypeError):
                        warnings.append(f"invalid folder rule {item!r} ignored")
            else:
                pol.folders = list(self.policy.folders)
            self.policy = pol
            self.warnings = warnings
            for w in warnings:
                log.warning("permissions: %s", w)
            return pol

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            p = self.policy
            data = {
                "version": 1,
                "capabilities": {c.id: str(p.level(c.id)) for c in CAPABILITIES},
                "always_confirm": sorted(str(r) for r in p.always_confirm),
                "folders": [{"path": f.path, "access": str(f.access), **({"note": f.note} if f.note else {})}
                            for f in p.folders],
            }
            header = ("# Assistant permissions. Levels: allow | ask | block.\n"
                      "# Folder access: full (trusted) | read (protected) | ask | none (blocked).\n\n")
            atomic_write_text(self.path, header + tomli_w.dumps(data))

    # ---- mutations (used by the settings UI and the "always allow" answer) ----
    def set_level(self, capability: str, level: Level) -> None:
        cap = CAPS_BY_ID[capability]
        if level not in cap.choices:
            raise ValueError(f"{capability} cannot be set to {level}")
        with self._lock:
            self.policy.levels[capability] = level
            self.save()

    def set_always_confirm(self, risk: Risk, on: bool) -> None:
        if not on and risk in LOCKED_RISKS:
            raise ValueError(f"'{risk}' always needs confirmation")
        with self._lock:
            (self.policy.always_confirm.add if on else self.policy.always_confirm.discard)(risk)
            self.save()

    def set_folder(self, path: str, access: Access | None, note: str = "") -> None:
        """Add/replace a folder rule; ``access=None`` removes it."""
        with self._lock:
            key = path.rstrip("\\/").casefold()
            self.policy.folders = [f for f in self.policy.folders if f.path.rstrip("\\/").casefold() != key]
            if access is not None:
                self.policy.folders.append(FolderRule(path, access, note))
            self.save()


def _is_windows_env(env: dict[str, str] | None) -> bool:
    if env is not None:
        return any(k.upper() == "WINDIR" for k in env)
    return os.name == "nt"
