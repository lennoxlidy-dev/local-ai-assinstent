"""``--self-test``: start with a throw-away home, open every window once, report problems, quit.

For checking a build (the .exe or a fresh install): a missing Qt module or a broken QML file
shows up here as a message instead of as an empty window. Exit code 0 = passed, 1 = problems.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

if TYPE_CHECKING:
    from .app import TrayApp

log = logging.getLogger(__name__)
QML_DIR = Path(__file__).parent / "qml"
_QML_MSG = re.compile(r"\.qml\b|QQml|\bQML\b|is not installed|is not a type")


def compile_qml(qml_dir: Path = QML_DIR) -> tuple[int, list[str]]:
    """Compile every QML file (syntax, imports, types) without creating any of them."""
    engine = QQmlEngine()
    files = sorted(qml_dir.rglob("*.qml"))
    errors: list[str] = []
    for f in files:
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(f)), engine)
        if comp.isError():
            errors += [e.toString() for e in comp.errors()]
    engine.deleteLater()
    return len(files), errors


class _Collector(logging.Handler):
    """Errors from anywhere, plus QML warnings (Qt's messages go to the "qt" logger)."""

    def __init__(self, sink: list[str]):
        super().__init__(logging.WARNING)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR or (record.name == "qt" and _QML_MSG.search(msg)):
            self.sink.append(f"{record.levelname.lower()}: {msg.splitlines()[0] if msg else ''}")


class SelfTest(QObject):
    """Steps through the windows once the app runs; ``result`` becomes the exit code."""

    def __init__(self, tray: TrayApp, *, out: Callable[[str], None] = print):
        super().__init__()
        self.tray = tray
        self.out = out
        self.problems: list[str] = []
        self.result = 1
        self._done = False
        self._collector = _Collector(self.problems)
        self._steps: Iterator[int] = self._run()

    def start(self) -> None:
        logging.getLogger().addHandler(self._collector)
        QTimer.singleShot(0, self._next)

    def _next(self) -> None:
        try:
            delay = next(self._steps)
        except StopIteration:
            self._finish()
            return
        except Exception as e:  # noqa: BLE001 - a broken step is a test result, not a crash
            log.exception("self-test step failed")
            self.problems.append(f"{type(e).__name__}: {e}")
            self._finish()
            return
        QTimer.singleShot(delay, self._next)

    def _step(self, text: str, ok: bool = True) -> None:
        self.out(f"  {'ok  ' if ok else 'FAIL'}  {text}")
        if not ok:
            self.problems.append(text)

    def _run(self) -> Iterator[int]:
        from .settings import TABS

        n, errors = compile_qml()
        self.problems += errors
        self._step(f"{n} QML files compile", not errors)
        self._step("popup window loaded", self.tray.popup.view is not None)
        for _ in range(600):                   # the tool system starts right after the tray
            if self.tray.tools is not None:
                break
            yield 100
        self._step("assistant started", self.tray.tools is not None)
        if self.tray.tools is None:
            return
        yield 1500                             # first start: the setup wizard opens by itself
        if self.tray.setup_window is None:
            self.tray.open_setup(first_run=False)
            yield 1500
        self._step("setup wizard opens", self.tray.setup_window is not None)

        self.tray.open_settings()
        yield 1000
        win = self.tray.settings_window
        self._step("settings window opens", win is not None)
        if win is not None:
            before = len(self.problems)
            for tab in TABS:
                win.model.call("tab", {"name": tab})
                yield 400
            self._step(f"all {len(TABS)} settings tabs load", len(self.problems) == before)
            if win.window is not None:
                win.window.close()             # like the X button: must free it and resume what it paused
            yield 600
            self._step("settings window closes", self.tray.settings_window is None)

        self.tray.popup.open_chat()
        yield 800
        self.tray.popup.collapse()
        yield 300
        self._step("popup chat opens")

    def abort(self, reason: str) -> None:
        self.problems.append(reason)
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        logging.getLogger().removeHandler(self._collector)
        if self.problems:
            self.out(f"self-test FAILED ({len(self.problems)} problem(s)):")
            for p in dict.fromkeys(self.problems):          # unique, in order
                self.out(f"  - {p}")
        else:
            self.out("self-test passed")
        self.result = 1 if self.problems else 0
        self.tray.quit()


__all__ = ["SelfTest", "compile_qml"]
