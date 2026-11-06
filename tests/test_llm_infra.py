"""Downloads, llama.cpp install selection, model choice, server process management."""

import hashlib
import io
import os
import stat
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from assistant.core.cancel import CancelToken, Cancelled
from assistant.llm.download import DownloadError, download
from assistant.llm.models import CATALOG, choose_model, is_installed, model_files
from assistant.llm.server import LlamaServer, ServerError, ServerOptions, ServerState, build_args
from assistant.llm.setup import current_server, install_llama, select_assets

DATA = os.urandom(3 * 2**20 + 123)


class FileServer:
    """Serves DATA with HTTP Range support; can drop the connection mid-way once."""

    def __init__(self, data=DATA):
        self.data = data
        self.cut_after = None
        self.ranges = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                start = 0
                rng = self.headers.get("Range")
                outer.ranges.append(rng)
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                    self.send_response(206)
                else:
                    self.send_response(200)
                body = outer.data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if outer.cut_after is not None:
                    n, outer.cut_after = outer.cut_after, None
                    self.wfile.write(body[:n])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/file.bin"


@pytest.fixture
def fs():
    s = FileServer()
    yield s
    s.httpd.shutdown()


def test_download_resumes_and_verifies(fs, tmp_path):
    sha = hashlib.sha256(DATA).hexdigest()
    fs.cut_after = 1_000_000                                     # connection drops after ~1 MB
    progress = []
    out = download(fs.url, tmp_path / "m.gguf", sha256=sha, size=len(DATA),
                   progress=lambda d, t, r: progress.append(d), retries=3)
    assert out.read_bytes() == DATA and not (tmp_path / "m.gguf.part").exists()
    assert any(r and r.startswith("bytes=") for r in fs.ranges)    # second request resumed
    assert progress[-1] == len(DATA)
    assert download(fs.url, out, sha256=sha, size=len(DATA)) == out  # already there: no re-download


def test_download_checksum_mismatch(fs, tmp_path):
    with pytest.raises(DownloadError, match="checksum"):
        download(fs.url, tmp_path / "x.bin", sha256="0" * 64, size=len(DATA))
    assert not (tmp_path / "x.bin").exists() and not (tmp_path / "x.bin.part").exists()
    with pytest.raises(DownloadError, match="closed after"):           # server has fewer bytes than promised
        download(fs.url, tmp_path / "y.bin", size=len(DATA) + 1, retries=0)


def test_download_cancel(fs, tmp_path):
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        download(fs.url, tmp_path / "z.bin", cancel=tok)


RELEASE = {"tag_name": "b9999", "assets": [
    {"name": n, "browser_download_url": f"https://example/{n}", "size": 1, "digest": "sha256:" + "a" * 64}
    for n in ("llama-b9999-bin-win-cpu-x64.zip", "llama-b9999-bin-win-vulkan-x64.zip",
              "llama-b9999-bin-win-cuda-12.4-x64.zip", "llama-b9999-bin-win-cuda-13.5-x64.zip",
              "cudart-llama-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip",
              "llama-b9999-bin-win-cuda-13.5-arm64.zip", "llama-b9999-bin-ubuntu-x64.tar.gz")]}


def test_select_assets():
    label, assets = select_assets(RELEASE, driver="581.42")
    assert label == "cuda-13.5" and [a.name for a in assets] == ["llama-b9999-bin-win-cuda-13.5-x64.zip",
                                                                "cudart-llama-bin-win-cuda-13.5-x64.zip"]
    assert assets[0].sha256 == "a" * 64
    assert select_assets(RELEASE, driver="552.22")[0] == "cuda-12.4"     # older driver: CUDA 12
    assert select_assets(RELEASE, driver=None)[0] == "vulkan"             # no NVIDIA GPU
    assert select_assets(RELEASE, backend="cpu", driver="581")[0] == "cpu"


def test_install_llama_from_zips(tmp_path, monkeypatch):
    zips = {}
    for name, files in {"llama-b9999-bin-win-cuda-12.4-x64.zip": {"llama-server.exe": b"MZ", "ggml-cuda.dll": b"x"},
                        "cudart-llama-bin-win-cuda-12.4-x64.zip": {"cudart64_12.dll": b"y"}}.items():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for fn, data in files.items():
                z.writestr(fn, data)
        zips[name] = buf.getvalue()
    release = {"tag_name": "b9999", "assets": [{"name": n, "browser_download_url": f"file://{n}",
                                                 "size": len(d)} for n, d in zips.items()]}

    def fake_download(url, dest, **kw):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zips[url.removeprefix("file://")])
        return dest

    monkeypatch.setattr("assistant.llm.setup.download", fake_download)
    monkeypatch.setattr("assistant.llm.setup.EXE", "llama-server.exe")
    exe = install_llama(tmp_path / "bin", tmp_path / "cache", driver="552.1", release=release)
    assert exe.name == "llama-server.exe" and (exe.parent / "cudart64_12.dll").exists()
    assert current_server(tmp_path / "bin") == exe
    # evil zip with path traversal is refused
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.exe", b"x")
    zips["llama-b9999-bin-win-cuda-12.4-x64.zip"] = buf.getvalue()
    release["tag_name"] = "b10000"
    with pytest.raises(RuntimeError, match="unsafe"):
        install_llama(tmp_path / "bin", tmp_path / "cache", driver="552.1", release=release)
    assert current_server(tmp_path / "bin") == exe                       # old install still active


