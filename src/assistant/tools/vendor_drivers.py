"""Driver updates from the makers themselves - next to Windows Update (``drivers.py``), which often lags behind.

* **NVIDIA**: the newest Game Ready driver for this GPU - the same package the NVIDIA App installs (the app itself
  can't be driven by another program). NVIDIA's driver service says which one is newest (no admin rights needed);
  installing is part of the one elevated step: download from NVIDIA's server into a folder only administrators can
  write, refuse it unless it is signed by NVIDIA, run it silently, read the driver version back.
* **Dell Command | Update** (business Dells): its command line installs the driver updates it finds - drivers only,
  no BIOS or firmware.
* **The makers' own apps** without a command line (NVIDIA App, Lenovo Vantage, MyASUS, Armoury Crate, MSI Center,
  HP Support Assistant, Intel Driver & Support Assistant, AMD Software, ...): found among the installed apps and
  named, so "open Lenovo Vantage" opens it for the rest.

Everything that goes into the elevated script is checked here first - NVIDIA's download address and version (text
from the internet is data, never commands) and the Dell path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any

from .base import ToolContext, ToolError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------- NVIDIA

#: NVIDIA's driver service (the one its driver download page uses): the newest driver for a product id
LOOKUP_URL = ("https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
              "?func=DriverManualLookup&pfid={pfid}&osID={os_id}&dch=1")
OS_ID = 57                   # "Windows 10 64-bit": NVIDIA's packages for Windows 10 and 11 are the same
LOOKUP_TIMEOUT_S = 60.0
#: the package's own switches: silent (lowercase s: unpacking and installing), no restart, no licence page
NV_ARGS = ("-s", "-noreboot", "-noeula")

_NV_URL = re.compile(r"^https://(?:[a-z0-9-]+\.)?download\.nvidia\.com/Windows/[A-Za-z0-9._/-]+\.exe$")
_NV_VERSION = re.compile(r"^\d{3}\.\d{2}$")
_VARIANT = re.compile(r"( \([A-Z]+\)| [0-9]+GB| COLLECTORS EDITION| with Max-Q Design)$")

# (Windows PowerShell: the same web stack the maintained nvidia-update script uses against this service)
LOOKUP_PS = """$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
(Invoke-WebRequest -Uri '__URL__' -UseBasicParsing -TimeoutSec 30).Content"""

_NV_PART = r"""$out.nvidia = & { $dir = Join-Path $env:ProgramFiles 'Assistant\driver-download'
try { if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
$null = New-Item -ItemType Directory -Path $dir -Force
if ((Get-PSDrive ($env:SystemDrive.Trim(':'))).Free -lt 4GB) { throw 'less than 4 GB free on the system drive' }
$f = Join-Path $dir '__FILE__'
if (Get-Command curl.exe -ErrorAction Ignore) {
  & curl.exe -fsSL --retry 3 -o $f '__URL__'; if ($LASTEXITCODE) { throw "the download failed (curl $LASTEXITCODE)" } }
else { Invoke-WebRequest -Uri '__URL__' -OutFile $f -UseBasicParsing }
$sig = Get-AuthenticodeSignature -FilePath $f
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=NVIDIA Corporation') {
  throw "the download is not signed by NVIDIA ($($sig.Status))" }
$p = Start-Process -FilePath $f -ArgumentList __ARGS__ -PassThru; $null = $p.Handle; $p.WaitForExit()
$t = (Get-Date).AddMinutes(30)
while ((Get-Date) -lt $t -and (Get-Process setup -ErrorAction Ignore | Where-Object { $_.Path -like "$env:SystemDrive\NVIDIA\*" })) {
  Start-Sleep 2 }
$v = [string](Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match '^NVIDIA' } | Select-Object -First 1).DriverVersion
$v = $v -replace '\.', ''
@{ code = $p.ExitCode; now = $(if ($v.Length -ge 5) { $v.Substring($v.Length - 5).Insert(3, '.') } else { '' }) }
} catch { @{ error = $_.Exception.Message } } finally { Remove-Item $dir -Recurse -Force -ErrorAction Ignore } }"""


@dataclass
class NvidiaDriver:
    """This PC's NVIDIA graphics driver: installed and newest version (or why the newest isn't known)."""

    gpu: str                     # "GeForce RTX 4070 Laptop GPU"
    installed: str = ""          # "566.36"
    latest: str = ""             # "572.16"
    url: str = ""                # NVIDIA's download address
    name: str = ""               # "GeForce Game Ready Driver"
    size: str = ""               # as NVIDIA says it: "665.43 MB"
    date: str = ""
    problem: str = ""            # why the newest driver couldn't be checked

    @property
    def newer(self) -> bool:
        return bool(self.latest and self.url) and version_key(self.latest) > version_key(self.installed)

    @property
    def file(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    def as_data(self) -> dict[str, Any]:
        return {**asdict(self), "newer": self.newer}


def version_key(v: str) -> tuple[int, ...]:
    """'566.36' -> (566, 36); anything unreadable sorts first."""
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def clean_gpu_name(name: str) -> str:
    """'NVIDIA GeForce RTX 4070 Laptop GPU' -> 'GeForce RTX 4070 Laptop GPU' (as in NVIDIA's product list)."""
    n = re.sub(r"^NVIDIA\s+", "", " ".join(name.split()))
    n = _VARIANT.sub("", n).strip()
    return re.sub(r"\bSuper\b", "SUPER", n)


def product_id(gpu: str, notebook: bool) -> int | None:
    """NVIDIA's product id of a GPU (cleaned name); laptop GPUs are looked up in the notebook list first."""
    from .nvidia_ids import GPU_IDS

    kinds = ("notebook", "desktop") if notebook else ("desktop", "notebook")
    for kind in kinds:
        if gpu in GPU_IDS[kind]:
            return GPU_IDS[kind][gpu]
    low = gpu.casefold()
    for kind in kinds:
        for name, pfid in GPU_IDS[kind].items():
            if name.casefold() == low:
                return pfid
    return None


def _is_notebook(name: str) -> bool:
    if "laptop" in name.lower():
        return True
    try:
        import psutil

        return psutil.sensors_battery() is not None      # (older laptop GPUs have desktop-like names)
    except Exception:
        return False


def json_line(text: str) -> Any:
    """The JSON line of a PowerShell output (ignores anything printed before it)."""
    for line in reversed([x.strip().lstrip("\ufeff") for x in (text or "").splitlines() if x.strip()]):
        if line[:1] in "[{":
            return json.loads(line)
    raise ValueError("no JSON in the output")


def parse_lookup(text: str) -> dict[str, str] | None:
    """NVIDIA's answer -> {version, url, name, size, date}; None: no driver for it. Raises ValueError if odd."""
    try:
        data = json.loads((text or "").strip().lstrip("\ufeff"))   # (the whole answer - it may span lines)
    except json.JSONDecodeError:
        data = json_line(text)
    if not isinstance(data, dict) or str(data.get("Success")) != "1":
        return None
    ids = data.get("IDS") or []
    info = ids[0].get("downloadInfo") if ids and isinstance(ids[0], dict) else None
    if not isinstance(info, dict) or str(info.get("Success", "1")) != "1":
        return None
    version, url = str(info.get("Version", "")).strip(), str(info.get("DownloadURL", "")).strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]        # (NVIDIA's download server speaks HTTPS too)
    if not _NV_VERSION.match(version):
        raise ValueError(f"unexpected driver version {version[:40]!r}")
    if not _NV_URL.match(url) or ".." in url:
        raise ValueError("unexpected download address")
    return {"version": version, "url": url, "name": urllib.parse.unquote(str(info.get("Name", ""))).strip()[:80],
            "size": str(info.get("DownloadURLFileSize", "")).strip()[:20],
            "date": str(info.get("ReleaseDateTime", "")).strip()[:40]}


