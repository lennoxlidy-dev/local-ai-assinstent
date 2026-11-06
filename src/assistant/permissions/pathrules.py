"""Path normalization and folder-rule matching - the part of the gate attackers would aim at.

Windows paths have many spellings for the same file. Before comparing a path
with a folder rule we canonicalize it, otherwise ``C:\\Windows.\\System32``,
``c:/WINDOWS/system32``, ``C:\\PROGRA~1``, a junction, or ``\\\\?\\C:\\Windows``
would all slip past a naive prefix check. We:

* expand ``%VARS%`` and ``~``; reject device paths (``\\\\.\\``), alternate data
  streams (``file:stream``), reserved names (``NUL``, ``COM1``...), control chars
* collapse ``.``/``..``, strip trailing dots/spaces per component (Win32 does that too)
* resolve symlinks/junctions/8.3 names with ``realpath`` (on Windows) and compare
  case-insensitively.

The Windows logic is implemented with ``ntpath`` so it is unit-tested on Linux too.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .policy import Access, FolderRule, Level


class PathError(ValueError):
    pass


_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)$", re.IGNORECASE)
_ENV = re.compile(r"%([^%\\/:*?\"<>|]+)%")


@dataclass(frozen=True)
class NormPath:
    path: str  # canonical display form
    key: str   # comparison form (case-folded on Windows)

    def __str__(self) -> str:
        return self.path


class PathNormalizer:
    """Canonicalize user/LLM supplied paths. ``flavor`` = "windows" | "posix"."""

    def __init__(self, flavor: str | None = None, *, env: Mapping[str, str] | None = None,
                 resolver: Callable[[str], str] | None = None, home: str | None = None,
                 aliases: Mapping[str, str] | None = None):
        self.flavor = flavor or ("windows" if os.name == "nt" else "posix")
        self.env = {k.upper(): v for k, v in (env if env is not None else os.environ).items()}
        if resolver is None and self.flavor == ("windows" if os.name == "nt" else "posix"):
            resolver = os.path.realpath  # resolve links/junctions only for the real host flavor
        self.resolver = resolver
        self.home = home or (self.env.get("USERPROFILE") if self.flavor == "windows" else self.env.get("HOME")) or ""
        #: Leading folder words a model may use: "Downloads\\x.pdf" -> real Downloads folder
        self.aliases = {k.casefold(): v for k, v in (aliases or {}).items()}

    # ------------------------------------------------------------------ public
    def normalize(self, raw: str, *, base: str | None = None) -> NormPath:
        if not isinstance(raw, str) or not raw.strip():
            raise PathError("empty path")
        if any(ord(c) < 32 for c in raw):
            raise PathError("path contains control characters")
        if self.flavor == "windows":
            return self._windows(raw.strip(), base)
        return self._posix(raw.strip(), base)

    def key(self, raw: str) -> str:
        return self.normalize(raw).key

    # ------------------------------------------------------------------ windows
    def _windows(self, p: str, base: str | None) -> NormPath:
        p = p.strip('"')
        p = _ENV.sub(lambda m: self.env.get(m.group(1).upper(), m.group(0)), p)
        if "%" in p and _ENV.search(p):
            raise PathError(f"unknown environment variable in {p!r}")
        p = p.replace("/", "\\")
        if p == "~" or p.startswith("~\\"):
            p = self.home + p[1:]
        p = self._strip_prefix(p)
        if not self._win_is_abs(p):
            if re.match(r"^[A-Za-z]:(?!\\)", p):
                raise PathError(f"drive-relative paths are not allowed: {p!r}")
            first, _, rest = p.partition("\\")
            if first.casefold() in self.aliases:
                p = self.aliases[first.casefold()] + ("\\" + rest if rest else "")
            elif base:
                p = ntpath.join(base, p)
            elif self.home:
                p = ntpath.join(self.home, p)
            else:
                raise PathError(f"relative path without a base folder: {p!r}")
        p = ntpath.normpath(p)
        p = self._clean_components(p)
        if self.resolver is not None:
            try:
                resolved = self.resolver(p)
            except (OSError, ValueError) as e:
                raise PathError(f"cannot resolve {p!r}: {e}") from None
            p = self._clean_components(ntpath.normpath(self._strip_prefix(resolved.replace("/", "\\"))))
        return NormPath(p, ntpath.normcase(p).casefold())

    @staticmethod
    def _win_is_abs(p: str) -> bool:
        return bool(re.match(r"^[A-Za-z]:\\", p)) or p.startswith("\\\\")

    @staticmethod
    def _strip_prefix(p: str) -> str:
        if p.startswith("\\\\?\\UNC\\"):
            return "\\\\" + p[8:]
        if p.startswith("\\\\?\\") and re.match(r"^[A-Za-z]:", p[4:]):
            return p[4:]
        if p.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
            raise PathError(f"device paths are not allowed: {p!r}")
        return p

    @staticmethod
    def _clean_components(p: str) -> str:
        drive, rest = ntpath.splitdrive(p)
        if not drive:
            raise PathError(f"not an absolute path: {p!r}")
        if drive.startswith("\\\\"):
            parts = [x for x in drive.split("\\") if x]
            if len(parts) < 2:
                raise PathError(f"incomplete network path: {p!r}")
        parts = []
        for comp in rest.split("\\"):
            if not comp:
                continue
            cleaned = comp.rstrip(" .")
            if not cleaned:
                raise PathError(f"invalid path component {comp!r}")
            if ":" in cleaned:
                raise PathError(f"alternate data streams are not allowed: {comp!r}")
            if any(ch in cleaned for ch in '<>"|?*'):
                raise PathError(f"invalid characters in {comp!r}")
            if _RESERVED.match(cleaned.split(".")[0].rstrip(" ")):
                raise PathError(f"reserved device name: {comp!r}")
            parts.append(cleaned)
        return drive + "\\" + "\\".join(parts) if parts else drive + "\\"

    # ------------------------------------------------------------------ posix (development only)
    def _posix(self, p: str, base: str | None) -> NormPath:
        p = os.path.expandvars(p) if self.env is os.environ else p
        if p == "~" or p.startswith("~/"):
            p = self.home + p[1:]
        if not p.startswith("/"):
            first, _, rest = p.partition("/")
            if first.casefold() in self.aliases:
                p = self.aliases[first.casefold()] + ("/" + rest if rest else "")
            else:
                p = posixpath.join(base or self.home or "/", p)
        p = posixpath.normpath(p)
        if self.resolver is not None:
            p = posixpath.normpath(self.resolver(p))
        return NormPath(p, p)


def is_within(key: str, root_key: str, sep: str) -> bool:
    if key == root_key:
        return True
    root = root_key if root_key.endswith(sep) else root_key + sep
    return key.startswith(root)


class PathRules:
    """Decide what a (normalized) path allows, from folder rules + internal protections."""

    def __init__(self, normalizer: PathNormalizer, rules: list[FolderRule], *, internal: Sequence[str] = ()):
        self.norm = normalizer
        self.sep = "\\" if normalizer.flavor == "windows" else "/"
        self._internal = [self._safe_key(p) for p in internal]
        self._rules: list[tuple[str, FolderRule]] = []
        for r in rules:
            k = self._safe_key(r.path)
            if k:
                self._rules.append((k, r))
        self._rules.sort(key=lambda x: len(x[0]), reverse=True)  # most specific first

    def _safe_key(self, p: str) -> str:
        try:
            return self.norm.normalize(p).key
        except PathError:
            return ""

    def rule_for(self, path: NormPath) -> FolderRule | None:
        for k, r in self._rules:
            if is_within(path.key, k, self.sep):
                return r
        return None

    def is_internal(self, path: NormPath) -> bool:
        return any(k and is_within(path.key, k, self.sep) for k in self._internal)

    def level(self, path: NormPath, mode: str, capability_level: Level) -> tuple[Level, str]:
        """Effective level for ``mode`` ("read"/"write") on ``path`` given the capability's level."""
        if mode == "write" and self.is_internal(path):
            return Level.BLOCK, "the assistant's own settings and data are protected"
        rule = self.rule_for(path)
        if capability_level is Level.BLOCK:
            return Level.BLOCK, "this capability is blocked"
        if rule is None:
            return capability_level, ""
        label = f"{rule.note or 'folder rule'}: {rule.path}"
        if rule.access is Access.NONE:
            return Level.BLOCK, f"blocked folder ({label})"
        if rule.access is Access.READ:
            return (capability_level, "") if mode == "read" else (Level.BLOCK, f"protected folder ({label})")
        if rule.access is Access.ASK:
            return Level.ASK, f"folder needs confirmation ({label})"
        # FULL: trusted folder
        return Level.ALLOW, f"trusted folder ({label})"
