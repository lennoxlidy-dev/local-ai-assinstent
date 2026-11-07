"""Download what's missing in the background (during the setup wizard, or from the tray menu).

Order: speech models first (small; the wizard needs them to speak, listen and calibrate the wake
word), then llama.cpp, then the language model(s) for the chosen profile. The plan is recomputed
before every item, so switching "speed vs smart" in the wizard changes what comes next.
Downloads are resumable and SHA-256 verified (llm/download.py); failures are retried with backoff.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.cancel import CancelToken, Cancelled
from ..core.context import AppContext
from ..llm.download import ServerFileChanged

log = logging.getLogger(__name__)

Progress = Callable[[int, int, float], None]
LLAMA_ESTIMATE = 450 * 2**20      # CUDA build + runtime DLLs (exact size known only after the release lookup)


@dataclass
class Job:
    id: str                      # voice:kws | llama | llm:<model id>
    title: str
    size: int                    # bytes (estimate)
    run: Callable[[Progress, CancelToken], object]
    kind: str = "voice"          # voice | llama | llm
    done_bytes: int = 0
    attempts: int = 0
    error: str = ""


@dataclass
class SetupStatus:
    running: bool = False
    finished: bool = False
    current: str = ""            # title of the item being downloaded
    current_done: int = 0
    current_total: int = 0
    rate: float = 0.0            # bytes/s
    done_bytes: int = 0          # all items
    total_bytes: int = 0
    failed: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)   # job ids
    outdated: list[str] = field(default_factory=list)    # titles whose file changed on the server
    error: str = ""

    @property
    def percent(self) -> float:
        return 100.0 * self.done_bytes / self.total_bytes if self.total_bytes else 100.0

    @property
    def eta_s(self) -> float | None:
        left = self.total_bytes - self.done_bytes
        return left / self.rate if self.rate > 0 and left > 0 else None


def default_plan(ctx: AppContext) -> list[Job]:
    """Everything that is missing right now, in download order."""
    from ..voice.models import VOICE_MODELS, ensure, installed

    jobs: list[Job] = []
    models_dir, cache_dir = ctx.paths.models_dir, ctx.paths.cache_dir
    for vid in ("kws", "vad", "tts", "stt", "speaker"):
        if not installed(vid, models_dir):
            m = VOICE_MODELS[vid]
            jobs.append(Job(f"voice:{vid}", m.title, m.size,
                            (lambda v: lambda p, c: ensure(v, models_dir, cache_dir, progress=p, cancel=c))(vid)))
    c = ctx.config.data.llm
    from ..llm.setup import current_server

    if not c.server_path and current_server(ctx.paths.bin_dir) is None:
        def install(p: Progress, cancel: CancelToken) -> None:
            from ..core import hardware
            from ..llm.setup import install_llama

            gpus = hardware.nvidia_gpus()
            install_llama(ctx.paths.bin_dir, ctx.paths.cache_dir, driver=gpus[0].driver if gpus else None,
                          progress=p, cancel=cancel)

        jobs.append(Job("llama", "llama.cpp (language model server)", LLAMA_ESTIMATE, install, kind="llama"))
    for mid in llm_models_to_get(ctx):
        from ..llm.models import get_model, is_installed
        from ..llm.setup import download_model

        spec = get_model(mid)
        if is_installed(spec, models_dir, vision=c.vision):
            continue
        jobs.append(Job(f"llm:{mid}", spec.title, spec.download_bytes,
                        (lambda s: lambda p, cancel: download_model(s, models_dir, vision=c.vision, progress=p,
                                                                    cancel=cancel))(spec), kind="llm"))
    return jobs


def llm_models_to_get(ctx: AppContext) -> list[str]:
    """smart -> the smart model; fast -> the fast one; auto -> the one auto would pick, then the other
    (the benchmark compares both and switching is instant)."""
    c = ctx.config.data.llm
    if c.profile == "smart":
        return [c.model_smart]
    if c.profile == "fast":
        return [c.model_fast]
    from ..core import hardware
    from ..llm.models import choose_model

    gpus = hardware.nvidia_gpus()
    free = max((g.vram_free_mb for g in gpus), default=None) if gpus else None
    first, _why = choose_model("auto", c.model_smart, c.model_fast, vram_free_mb=free, bench=None,
                               ctx=c.context_tokens)
    other = c.model_fast if first == c.model_smart else c.model_smart
    return [first, other]


class SetupManager:
    RETRIES = (10.0, 30.0, 90.0)     # seconds between attempts
    EVENT_EVERY_S = 0.25

    def __init__(self, ctx: AppContext, plan: Callable[[], list[Job]] | None = None):
        self.ctx = ctx
        self._plan = plan or (lambda: default_plan(ctx))
        self._real = plan is None
        self.status = SetupStatus()
        self.cancel_token = CancelToken()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_event = 0.0
        self._done_ids: set[str] = set()
        self._failed: dict[str, str] = {}
        self._outdated: dict[str, str] = {}          # job id -> title

    # ------------------------------------------------------------------ control
    def missing(self) -> list[Job]:
        try:
            return [j for j in self._plan() if j.id not in self._done_ids]
        except Exception:
            log.exception("could not work out what to download")
            return []

    def start(self) -> bool:
        """Start in the background. False if nothing is missing (or it already runs)."""
        if self._real and os.environ.get("ASSISTANT_NO_DOWNLOADS"):   # tests / offline machines
            log.info("model downloads disabled (ASSISTANT_NO_DOWNLOADS)")
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            jobs = self.missing()
            if not jobs:
                self.status = SetupStatus(finished=True)
                return False
            if self.cancel_token.cancelled:
                self.cancel_token = CancelToken()
            self.status = SetupStatus(running=True, total_bytes=sum(j.size for j in jobs))
            self._failed.clear()
            self._outdated.clear()
            self._thread = threading.Thread(target=self._run, name="setup-downloads", daemon=True)
            self._thread.start()
            return True

    def cancel(self) -> None:
        self.cancel_token.cancel("setup cancelled")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        st = self.status
        bus = self.ctx.bus
        try:
            while not self.cancel_token.cancelled:
                jobs = [j for j in self.missing() if j.id not in self._failed]
                if not jobs:
                    break
                job = jobs[0]
                # keep the overall total honest when the plan changed (e.g. another model was chosen)
                st.total_bytes = st.done_bytes + sum(j.size for j in jobs)
                if not self._disk_ok(jobs):
                    break
                self._run_job(job)
        finally:
            st.running = False
            st.finished = True
            st.current, st.rate = "", 0.0
            st.failed = [f"{jid}: {err}" for jid, err in self._failed.items()]
            st.outdated = list(self._outdated.values())
            ok = not self._failed and not self.cancel_token.cancelled and not st.error
            log.info("setup downloads finished (%s)", "ok" if ok else st.error or "with errors")
            bus.publish("setup.finished", ok=ok, failed=list(self._failed), error=st.error, outdated=st.outdated)

    def _disk_ok(self, jobs: list[Job]) -> bool:
        need = int(sum(j.size for j in jobs) * 1.1) + 200 * 2**20
        try:
            target = self.ctx.paths.models_dir
            target.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(target).free
        except OSError:
            return True
        if free >= need:
            return True
        self.status.error = (f"Not enough disk space: {need / 2**30:.1f} GB needed, {free / 2**30:.1f} GB free "
                             f"on {target.anchor or target}")
        log.warning(self.status.error)
        self.ctx.bus.publish("setup.failed", job="disk", error=self.status.error)
        return False

    def _run_job(self, job: Job) -> None:
        st = self.status
        st.current, st.current_done, st.current_total = job.title, 0, job.size
        base = st.done_bytes
        self.ctx.bus.publish("setup.job_started", job=job.id, title=job.title, kind=job.kind)
        log.info("downloading %s", job.title)

        files = {"acc": 0, "last_done": 0, "last_total": 0}   # a job may download several files in a row

        def progress(done: int, total: int, rate: float) -> None:
            if done < files["last_done"]:                       # the next file of this job started
                files["acc"] += files["last_total"]
            files["last_done"], files["last_total"] = done, total
            job.done_bytes = min(files["acc"] + done, job.size)
            st.current_done, st.current_total, st.rate = job.done_bytes, job.size, rate
            st.done_bytes = base + job.done_bytes
            now = time.monotonic()
            if now - self._last_event >= self.EVENT_EVERY_S:
                self._last_event = now
                self.ctx.bus.publish("setup.progress", job=job.id, title=job.title, done=job.done_bytes,
                                     total=job.size, rate=rate, overall=st.percent)

        for attempt, delay in enumerate((0.0, *self.RETRIES)):
            if delay and self.cancel_token.wait(delay):
                return
            job.attempts = attempt + 1
            files.update(acc=0, last_done=0, last_total=0)
            try:
                job.run(progress, self.cancel_token)
            except Cancelled:
                return
            except ServerFileChanged as e:  # the same other file would come again: don't retry
                job.error = str(e)[:300]
                self._outdated[job.id] = job.title
                log.warning("download of %s failed: %s", job.title, e)
                break
            except Exception as e:   # network trouble, checksum mismatch, server error ...
                job.error = str(e)[:300]
                log.warning("download of %s failed (attempt %d): %s", job.title, attempt + 1, e)
                self.ctx.bus.publish("setup.retry", job=job.id, title=job.title, error=job.error,
                                     attempt=attempt + 1)
                continue
            st.done_bytes = base + job.size
            self._done_ids.add(job.id)
            st.completed.append(job.id)
            self.ctx.bus.publish("setup.job_done", job=job.id, title=job.title, kind=job.kind)
            return
        self._failed[job.id] = job.error
        st.done_bytes = base + job.size          # don't let a failed item stall the overall bar
        self.ctx.bus.publish("setup.failed", job=job.id, error=job.error)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def describe(status: SetupStatus, lang: str = "en") -> str:
    """One line for the wizard / tray: 'Downloading Parakeet … 45% · 12.3 MB/s · 3 min left'."""
    de = lang == "de"
    if status.error:
        return status.error
    if status.finished and not status.running:
        if status.outdated:
            names = ", ".join(status.outdated)
            return (f"{names}: Die Datei auf dem Download-Server wurde geändert und passt nicht zu dieser Version - "
                    "bitte den Assistenten aktualisieren (neueste Version laden, install.bat ausführen)." if de else
                    f"{names}: the file on the download server has changed and doesn't match this version - "
                    "please update the assistant (download the latest version and run install.bat).")
        if status.failed:
            return ("Einige Downloads sind fehlgeschlagen - sie werden beim nächsten Start erneut versucht."
                    if de else "Some downloads failed - they'll be retried at the next start.")
        return "Alle Modelle sind installiert." if de else "All models are installed."
    if not status.running:
        return ""
    parts = [("Lade " if de else "Downloading ") + status.current]
    if status.current_total:
        parts.append(f"{100 * status.current_done / status.current_total:.0f}%")
    if status.rate:
        parts.append(f"{status.rate / 2**20:.1f} MB/s")
    eta = status.eta_s
    if eta is not None:
        mins = max(1, round(eta / 60))
        parts.append((f"noch ca. {mins} Min." if de else f"~{mins} min left") if eta >= 60
                     else ("gleich fertig" if de else "almost done"))
    return " · ".join(parts)


__all__ = ["Job", "SetupManager", "SetupStatus", "default_plan", "describe", "human_size", "llm_models_to_get"]

