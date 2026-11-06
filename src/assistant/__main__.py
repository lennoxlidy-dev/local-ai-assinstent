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
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from .core.context import fix_std_streams

    fix_std_streams()
    args = parse_args(argv)
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


def cli_entry() -> int:
    """``assistant-cli`` console script: the debug CLI by default."""
    argv = sys.argv[1:]
    if "--cli" not in argv:
        argv = ["--cli", *argv]
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
