"""Install / update llama.cpp (Windows release build) and download models.

Release layout (checked against llama.cpp's release workflow, Nov 2026):

* ``llama-<tag>-bin-win-cuda-<12.4|13.5>-x64.zip`` - full toolset (llama-server.exe ...) + ggml-cuda.dll
* ``cudart-llama-bin-win-cuda-<ver>-x64.zip``      - CUDA runtime DLLs (extract next to the exe)
* ``llama-<tag>-bin-win-vulkan-x64.zip`` / ``...-win-cpu-x64.zip`` - fallbacks

CUDA 13.x needs an NVIDIA driver >= 580; otherwise we take the 12.4 build.
Each version is extracted into its own folder and ``current.json`` is switched
atomically afterwards, so a failed update never breaks the working install.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.cancel import CancelToken
from .download import USER_AGENT, Progress, download
from .models import ModelSpec, model_files

log = logging.getLogger(__name__)

RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"


@dataclass
class Asset:
    name: str
    url: str
    size: int
    sha256: str | None


def _assets(release: dict[str, Any]) -> list[Asset]:
    out = []
    for a in release.get("assets", []):
        digest = a.get("digest") or ""
        out.append(Asset(a["name"], a["browser_download_url"], int(a.get("size", 0)),
                         digest.split(":", 1)[1] if digest.startswith("sha256:") else None))
    return out


def driver_major(driver: str | None) -> int | None:
    m = re.match(r"(\d+)", driver or "")
    return int(m.group(1)) if m else None


def select_assets(release: dict[str, Any], *, backend: str = "auto", driver: str | None = None,
                  arch: str = "x64") -> tuple[str, list[Asset]]:
    """Choose the zip(s) for this PC. Returns (backend_label, assets)."""
    assets = _assets(release)
    by_name = {a.name: a for a in assets}

    def main_zip(kind: str) -> Asset | None:
        pat = re.compile(rf"^llama-[\w.-]+-bin-win-{re.escape(kind)}-{arch}\.zip$")
        return next((a for a in assets if pat.match(a.name)), None)

    cuda_versions = sorted({m.group(1) for a in assets
                            if (m := re.match(rf"^llama-[\w.-]+-bin-win-cuda-([\d.]+)-{arch}\.zip$", a.name))},
                           key=lambda v: tuple(int(x) for x in v.split(".")))
    if backend in ("auto", "cuda") and cuda_versions and (backend == "cuda" or driver is not None):
        major = driver_major(driver)
        usable = [v for v in cuda_versions if int(v.split(".")[0]) < 13 or (major is not None and major >= 580)]
        if usable:
            ver = usable[-1]
            z = main_zip(f"cuda-{ver}")
            rt = by_name.get(f"cudart-llama-bin-win-cuda-{ver}-{arch}.zip")
            if z is not None:
                return f"cuda-{ver}", [z] + ([rt] if rt else [])
    for kind in (["vulkan", "cpu"] if backend in ("auto", "vulkan") else ["cpu"]):
        z = main_zip(kind)
        if z is not None:
            return kind, [z]
    raise RuntimeError("no suitable llama.cpp Windows build in the latest release")


def fetch_release(timeout: float = 20) -> dict[str, Any]:
    req = urllib.request.Request(RELEASE_API, headers={"User-Agent": USER_AGENT,
                                                        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def current_server(bin_dir: Path) -> Path | None:
    """Path of the installed llama-server, if any."""
    root = Path(bin_dir) / "llama"
    try:
        info = json.loads((root / "current.json").read_text(encoding="utf-8"))
        exe = root / info["folder"] / EXE
        return exe if exe.exists() else None
    except (OSError, ValueError, KeyError):
        return None


def installed_version(bin_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((Path(bin_dir) / "llama" / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def install_llama(bin_dir: Path, cache_dir: Path, *, driver: str | None, backend: str = "auto",
                  release: dict[str, Any] | None = None, progress: Progress | None = None,
                  cancel: CancelToken | None = None) -> Path:
    """Download + extract the right build. Returns the path to llama-server.exe."""
    release = release or fetch_release()
    tag = release.get("tag_name", "unknown")
    label, assets = select_assets(release, backend=backend, driver=driver)
    current = installed_version(bin_dir)
    root = Path(bin_dir) / "llama"
    folder = f"{tag}-{label}"
    if current and current.get("folder") == folder and current_server(bin_dir):
        log.info("llama.cpp %s (%s) is already installed", tag, label)
        return current_server(bin_dir)  # type: ignore[return-value]
    target = root / folder
    tmp = root / (folder + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for a in assets:
        z = download(a.url, Path(cache_dir) / a.name, sha256=a.sha256, size=a.size or None, progress=progress,
                     cancel=cancel)
        with zipfile.ZipFile(z) as zf:
            for member in zf.infolist():
                name = Path(member.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise RuntimeError(f"unsafe path in {a.name}: {member.filename}")
                # flatten "llama-bXXXX/..." style prefixes: everything belongs next to the exe
                dest = tmp / name.name if len(name.parts) > 1 and not member.is_dir() else tmp / name
                if member.is_dir():
                    continue
                with zf.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    if not (tmp / EXE).exists():
        raise RuntimeError(f"{EXE} missing from the downloaded build")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(tmp, target)
    info = {"tag": tag, "backend": label, "folder": folder}
    (root / "current.json.tmp").write_text(json.dumps(info), encoding="utf-8")
    os.replace(root / "current.json.tmp", root / "current.json")
    # keep only the current and one previous version
    for old in sorted((p for p in root.iterdir() if p.is_dir() and p.name not in (folder,)),
                      key=lambda p: p.stat().st_mtime)[:-1]:
        shutil.rmtree(old, ignore_errors=True)
    log.info("installed llama.cpp %s (%s) in %s", tag, label, target)
    return target / EXE


def download_model(spec: ModelSpec, models_dir: Path, *, vision: bool = True, progress: Progress | None = None,
                   cancel: CancelToken | None = None) -> tuple[Path, Path | None]:
    w, mm = model_files(spec, models_dir, vision=vision)
    download(spec.weights.url, w, sha256=spec.weights.sha256, size=spec.weights.size, progress=progress,
             cancel=cancel)
    if mm is not None and spec.mmproj is not None:
        download(spec.mmproj.url, mm, sha256=spec.mmproj.sha256, size=spec.mmproj.size, progress=progress,
                 cancel=cancel)
    return w, mm
