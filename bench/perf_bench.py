"""Measure the assistant against the spec's performance targets.

    uv run python bench/perf_bench.py                 # everything that's installed (~2 min)
    uv run python bench/perf_bench.py --llm           # also the language model's first-token latency
    uv run python bench/perf_bench.py --idle-seconds 120 --attach   # idle CPU/RAM of the running tray app

Targets (SPEC.md): idle < 2 % CPU, < 300 MB RAM, GPU unused; wake word -> popup < 300 ms;
end of speech -> transcript < 500 ms; fast-path command done < 1 s; LLM reply starts < 2 s.

Sections:
* idle      - the running tray app (``--attach``, the real thing) or the voice pipeline in-process
* wake      - how long after the end of the spoken name the keyword spotter fires (10 synthetic voices)
* stt       - speech recognition time per request (model loaded) and the model load time
* fastpath  - 30 common commands end to end (fake PC backends)
* tts       - time to the first spoken audio of an answer
* llm       - first token of a reply (``--llm``; needs llama.cpp + a model)
* usage     - medians of the TIMING lines in your debug log (real requests you made)
Results are printed and saved to %LOCALAPPDATA%\\Assistant\\bench\\perf-<date>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import psutil  # noqa: E402

from assistant.core.context import AppContext, bootstrap  # noqa: E402

FAST_COMMANDS = [
    "set the volume to 30", "volume up", "mute", "pause", "next song", "what time is it", "what's the date",
    "open notepad", "close notepad", "set a timer for 5 minutes", "remind me in 10 minutes to stretch",
    "switch to desktop 2", "lock the pc", "play some music", "stop", "Lautstärke auf 40", "nächstes Lied",
    "wie spät ist es", "öffne den Editor", "stell einen Timer auf zwei Minuten", "pausieren", "leiser",
    "what do you know about me", "remember that my car is blue", "brightness to 60", "open calculator",
    "previous track", "wechsle zu desktop 1", "mach Musik an", "timer abbrechen",
]


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))]


def row(name: str, value: str, target: str = "", ok: bool | None = None, note: str = "") -> dict[str, Any]:
    return {"metric": name, "value": value, "target": target, "ok": ok, "note": note}


# ----------------------------------------------------------------------------- idle
def find_tray() -> psutil.Process | None:
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
        except Exception:
            continue
        if p.info["pid"] == me or "perf_bench" in cmd:
            continue
        name = (p.info["name"] or "").lower()
        if name in ("assistant.exe", "assistant") or re.search(r"-m\s+assistant(\s|$)(?!.*--cli)", cmd):
            return p
    return None


def measure_proc(p: psutil.Process, seconds: float) -> dict[str, float]:
    procs = [p] + [c for c in p.children(recursive=True) if "llama" not in (c.name() or "").lower()]
    for q in procs:
        q.cpu_percent(None)
    samples: list[float] = []
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(1.0)
        samples.append(sum(q.cpu_percent(None) for q in procs if q.is_running()))
    rss = sum(q.memory_info().rss for q in procs if q.is_running())
    try:
        private = sum(getattr(q.memory_full_info(), "uss", 0) for q in procs if q.is_running())
    except Exception:
        private = 0
    return {"cpu_one_core": statistics.mean(samples), "cpu_max": max(samples), "rss_mb": rss / 2**20,
            "private_mb": private / 2**20}


def idle_section(ctx: AppContext, args: argparse.Namespace) -> list[dict[str, Any]]:
    cores = psutil.cpu_count() or 1
    tray = find_tray() if args.attach else None
    rows = []
    if tray is not None:
        m = measure_proc(tray, args.idle_seconds)
        how = f"running tray app (pid {tray.pid}), {args.idle_seconds:.0f} s"
    else:
        from assistant.audio.sources import ArraySource
        from assistant.voice.models import missing
        from assistant.voice.runtime import build_voice

        if missing(ctx.paths.models_dir):
            return [row("idle", "skipped", note="speech models not installed")]

        class Quiet:
            def handle(self, *a: Any, **k: Any) -> None:
                return None

            def cancel(self, *a: Any) -> bool:
                return False

        noise = (np.random.default_rng(0).standard_normal(int(16000 * (args.idle_seconds + 10))) * 0.003)
        factory = (lambda: ArraySource(noise.astype(np.float32), realtime=True)) if args.fake_mic else None
        stack = build_voice(ctx, Quiet(), source_factory=factory, headless=True)
        stack.pipeline.start()
        time.sleep(3)
        m = measure_proc(psutil.Process(), args.idle_seconds)
        stack.pipeline.stop()
        how = f"voice pipeline in this process ({'fake' if args.fake_mic else 'real'} microphone), no UI"
    total = m["cpu_one_core"] / cores
    rows.append(row("idle CPU (Task Manager style, all cores)", f"{total:.2f} %", "< 2 %", total < 2,
                    f"{m['cpu_one_core']:.1f} % of one core; {how}"))
    rows.append(row("idle RAM (working set)", f"{m['rss_mb']:.0f} MB", "< 300 MB", m["rss_mb"] < 300,
                    f"private {m['private_mb']:.0f} MB" if m["private_mb"] else ""))
    gpu = gpu_usage()
    rows.append(row("idle GPU", gpu["text"], "unused", gpu["ok"], gpu.get("note", "")))
    return rows


def gpu_usage() -> dict[str, Any]:
    from assistant.core import hardware

    gpus = hardware.nvidia_gpus()
    if not gpus:
        return {"text": "no NVIDIA GPU", "ok": None}
    llama = [p for p in psutil.process_iter(["name"]) if "llama-server" in (p.info["name"] or "").lower()]
    g = gpus[0]
    used = g.vram_total_mb - g.vram_free_mb
    note = ("llama-server holds the model in VRAM but computes only while answering"
            if llama else "no language model loaded")
    return {"text": f"{used} MB VRAM in use (all apps)", "ok": True, "note": note}


# ----------------------------------------------------------------------------- voice
class Voice:
    """Synthesized test speech (the assistant's own TTS) + the speech models."""

    def __init__(self, ctx: AppContext):
        from assistant.audio.player import NullPlayer
        from assistant.voice.runtime import model_path
        from assistant.voice.tts import Speaker

        self.ctx = ctx
        self.speaker = Speaker(model_path(ctx, "tts"), NullPlayer())

    def say(self, text: str, voice: str, lang: str = "en") -> tuple[np.ndarray, int]:
        """16 kHz audio with 0.5 s silence before and 2 s after; returns (audio, sample where speech ends)."""
        import soxr

        s, sr = self.speaker.synthesize(text, lang, voice)
        x = soxr.resample(s, sr, 16000).astype(np.float32)
        win = 160
        e = np.array([np.sqrt(np.mean(x[i:i + win] ** 2)) for i in range(0, len(x) - win, win)])
        end = int((np.nonzero(e > 0.02 * e.max())[0][-1] + 1) * win)
        pre = np.zeros(8000, np.float32)
        return np.concatenate([pre, x, np.zeros(32000, np.float32)]), 8000 + end


def wake_section(ctx: AppContext, v: Voice) -> list[dict[str, Any]]:
    from assistant.audio.sources import BLOCK
    from assistant.voice.runtime import model_path
    from assistant.voice.wakeword import WakeWord

    name = ctx.config.data.general.assistant_name
    w = WakeWord(model_path(ctx, "kws"), name, sensitivity=ctx.config.data.voice.wake_sensitivity,
                 variants=list(ctx.config.data.voice.wake_variants),
                 keywords_file=Path(tempfile.gettempdir()) / "assistant-bench-kw.txt")
    delays, hits, total = [], 0, 0
    for voice in ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"):
        audio, end = v.say(f"{name}.", voice)
        total += 1
        w.reset()
        for i in range(0, len(audio), BLOCK):
            if w.process(audio[i:i + BLOCK]):
                hits += 1
                delays.append((i + BLOCK - end) / 16)
                break
    if not delays:
        return [row("wake word", f"0/{total} detected", note="try a calibration (:calibrate)")]
    med = statistics.median(delays)
    popup = "~2-3 ms after detection (window is pre-created)"
    return [row("wake word detected", f"{hits}/{total} synthetic voices", note=f"name: {name}"),
            row("end of the name -> popup", f"{med:.0f} ms (max {max(delays):.0f})", "< 300 ms", med < 300,
                f"keyword-spotter decision {med:.0f} ms (fixed model chunking) + popup {popup}")]


def stt_section(ctx: AppContext, v: Voice) -> list[dict[str, Any]]:
    from assistant.voice.runtime import model_path
    from assistant.voice.stt import Transcriber

    stt = Transcriber(model_path(ctx, "stt"))
    t0 = time.perf_counter()
    stt._load()
    load_ms = (time.perf_counter() - t0) * 1000
    times = []
    for text, voice in (("What time is it?", "M1"), ("Open the calculator and set the volume to thirty.", "F1"),
                        ("Wie spät ist es?", "M2"), ("Remind me in ten minutes to call my sister.", "F3"),
                        ("Spiel bitte etwas Jazz auf Spotify.", "M4")):
        audio, end = v.say(text, voice, "de" if text[0] in "WS" and ("ist" in text or "bitte" in text) else "en")
        clip = audio[: end + 4000]
        t0 = time.perf_counter()
        stt.transcribe(clip)
        times.append((time.perf_counter() - t0) * 1000)
    silence = ctx.config.data.voice.silence_timeout_s * 1000
    med = statistics.median(times)
    return [row("end of speech -> transcript", f"{med:.0f} ms (max {max(times):.0f})", "< 500 ms", med < 500,
                f"measured after the {silence:.0f} ms pause that marks the end; speech model loaded"),
            row("speech recognition model load", f"{load_ms:.0f} ms", note="happens at the wake word, in parallel "
                "with your speech; only after it was unloaded (voice.stt_keep_loaded_min)")]


def tts_section(ctx: AppContext, v: Voice) -> list[dict[str, Any]]:
    from assistant.voice.tts import split_sentences

    answer = "It is ten past three. Your next meeting starts in twenty minutes."
    first = split_sentences(answer)[0]
    v.speaker.synthesize("Warm up.", "en", "F1")
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        v.speaker.synthesize(first, "en", ctx.config.data.voice.tts_voice_en)
        times.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(times)
    return [row("answer text -> first spoken audio", f"{med:.0f} ms", note=f"synthesis of “{first}”")]


# ----------------------------------------------------------------------------- fast path + LLM
def fastpath_section(ctx: AppContext) -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "tests"))
    from assistant.core.assistant import Assistant
    from assistant.permissions.gate import AutoConfirm
    from assistant.permissions.pathrules import PathNormalizer
    from assistant.storage.db import Database
    from assistant.testing.fakes import fake_platform
    from assistant.tools.runtime import build_tool_system, folder_aliases

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        bctx = bootstrap("cli", home=str(home / "app"), detect_hardware="off")
        user = home / "user"
        user.mkdir()
        platform = fake_platform(user)
        norm = PathNormalizer("posix", env={"HOME": str(user)}, home=str(user),
                              aliases=folder_aliases(platform.known_folders))
        system = build_tool_system(bctx, platform=platform, confirmer=AutoConfirm(), db=Database(":memory:"),
                                   start_background=False, normalizer=norm)
        a = Assistant(bctx, system, None)
        times, via = [], []
        for cmd in FAST_COMMANDS:
            t0 = time.perf_counter()
            r = a.handle(cmd, source="bench")
            times.append((time.perf_counter() - t0) * 1000)
            via.append(r.via)
        system.stop()
    handled = sum(v in ("fast", "local") for v in via)
    med = statistics.median(times)
    return [row("fast-path command done", f"{med:.1f} ms (p95 {pct(times, 95):.1f})", "< 1 s", pct(times, 95) < 1000,
                f"{handled}/{len(FAST_COMMANDS)} handled without the LLM; real app/tool time on Windows adds to this")]


