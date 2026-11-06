"""Memory tools: remember / recall / forget facts about the user."""

from __future__ import annotations

from typing import Any

from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .schema import Bool, Int, Str, obj


def _store(ctx: ToolContext):  # type: ignore[no-untyped-def]
    m = ctx.services.get("memory")
    if m is None:
        raise ToolError("memory is not available")
    return m


def _run_remember(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    f = _store(ctx).add(a["fact"], source=ctx.task.source)
    return ToolResult(True, f"I'll remember that: {f.text}", data={"id": f.id})


def _run_recall(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    facts = _store(ctx).search(a.get("query", ""), a["limit"])
    if not facts:
        return ToolResult(True, "I don't have anything remembered about that.")
    return ToolResult(True, "\n".join(f"#{f.id}: {f.text}" for f in facts))


def _plan_forget(a: dict[str, Any], ctx: ToolContext) -> Plan:
    if "id" not in a and not a.get("query"):
        raise ToolError("Say what to forget (text or id).")
    return Plan(f"Forget: {a.get('query') or '#' + str(a['id'])}", capability="memory")


def _run_forget(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    store = _store(ctx)
    if "id" in a:
        ok = store.delete(a["id"])
        return ToolResult(ok, "Forgotten." if ok else "No fact with that id.")
    matches = store.search(a["query"], 5)
    if not matches:
        return ToolResult(False, "I don't have anything like that remembered.")
    if len(matches) > 1 and not a.get("all"):
        listing = "\n".join(f"#{f.id}: {f.text}" for f in matches)
        return ToolResult(False, f"Several facts match; which one (id)?\n{listing}")
    for f in matches:
        store.delete(f.id)
    return ToolResult(True, f"Forgotten: {matches[0].text}" if len(matches) == 1 else f"Forgot {len(matches)} facts.")


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "remember", "Store a fact about the user for later (they said 'remember that ...').",
        obj(("fact",), fact=Str("The fact, as a short self-contained sentence", min_len=3, max_len=500)),
        "memory", lambda a, c: Plan(f"Remember: {a['fact']}", capability="memory"), _run_remember))
    reg.register(Tool(
        "recall", "Look up remembered facts about the user.",
        obj((), query=Str("What to look for; empty = everything", max_len=200),
            limit=Int("Max facts", min=1, max=50, default=10)),
        "memory", lambda a, c: Plan("Recall facts", capability="memory"), _run_recall))
    reg.register(Tool(
        "forget", "Delete a remembered fact.",
        obj((), query=Str("Text of the fact to forget", max_len=200), id=Int("Fact id", min=1),
            all=Bool("Forget all matching facts", default=False)),
        "memory", _plan_forget, _run_forget))
