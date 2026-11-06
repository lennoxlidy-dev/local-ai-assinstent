"""Bootstrapping shared by the tray app and the CLI."""

from __future__ import annotations

import logging
import platform
import sys
import threading
from dataclasses import dataclass, field

from .. import __version__
from .autostart import Autostart
from .config import ConfigStore
from .events import EventBus, Topic
from .logging_setup import setup_logging
from .paths import AppPaths, resolve_paths
from .state import AppState

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Everything a component needs, passed explicitly (no globals -> easy to test)."""

    mode: str                 # "tray" | "cli"
    paths: AppPaths
    config: ConfigStore
    bus: EventBus
    state: AppState
    autostart: Autostart
    hardware: dict = field(default_factory=dict)

    def apply_autostart(self) -> None:
        if self.mode == "tray":  # the CLI never touches login items
            st = self.autostart.sync(self.config.data.general.autostart)
            log.info("autostart: %s", st)


def bootstrap(mode: str, *, home: str | None = None, console_log: bool = False,
              console_level: str = "INFO", detect_hardware: str = "background") -> AppContext:
    """Create directories, logging and config. ``detect_hardware``: "now" | "background" | "off"."""
    paths = resolve_paths(home).ensure()
    log_name = "assistant" if mode == "tray" else "cli"

    # Logging first with defaults (so config warnings are captured) ...
    setup_logging(paths.logs_dir, name=log_name, console=console_log, console_level=console_level)
    store = ConfigStore(paths.config_file)
    store.load()
    # ... then again with the user's levels.
    dbg = store.data.debug
    setup_logging(paths.logs_dir, name=log_name, level=dbg.log_level, debug_log=dbg.debug_log,
                  console=console_log, console_level=console_level)

    bus = EventBus()
    def config_changed(keys: list[str]) -> None:
        bus.publish(Topic.CONFIG_CHANGED, keys=keys)

    store.on_change(config_changed)
    ctx = AppContext(mode=mode, paths=paths, config=store, bus=bus, state=AppState(bus),
                     autostart=Autostart())

    log.info("Assistant %s starting (%s) - Python %s on %s", __version__, mode,
             platform.python_version(), platform.platform())
    log.info("config: %s | data: %s", paths.config_dir, paths.local_dir)

    if detect_hardware == "now":
        _detect_hw(ctx)
    elif detect_hardware == "background":
        threading.Thread(target=_detect_hw, args=(ctx,), name="hw-detect", daemon=True).start()
    return ctx


def _detect_hw(ctx: AppContext) -> None:
    from . import hardware

    try:
        info = hardware.detect()
    except Exception:  # pragma: no cover - never fatal
        log.exception("hardware detection failed")
        return
    ctx.hardware = info.as_dict()
    gpu = info.best_gpu
    log.info("hardware: %s | %d/%d cores | RAM %d MB (%d free) | GPU %s",
             info.cpu, info.physical_cores, info.logical_cores, info.ram_total_mb, info.ram_available_mb,
             f"{gpu.name} {gpu.vram_total_mb} MB ({gpu.vram_free_mb} free)" if gpu else "none (CPU only)")


def fix_std_streams() -> None:
    """Under pythonw.exe stdout/stderr are None; give them a sink so libraries can't crash on write."""
    import os

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
