"""Streaming client against a fake llama-server speaking the real SSE format."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assistant.core.cancel import CancelToken, Cancelled
from assistant.llm.client import LlamaClient, LLMError, extract_text_tool_calls, parse_arguments


def chunk(delta=None, finish=None, **extra):
    d = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}
    return f"data: {json.dumps(d)}\n\n".encode()


class FakeServer:
    def __init__(self):
        self.script = []      # list of bytes chunks for the next request
        self.requests = []
        self.status = 200
        self.delay = 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200 if self.path == "/health" else 404)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"body": body, "auth": self.headers.get("Authorization")})
                self.send_response(outer.status)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if outer.status != 200:
                    self.wfile.write(b'{"error":"bad"}')
                    return
                try:
                    for c in outer.script:
                        self.wfile.write(c)
                        self.wfile.flush()
                        time.sleep(outer.delay)
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def client(self):
        return LlamaClient(f"http://127.0.0.1:{self.port}", "secret")


@pytest.fixture
def srv():
    s = FakeServer()
    yield s
    s.httpd.shutdown()


def test_streams_content_reasoning_and_timings(srv):
    srv.script = [chunk({"role": "assistant", "content": None}), chunk({"reasoning_content": "hmm"}),
                  chunk({"content": "Hello"}), chunk({"content": " world"}), chunk(finish="stop"),
                  b'data: {"choices":[],"usage":{"prompt_tokens":5},"timings":{"cache_n":4,"prompt_n":1}}\n\n']
    got = []
    res = srv.client().chat([{"role": "user", "content": "hi"}], thinking=False, on_content=got.append,
                            sampling={"temperature": 0.5})
    assert res.content == "Hello world" and got == ["Hello", " world"] and res.reasoning == "hmm"
    assert res.finish_reason == "stop" and res.timings["cache_n"] == 4 and res.ttft_ms is not None
    req = srv.requests[0]
    assert req["auth"] == "Bearer secret"
    assert req["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert req["body"]["temperature"] == 0.5 and req["body"]["cache_prompt"] is True


def test_assembles_streamed_tool_calls(srv):
    srv.script = [chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                         "function": {"name": "volume", "arguments": "{"}}]}),
                  chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"level\":"}}]}),
                  chunk({"tool_calls": [{"index": 0, "function": {"arguments": "40}"}}]}),
                  chunk({"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "media",
                                                                             "arguments": "{\"action\":\"pause\"}"}}]}),
                  chunk(finish="tool_calls")]
    res = srv.client().chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert [(c.id, c.name, c.args()) for c in res.tool_calls] == [("c1", "volume", {"level": 40}),
                                                                  ("c2", "media", {"action": "pause"})]
    assert srv.requests[0]["body"]["parallel_tool_calls"] is True


def test_http_error_and_cancel(srv):
    srv.status = 500
    with pytest.raises(LLMError, match="HTTP 500"):
        srv.client().chat([{"role": "user", "content": "x"}])
    srv.status = 200
    srv.delay = 0.2
    srv.script = [chunk({"content": "tok"}) for _ in range(50)]
    tok = CancelToken()
    threading.Timer(0.3, tok.cancel, args=("panic",)).start()
    t0 = time.perf_counter()
    with pytest.raises(Cancelled):
        srv.client().chat([{"role": "user", "content": "x"}], cancel=tok)
    assert time.perf_counter() - t0 < 2


def test_server_down():
    with pytest.raises(LLMError, match="cannot talk"):
        LlamaClient("http://127.0.0.1:1").chat([{"role": "user", "content": "x"}])
    assert LlamaClient("http://127.0.0.1:1").health() is False


def test_argument_repair_and_text_fallback():
    assert parse_arguments('{"a": 1,}') == {"a": 1}
    assert parse_arguments('{"a": {"b": 2}') == {"a": {"b": 2}}
    assert parse_arguments('"{\\"a\\": 1}"') == {"a": 1}
    assert parse_arguments("") == {}
    with pytest.raises(ValueError):
        parse_arguments("[1, 2]")
    text, calls = extract_text_tool_calls('Sure. <tool_call>{"name": "volume", "arguments": {"level": 5}}</tool_call>')
    assert text == "Sure." and calls[0].name == "volume" and calls[0].args() == {"level": 5}
