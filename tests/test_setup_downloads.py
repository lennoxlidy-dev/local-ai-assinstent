"""Milestone 5: background downloads while the wizard runs (fake download jobs, no network)."""

import threading
import time

import pytest

from assistant.core.context import bootstrap
from assistant.onboarding.downloads import Job, SetupManager, SetupStatus, default_plan, describe


@pytest.fixture
def ctx(tmp_path):
    return bootstrap("cli", home=str(tmp_path / "home"), detect_hardware="off")


class FakeWorld:
    """Pretends to download: 'installed' grows as jobs finish; the plan lists what's still missing."""

    def __init__(self, items):
        self.items = list(items)          # (id, size, fail_times)
        self.installed: set[str] = set()
        self.fails = {i: f for i, _s, f in self.items}
        self.order: list[str] = []

    def plan(self):
        return [Job(i, i.upper(), s, self._runner(i, s)) for i, s, _f in self.items if i not in self.installed]

    def _runner(self, jid, size):
        def run(progress, cancel):
            if self.fails[jid] > 0:
                self.fails[jid] -= 1
                raise ConnectionError("network down")
            for k in range(1, 5):
                cancel.check()
                progress(size * k // 4, size, 2e6)
            self.installed.add(jid)
            self.order.append(jid)
        return run


def _collect(ctx):
    events = []
    ctx.bus.subscribe("*", lambda e: events.append(e) if e.topic.startswith("setup.") else None)
    return events


def test_downloads_in_order_with_progress(ctx):
    world = FakeWorld([("voice:kws", 1000, 0), ("llama", 5000, 0), ("llm:a", 9000, 0)])
    events = _collect(ctx)
    m = SetupManager(ctx, plan=world.plan)
    m.EVENT_EVERY_S = 0
    assert m.start()
    m.join(5)
    assert world.order == ["voice:kws", "llama", "llm:a"]
    assert m.status.finished and m.status.percent == pytest.approx(100.0)
    topics = [e.topic for e in events]
    assert topics.count("setup.job_done") == 3 and topics[-1] == "setup.finished"
    assert events[-1].data["ok"] is True and "setup.progress" in topics
    assert not m.start()                       # nothing left to do


def test_retries_then_gives_up_without_blocking_the_rest(ctx):
    world = FakeWorld([("voice:stt", 1000, 2), ("llama", 1000, 9), ("llm:a", 1000, 0)])
    events = _collect(ctx)
    m = SetupManager(ctx, plan=world.plan)
    m.RETRIES = (0.01, 0.01, 0.01)
    m.start()
    m.join(5)
    assert world.order == ["voice:stt", "llm:a"]
    assert [e.data["job"] for e in events if e.topic == "setup.failed"] == ["llama"]
    fin = events[-1]
    assert fin.topic == "setup.finished" and fin.data["ok"] is False and fin.data["failed"] == ["llama"]
    assert "llama" in m.status.failed[0]


def test_a_changed_server_file_is_not_retried(ctx):
    from assistant.llm.download import ServerFileChanged

    calls = []

    def changed(progress, cancel):
        calls.append(1)
        raise ServerFileChanged("x.tar.bz2: the file on the server has changed (2 bytes instead of 1)")

    ok = FakeWorld([("llm:a", 1000, 0)])
    events = _collect(ctx)
    m = SetupManager(ctx, plan=lambda: [Job("voice:stt", "Speech recognition", 500, changed), *ok.plan()])
    m.RETRIES = (5.0, 5.0)                    # a retry would show up as a slow test
    t0 = time.monotonic()
    m.start()
    m.join(5)
    assert time.monotonic() - t0 < 2 and len(calls) == 1
    assert ok.order == ["llm:a"]               # the rest still downloads
    assert m.status.outdated == ["Speech recognition"]
    assert events[-1].data["outdated"] == ["Speech recognition"]
    text = describe(m.status)
    assert text.startswith("Speech recognition: the file on the download server has changed")
    assert "update the assistant" in text
    assert "aktualisieren" in describe(m.status, "de")


def test_plan_changes_are_picked_up(ctx):
    """Choosing another model in the wizard while the voice models download."""
    world = FakeWorld([("voice:kws", 1000, 0), ("llm:smart", 9000, 0)])
    gate = threading.Event()
    orig = world._runner

    def slow_runner(jid, size):
        run = orig(jid, size)

        def wrapped(progress, cancel):
            if jid == "voice:kws":
                gate.wait(2)
            run(progress, cancel)
        return wrapped

    world._runner = slow_runner
    m = SetupManager(ctx, plan=world.plan)
    m.start()
    time.sleep(0.05)
    world.items[1] = ("llm:fast", 4000, 0)     # user picked "fast"
    world.fails["llm:fast"] = 0
    gate.set()
    m.join(5)
    assert world.order == ["voice:kws", "llm:fast"]


def test_cancel_stops_quickly(ctx):
    started = threading.Event()

    def forever(progress, cancel):
        started.set()
        while not cancel.wait(0.01):
            progress(1, 100, 1.0)
        cancel.check()

    m = SetupManager(ctx, plan=lambda: [Job("llm:big", "Big model", 100, forever)])
    m.start()
    assert started.wait(2)
    m.cancel()
    m.join(2)
    assert not m.running and m.status.finished


def test_disk_space_check(ctx, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("U", (), {"free": 10 * 2**20})())
    events = _collect(ctx)
    m = SetupManager(ctx, plan=lambda: [Job("llm:a", "Model A", 5 * 2**30, lambda p, c: None)])
    m.start()
    m.join(2)
    assert "Not enough disk space" in m.status.error
    assert any(e.topic == "setup.failed" and e.data["job"] == "disk" for e in events)


def test_default_plan_lists_missing_items_in_order(ctx):
    ctx.config.set("llm.profile", "fast")
    ids = [j.id for j in default_plan(ctx)]
    assert ids[:5] == ["voice:kws", "voice:vad", "voice:tts", "voice:stt", "voice:speaker"]
    assert ids[5] == "llama" and ids[6:] == ["llm:" + ctx.config.data.llm.model_fast]
    ctx.config.set("llm.profile", "auto")
    llm = [i for i in (j.id for j in default_plan(ctx)) if i.startswith("llm:")]
    assert len(llm) == 2                         # auto: its pick first, then the other one


def test_describe():
    st = SetupStatus(running=True, current="Parakeet", current_done=50, current_total=100, rate=2 * 2**20,
                     done_bytes=100, total_bytes=100 + 600 * 2**20)
    line = describe(st)
    assert line.startswith("Downloading Parakeet · 50% · 2.0 MB/s") and "min left" in line
    assert describe(SetupStatus(finished=True)) == "All models are installed."
    assert "Lade" in describe(st, "de")


def test_cli_setup_keeps_going_and_explains_a_changed_file(tmp_path, monkeypatch, capsys):
    """install.bat runs --setup: one changed file must not stop the other downloads."""
    import assistant.__main__ as entry
    from assistant.core import hardware
    from assistant.llm import setup as llm_setup
    from assistant.llm.download import ServerFileChanged
    from assistant.voice import models as voice_models

    done = []
    monkeypatch.setattr(hardware, "nvidia_gpus", lambda: [])
    monkeypatch.setattr(llm_setup, "install_llama", lambda *a, **k: done.append("llama") or tmp_path / "srv.exe")
    monkeypatch.setattr(llm_setup, "download_model", lambda spec, *a, **k: done.append(spec.id))

    def ensure(vid, *a, **k):
        if vid == "stt":
            raise ServerFileChanged("parakeet.tar.bz2: the file on the server has changed (2 bytes instead of 1)")
        done.append(vid)

    monkeypatch.setattr(voice_models, "ensure", ensure)
    code = entry.main(["--setup", "--models", "qwen3.5-4b", "--home", str(tmp_path / "home")])
    out = capsys.readouterr().out
    assert code == 1
    assert done == ["llama", "qwen3.5-4b", "kws", "vad", "tts", "speaker"]
    assert "FAILED: parakeet.tar.bz2" in out and "Please update the assistant" in out
    assert entry.main(["--setup", "--models", "nope", "--home", str(tmp_path / "home")]) == 2
