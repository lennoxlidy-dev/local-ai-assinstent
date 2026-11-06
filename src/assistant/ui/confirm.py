"""Permission questions as a small always-on-top dialog (until the popup does it in M4).

The gate calls ``confirm()`` on a worker thread; the dialog must live on the
GUI thread, so the request is handed over with a queued signal and the worker
waits (cancellable, with a timeout -> "no").
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from ..core.cancel import CancelToken
from ..permissions.gate import ConfirmChoice, ConfirmRequest

log = logging.getLogger(__name__)

TIMEOUT_S = 90


@dataclass
class _Pending:
    request: ConfirmRequest
    done: threading.Event = field(default_factory=threading.Event)
    choice: ConfirmChoice = ConfirmChoice.DENY


class GuiConfirmer(QObject):
    _ask = Signal(object)

    def __init__(self, title: str = "Assistant", parent: QObject | None = None):
        super().__init__(parent)
        self.title = title
        self._ask.connect(self._show, Qt.ConnectionType.QueuedConnection)

    # called on a worker thread
    def confirm(self, request: ConfirmRequest, cancel: CancelToken) -> ConfirmChoice:
        p = _Pending(request)
        self._ask.emit(p)
        waited = 0.0
        while not p.done.wait(0.2):
            waited += 0.2
            if cancel.cancelled or waited >= TIMEOUT_S:
                log.info("confirmation %s", "cancelled" if cancel.cancelled else "timed out -> deny")
                return ConfirmChoice.DENY
        return p.choice

    @Slot(object)
    def _show(self, p: _Pending) -> None:
        req = p.request
        box = QMessageBox()
        box.setWindowTitle(f"{self.title} - permission needed")
        box.setIcon(QMessageBox.Icon.Warning if req.action.risks else QMessageBox.Icon.Question)
        box.setText(f"<b>{req.action.summary}</b>")
        details = list(req.action.details)
        if req.risk_titles:
            details.append("⚠ " + "; ".join(req.risk_titles))
        details += [f"({r})" for r in req.reasons]
        box.setInformativeText("\n".join(d for d in details if d))
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        once = box.addButton("Allow once", QMessageBox.ButtonRole.AcceptRole)
        task = box.addButton("Allow for this task", QMessageBox.ButtonRole.AcceptRole) if req.allow_task else None
        always = box.addButton(f"Always allow “{req.capability_title}”", QMessageBox.ButtonRole.AcceptRole) \
            if req.allow_always else None
        deny = box.addButton("Don't allow", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(deny)
        box.setEscapeButton(deny)
        box.exec()
        clicked = box.clickedButton()
        p.choice = (ConfirmChoice.ONCE if clicked is once else ConfirmChoice.TASK if clicked is task
                    else ConfirmChoice.ALWAYS if clicked is always else ConfirmChoice.DENY)
        p.done.set()
