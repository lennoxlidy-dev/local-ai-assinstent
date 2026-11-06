"""Entry point.

    python -m assistant              tray app (default; use pythonw.exe for no console)
    python -m assistant --cli        text-mode debug CLI
    python -m assistant --debug      tray app + log to the console
    python -m assistant --smoke-test 3   start, run 3 s, quit (automated check)
"""

from __future__ import annotations

import argparse
import sys

from . import __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="assistant", description="Local voice assistant")
    p.add_argument("--cli", action="store_true", help="text-mode debug CLI instead of the tray app")
    p.add_argument("--debug", action="store_true", help="also log to the console (DEBUG level)")
    p.add_argument("--home", metavar="DIR", help="keep all data in DIR (portable mode / testing)")
    p.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)  # launched at login
    p.add_argument("--smoke-test", type=float, metavar="SECONDS", help="start, then quit after SECONDS")
    p.add_argument("--setup", action="store_true", help="download llama.cpp and the language models, then exit")
    p.add_argument("--models", metavar="IDS", help="with --setup: comma-separated model ids (default: smart + fast)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from .core.context import fix_std_streams

    fix_std_streams()
    args = parse_args(argv)
    if args.setup:
        return run_setup(args)
    if args.cli:
        return run_cli(args)
    from .ui.app import run_tray

    return run_tray(home=args.home, autostarted=args.autostart, debug=args.debug,
                    smoke_test_s=args.smoke_test)


def run_cli(args: argparse.Namespace) -> int:
    from .cli import DebugCLI
    from .core.context import bootstrap
    from .win.shell import enable_per_monitor_dpi_awareness

    enable_per_monitor_dpi_awareness()
    ctx = bootstrap("cli", home=args.home, console_log=args.debug,
                    console_level="DEBUG" if args.debug else "WARNING", detect_hardware="off")
    return DebugCLI(ctx).run()


def run_setup(args: argparse.Namespace) -> int:
    """Console installer for llama.cpp + models (the one-click installer in M7 calls this too)."""
    from .core import hardware
    from .core.cancel import Cancelled
    from .core.context import bootstrap
    from .llm.download import DownloadError
    from .llm.models import get_model
    from .llm.setup import download_model, install_llama

    ctx = bootstrap("cli", home=args.home, console_log=args.debug, detect_hardware="off")
    c = ctx.config.data.llm
    ids = [m.strip() for m in (args.models or f"{c.model_smart},{c.model_fast}").split(",") if m.strip()]

    def bar(done: int, total: int, rate: float) -> None:
        pct = done / total * 100 if total else 0
        sys.stdout.write(f"\r  {pct:5.1f}%  {done / 2**20:8.0f} / {total / 2**20:.0f} MB  {rate / 2**20:5.1f} MB/s ")
        sys.stdout.flush()

    try:
        gpu = hardware.nvidia_gpus()
        driver = gpu[0].driver if gpu else None
        print(f"GPU: {gpu[0].name} (driver {driver})" if gpu else "No NVIDIA GPU found: using the Vulkan/CPU build")
        print("Installing llama.cpp ...")
        exe = install_llama(ctx.paths.bin_dir, ctx.paths.cache_dir, driver=driver, progress=bar)
        print(f"\n  {exe}")
        for mid in ids:
            spec = get_model(mid)
            print(f"Downloading {spec.title} ({spec.download_bytes / 2**30:.1f} GB) ...")
            download_model(spec, ctx.paths.models_dir, vision=c.vision, progress=bar)
            print()
    except (DownloadError, OSError, RuntimeError, KeyError) as e:
        print(f"\nSetup failed: {e}")
        return 1
    except (KeyboardInterrupt, Cancelled):
        print("\nCancelled - run it again to resume the downloads.")
        return 130
    print("Done. Start the assistant with scripts\\run-tray.bat or try scripts\\run-cli.bat")
    return 0


def cli_entry() -> int:
    """``assistant-cli`` console script: the debug CLI by default."""
    argv = sys.argv[1:]
    if "--cli" not in argv:
        argv = ["--cli", *argv]
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
