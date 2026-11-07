"""Write packaging/assistant.ico: the tray orb at 16-256 px (PNG-compressed ICO, Windows Vista+)."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main() -> int:
    app = QGuiApplication([])  # noqa: F841 - QPixmap needs an application
    from assistant.ui.icons import orb_icon

    icon = orb_icon("#7C5CFF")
    pngs = []
    for s in SIZES:
        pm = icon.pixmap(s, s)
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.toImage().save(buf, "PNG")
        pngs.append((s, bytes(data.data())))
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for s, png in pngs:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    out = ROOT / "packaging" / "assistant.ico"
    out.write_bytes(header + entries + blobs)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