def nvidia_driver(ctx: ToolContext) -> NvidiaDriver | None:
    """None if this PC has no NVIDIA graphics card; otherwise its driver and NVIDIA's newest (or the problem)."""
    from ..core import hardware

    try:
        gpus = hardware.nvidia_gpus()
    except Exception as e:
        log.info("no NVIDIA GPU info: %s", e)
        gpus = []
    if not gpus:
        return None
    g = gpus[0]
    d = NvidiaDriver(gpu=clean_gpu_name(g.name), installed=(g.driver or "").strip())
    pfid = product_id(d.gpu, _is_notebook(g.name))
    if pfid is None:
        d.problem = f"the {d.gpu} isn't in NVIDIA's driver list yet - the NVIDIA App can still update it"
        return d
    url = LOOKUP_URL.format(pfid=pfid, os_id=OS_ID)
    out = ctx.platform.shell.run(LOOKUP_PS.replace("__URL__", url), "powershell", LOOKUP_TIMEOUT_S, ctx.task.cancel)
    if out.timed_out:
        d.problem = "NVIDIA's driver service didn't answer in time"
        return d
    try:
        info = parse_lookup(out.stdout)
    except (ValueError, json.JSONDecodeError, AttributeError) as e:
        msg = (out.stderr or out.stdout or "").strip().splitlines()
        d.problem = ("NVIDIA's driver service couldn't be asked"
                     + (f": {msg[-1][:160]}" if msg and out.code else f" ({e})"))
        return d
    if info is None:
        d.problem = f"NVIDIA's driver service has no Windows driver for the {d.gpu}"
        return d
    d.latest, d.url, d.name, d.size, d.date = info["version"], info["url"], info["name"], info["size"], info["date"]
    log.info("NVIDIA %s: driver %s installed, %s available", d.gpu, d.installed or "?", d.latest)
    return d