def test_choose_model():
    assert choose_model("smart", "qwen3.5-9b", "qwen3.5-4b", vram_free_mb=100, bench=None, ctx=8192)[0] == "qwen3.5-9b"
    assert choose_model("auto", "qwen3.5-9b", "qwen3.5-4b", vram_free_mb=7800, bench=None, ctx=8192)[0] == "qwen3.5-9b"
    assert choose_model("auto", "qwen3.5-9b", "qwen3.5-4b", vram_free_mb=4000, bench=None, ctx=8192)[0] == "qwen3.5-4b"
    assert choose_model("auto", "qwen3.5-9b", "qwen3.5-4b", vram_free_mb=None, bench=None, ctx=8192)[0] == "qwen3.5-4b"
    bench = {"recommended": "qwen3.5-4b", "date": "x", "reason": "faster"}
    assert choose_model("auto", "qwen3.5-9b", "qwen3.5-4b", vram_free_mb=7800, bench=bench, ctx=8192)[0] == "qwen3.5-4b"
    spec = CATALOG["qwen3.5-9b"]
    assert 6000 < spec.vram_estimate_mb(8192) < 7800                     # fits an 8 GB laptop GPU
    assert spec.vram_estimate_mb(8192, with_vision=False) < spec.vram_estimate_mb(8192)


def test_is_installed(tmp_path):
    spec = CATALOG["qwen3.5-0.8b"]
    w, mm = model_files(spec, tmp_path)
    assert mm is None and not is_installed(spec, tmp_path)
    with open(w, "wb") as fh:
        fh.truncate(spec.weights.size)
    assert is_installed(spec, tmp_path)


FAKE_SERVER = r'''
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
if os.environ.get("FAKE_CRASH_FILE") and not os.path.exists(os.environ["FAKE_CRASH_FILE"]):
    open(os.environ["FAKE_CRASH_FILE"], "w").close()
    time.sleep(1.5); sys.exit(3)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        ok = self.path == "/health" and time.time() - START > 0.3
        self.send_response(200 if ok else 503); self.end_headers()
        self.wfile.write(json.dumps({"key": os.environ.get("LLAMA_API_KEY", "")}).encode())
START = time.time()
print("fake llama-server", " ".join(args), flush=True)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


@pytest.fixture
def fake_exe(tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER)
    exe = tmp_path / "llama-server"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    return exe, model


def test_server_lifecycle(fake_exe, tmp_path):
    exe, model = fake_exe
    states = []
    srv = LlamaServer(ServerOptions(exe=exe, model=model, port=18771), tmp_path / "logs",
                      lambda s, d: states.append(s))
    srv.start(wait=True, timeout=20)
    assert srv.state is ServerState.READY and srv.running() and srv.client().health()
    status, body = srv.client().get("/health")
    assert body["key"] == srv.api_key                                    # key passed via env, not argv
    assert srv.api_key not in " ".join(build_args(srv.opts, srv.port))
    log = (tmp_path / "logs" / "llama-server.log").read_text()
    assert "--jinja" in log and "-ctk q8_0" in log
    srv.stop()
    assert srv.state is ServerState.STOPPED and not srv.running()
    assert states[:2] == [ServerState.STARTING, ServerState.READY]


def test_server_port_in_use_and_missing_files(fake_exe, tmp_path):
    import socket

    exe, model = fake_exe
    s = socket.socket()
    s.bind(("127.0.0.1", 18780))
    s.listen()
    try:
        srv = LlamaServer(ServerOptions(exe=exe, model=model, port=18780), tmp_path / "logs")
        srv.start(wait=True, timeout=20)
        assert srv.port == 18781
        srv.stop()
    finally:
        s.close()
    bad = LlamaServer(ServerOptions(exe=exe, model=tmp_path / "missing.gguf"), tmp_path / "logs")
    with pytest.raises(ServerError, match="model not found"):
        bad.start()


def test_server_restarts_after_crash(fake_exe, tmp_path, monkeypatch):
    exe, model = fake_exe
    srv = LlamaServer(ServerOptions(exe=exe, model=model, port=18790), tmp_path / "logs")
    srv.start(wait=True, timeout=20)
    first = srv.proc.pid
    srv.proc.kill()                                                      # simulate a crash
    deadline = time.time() + 15
    while time.time() < deadline and (srv.proc is None or srv.proc.pid == first or srv.state is not ServerState.READY):
        time.sleep(0.2)
    assert srv.state is ServerState.READY and srv.proc.pid != first
    srv.stop()
