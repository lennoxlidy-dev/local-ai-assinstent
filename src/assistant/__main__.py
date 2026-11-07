"""Entry point.

    python -m assistant              tray app (default; use pythonw.exe for no console)
    python -m assistant --cli        text-mode debug CLI
    python -m assistant --debug      tray app + log to the console
    python -m assistant --smoke-test 3   start, run 3 s, quit (automated check)
    python -m assistant --onboarding     open the setup wizard again
    python -m assistant --self-test      open every window once (temporary home), report problems
    python -m assistant --quit           ask the running assistant to quit (installer, uninstaller)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="assistant", description="Local voice assistant")
    p.add_argument("--cli", action="store_true", help="text-mode debug CLI instead of the tray app")
    p.add_argument("--debug", action="store_true", help="also log to the console (DEBUG level)")
    p.add_argument("--home", metavar="DIR", help="keep all data in DIR (portable mode / testing)")
    p.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)  # launched at login
    p.add_argument("--smoke-test", type=float, metavar="SECONDS", help="start, then quit after SECONDS")
    p.add_argument("--self-test", action="store_true",
                   help="open every window once with a temporary home, report problems, quit (checks a build)")
    p.add_argument("--quit", action="store_true", help="ask the running assistant to quit, wait until it has")
    p.add_argument("--setup", action="store_true", help="download llama.cpp and the language models, then exit")
    p.add_argument("--onboarding", action="store_true", help="open the setup wizard (also when it was done before)")
    p.add_argument("--models", metavar="IDS",
                   help="with --setup: comma-separated model ids, or 'auto' (default: smart + fast)")
    p.add_argument("--voice-test", nargs="+", metavar="WAV", help="run WAV files through the voice pipeline")
    p.add_argument("--speak-out", metavar="WAV", help="with --voice-test: save the spoken replies")
    p.add_argument("--fast", action="store_true", help="with --voice-test: feed audio faster than real time")
    p.add_argument("--say", metavar="TEXT", help="synthesize TEXT with the assistant's voice (needs --out)")
    p.add_argument("--lang", default=None, help="language for --say (en|de; default: config)")
    p.add_argument("--voice", default=None, help="voice for --say (F1..F5, M1..M5)")
    p.add_argument("--out", metavar="WAV", help="output file for --say")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from .core.context import fix_std_streams

    fix_std_streams()
    args = parse_args(argv)
    if args.quit:
        return run_quit(args)
    if args.setup:
        return run_setup(args)
    if args.say or args.voice_test:
        return run_voice_tools(args)
    if args.cli and not args.self_test:     # AssistantCLI.exe / assistant-cli add --cli
        return run_cli(args)
    from .ui.app import run_tray

    return run_tray(home=args.home, autostarted=args.autostart, debug=args.debug,
                    smoke_test_s=args.smoke_test, open_setup=args.onboarding, self_test=args.self_test)


def run_cli(args: argparse.Namespace) -> int:
    from .cli import DebugCLI
    from .core.context import bootstrap
    from .win.shell import enable_per_monitor_dpi_awareness

    enable_per_monitor_dpi_awareness()
    ctx = bootstrap("cli", home=args.home, console_log=args.debug,
                    console_level="DEBUG" if args.debug else "WARNING", detect_hardware="off")
    return DebugCLI(ctx).run()


def run_quit(args: argparse.Namespace, wait_s: float = 20.0) -> int:
    """Ask the tray app (same data folder) to quit and wait for it: 0 = not running (any more)."""
    import time

    from PySide6.QtCore import QCoreApplication, QLockFile

    from .core.paths import resolve_paths
    from .ui.single_instance import SingleInstance

    lock_file = resolve_paths(args.home).lock_file
    if not lock_file.exists():
        return 0
    _app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    lock = QLockFile(str(lock_file))
    if lock.tryLock(100):
        lock.unlock()
        return 0
    if not SingleInstance(lock_file).send("quit"):
        print("the running assistant did not answer", file=sys.stderr)
        return 1
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if lock.tryLock(250):
            lock.unlock()
            return 0
    print(f"the assistant is still running after {wait_s:.0f} s", file=sys.stderr)
    return 1


def run_setup(args: argparse.Namespace) -> int:
    """Console installer for llama.cpp + models (the one-click installer in M7 calls this too)."""
    from .core import hardware
    from .core.cancel import Cancelled
    from .core.context import bootstrap
    from .llm.download import DownloadError, ServerFileChanged
    from .llm.models import get_model
    from .llm.setup import download_model, install_llama

    ctx = bootstrap("cli", home=args.home, console_log=args.debug, detect_hardware="off")
    c = ctx.config.data.llm
    if args.models == "auto":       # the installer: just the model that suits this PC (the wizard can add more)
        from .onboarding.downloads import llm_models_to_get

        ids = llm_models_to_get(ctx)[:1]
    else:
        ids = [m.strip() for m in (args.models or f"{c.model_smart},{c.model_fast}").split(",") if m.strip()]

    def bar(done: int, total: int, rate: float) -> None:
        pct = done / total * 100 if total else 0
        sys.stdout.write(f"\r  {pct:5.1f}%  {done / 2**20:8.0f} / {total / 2**20:.0f} MB  {rate / 2**20:5.1f} MB/s ")
        sys.stdout.flush()

    from .voice.models import CORE_VOICE, VOICE_MODELS, ensure

    gpu = hardware.nvidia_gpus()
    driver = gpu[0].driver if gpu else None
    print(f"GPU: {gpu[0].name} (driver {driver})" if gpu else "No NVIDIA GPU found: using the Vulkan/CPU build")

    def llama() -> None:
        print(f"\n  {install_llama(ctx.paths.bin_dir, ctx.paths.cache_dir, driver=driver, progress=bar)}")

    try:
        specs = [get_model(mid) for mid in ids]
    except KeyError as e:
        print(f"Unknown model: {e}")
        return 2
    steps: list[tuple[str, Callable[[], object]]] = [("llama.cpp", llama)]
    for spec in specs:
        steps.append((f"{spec.title} ({spec.download_bytes / 2**30:.1f} GB)",
                      lambda spec=spec: download_model(spec, ctx.paths.models_dir, vision=c.vision, progress=bar)))
    for vid in (*CORE_VOICE, "speaker"):
        vm = VOICE_MODELS[vid]
        steps.append((f"{vm.title} ({vm.size / 2**20:.0f} MB)",
                      lambda vid=vid: ensure(vid, ctx.paths.models_dir, ctx.paths.cache_dir, progress=bar)))
    failed: list[str] = []
    changed = False
    try:
        for title, step in steps:           # one failed download doesn't stop the others
            print(f"Downloading {title} ...")
            try:
                step()
                print()
            except (DownloadError, OSError, RuntimeError, KeyError) as e:
                print(f"\n  FAILED: {e}")
                failed.append(title)
                changed = changed or isinstance(e, ServerFileChanged)
    except (KeyboardInterrupt, Cancelled):
        print("\nCancelled - run it again to resume the downloads.")
        return 130
    if failed:
        print(f"\nNot downloaded: {', '.join(failed)}")
        if changed:
            print("A file on the download server has changed since this version of the assistant was made, so it "
                  "can't be verified.\nPlease update the assistant (download the latest version and run install.bat).")
        else:
            print("Run this again to retry; finished files are kept and interrupted ones resume.")
        return 1
    print("Done. Start the assistant with scripts\\run-tray.bat or try scripts\\run-cli.bat")
    return 0


def run_voice_tools(args: argparse.Namespace) -> int:
    """--say (make test audio) and --voice-test (WAV harness)."""
    from pathlib import Path

    from .core.context import bootstrap
    from .voice.harness import run_wavs, synthesize

    ctx = bootstrap("cli", home=args.home, console_log=args.debug,
                    console_level="DEBUG" if args.debug else "WARNING", detect_hardware="off")
    if args.say:
        if not args.out:
            print("--say needs --out file.wav")
            return 2
        lang = args.lang or ctx.config.data.general.language
        out = synthesize(ctx, args.say, lang, Path(args.out), args.voice or "F1")
        print(f"wrote {out}")
        return 0
    from .core.assistant import Assistant
    from .llm.service import LlmService
    from .tools.runtime import build_tool_system

    tools = build_tool_system(ctx)
    assistant = Assistant(ctx, tools, LlmService(ctx))
    run_wavs(ctx, assistant, [Path(p) for p in args.voice_test], realtime=not args.fast,
             speak_out=Path(args.speak_out) if args.speak_out else None)
    tools.stop()
    return 0


def cli_entry() -> int:
    """``assistant-cli`` console script: the debug CLI by default."""
    argv = sys.argv[1:]
    if "--cli" not in argv:
        argv = ["--cli", *argv]
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
