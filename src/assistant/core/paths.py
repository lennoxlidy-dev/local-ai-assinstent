"""Where the assistant keeps its files.

Layout (Windows):

* ``%APPDATA%\\Assistant``       - small user data: config.toml, permissions.toml, assistant.db
* ``%LOCALAPPDATA%\\Assistant``  - big / machine-specific: models, llama.cpp binaries, logs, caches

Models are deliberately *not* in the roaming profile: they are gigabytes and
must never be synced by roaming profiles / enterprise folder redirection.

``ASSISTANT_HOME`` (env var or ``--home``) puts everything in one folder,
which is used for portable installs and for tests.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import APP_ID


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    local_dir: Path

    # --- user data (roaming) -------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def permissions_file(self) -> Path:
        return self.config_dir / "permissions.toml"

    @property
    def database(self) -> Path:
        return self.config_dir / "assistant.db"

    # --- machine data (local) ------------------------------------------------
    @property
    def logs_dir(self) -> Path:
        return self.local_dir / "logs"

    @property
    def models_dir(self) -> Path:
        return self.local_dir / "models"

    @property
    def bin_dir(self) -> Path:
        return self.local_dir / "bin"

    @property
    def cache_dir(self) -> Path:
        return self.local_dir / "cache"

    @property
    def lock_file(self) -> Path:
        return self.local_dir / "instance.lock"

    def ensure(self) -> AppPaths:
        for d in (self.config_dir, self.local_dir, self.logs_dir, self.models_dir, self.bin_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def resolve_paths(home: str | os.PathLike[str] | None = None) -> AppPaths:
    """Return the app's directories (not created yet; call ``.ensure()``)."""
    home = home or os.environ.get("ASSISTANT_HOME")
    if home:
        root = Path(home).expanduser().resolve()
        return AppPaths(config_dir=root, local_dir=root)

    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:  # development on Linux/macOS
        roaming = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        local = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return AppPaths(config_dir=roaming / APP_ID, local_dir=local / APP_ID)
