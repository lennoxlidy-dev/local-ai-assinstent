"""Resumable, verified downloads (models, llama.cpp) - stdlib only.

* resumes ``*.part`` files with HTTP Range requests
* verifies size and SHA-256 before the file is renamed into place
* reports progress (for the tray/onboarding UI) and honours cancellation
* uses the system proxy/certificates (urllib reads the Windows proxy settings)
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..core.cancel import CancelToken, Cancelled

log = logging.getLogger(__name__)

Progress = Callable[[int, int, float], None]  # done_bytes, total_bytes, bytes_per_second
USER_AGENT = "LocalAssistant/0.3 (+offline voice assistant; downloads models only)"


class DownloadError(Exception):
    pass


def sha256_file(path: Path, chunk: int = 4 << 20, cancel: CancelToken | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            if cancel is not None:
                cancel.check()
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, *, sha256: str | None = None, size: int | None = None,
             progress: Progress | None = None, cancel: CancelToken | None = None,
             timeout: float = 30.0, retries: int = 4) -> Path:
    """Download ``url`` to ``dest`` (atomically). Returns ``dest``. Raises DownloadError/Cancelled."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (size is None or dest.stat().st_size == size):
        if sha256 is None or sha256_file(dest, cancel=cancel) == sha256:
            return dest
        log.warning("%s exists but its checksum is wrong; downloading again", dest.name)
        dest.unlink()
    part = dest.with_name(dest.name + ".part")
    attempt = 0
    while True:
        try:
            _fetch(url, part, size, progress, cancel, timeout)
            break
        except Cancelled:
            raise
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                raise DownloadError(f"{url}: HTTP {e.code} {e.reason}") from None
            attempt += 1
            if attempt > retries:
                raise DownloadError(f"{url}: {e}") from None
            wait = min(30, 2 ** attempt)
            log.warning("download interrupted (%s); retrying in %ds", e, wait)
            if cancel is not None and cancel.wait(wait):
                raise Cancelled("download cancelled") from None
            if cancel is None:
                time.sleep(wait)
    got = part.stat().st_size
    if size is not None and got != size:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{dest.name}: got {got} bytes, expected {size}")
    if sha256 is not None:
        digest = sha256_file(part, cancel=cancel)
        if digest != sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{dest.name}: checksum mismatch (got {digest[:12]}…, expected {sha256[:12]}…)")
    os.replace(part, dest)
    log.info("downloaded %s (%.1f MB)", dest.name, got / 2**20)
    return dest


def _fetch(url: str, part: Path, size: int | None, progress: Progress | None, cancel: CancelToken | None,
           timeout: float) -> None:
    have = part.stat().st_size if part.exists() else 0
    if size is not None and have > size:
        part.unlink()
        have = 0
    if size is not None and have == size:
        return
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        if have and status != 206:  # server ignored the range -> start over
            have = 0
        total = size or (int(resp.headers.get("Content-Length", 0)) + have) or 0
        mode = "ab" if have else "wb"
        t0, done0 = time.monotonic(), have
        last = 0.0
        with open(part, mode) as fh:
            done = have
            while True:
                if cancel is not None and cancel.cancelled:
                    raise Cancelled("download cancelled")
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                now = time.monotonic()
                if progress is not None and (now - last > 0.25 or done == total):
                    last = now
                    rate = (done - done0) / max(now - t0, 1e-6)
                    progress(done, total, rate)
        if total and done < total:  # connection closed early -> retry resumes from here
            raise ConnectionError(f"connection closed after {done} of {total} bytes")
