"""Assemble the tool system: platform backends, permission gate, action log, services, registry."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.context import AppContext
from ..core.events import Topic
from ..permissions.gate import Confirmer, PermissionGate, Task
from ..permissions.pathrules import PathError, PathNormalizer, PathRules
from ..permissions.policy import Level, PolicyStore
from ..storage.action_log import ActionLog
from ..storage.db import Database
from ..storage.memory import MemoryStore
from ..storage.routines import RoutineStore
from ..storage.timers import TimerEntry, TimerStore
from . import apps, drivers, files, media, memory, routines, screen, system, terminal
from .apps import AppService
from .base import ToolRegistry, ToolResult
from .file_index import FileIndex
from .platform import Platform, create_platform
from .timers import TimerService

log = logging.getLogger(__name__)

FOLDER_WORDS = {
    "desktop": ("desktop", "schreibtisch"), "documents": ("documents", "dokumente", "my documents", "eigene dateien"),
    "downloads": ("downloads",), "pictures": ("pictures", "bilder", "photos", "fotos"), "music": ("music", "musik"),
    "videos": ("videos", "filme"), "home": ("home",),
}


def folder_aliases(known: dict[str, str]) -> dict[str, str]:
    out = {}
    for key, words in FOLDER_WORDS.items():
        if known.get(key):
            for w in words:
                out[w] = known[key]
    return out


@dataclass
class ToolSystem:
    registry: ToolRegistry
    gate: PermissionGate
    policy: PolicyStore
    db: Database
    action_log: ActionLog
    platform: Platform
    normalizer: PathNormalizer
    timers: TimerService
    file_index: FileIndex | None = None
    services: dict[str, Any] = field(default_factory=dict)

    def invoke(self, name: str, args: dict[str, Any] | None, task: Task | None = None) -> ToolResult:
        return self.registry.invoke(name, args, task or Task(source="user"))

    def reload_policy(self) -> None:
        self.policy.load()
        rules = _rules(self.normalizer, self.policy, self.services["internal_dirs"])
        self.gate.rules = rules
        self.services["path_rules"] = rules

    def stop(self) -> None:
        self.timers.stop()
        if self.file_index is not None:
            self.file_index.stop()


def _rules(normalizer: PathNormalizer, policy: PolicyStore, internal: list[str]) -> PathRules:
    return PathRules(normalizer, policy.policy.folders, internal=internal)


def build_tool_system(ctx: AppContext, *, platform: Platform | None = None, confirmer: Confirmer | None = None,
                      db: Database | None = None, start_background: bool = True,
                      normalizer: PathNormalizer | None = None) -> ToolSystem:
    platform = platform or create_platform(ctx.paths)
    db = db or Database(ctx.paths.database)
    if normalizer is None:
        normalizer = PathNormalizer(home=platform.known_folders.get("home"),
                                    aliases=folder_aliases(platform.known_folders))
    policy = PolicyStore(ctx.paths.permissions_file)
    policy.load()
    internal = [str(ctx.paths.config_dir), str(ctx.paths.local_dir)]
    rules = _rules(normalizer, policy, internal)
    gate = PermissionGate(policy, rules, confirmer)
    action_log = ActionLog(db)

    def on_timer(t: TimerEntry) -> None:
        title = "Timer" if t.kind == "timer" else "Reminder"
        ctx.bus.publish(Topic.NOTIFY, title=title, message=t.label or ("Time's up!" if t.kind == "timer" else ""),
                        level="alarm")

    timers = TimerService(TimerStore(db), on_timer)
    services: dict[str, Any] = {
        "config_get": ctx.config.get,
        "memory": MemoryStore(db),
        "routines": RoutineStore(db),
        "timers": timers,
        "path_rules": rules,
        "internal_dirs": internal,
        "apps": AppService(platform, ctx.config.get),
    }
    file_index = None
    if platform.search is None or not ctx.config.data.files.use_everything:
        platform.search = None if not ctx.config.data.files.use_everything else platform.search

        def roots() -> list[str]:
            base = [platform.known_folders.get(k) for k in ("desktop", "documents", "downloads", "pictures",
                                                            "music", "videos", "home")]
            extra = list(ctx.config.data.files.index_roots)
            seen, out = set(), []
            for r in [*base, *extra]:
                if r and Path(r).is_dir() and r.casefold() not in seen:
                    seen.add(r.casefold())
                    out.append(r)
            return out

        def allowed(path: str) -> bool:
            try:
                return services["path_rules"].level(normalizer.normalize(path), "read", Level.ALLOW)[0] \
                    is not Level.BLOCK
            except PathError:
                return False

        file_index = FileIndex(ctx.paths.cache_dir / "files.db", roots, allowed)
        services["file_index"] = file_index

    from ..audio.sound import SoundEngine

    if isinstance(platform.sound, SoundEngine):    # routine sounds play where the assistant's voice plays
        platform.sound.device = lambda: ctx.config.data.voice.output_device
    registry = ToolRegistry(gate, action_log, platform, normalizer, bus=ctx.bus, services=services)
    for module in (files, apps, media, system, screen, terminal, drivers, memory, routines):
        module.register(registry)
    def policy_changed() -> None:
        ctx.bus.publish("permissions.changed")

    gate.on_policy_change = policy_changed

    if start_background:
        timers.start()
        if file_index is not None and platform.name == "windows":
            file_index.start()
    log.info("tools ready: %d tools, platform=%s, search=%s", len(registry.names()), platform.name,
             platform.search.name if platform.search else ("file index" if file_index else "none"))
    return ToolSystem(registry, gate, policy, db, action_log, platform, normalizer, timers, file_index, services)