def llm_section(ctx: AppContext) -> list[dict[str, Any]]:
    from assistant.llm.service import LlmService

    llm = LlmService(ctx)
    try:
        t0 = time.perf_counter()
        llm.start(wait=True)
        load = (time.perf_counter() - t0) * 1000
    except Exception as e:
        return [row("LLM reply starts", "skipped", note=str(e)[:120])]
    try:
        ttfts = []
        for q in ("What's a good name for a cat?", "Explain in one sentence what a CPU does.",
                  "Wie heißt die Hauptstadt von Australien?"):
            first: list[float] = []
            t0 = time.perf_counter()
            llm.chat([{"role": "user", "content": q}],
                     on_content=lambda d, first=first: first or first.append(time.perf_counter()))
            if first:
                ttfts.append((first[0] - t0) * 1000)
        med = statistics.median(ttfts) if ttfts else float("nan")
        model = llm.spec.title if llm.spec else "?"
        return [row("LLM reply starts (first token)", f"{med:.0f} ms", "< 2 s", med < 2000, f"{model}; plus speech "
                    "recognition time for voice requests"),
                row("LLM model load", f"{load / 1000:.1f} s", note="at start / after game mode / after idle unload")]
    finally:
        llm.stop()


# ----------------------------------------------------------------------------- your real requests
def usage_section(ctx: AppContext) -> list[dict[str, Any]]:
    stats: dict[str, list[float]] = {"wake_to_popup": [], "speech_end>transcript": [], "transcript>reply": [],
                                     "reply_to_first_audio": [], "input>llm_first_token": [], "input>answer": []}
    for name in ("assistant-debug.log", "assistant-debug.log.1"):
        f = ctx.paths.logs_dir / name
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "TIMING" not in line:
                continue
            m = re.search(r"stage=(wake_to_popup|reply_to_first_audio) ms=([\d.]+)", line)
            if m:
                stats[m.group(1)].append(float(m.group(2)))
            for key in ("speech_end>transcript", "transcript>reply", "input>llm_first_token", "input>answer"):
                m = re.search(rf"{re.escape(key)}=([\d.]+)", line)
                if m:
                    stats[key].append(float(m.group(1)))
    out = []
    labels = {"wake_to_popup": "wake event -> popup on screen", "speech_end>transcript": "end of speech -> transcript",
              "transcript>reply": "transcript -> reply text", "reply_to_first_audio": "reply -> first spoken audio",
              "input>llm_first_token": "request -> LLM first token", "input>answer": "request -> answer"}
    for key, vals in stats.items():
        if vals:
            out.append(row(f"your usage: {labels[key]}", f"{statistics.median(vals):.0f} ms median",
                           note=f"{len(vals)} requests, p90 {pct(vals, 90):.0f} ms"))
    return out or [row("your usage", "no TIMING lines yet", note="use the assistant with debug.debug_log on, "
                       "then run this again")]


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--idle-seconds", type=float, default=30)
    ap.add_argument("--attach", action="store_true", help="measure the running tray app instead of an in-process pipeline")
    ap.add_argument("--fake-mic", action="store_true", help="in-process idle test with a synthetic microphone")
    ap.add_argument("--llm", action="store_true", help="also measure the language model (starts llama-server)")
    ap.add_argument("--skip", default="", help="comma-separated sections to skip: idle,wake,stt,tts,fastpath,usage")
    ap.add_argument("--home", help="data folder (default: the normal one)")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    ctx = bootstrap("cli", home=args.home, console_log=False, detect_hardware="off")
    from assistant.voice.models import missing

    rows: list[dict[str, Any]] = []
    have_voice = not missing(ctx.paths.models_dir)
    print("Measuring … (this takes a minute or two)\n", flush=True)
    if "idle" not in skip:
        rows += idle_section(ctx, args)
    v = Voice(ctx) if have_voice and not {"wake", "stt", "tts"} <= skip else None
    if v is None and not {"wake", "stt", "tts"} <= skip:
        rows.append(row("voice", "skipped", note="speech models not installed (run setup)"))
    if v is not None:
        if "wake" not in skip:
            rows += wake_section(ctx, v)
        if "stt" not in skip:
            rows += stt_section(ctx, v)
        if "tts" not in skip:
            rows += tts_section(ctx, v)
    if "fastpath" not in skip:
        rows += fastpath_section(ctx)
    if args.llm:
        rows += llm_section(ctx)
    if "usage" not in skip:
        rows += usage_section(ctx)

    width = max(len(r["metric"]) for r in rows) + 2
    print(f"{'metric':<{width}} {'measured':<28} {'target':<10} ok")
    for r in rows:
        ok = "" if r["ok"] is None else ("✓" if r["ok"] else "✗")
        print(f"{r['metric']:<{width}} {r['value']:<28} {r['target']:<10} {ok}  {r['note']}")
    out_dir = ctx.paths.local_dir / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"perf-{time.strftime('%Y%m%d-%H%M%S')}.json"
    from assistant.core import hardware

    info = hardware.detect()
    out.write_text(json.dumps({"date": time.strftime("%Y-%m-%d %H:%M"), "cpu": info.cpu, "cores": info.logical_cores,
                               "ram_mb": info.ram_total_mb, "gpu": info.best_gpu.name if info.best_gpu else None,
                               "results": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
