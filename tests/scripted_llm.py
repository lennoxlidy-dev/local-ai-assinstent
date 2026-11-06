"""A scripted stand-in for LlmService: returns pre-written ChatResults (tool calls / answers)."""

from __future__ import annotations

import copy
import json

from assistant.llm.client import ChatResult, ToolCall


def call(name, /, **args):
    return ToolCall(f"call_{name}_{len(json.dumps(args))}", name, json.dumps(args))


def tools(*calls, content=""):
    return ChatResult(content=content, tool_calls=list(calls), finish_reason="tool_calls", ttft_ms=5.0)


def answer(text):
    return ChatResult(content=text, finish_reason="stop", ttft_ms=5.0)


class ScriptedLLM:
    ready = True
    spec = None

    def __init__(self, *script):
        self.script = list(script)
        self.seen = []            # messages of every call
        self.warmup_messages = None

    def chat(self, messages, *, tools=None, thinking=False, max_tokens=1024, on_content=None, cancel=None):
        self.seen.append(copy.deepcopy(messages))
        if cancel is not None:
            cancel.check()
        item = self.script.pop(0) if self.script else answer("(script exhausted)")
        res = item(messages) if callable(item) else item
        if on_content and res.content:
            on_content(res.content)
        return res

    def describe_image(self, png, question, cancel=None):
        return "x=50% y=25%"