def nvidia_part(d: NvidiaDriver) -> str:
    """The NVIDIA piece of the elevated script (address and file name checked again here)."""
    if not (_NV_URL.match(d.url) and ".." not in d.url and _NV_VERSION.match(d.latest)):
        raise ToolError("Unexpected NVIDIA download address.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.exe", d.file):
        raise ToolError("Unexpected NVIDIA file name.")
    args = ",".join(f"'{a}'" for a in NV_ARGS)
    return _NV_PART.replace("__URL__", d.url).replace("__FILE__", d.file).replace("__ARGS__", args)


# ---------------------------------------------------------------------------------------------- Dell

DCU_PATHS = (r"%ProgramFiles%\Dell\CommandUpdate\dcu-cli.exe", r"%ProgramFiles(x86)%\Dell\CommandUpdate\dcu-cli.exe")
DCU_ARGS = ("/applyUpdates", "-updateType=driver", "-reboot=disable", "-silent")
#: dcu-cli exit codes (Dell's documentation)
DCU_CODES = {0: "installed the driver updates it found", 1: "installed driver updates (a restart finishes them)",
             2: "an unknown error occurred", 3: "this is not a Dell PC", 5: "an earlier update still needs a restart",
             6: "it is already running", 7: "this Dell model isn't supported", 500: "no driver updates"}

_DELL_PART = r"""$out.dell = & { try {
$p = Start-Process -FilePath '__DCU__' -ArgumentList __ARGS__ -Wait -PassThru -WindowStyle Hidden
@{ code = $p.ExitCode } } catch { @{ error = $_.Exception.Message } } }"""


def _dcu_candidates() -> list[str]:
    out = []
    for p in DCU_PATHS:
        path = os.path.expandvars(p)
        if "%" not in path:
            out.append(path)
    return out


def dell_command_update() -> str | None:
    """The command line of Dell Command | Update, if it is installed (its Store version has none)."""
    for path in _dcu_candidates():
        if os.path.isfile(path):
            return path
    return None


def dell_command(path: str) -> str:
    return f"{path} {' '.join(DCU_ARGS)}"


def dell_part(path: str) -> str:
    if path not in _dcu_candidates() or "'" in path:
        raise ToolError("Unexpected Dell Command | Update path.")
    return _DELL_PART.replace("__DCU__", path).replace("__ARGS__", ",".join(f"'{a}'" for a in DCU_ARGS))


def dell_result(code: int) -> str:
    return DCU_CODES.get(code, f"stopped with code {code}")


# ---------------------------------------------------------------------------------------------- makers' apps

#: driver / update apps of GPU and PC makers (as their Start menu entries begin)
MAKER_APPS = ("NVIDIA App", "GeForce Experience", "AMD Software", "Intel Driver & Support Assistant",
              "Lenovo Vantage", "Legion Space", "Lenovo System Update", "MyASUS", "Armoury Crate", "MSI Center",
              "Dragon Center", "HP Support Assistant", "OMEN Gaming Hub", "SupportAssist", "Dell Update",
              "Alienware Update", "Alienware Command Center", "Acer Care Center", "Care Center",
              "GIGABYTE Control Center")


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[®™©]", "", name).casefold().split())


def maker_apps(ctx: ToolContext) -> list[str]:
    """The makers' driver/update apps installed here, by their names in the Start menu."""
    svc = ctx.services.get("apps")
    if svc is None:
        return []
    try:
        apps = svc.apps()
    except Exception:
        return []
    found: list[str] = []
    for want in MAKER_APPS:
        w = _norm(want)
        for a in apps:
            n = _norm(a.name)
            if (n == w or (n.startswith(w) and not n[len(w)].isalnum())) and a.name not in found:
                found.append(a.name)
                break
    return found


__all__ = ["DCU_ARGS", "LOOKUP_URL", "MAKER_APPS", "NV_ARGS", "NvidiaDriver", "clean_gpu_name", "dell_command",
           "dell_command_update", "dell_part", "dell_result", "json_line", "maker_apps", "nvidia_driver",
           "nvidia_part", "parse_lookup", "product_id", "version_key"]
