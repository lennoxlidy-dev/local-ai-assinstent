# PyInstaller spec - a folder with two programs sharing all files:
#   Assistant.exe     the tray app (no console window)
#   AssistantCLI.exe  console: debug CLI, --setup (downloads), --voice-test, --say
# Build:  scripts\build-exe.bat   (output: dist\Assistant\)
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
SRC = ROOT / "src"
WIN = sys.platform == "win32"
ICON = str(ROOT / "packaging" / "assistant.ico")

datas = [(str(SRC / "assistant" / "ui" / "qml"), "assistant/ui/qml"), (ICON, "packaging")]
binaries = []
for pkg in ("sherpa_onnx",):
    datas += collect_data_files(pkg)
    binaries += collect_dynamic_libs(pkg)
# (the PortAudio DLL of sounddevice comes in through PyInstaller's own sounddevice hook)
hidden = collect_submodules("assistant") + ["sentencepiece", "soxr", "tomli_w", "rapidfuzz", "send2trash",
                                            "pypdfium2", "sounddevice", "psutil"]
if WIN:
    hidden += collect_submodules("winrt") + collect_submodules("pyvda") + collect_submodules("pycaw")
    hidden += ["pythoncom", "pywintypes", "win32api", "win32con", "win32gui", "win32process", "win32clipboard",
               "win32com.client", "comtypes", "comtypes.client", "uiautomation", "mss"]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PySide6.QtWebEngineCore", "PySide6.QtMultimedia"],
    noarchive=False,
)
# Qt files the app never loads: other Quick Controls styles (all QML uses QtQuick.Controls.Basic),
# QML dialogs and test modules, Qt's own translations (no QTranslator) and the software OpenGL
# DLL (Qt Quick runs on its software renderer). About 45 MB; `AssistantCLI.exe --self-test`
# (run by build-exe.ps1) checks that every window still loads.
_DROP_DIRS = tuple(f"qml/qtquick/controls/{style}/" for style in (
    "fluentwinui3", "material", "universal", "imagine", "fusion", "windows", "macos", "ios", "designer"))
_DROP_DIRS += ("qml/qtquick/dialogs/", "qml/qtquick/virtualkeyboard/", "qml/qttest/", "qml/qt/labs/stylekit/")
_DROP_LIBS = ("quickcontrols2fluentwinui3", "quickcontrols2material", "quickcontrols2universal", "quickcontrols2imagine",
              "quickcontrols2fusion", "quickcontrols2windowsstyleimpl", "quickcontrols2macos", "quickcontrols2ios",
              "labsstylekit", "quickdialogs2", "qt6quicktest", "qt6test", "opengl32sw")


def _keep(entry) -> bool:
    dest = entry[0].replace("\\", "/").lower()
    if not dest.startswith("pyside6/"):
        return True
    name = dest.rsplit("/", 1)[-1]
    return not ("/translations/" in dest or any(d in dest for d in _DROP_DIRS) or any(t in name for t in _DROP_LIBS))


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)  # noqa: F821
common = dict(exclude_binaries=True, debug=False, strip=False, upx=False, icon=ICON)
gui = EXE(pyz, a.scripts, [], name="Assistant", console=False, **common)  # noqa: F821
cli = EXE(pyz, a.scripts, [], name="AssistantCLI", console=True, **common)  # noqa: F821
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="Assistant")  # noqa: F821
