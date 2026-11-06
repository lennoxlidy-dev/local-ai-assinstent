"""Start, stop and health-check llama-server.

* runs hidden (no console window), output -> ``logs/llama-server.log``
* random API key per launch: web pages in your browser can't use the local model
* Windows job object with KILL_ON_JOB_CLOSE: if the assistant dies, the server
  (and its VRAM) goes with it - no orphaned process
* watchdog restarts a crashed server (with backoff); ``--sleep-idle-seconds``
  makes llama-server free the model after idle time and reload on demand
* :meth:`stop` frees VRAM immediately (game mode)
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.proc import hidden_flags
from .client import LlamaClient

log = logging.getLogger(__name__)


class ServerState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"


@dataclass
class ServerOptions:
    exe: Path
    model: Path
    mmproj: Path | None = None
    mmproj_offload: bool = True
    port: int = 8771
    ctx: int = 8192
    gpu_layers: str = "all"          # "all" | "auto" | number
    threads: int | None = None
    sleep_idle_s: int = 0            # 0 = never unload
    kv_type: str = "q8_0"            # quantized KV cache: half the VRAM of f16, negligible quality loss
    extra_args: list[str] = field(default_factory=list)


class ServerError(Exception):
    pass


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) if sys.platform != "win32" else None
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def build_args(opts: ServerOptions, port: int) -> list[str]:
    args = [str(opts.exe), "-m", str(opts.model), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(opts.ctx), "-np", "1", "-ngl", str(opts.gpu_layers), "--jinja", "--no-webui",
            "-ctk", opts.kv_type, "-ctv", opts.kv_type, "--timeout", "600"]
    if opts.mmproj is not None:
        args += ["--mmproj", str(opts.mmproj)]
        if not opts.mmproj_offload:
            args.append("--no-mmproj-offload")
    else:
        args.append("--no-mmproj")
    if opts.threads:
        args += ["-t", str(opts.threads)]
    if opts.sleep_idle_s > 0:
        args += ["--sleep-idle-seconds", str(opts.sleep_idle_s)]
    return args + list(opts.extra_args)


class _KillOnClose:
    """Windows job object: children die with us (also on crash / Task Manager kill)."""

    def __init__(self) -> None:
        self.job = None
        if sys.platform != "win32":
            return
        try:
            import win32job

            job: Any = win32job.CreateJobObject(None, "")  # returns a PyHANDLE (the type stub says None)
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            self.job = job
        except Exception as e:  # pragma: no cover - best effort
            log.warning("job object unavailable (%s); llama-server may outlive a crash", e)
            self.job = None

    def assign(self, proc: subprocess.Popen) -> None:
        if self.job is None:
            return
        try:
            import win32api
            import win32con
            import win32job

            h = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
            win32job.AssignProcessToJobObject(self.job, h)
        except Exception as e:  # pragma: no cover
            log.warning("could not attach llama-server to job object: %s", e)


class LlamaServer:
    MAX_RESTARTS = 3          # within RESTART_WINDOW_S
    RESTART_WINDOW_S = 300

    def __init__(self, opts: ServerOptions, log_dir: Path,
                 on_state: Callable[[ServerState, str], None] | None = None):
        self.opts = opts
        self.log_dir = Path(log_dir)
        self.on_state = on_state
        self.state = ServerState.STOPPED
        self.detail = ""
        self.api_key = secrets.token_urlsafe(24)
        self.port = opts.port
        self.proc: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._stopping = False
        self._restarts: list[float] = []
        self._job = _KillOnClose()
        self._log_fh = None
        self._watchdog: threading.Thread | None = None
        self.load_ms: float | None = None

    # ------------------------------------------------------------------ public
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self) -> LlamaClient:
        return LlamaClient(self.base_url, self.api_key)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, wait: bool = True, timeout: float = 240.0) -> None:
        with self._lock:
            if self.running():
                if wait:
                    self.wait_ready(timeout)
                return
            for path, what in ((self.opts.exe, "llama-server"), (self.opts.model, "model")):
                if not Path(path).exists():
                    self._set(ServerState.FAILED, f"{what} not found: {path}")
                    raise ServerError(self.detail)
            self.port = self._pick_port(self.opts.port)
            args = build_args(self.opts, self.port)
            env = dict(os.environ, LLAMA_API_KEY=self.api_key)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_fh = open(self.log_dir / "llama-server.log", "ab")  # noqa: SIM115
            self._log_fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)}\n".encode())
            self._log_fh.flush()
            log.info("starting llama-server on port %d: %s", self.port, " ".join(args[1:]))
            self._stopping = False
            self._t_start = time.perf_counter()
            self.proc = subprocess.Popen(args, stdout=self._log_fh, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=env, cwd=str(Path(self.opts.exe).parent),
                                         creationflags=hidden_flags(), start_new_session=sys.platform != "win32")
            self._job.assign(self.proc)
            self._set(ServerState.STARTING, f"loading {Path(self.opts.model).name}")
            if self._watchdog is None or not self._watchdog.is_alive():
                self._watchdog = threading.Thread(target=self._watch, name="llm-watchdog", daemon=True)
                self._watchdog.start()
        if wait:
            self.wait_ready(timeout)

    def wait_ready(self, timeout: float = 240.0) -> None:
        deadline = time.monotonic() + timeout
        client = self.client()
        while time.monotonic() < deadline:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                code = proc.returncode if proc else None
                self._set(ServerState.FAILED, f"llama-server exited (code {code}); see logs/llama-server.log "
                                              f"{self._log_tail()}")
                raise ServerError(self.detail)
            if client.health():
                self.load_ms = (time.perf_counter() - self._t_start) * 1000
                log.debug("TIMING stage=llm_load ms=%.1f", self.load_ms)
                self._set(ServerState.READY, f"{Path(self.opts.model).name} ready")
                return
            time.sleep(0.25)
        self._set(ServerState.FAILED, f"llama-server not ready after {timeout:.0f}s")
        raise ServerError(self.detail)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._stopping = True
            proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            log.info("stopping llama-server")
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None
        self._set(ServerState.STOPPED, "")

    def restart(self) -> None:
        self.stop()
        self.start()

    # ------------------------------------------------------------------ internals
    def _pick_port(self, preferred: int) -> int:
        for p in [preferred, *range(preferred + 1, preferred + 20)]:
            if port_free(p):
                return p
        raise ServerError(f"no free port near {preferred}")

    def _set(self, state: ServerState, detail: str) -> None:
        self.state, self.detail = state, detail
        if self.on_state is not None:
            try:
                self.on_state(state, detail)
            except Exception:  # pragma: no cover
                log.exception("server state callback failed")

    def _log_tail(self, n: int = 400) -> str:
        try:
            with open(self.log_dir / "llama-server.log", "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - n))
                return "| " + fh.read().decode("utf-8", "replace").strip().replace("\n", " | ")[-n:]
        except OSError:
            return ""

    def _watch(self) -> None:
        while True:
            time.sleep(1.0)
            with self._lock:
                proc = self.proc
                stopping = self._stopping
            if proc is None:
                if stopping:
                    return
                continue
            if proc.poll() is None or stopping:
                continue
            # crashed while we wanted it running
            now = time.time()
            self._restarts = [t for t in self._restarts if now - t < self.RESTART_WINDOW_S]
            if len(self._restarts) >= self.MAX_RESTARTS or self.state is ServerState.STARTING:
                self._set(ServerState.FAILED, f"llama-server keeps crashing (code {proc.returncode}) "
                                              f"{self._log_tail()}")
                with self._lock:
                    self.proc = None
                return
            self._restarts.append(now)
            log.warning("llama-server exited unexpectedly (code %s); restarting", proc.returncode)
            try:
                with self._lock:
                    self.proc = None
                self.start(wait=True)
            except ServerError as e:
                log.error("restart failed: %s", e)
                return
