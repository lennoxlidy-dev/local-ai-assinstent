"""Entry point of the frozen build: Assistant.exe = tray app, AssistantCLI.exe = debug CLI / --setup."""

import os
import sys

from assistant.__main__ import cli_entry, main

if __name__ == "__main__":
    name = os.path.basename(sys.argv[0]).lower()
    sys.exit(cli_entry() if "cli" in name else main())
