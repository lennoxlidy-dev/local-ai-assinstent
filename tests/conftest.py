import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated ASSISTANT_HOME for one test."""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path))
    return tmp_path
