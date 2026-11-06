"""Thread quality-of-service: EcoQoS ("efficiency mode") for the always-on listening thread.

On hybrid CPUs (e.g. the i9-13900HX) Windows schedules EcoQoS threads on the
efficiency cores at low clock - the wake-word loop costs almost nothing there.
"""

from __future__ import annotations

import ctypes
import logging

from . import IS_WINDOWS

log = logging.getLogger(__name__)

THREAD_POWER_THROTTLING_CURRENT_VERSION = 1
THREAD_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ThreadPowerThrottling = 3   # THREAD_INFORMATION_CLASS


class THREAD_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32), ("ControlMask", ctypes.c_uint32), ("StateMask", ctypes.c_uint32)]


def set_thread_eco_qos(on: bool) -> bool:
    if not IS_WINDOWS:
        return False
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.GetCurrentThread.restype = ctypes.c_void_p
    k32.SetThreadInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    k32.SetThreadInformation.restype = ctypes.c_int
    st = THREAD_POWER_THROTTLING_STATE(THREAD_POWER_THROTTLING_CURRENT_VERSION,
                                       THREAD_POWER_THROTTLING_EXECUTION_SPEED,
                                       THREAD_POWER_THROTTLING_EXECUTION_SPEED if on else 0)
    ok = bool(k32.SetThreadInformation(k32.GetCurrentThread(), ThreadPowerThrottling, ctypes.byref(st),
                                       ctypes.sizeof(st)))
    log.debug("EcoQoS %s for thread: %s", "on" if on else "off", ok)
    return ok
