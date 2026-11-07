"""The permission gate: the single place that decides whether an action may run.

``evaluate`` is pure (policy + path rules + risks -> Allow/Ask/Block).
``authorize`` adds the human: on Ask it shows the *exact* action (command,
paths) through a :class:`Confirmer` (CLI prompt, popup buttons, voice
"yes"/"no") and records the answer. A "for this task" answer is remembered
only for the current task and never covers risky actions; "always" persists
the capability as Allow (offered only when nothing risky is involved).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..core.cancel import CancelToken
from .pathrules import NormPath, PathRules
from .policy import CAPS_BY_ID, LEVEL_ORDER, RISK_TITLES, Level, PolicyStore, Risk, stricter

log = logging.getLogger(__name__)


#: Capabilities that change something or can leak data. After the LLM has read untrusted
#: content, these always ask (even if set to "allow") - enforced here, not in the prompt.
SIDE_EFFECT_CAPS = frozenset({
    "apps.open", "apps.close", "files.write", "files.modify", "files.delete", "terminal.run",
    "software.install", "system.admin", "system.power", "input", "memory", "routines", "system.clipboard",
})


class ConfirmChoice(StrEnum):
    ONCE = "once"
    TASK = "task"      # allow this capability for the rest of the current task
    ALWAYS = "always"  # persist capability = allow
    DENY = "deny"


@dataclass
class Action:
    tool: str
    capability: str
    summary: str                                   # "Move file"
    details: list[str] = field(default_factory=list)  # exact command / from / to lines
    paths: list[tuple[NormPath, str]] = field(default_factory=list)  # (path, "read"|"write")
    risks: set[Risk] = field(default_factory=set)


@dataclass
class Decision:
    level: Level
    reasons: list[str] = field(default_factory=list)
    risky: bool = False


@dataclass
class ConfirmRequest:
    action: Action
    reasons: list[str]
    allow_task: bool
    allow_always: bool

    @property
    def capability_title(self) -> str:
        cap = CAPS_BY_ID.get(self.action.capability)
        return cap.title if cap else self.action.capability

    @property
    def risk_titles(self) -> list[str]:
        return [RISK_TITLES[r] for r in sorted(self.action.risks)]

    def text(self) -> str:
        lines = [self.action.summary, *[f"  {d}" for d in self.action.details]]
        if self.action.risks:
            lines.append("  ⚠ " + "; ".join(self.risk_titles))
        return "\n".join(lines)


class Confirmer(Protocol):
    def confirm(self, request: ConfirmRequest, cancel: CancelToken) -> ConfirmChoice: ...


class DenyAll:
    """Default confirmer when no UI is attached: anything that needs asking is denied."""

    def confirm(self, request: ConfirmRequest, cancel: CancelToken) -> ConfirmChoice:
        return ConfirmChoice.DENY


class AutoConfirm:
    """Tests: answer with a fixed choice and record the requests."""

    def __init__(self, choice: ConfirmChoice = ConfirmChoice.ONCE):
        self.choice = choice
        self.requests: list[ConfirmRequest] = []

    def confirm(self, request: ConfirmRequest, cancel: CancelToken) -> ConfirmChoice:
        self.requests.append(request)
        return self.choice


@dataclass
class Task:
    """One user request (and its follow-up steps). Carries cancellation and per-task grants."""

    source: str = "user"                     # "fast" | "llm" | "routine" | "cli" | "user"
    cancel: CancelToken = field(default_factory=CancelToken)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    granted: set[str] = field(default_factory=set)
    #: set once the LLM has seen untrusted content (file/screen/web/command output) in this task.
    #: From then on every side-effecting action needs a fresh confirmation (prompt-injection defense).
    tainted: bool = False
    taint_source: str = ""
    #: speak text right away (voice requests: the same sentence stream the LLM's answer uses); routines'
    #: "say" steps use it so an announcement comes when its step is reached, not after the whole routine
    say: Callable[[str], None] | None = field(default=None, repr=False, compare=False)
    #: set when the routine (or the request) is done - a routine's sound can play until then
    finished: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)


@dataclass
class AuthResult:
    allowed: bool
    decision: str          # allowed | approved | denied | blocked | cancelled
    message: str = ""
    reasons: list[str] = field(default_factory=list)


class PermissionGate:
    def __init__(self, policy: PolicyStore, rules: PathRules, confirmer: Confirmer | None = None):
        self.policy_store = policy
        self.rules = rules
        self.confirmer: Confirmer = confirmer or DenyAll()
        self._confirm_lock = threading.Lock()  # one question at a time
        self.on_policy_change: Callable[[], None] | None = None

    # ------------------------------------------------------------------ pure decision
    def evaluate(self, action: Action, task: Task | None = None) -> Decision:
        pol = self.policy_store.policy
        cap_level = pol.level(action.capability)
        reasons: list[str] = []
        if action.capability not in CAPS_BY_ID:
            return Decision(Level.BLOCK, [f"unknown capability '{action.capability}'"])
        if cap_level is Level.BLOCK:
            return Decision(Level.BLOCK, [f"'{CAPS_BY_ID[action.capability].title}' is blocked in your permissions"])

        level = cap_level
        if cap_level is Level.ASK:
            reasons.append(f"'{CAPS_BY_ID[action.capability].title}' is set to ask")
        path_levels = []
        for path, mode in action.paths:
            plevel, why = self.rules.level(path, mode, cap_level)
            path_levels.append(plevel)
            if plevel is Level.BLOCK:
                return Decision(Level.BLOCK, [f"{path}: {why}"])
            if why and plevel is Level.ASK:
                reasons.append(f"{path}: {why}")
        if path_levels:
            # Path rules refine the capability level: a trusted folder can lower ASK to ALLOW,
            # a protected/ask folder can raise it. The strictest path wins.
            level = path_levels[0]
            for pl in path_levels[1:]:
                level = stricter(level, pl)

        risky = any(pol.needs_confirm(r) for r in action.risks)
        tainted = task is not None and task.tainted and action.capability in SIDE_EFFECT_CAPS
        if risky:
            level = stricter(level, Level.ASK)
            reasons.append("risky: " + "; ".join(RISK_TITLES[r] for r in sorted(action.risks)
                                                  if pol.needs_confirm(r)))
        if tainted:
            level = stricter(level, Level.ASK)
            reasons.append(f"the assistant read untrusted content ({task.taint_source}) in this task, "  # type: ignore[union-attr]
                           "so it must not act on its own")
        elif not risky and level is Level.ASK and task is not None and action.capability in task.granted:
            level = Level.ALLOW
            reasons.append("approved earlier in this task")
        return Decision(level, reasons, risky or tainted)

    # ------------------------------------------------------------------ with the human in the loop
    def authorize(self, action: Action, task: Task) -> AuthResult:
        if task.cancel.cancelled:
            return AuthResult(False, "cancelled", "cancelled")
        decision = self.evaluate(action, task)
        if decision.level is Level.ALLOW:
            return AuthResult(True, "allowed", reasons=decision.reasons)
        if decision.level is Level.BLOCK:
            return AuthResult(False, "blocked", "Blocked: " + "; ".join(decision.reasons), decision.reasons)

        cap = CAPS_BY_ID[action.capability]
        no_path_asks = all(self.rules.level(p, m, Level.ALLOW)[0] is Level.ALLOW for p, m in action.paths)
        request = ConfirmRequest(
            action=action, reasons=decision.reasons,
            allow_task=not decision.risky,
            allow_always=(not decision.risky and Level.ALLOW in cap.choices
                          and self.policy_store.policy.level(action.capability) is Level.ASK and no_path_asks),
        )
        with self._confirm_lock:
            try:
                choice = self.confirmer.confirm(request, task.cancel)
            except Exception:
                log.exception("confirmation failed; denying")
                choice = ConfirmChoice.DENY
        if task.cancel.cancelled:
            return AuthResult(False, "cancelled", "cancelled")
        if choice is ConfirmChoice.DENY:
            return AuthResult(False, "denied", "The user declined this action.", decision.reasons)
        if choice is ConfirmChoice.TASK and request.allow_task:
            task.granted.add(action.capability)
        elif choice is ConfirmChoice.ALWAYS and request.allow_always:
            self.policy_store.set_level(action.capability, Level.ALLOW)
            log.info("capability %s set to allow by user", action.capability)
            if self.on_policy_change:
                self.on_policy_change()
        return AuthResult(True, "approved", reasons=decision.reasons)


__all__ = ["LEVEL_ORDER", "Action", "AuthResult", "AutoConfirm", "ConfirmChoice", "ConfirmRequest",
           "Confirmer", "Decision", "DenyAll", "PermissionGate", "Task"]
