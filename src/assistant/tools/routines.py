"""Routines: named multi-step macros. Each step is a normal tool call and passes the permission gate."""

from __future__ import annotations

from typing import Any

from ..storage.routines import Routine
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .schema import Arr, Obj, Str, obj

#: Pseudo-steps understood by the runner in addition to real tools.
PSEUDO = {"wait": "seconds", "say": "text"}
NOT_IN_ROUTINES = {"run_routine", "create_routine", "delete_routine"}
MAX_STEPS = 25


def validate_steps(reg: ToolRegistry, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check every step against the tool's schema now, not when the routine runs."""
    if not steps:
        raise ToolError("A routine needs at least one step.")
    if len(steps) > MAX_STEPS:
        raise ToolError(f"At most {MAX_STEPS} steps.")
    clean = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
            raise ToolError(f"Step {i} must look like {{\"tool\": \"open_app\", \"args\": {{...}}}}.")
        name, args = step["tool"], step.get("args") or {}
        if name == "wait":
            secs = args.get("seconds", 1)
            if not isinstance(secs, (int, float)) or not 0 < secs <= 120:
                raise ToolError(f"Step {i}: wait needs 0 < seconds <= 120.")
            clean.append({"tool": "wait", "args": {"seconds": secs}})
            continue
        if name == "say":
            clean.append({"tool": "say", "args": {"text": str(args.get("text", ""))[:300]}})
            continue
        if name in NOT_IN_ROUTINES:
            raise ToolError(f"Step {i}: routines can't contain {name}.")
        tool = reg.get(name)
        if tool is None:
            raise ToolError(f"Step {i}: unknown tool '{name}'.")
        try:
            clean.append({"tool": name, "args": tool.params.validate(args, f"step{i}.args")})
        except ValueError as e:
            raise ToolError(f"Step {i}: {e}") from None
    return clean


def run_routine(reg: ToolRegistry, routine: Routine, ctx: ToolContext) -> ToolResult:
    lines, ok_all = [], True
    for i, step in enumerate(routine.steps, 1):
        ctx.check_cancel()
        name, args = step["tool"], step.get("args", {})
        if name == "wait":
            if ctx.task.cancel.wait(float(args.get("seconds", 1))):
                ctx.check_cancel()
            continue
        if name == "say":
            lines.append(args.get("text", ""))
            continue
        res = reg.invoke(name, args, ctx.task)
        ok_all &= res.ok
        lines.append(f"{i}. {name}: {res.text.splitlines()[0] if res.text else ('ok' if res.ok else 'failed')}")
    head = f"Routine “{routine.name}” {'done' if ok_all else 'finished with problems'}."
    return ToolResult(ok_all, head + "\n" + "\n".join(x for x in lines if x))


def _store(ctx: ToolContext):  # type: ignore[no-untyped-def]
    s = ctx.services.get("routines")
    if s is None:
        raise ToolError("routines are not available")
    return s


def _plan_run(a: dict[str, Any], ctx: ToolContext) -> Plan:
    if ctx.task.source == "routine":
        raise ToolError("Routines can't start other routines.")
    r = _store(ctx).get(a["name"])
    if r is None:
        names = ", ".join(x.name for x in _store(ctx).all()) or "none yet"
        raise ToolError(f"No routine called “{a['name']}”. Routines: {names}.")
    return Plan(f"Run routine “{r.name}” ({len(r.steps)} steps)", [f"{s['tool']} {s.get('args', {})}" for s in r.steps],
                capability="routines", state={"routine": r})


def _run_run(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    previous = ctx.task.source
    ctx.task.source = "routine"
    try:
        return run_routine(ctx.services["registry"], plan.state["routine"], ctx)
    finally:
        ctx.task.source = previous


def _plan_create(a: dict[str, Any], ctx: ToolContext) -> Plan:
    steps = validate_steps(ctx.services["registry"], a["steps"])
    return Plan(f"Save routine “{a['name']}”", [f"{s['tool']} {s['args']}" for s in steps], capability="routines",
                state={"steps": steps})


def _run_create(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    r = _store(ctx).save(Routine(a["name"].strip(), plan.state["steps"], a.get("description", "")))
    return ToolResult(True, f"Saved routine “{r.name}” with {len(r.steps)} steps.")


def _run_list(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    rs = _store(ctx).all()
    if not rs:
        return ToolResult(True, "No routines yet.")
    return ToolResult(True, "\n".join(f"- {r.name}: " + ", ".join(s["tool"] for s in r.steps) for r in rs))


def _run_delete(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    ok = _store(ctx).delete(a["name"])
    return ToolResult(ok, f"Deleted routine “{a['name']}”." if ok else f"No routine called “{a['name']}”.")


def register(reg: ToolRegistry) -> None:
    name = Str("Routine name, e.g. 'gaming mode'", min_len=1, max_len=80)
    step = Obj(description="One step", props={"tool": Str("Tool name, or 'wait' / 'say'", min_len=1),
                                              "args": Obj(description="Arguments for the tool", open=True)},
               required=("tool",))
    reg.register(Tool("run_routine", "Run one of the user's saved routines by name.",
                      obj(("name",), name=name), "routines", _plan_run, _run_run))
    reg.register(Tool(
        "create_routine", "Save a named routine (sequence of tool calls), e.g. gaming mode = open Steam, open "
                          "Discord, volume 40.",
        obj(("name", "steps"), name=name, description=Str("What it does", max_len=200),
            steps=Arr("Steps in order", items=step, min_items=1, max_items=MAX_STEPS)),
        "routines", _plan_create, _run_create))
    reg.register(Tool("list_routines", "List saved routines.", obj(), "routines",
                      lambda a, c: Plan("List routines", capability="routines"), _run_list))
    reg.register(Tool("delete_routine", "Delete a saved routine.", obj(("name",), name=name), "routines",
                      lambda a, c: Plan(f"Delete routine “{a['name']}”", capability="routines"), _run_delete))
