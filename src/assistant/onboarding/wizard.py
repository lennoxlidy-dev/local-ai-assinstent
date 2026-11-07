"""The wizard's state machine: one question at a time, back/skip, answers applied immediately.

Applying right away means a changed language switches the next questions, a chosen voice is
used for the next spoken question, and nothing is lost if the window is closed half-way.
Skipping a question applies its (shown) default, so what you see is what you get.
"""

from __future__ import annotations

import logging
from typing import Any

from .env import WizardEnv
from .interpret import Interpretation, interpret
from .questions import QUESTIONS, Question, T, label

log = logging.getLogger(__name__)


class Wizard:
    def __init__(self, env: WizardEnv, questions: list[Question] | None = None):
        self.env = env
        self.questions = list(questions if questions is not None else QUESTIONS)
        self._by_id = {q.id: q for q in self.questions}
        self.answers: dict[str, Any] = {}
        self.skipped: set[str] = set()
        self.history: list[int] = []
        self.pos = 0
        self.done = False
        self._seek(0, +1)

    # ------------------------------------------------------------------ state
    def default(self, q: Question) -> Any:
        try:
            return q.default(self.env)
        except Exception:
            log.exception("default for %s failed", q.id)
            return None

    def value_of(self, qid: str) -> Any:
        """Answer if given, else the default (used by ``when`` conditions)."""
        if qid in self.answers:
            return self.answers[qid]
        q = self._by_id.get(qid)
        return self.default(q) if q is not None else None

    def visible(self, q: Question) -> bool:
        try:
            return q.when is None or bool(q.when(self.env, self.value_of))
        except Exception:
            log.exception("condition for %s failed", q.id)
            return True

    @property
    def current(self) -> Question | None:
        return self.questions[self.pos] if 0 <= self.pos < len(self.questions) else None

    @property
    def at_end(self) -> bool:
        return self.current is None

    def value(self) -> Any:
        q = self.current
        return None if q is None else self.value_of(q.id)

    def progress(self) -> tuple[int, int]:
        """(1-based number of the current question, number of questions that will be shown)."""
        shown = [i for i, q in enumerate(self.questions) if self.visible(q)]
        if self.current is None:
            return len(shown), len(shown)
        return (shown.index(self.pos) + 1 if self.pos in shown else 1), len(shown)

    def _seek(self, start: int, step: int) -> None:
        i = start
        while 0 <= i < len(self.questions) and not self.visible(self.questions[i]):
            i += step
        self.pos = i if i >= 0 else self._first_visible()

    def _first_visible(self) -> int:
        return next((i for i, q in enumerate(self.questions) if self.visible(q)), len(self.questions))

    # ------------------------------------------------------------------ moves
    def answer(self, value: Any) -> T | None:
        """Validate + apply the answer to the current question and move on. Returns an error text or None."""
        q = self.current
        if q is None:
            return None
        err = self._apply(q, value)
        if err is not None:
            return err
        self.answers[q.id] = value
        self.skipped.discard(q.id)
        self._next()
        return None

    def set_value(self, value: Any) -> T | None:
        """Apply without moving on (live changes on a page: microphone, voice preview, language)."""
        q = self.current
        if q is None:
            return None
        err = self._apply(q, value)
        if err is None:
            self.answers[q.id] = value
        return err

    def skip(self) -> None:
        """Keep the default: it is applied, so the shown suggestion becomes the setting."""
        q = self.current
        if q is None:
            return
        if q.id not in self.answers:
            default = self.default(q)
            if default is not None and self._apply(q, default) is not None:
                log.info("default for %s could not be applied", q.id)
        self.skipped.add(q.id)
        self._next()

    def back(self) -> bool:
        if not self.history:
            return False
        self.pos = self.history.pop()
        return True

    def handle_text(self, text: str) -> Interpretation:
        """A typed/spoken reply: answer, navigate, or report it as unclear (caller handles 'repeat'/'start')."""
        q = self.current
        if q is None:
            return Interpretation("unclear", heard=text)
        it = interpret(q, text, self.env)
        if it.action == "answer":
            err = self.answer(it.value)
            if err is not None:
                return Interpretation("invalid", err, text)
        elif it.action == "skip":
            self.skip()
        elif it.action == "back":
            self.back()
        return it

    def _next(self) -> None:
        self.history.append(self.pos)
        self._seek(self.pos + 1, +1)
        if self.pos >= len(self.questions):
            self.pos = len(self.questions)

    def _apply(self, q: Question, value: Any) -> T | None:
        if q.validate is not None:
            err = q.validate(self.env, value)
            if err is not None:
                return err
        try:
            q.apply(self.env, value)
        except Exception as e:
            log.exception("applying %s failed", q.id)
            return {"en": f"That didn't work: {e}", "de": f"Das hat nicht geklappt: {e}"}
        self.env.ctx.bus.publish("onboarding.answer", id=q.id)
        return None

    def finish(self, apply_remaining: bool = True) -> None:
        """Done (or closed early): the remaining questions keep their defaults."""
        if apply_remaining:
            while self.current is not None:
                self.skip()
        self.env.ctx.config.set("general.onboarding_done", True)
        self.done = True
        self.env.ctx.bus.publish("onboarding.done")
        log.info("onboarding finished (%d answered, %d skipped)", len(self.answers), len(self.skipped))

    def text(self, t: T | None) -> str:
        return label(t, self.env.lang)


__all__ = ["Wizard"]
