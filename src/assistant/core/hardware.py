"""Runtime hardware detection (RAM, CPU, NVIDIA VRAM).

VRAM comes from NVML (``nvml.dll`` ships with every NVIDIA driver, so no
extra install) via ctypes: fast and no subprocess. ``nvidia-smi`` is the
fallback. Nothing here needs admin rights.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field

import psutil

from .proc import run_hidden

log = logging.getLogger(__name__)


@dataclass
class GpuInfo:
    name: str
    vram_total_mb: int
    vram_free_mb: int
    driver: str = ""
    source: str = "nvml"


@dataclass
class HardwareInfo:
    os: str
    cpu: str
    physical_cores: int
    logical_cores: int
    ram_total_mb: int
    ram_available_mb: int
    gpus: list[GpuInfo] = field(default_factory=list)

    @property
    def best_gpu(self) -> GpuInfo | None:
        return max(self.gpus, key=lambda g: g.vram_total_mb, default=None)

    def as_dict(self) -> dict:
        return asdict(self)


def detect() -> HardwareInfo:
    vm = psutil.virtual_memory()
    info = HardwareInfo(
        os=_os_name(),
        cpu=_cpu_name(),
        physical_cores=psutil.cpu_count(logical=False) or 0,
        logical_cores=psutil.cpu_count(logical=True) or 0,
        ram_total_mb=vm.total // 2**20,
        ram_available_mb=vm.available // 2**20,
    )
    info.gpus = nvidia_gpus()
    return info


def nvidia_gpus() -> list[GpuInfo]:
    try:
        gpus = _nvml_gpus()
        if gpus:
            return gpus
    except Exception as e:  # missing driver, old driver, no GPU
        log.debug("NVML unavailable: %s", e)
    return _smi_gpus()


def vram_free_mb() -> int | None:
    """Free VRAM on the biggest NVIDIA GPU, or None if unknown."""
    gpus = nvidia_gpus()
    return max((g.vram_free_mb for g in gpus), default=None)


# ----------------------------------------------------------------------------- NVML

class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


def _load_nvml() -> ctypes.CDLL:
    if sys.platform == "win32":
        candidates = ["nvml.dll",
                      os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                   "NVIDIA Corporation", "NVSMI", "nvml.dll")]
    else:
        candidates = ["libnvidia-ml.so.1", "libnvidia-ml.so"]
    last: Exception | None = None
    for c in candidates:
        try:
            return ctypes.CDLL(c)
        except OSError as e:
            last = e
    raise OSError(f"NVML not found ({last})")


def _nvml_gpus() -> list[GpuInfo]:
    lib = _load_nvml()
    for fn in ("nvmlInit_v2", "nvmlShutdown", "nvmlDeviceGetCount_v2", "nvmlDeviceGetHandleByIndex_v2",
               "nvmlDeviceGetName", "nvmlDeviceGetMemoryInfo", "nvmlSystemGetDriverVersion"):
        getattr(lib, fn).restype = ctypes.c_int
    lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    lib.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
    lib.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)]
    lib.nvmlSystemGetDriverVersion.argtypes = [ctypes.c_char_p, ctypes.c_uint]

    if lib.nvmlInit_v2() != 0:
        raise OSError("nvmlInit failed")
    try:
        count = ctypes.c_uint(0)
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return []
        drv = ctypes.create_string_buffer(96)
        driver = drv.value.decode(errors="replace") if lib.nvmlSystemGetDriverVersion(drv, 96) == 0 else ""
        gpus = []
        for i in range(count.value):
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(handle)) != 0:
                continue
            name = ctypes.create_string_buffer(96)
            lib.nvmlDeviceGetName(handle, name, 96)
            mem = _NvmlMemory()
            if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) != 0:
                continue
            gpus.append(GpuInfo(name=name.value.decode(errors="replace"),
                                vram_total_mb=mem.total // 2**20, vram_free_mb=mem.free // 2**20,
                                driver=driver, source="nvml"))
        return gpus
    finally:
        lib.nvmlShutdown()


def _smi_gpus() -> list[GpuInfo]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    res = run_hidden([exe, "--query-gpu=name,memory.total,memory.free,driver_version",
                      "--format=csv,noheader,nounits"], timeout=5)
    gpus = []
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                try:
                    gpus.append(GpuInfo(parts[0], int(float(parts[1])), int(float(parts[2])),
                                        parts[3], source="nvidia-smi"))
                except ValueError:
                    pass
    return gpus


# ----------------------------------------------------------------------------- CPU / OS

def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    else:
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def _os_name() -> str:
    if sys.platform == "win32":
        build = sys.getwindowsversion().build  # type: ignore[attr-defined]
        return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
    return f"{platform.system()} {platform.release()}"
