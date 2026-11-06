"""OpenAI-compatible chat client for llama-server (stdlib http.client, streaming SSE).

Stream format (verified against llama.cpp master, Nov 2026)::

    data: {"choices":[{"delta":{"reasoning_content":"..."}}]}        thinking tokens
    data: {"choices":[{"delta":{"content":"..."}}]}                  answer tokens
    data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"…","function":{"name":"volume","arguments":"{"}}]}}]}
    data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"level\\":40}"}}]}}]}
    data: {"choices":[{"finish_reason":"tool_calls","delta":{}}]}
    data: {"choices":[],"usage":{...},"timings":{"cache_n":…,"prompt_n":…,"predicted_per_second":…}}
    data: [DONE]

Cancelling closes the socket; llama-server then stops generating.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.cancel import CancelToken, Cancelled

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def args(self) -> dict[str, Any]:
        """Parse the JSON arguments; tolerant of the usual small-model slips."""
        return parse_arguments(self.arguments)


@dataclass
class ChatResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)
    ttft_ms: float | None = None      # time to first generated token (any kind)
    first_content_ms: float | None = None
    total_ms: float = 0.0


def parse_arguments(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        fixed = raw
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)              # trailing commas
        if fixed.count("{") > fixed.count("}"):
            fixed += "}" * (fixed.count("{") - fixed.count("}"))  # truncated object
        try:
            val = json.loads(fixed)
        except json.JSONDecodeError:
            raise ValueError(f"tool arguments are not valid JSON: {raw[:200]}") from None
    if isinstance(val, str):  # double-encoded
        try:
            val = json.loads(val)
        except json.JSONDecodeError:
            pass
    if not isinstance(val, dict):
        raise ValueError("tool arguments must be a JSON object")
    return val


_TEXT_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def extract_text_tool_calls(content: str) -> tuple[str, list[ToolCall]]:
    """Fallback: a model that printed ``<tool_call>{json}</tool_call>`` instead of a native call."""
    calls = []
    for m in _TEXT_TOOL_CALL.finditer(content):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        name = obj.get("name")
        if isinstance(name, str):
            calls.append(ToolCall(f"call_{uuid.uuid4().hex[:8]}", name,
                                  json.dumps(obj.get("arguments") or obj.get("parameters") or {})))
    return (_TEXT_TOOL_CALL.sub("", content).strip(), calls) if calls else (content, [])


class LlamaClient:
    def __init__(self, base_url: str, api_key: str = "", *, connect_timeout: float = 5.0,
                 read_timeout: float = 180.0):
        u = urllib.parse.urlparse(base_url)
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 80
        self.api_key = api_key
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    # ------------------------------------------------------------------ plumbing
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _conn(self, timeout: float) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def get(self, path: str, timeout: float = 3.0) -> tuple[int, Any]:
        conn = self._conn(timeout)
        try:
            conn.request("GET", path, headers=self._headers())
            resp = conn.getresponse()
            body = resp.read()
            try:
                return resp.status, json.loads(body or b"null")
            except json.JSONDecodeError:
                return resp.status, body.decode("utf-8", "replace")
        finally:
            conn.close()

    def health(self) -> bool:
        try:
            status, _ = self.get("/health", timeout=2.0)
            return status == 200
        except OSError:
            return False

    # ------------------------------------------------------------------ chat
    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
             tool_choice: str | None = None, thinking: bool | None = None, thinking_style: str = "template",
             sampling: dict[str, Any] | None = None, max_tokens: int = 1024,
             on_content: Callable[[str], None] | None = None, on_reasoning: Callable[[str], None] | None = None,
             cancel: CancelToken | None = None, extra: dict[str, Any] | None = None) -> ChatResult:
        body: dict[str, Any] = {"messages": messages, "stream": True, "max_tokens": max_tokens,
                                "stream_options": {"include_usage": True}, "cache_prompt": True}
        if tools:
            body["tools"] = tools
            body["parallel_tool_calls"] = True
            if tool_choice:
                body["tool_choice"] = tool_choice
        if thinking is not None and thinking_style == "template":
            body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        elif thinking is not None and thinking_style == "effort":
            body["reasoning_effort"] = "medium" if thinking else "none"
        body.update(sampling or {})
        body.update(extra or {})
        return self._stream(body, on_content, on_reasoning, cancel)

    def _stream(self, body: dict[str, Any], on_content: Callable[[str], None] | None,
                on_reasoning: Callable[[str], None] | None, cancel: CancelToken | None) -> ChatResult:
        t0 = time.perf_counter()
        res = ChatResult()
        calls: dict[int, ToolCall] = {}
        conn = self._conn(self.connect_timeout)
        closed = threading.Event()

        def abort() -> None:  # cancel from another thread: closing the socket unblocks readline()
            if not closed.is_set():
                closed.set()
                try:
                    if conn.sock is not None:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        if cancel is not None:
            cancel.on_cancel(abort)
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            conn.request("POST", "/v1/chat/completions", body=payload, headers=self._headers())
            if conn.sock is not None:
                conn.sock.settimeout(self.read_timeout)
            resp = conn.getresponse()
            if resp.status != 200:
                text = resp.read().decode("utf-8", "replace")
                raise LLMError(f"llama-server HTTP {resp.status}: {text[:300]}")
            for raw in resp:
                if cancel is not None and cancel.cancelled:
                    raise Cancelled(cancel.reason)
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    raise LLMError(str(chunk["error"])[:300])
                if chunk.get("usage"):
                    res.usage = chunk["usage"]
                if chunk.get("timings"):
                    res.timings = chunk["timings"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        res.finish_reason = choice["finish_reason"]
                    rc = delta.get("reasoning_content")
                    if rc:
                        res.ttft_ms = res.ttft_ms or (time.perf_counter() - t0) * 1000
                        res.reasoning += rc
                        if on_reasoning:
                            on_reasoning(rc)
                    c = delta.get("content")
                    if c:
                        now = (time.perf_counter() - t0) * 1000
                        res.ttft_ms = res.ttft_ms or now
                        res.first_content_ms = res.first_content_ms or now
                        res.content += c
                        if on_content:
                            on_content(c)
                    for tc in delta.get("tool_calls") or []:
                        res.ttft_ms = res.ttft_ms or (time.perf_counter() - t0) * 1000
                        idx = int(tc.get("index", len(calls)))
                        call = calls.setdefault(idx, ToolCall(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", ""))
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            call.name += fn["name"]
                        if fn.get("arguments"):
                            call.arguments += fn["arguments"]
            if cancel is not None and cancel.cancelled:  # socket shut down by abort(): stream ended early
                raise Cancelled(cancel.reason)
        except (OSError, http.client.HTTPException) as e:
            if cancel is not None and cancel.cancelled:
                raise Cancelled(cancel.reason) from None
            if isinstance(e, TimeoutError | socket.timeout):
                raise LLMError("the language model took too long to answer") from None
            raise LLMError(f"cannot talk to llama-server: {e}") from None
        finally:
            closed.set()
            conn.close()
        res.tool_calls = [calls[i] for i in sorted(calls) if calls[i].name]
        if not res.tool_calls and "<tool_call>" in res.content:
            res.content, res.tool_calls = extract_text_tool_calls(res.content)
        res.total_ms = (time.perf_counter() - t0) * 1000
        return res
