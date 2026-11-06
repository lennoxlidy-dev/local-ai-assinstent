import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated ASSISTANT_HOME for one test."""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ts(tmp_path, monkeypatch):
    """A complete tool system on fake OS backends, with an auto-approving confirmer.

    Returns (tool_system, platform, confirmer, user_home).
    """
    from assistant.core.context import bootstrap
    from assistant.permissions.gate import AutoConfirm
    from assistant.permissions.pathrules import PathNormalizer
    from assistant.storage.db import Database
    from assistant.tools.runtime import build_tool_system, folder_aliases

    from fakes import fake_platform

    app_home = tmp_path / "appdata"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("ASSISTANT_HOME", str(app_home))
    ctx = bootstrap("cli", home=str(app_home), detect_hardware="off")
    platform = fake_platform(user_home)
    confirmer = AutoConfirm()
    norm = PathNormalizer("posix", env={"HOME": str(user_home)}, home=str(user_home),
                          aliases=folder_aliases(platform.known_folders))
    system = build_tool_system(ctx, platform=platform, confirmer=confirmer, db=Database(":memory:"),
                               start_background=False, normalizer=norm)
    system.ctx = ctx
    return system, platform, confirmer, user_home
