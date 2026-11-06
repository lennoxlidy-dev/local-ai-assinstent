import threading
import time

import pytest

from assistant.core.cancel import CancelToken, Cancelled
from assistant.core.events import EventBus, Topic
from assistant.core.state import AppState, Phase
from assistant.core.timing import Trace
from assistant.core.paths import resolve_paths
from assistant.core import hardware


def test_bus_delivers_and_isolates_handler_errors():
    bus = EventBus()
    got = []
    bus.subscribe("a", lambda e: got.append(("a", e.data)))
    bus.subscribe("*", lambda e: got.append(("*", e.topic)))
    bus.subscribe("a", lambda e: 1 / 0)  # must not break delivery
    unsub = bus.subscribe("a", lambda e: got.append(("late", 1)))
    unsub()
    bus.publish("a", x=1)
    assert got == [("a", {"x": 1}), ("*", "a")]


def test_state_publishes_only_changes():
    bus = EventBus()
    seen = []
    bus.subscribe(Topic.STATE_CHANGED, lambda e: seen.append(e.data["changed"]))
    st = AppState(bus)
    st.update(paused=True)
    st.update(paused=True)
    st.update(phase=Phase.LISTENING, game_mode=False)
    assert seen == [{"paused": True}, {"phase": Phase.LISTENING}]
    assert st.paused is True and st.phase == "listening"
    with pytest.raises(AttributeError):
        st.update(nonsense=1)


def test_cancel_token_tree_and_callbacks():
    root = CancelToken()
    child = root.child()
    grandchild = child.child()
    released = []
    grandchild.on_cancel(lambda: released.append("keys released"))
    root.cancel("panic")
    assert child.cancelled and grandchild.cancelled and grandchild.reason == "panic"
    assert released == ["keys released"]
    with pytest.raises(Cancelled):
        grandchild.check()
    late = root.child()  # created after cancel -> cancelled immediately
    assert late.cancelled
    called = []
    late.on_cancel(lambda: called.append(1))
    assert called == [1]


def test_cancel_wait_returns_early():
    tok = CancelToken()
    threading.Timer(0.05, tok.cancel).start()
    t = time.perf_counter()
    assert tok.wait(5) is True
    assert time.perf_counter() - t < 1


def test_trace_marks_and_summary(caplog):
    caplog.set_level("DEBUG", logger="assistant.timing")
    tr = Trace()
    tr.mark("wake")
    with tr.span("stt"):
        time.sleep(0.01)
    tr.mark("popup")
    out = tr.finish((("wake", "popup"),))
    assert out["wake>popup"] >= 0 and out["span:stt"] >= 9
    assert any("TIMING" in r.message and "summary" in r.message for r in caplog.records)


def test_paths_home_override(home):
    p = resolve_paths()
    assert p.config_dir == home and p.local_dir == home
    p.ensure()
    assert p.logs_dir.is_dir() and p.models_dir.is_dir()


def test_hardware_detect_without_gpu():
    info = hardware.detect()
    assert info.ram_total_mb > 0 and info.logical_cores > 0
    assert isinstance(info.gpus, list)


def test_nvidia_smi_parsing(monkeypatch):
    class R:
        returncode = 0
        stdout = "NVIDIA GeForce RTX 4070 Laptop GPU, 8188, 7612, 581.42\n"
    monkeypatch.setattr(hardware.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(hardware, "run_hidden", lambda *a, **k: R())
    (g,) = hardware._smi_gpus()
    assert g.name.endswith("Laptop GPU") and g.vram_total_mb == 8188 and g.vram_free_mb == 7612
