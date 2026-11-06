"""Real llama-server + a real (tiny) model. Skipped unless both are available.

    ASSISTANT_TEST_LLAMA=/path/to/llama-server ASSISTANT_TEST_MODEL=/path/to/Qwen3.5-0.8B-Q4_0.gguf pytest
"""

import os
from pathlib import Path

import pytest

from assistant.llm.agent import Agent
from assistant.llm.models import QWEN_SAMPLING
from assistant.llm.server import LlamaServer, ServerOptions
from assistant.permissions.gate import Task

LLAMA = Path(os.environ.get("ASSISTANT_TEST_LLAMA", "/home/user/ggml-org/llama.cpp/build/bin/llama-server"))
MODEL = Path(os.environ.get("ASSISTANT_TEST_MODEL", "/home/user/models/Qwen3.5-0.8B-Q4_0.gguf"))
pytestmark = pytest.mark.skipif(not (LLAMA.exists() and MODEL.exists()), reason="needs llama-server + a model")


class Adapter:
    """Minimal LlmService stand-in on top of a real server."""

    def __init__(self, server):
        self.client = server.client()
        self.ready, self.spec, self.warmup_messages = True, None, None

    def chat(self, messages, *, tools=None, thinking=False, max_tokens=256, on_content=None, cancel=None):
        return self.client.chat(messages, tools=tools, thinking=False, sampling={**QWEN_SAMPLING, "temperature": 0.2},
                                max_tokens=max_tokens, on_content=on_content, cancel=cancel)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    srv = LlamaServer(ServerOptions(exe=LLAMA, model=MODEL, port=18900, ctx=4096, gpu_layers="0"),
                      tmp_path_factory.mktemp("logs"))
    srv.start(wait=True, timeout=120)
    yield srv
    srv.stop()


def test_real_tool_call_round_trip(server, ts):
    system, p, conf, home = ts
    agent = Agent(Adapter(server), system, tool_filter={"volume", "media"}, max_steps=3)
    streamed = []
    res = agent.run("Set the volume to 40 percent.", system="You control the user's PC with tools. Be brief.",
                    context="", history=[], task=Task(source="llm"), on_content=streamed.append)
    assert p.audio.volume == 40, res
    assert res.calls[0]["name"] == "volume" and res.answer
    assert "".join(streamed).strip()


def test_real_server_cancel(server):
    from assistant.core.cancel import CancelToken, Cancelled
    import threading

    tok = CancelToken()
    threading.Timer(0.5, tok.cancel).start()
    with pytest.raises(Cancelled):
        server.client().chat([{"role": "user", "content": "Count slowly from 1 to 500, one number per line."}],
                             max_tokens=2000, cancel=tok)
    assert server.client().health()          # server survives client disconnects
