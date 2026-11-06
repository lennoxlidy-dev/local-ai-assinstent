"""One tray instance per user.

A ``QLockFile`` (auto-released if the process dies) decides who runs; a
per-user ``QLocalServer`` (a named pipe on Windows, restricted to the current
user) lets a second launch hand its request ("show settings") to the running
instance instead of starting twice - e.g. autostart + a manual double-click.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def _server_name(lock_path: Path) -> str:
    # Per user and per data dir (so a portable/test instance doesn't collide with the real one).
    tag = hashlib.sha1(f"{getpass.getuser()}|{lock_path.resolve()}".encode()).hexdigest()[:12]
    return f"assistant-{tag}"


class SingleInstance(QObject):
    message_received = Signal(str)

    def __init__(self, lock_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._lock = QLockFile(str(lock_path))
        self._name = _server_name(lock_path)
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """True if we are the primary instance (and now listening for messages)."""
        if not self._lock.tryLock(200):
            return False
        QLocalServer.removeServer(self._name)  # stale socket file (Unix only)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._name):
            log.warning("single-instance server failed: %s", server.errorString())
        else:
            server.newConnection.connect(self._on_connection)
            self._server = server
        return True

    def send(self, message: str, timeout_ms: int = 1500) -> bool:
        """Send a message to the primary instance."""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write(message.encode("utf-8") + b"\n")
        sock.flush()
        # waitForBytesWritten() returns False when flush() already wrote everything.
        ok = sock.bytesToWrite() == 0 or sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            sock.waitForDisconnected(timeout_ms)
        return ok

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def _on_connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            buf = bytearray()

            def on_ready(sock: QLocalSocket = sock, buf: bytearray = buf) -> None:
                buf.extend(sock.readAll().data())
                while b"\n" in buf:
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf[:] = rest
                    msg = line.decode("utf-8", "replace").strip()
                    if msg:
                        self.message_received.emit(msg[:200])

            sock.readyRead.connect(on_ready)
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                on_ready()
