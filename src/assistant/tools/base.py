"""Tool definitions, the registry, and the one code path every tool call takes:

    validate args -> plan (exact paths/command/risks) -> permission gate -> action log -> run -> log outcome
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.cancel import Cancelled
from ..core.events import EventBus
from ..core.timing import now_ms
from ..permissions.gate import Action, PermissionGate, Task
from ..permissions.pathrules import NormPath, PathError, PathNormalizer
from ..permissions.policy import Risk
from ..storage.action_log import ActionLog
from .schema import Obj, SchemaError

if TYPE_CHECKING:
    from .platform import Platform

log = logging.getLogger(__name__)


class ToolError(Exception):
    """An expected failure with a message meant for the user/LLM ("file not found")."""


@dataclass
class ToolResult:
    ok: bool
    text: str                     # concise; goes to the LLM and the popup
    data: Any = None              # structured data for the UI
    untrusted: bool = False       # content from files/screen/web: the agent must treat it as DATA


@dataclass
class Plan:
    summary: str
    details: list[str] = field(default_factory=list)
    paths: list[tuple[str, str]] = field(default_factory=list)  # (raw path, "read"|"write")
    risks: set[Risk] = field(default_factory=set)
    capability: str | None = None    # overrides Tool.capability (e.g. terminal.read vs terminal.run)
    state: dict[str, Any] = field(default_factory=dict)  # precomputed values handed to run()


@dataclass
class ToolContext:
    """What a tool may use. Everything OS-specific lives behind ``platform``."""

    platform: Platform
    normalizer: PathNormalizer
    task: Task
    bus: EventBus | None = None
    services: dict[str, Any] = field(default_factory=dict)  # memory, routines, timers, config, registry...
    resolved: dict[str, NormPath] = field(default_factory=dict)  # raw path -> canonical path (from planning)

    def path(self, raw: str) -> NormPath:
        """Canonical path for a raw argument (already checked by the gate)."""
        if raw in self.resolved:
            return self.resolved[raw]
        try:
            return self.normalizer.normalize(raw)
        except PathError as e:
            raise ToolError(str(e)) from None

    def check_cancel(self) -> None:
        self.task.cancel.check()


Planner = Callable[[dict[str, Any], ToolContext], Plan]
Runner = Callable[[dict[str, Any], ToolContext, Plan], ToolResult]


@dataclass
class Tool:
    name: str
    description: str
    params: Obj
    capability: str
    plan: Planner
    run: Runner
    llm: bool = True     # offer to the LLM (some tools are fast-path/UI only)

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function definition (what llama.cpp's server expects)."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.params.to_json()}}


class ToolRegistry:
    def __init__(self, gate: PermissionGate, action_log: ActionLog, platform: Platform,
                 normalizer: PathNormalizer, *, bus: EventBus | None = None,
                 services: dict[str, Any] | None = None):
        self.gate = gate
        self.action_log = action_log
        self.platform = platform
        self.normalizer = normalizer
        self.bus = bus
        self.services = services if services is not None else {}
        self.services.setdefault("registry", self)
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------ registration
    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, only: set[str] | None = None) -> list[dict[str, Any]]:
        return [t.schema() for n, t in sorted(self._tools.items()) if t.llm and (only is None or n in only)]

    def context(self, task: Task) -> ToolContext:
        return ToolContext(platform=self.platform, normalizer=self.normalizer, task=task, bus=self.bus,
                           services=self.services)

    # ------------------------------------------------------------------ the one way to run a tool
    def invoke(self, name: str, args: dict[str, Any] | None, task: Task) -> ToolResult:
        tool = self._tools.get(name)
        args = args if isinstance(args, dict) else {}
        if tool is None:
            self.action_log.begin(tool=name, args=args, decision="invalid", task_id=task.id, source=task.source,
                                  result="unknown tool")
            return ToolResult(False, f"Unknown tool '{name}'. Available: {', '.join(self.names())}")
        if task.cancel.cancelled:
            return ToolResult(False, "Cancelled.")

        dropped: list[str] = []
        try:
            clean = tool.params.validate(args, "args", dropped)
        except SchemaError as e:
            self.action_log.begin(tool=name, args=args, decision="invalid", task_id=task.id, source=task.source,
                                  capability=tool.capability, result=str(e))
            return ToolResult(False, f"Invalid arguments for {name}: {e}")
        if dropped:
            log.info("tool %s: ignored unknown argument(s) %s", name, ", ".join(dropped))

        ctx = self.context(task)
        try:
            plan = tool.plan(clean, ctx)
            norm_paths: list[tuple[NormPath, str]] = []
            for raw, mode in plan.paths:
                np = self.normalizer.normalize(raw)
                ctx.resolved[raw] = np
                norm_paths.append((np, mode))
        except (ToolError, PathError) as e:
            self.action_log.begin(tool=name, args=clean, decision="invalid", task_id=task.id, source=task.source,
                                  capability=tool.capability, result=str(e))
            return ToolResult(False, str(e))

        capability = plan.capability or tool.capability
        action = Action(tool=name, capability=capability, summary=plan.summary, details=plan.details,
                        paths=norm_paths, risks=set(plan.risks))
        auth = self.gate.authorize(action, task)
        entry = self.action_log.begin(tool=name, args=clean, decision=auth.decision, task_id=task.id,
                                      source=task.source, capability=capability, summary=_one_line(plan),
                                      risks=[str(r) for r in plan.risks],
                                      result=auth.message if not auth.allowed else None)
        self._publish("tool.start" if auth.allowed else "tool.refused", name=name, summary=plan.summary,
                      decision=auth.decision, task_id=task.id)
        if not auth.allowed:
            return ToolResult(False, auth.message or f"Not allowed ({auth.decision}).")

        t0 = now_ms()
        try:
            task.cancel.check()
            result = tool.run(clean, ctx, plan)
            outcome = "ok" if result.ok else "error"
        except Cancelled:
            result, outcome = ToolResult(False, "Cancelled."), "cancelled"
        except ToolError as e:
            result, outcome = ToolResult(False, str(e)), "error"
        except Exception as e:  # never let a tool crash the assistant
            log.exception("tool %s failed", name)
            result, outcome = ToolResult(False, f"{name} failed: {e.__class__.__name__}: {e}"), "error"
        ms = now_ms() - t0
        self.action_log.finish(entry, outcome=outcome, result=result.text[:500], duration_ms=ms)
        log.debug("TIMING tool=%s ms=%.1f outcome=%s", name, ms, outcome)
        self._publish("tool.done", name=name, ok=result.ok, text=result.text[:300], task_id=task.id)
        return result

    def _publish(self, topic: str, **data: Any) -> None:
        if self.bus is not None:
            self.bus.publish(topic, **data)


def _one_line(plan: Plan) -> str:
    s = plan.summary
    if plan.details:
        s += " | " + " | ".join(plan.details)
    return s[:500]


def describe_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)[:300]


def now() -> float:
    return time.time()
