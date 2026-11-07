"""LlmService: picks the model, owns the llama-server process, exposes chat() + vision.

Lifecycle: started in the background after app start (or lazily on the first
LLM request); warmed up with the system prompt + tool schemas so the first
real request hits llama.cpp's prompt cache; optionally sleeps after idle time
(llama-server frees the model and reloads it on the next request); ``stop()``
frees VRAM immediately (game mode).
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.cancel import CancelToken
from ..core.context import AppContext
from ..core.events import Topic
from .client import ChatResult, LlamaClient, LLMError
from .models import CATALOG, ModelSpec, choose_model, get_model, is_installed, model_files
from .server import LlamaServer, ServerError, ServerOptions, ServerState
from .setup import current_server

log = logging.getLogger(__name__)


def setup_hint() -> str:
    """Where to get missing models, named the way this copy was installed."""
    cli = "AssistantCLI.exe --setup" if getattr(sys, "frozen", False) else "assistant-cli --setup"
    return f"Settings > Model > Download missing models, or run: {cli}"


class LlmUnavailable(LLMError):
    """No model/server installed, or the server failed to start."""


def bench_result(ctx: AppContext) -> dict[str, Any] | None:
    f = ctx.paths.local_dir / "bench" / "latest.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class LlmService:
    def __init__(self, ctx: AppContext, *, server_exe: Path | None = None, client: LlamaClient | None = None):
        self.ctx = ctx
        self.server: LlamaServer | None = None
        self.spec: ModelSpec | None = None
        self.reason = ""
        self._client = client          # injected (tests / external server)
        self._exe_override = server_exe
        self._lock = threading.RLock()
        self._warm = False
        self.warmup_messages: Callable[[], tuple[list[dict[str, Any]], list[dict[str, Any]]]] | None = None

    # ------------------------------------------------------------------ status
    @property
    def ready(self) -> bool:
        if self._client is not None:
            return True
        return self.server is not None and self.server.state is ServerState.READY and self.server.running()

    def status(self) -> str:
        if self._client is not None:
            return "external server"
        if self.server is None:
            return "not started" + (f" ({self.reason})" if self.reason else "")
        return f"{self.server.state}: {self.server.detail}"

    def _on_state(self, state: ServerState, detail: str) -> None:
        self.ctx.state.update(llm_ready=state is ServerState.READY)
        self.ctx.bus.publish("llm.state", state=str(state), detail=detail,
                             model=self.spec.id if self.spec else "")
        if state is ServerState.FAILED:
            self.ctx.bus.publish(Topic.NOTIFY, title="Language model problem", message=detail[:240],
                                 level="warning")

    # ------------------------------------------------------------------ model selection
    def select(self) -> ModelSpec:
        c = self.ctx.config.data.llm
        from ..core import hardware

        free = hardware.vram_free_mb()
        model_id, reason = choose_model(c.profile, c.model_smart, c.model_fast, vram_free_mb=free,
                                        bench=bench_result(self.ctx), ctx=c.context_tokens)
        spec = get_model(model_id)
        models_dir = self.ctx.paths.models_dir
        if not is_installed(spec, models_dir, vision=False):
            for alt in (c.model_smart, c.model_fast, *CATALOG):
                s = CATALOG.get(alt)
                if s is not None and not s.hidden and is_installed(s, models_dir, vision=False):
                    reason = f"{model_id} is not downloaded; using {s.id}"
                    spec = s
                    break
        self.spec, self.reason = spec, reason
        log.info("LLM model: %s (%s)", spec.id, reason)
        return spec

    def _options(self, spec: ModelSpec) -> ServerOptions:
        c = self.ctx.config.data.llm
        exe = self._exe_override or (Path(c.server_path) if c.server_path else current_server(self.ctx.paths.bin_dir))
        if exe is None:
            raise LlmUnavailable(f"llama.cpp is not installed yet ({setup_hint()})")
        weights, mmproj = model_files(spec, self.ctx.paths.models_dir, vision=c.vision)
        if mmproj is not None and not mmproj.exists():
            mmproj = None
        from ..core import hardware

        free = hardware.vram_free_mb()
        need = spec.vram_estimate_mb(c.context_tokens, with_vision=mmproj is not None)
        gpu_layers, offload_vision = "all", True
        if free is None:
            gpu_layers = "0"
        elif need > free - 256:
            # projector on the CPU first (vision gets slower, chat stays fast); then let llama.cpp fit layers
            offload_vision = False
            if spec.vram_estimate_mb(c.context_tokens, with_vision=False) > free - 256:
                gpu_layers = "auto"
        extra = list(spec.server_args) + list(c.extra_args)
        if spec.vision and mmproj is not None:
            extra += ["--image-max-tokens", "1024"]  # screenshots: enough detail, bounded latency
        return ServerOptions(exe=Path(exe), model=weights, mmproj=mmproj, mmproj_offload=offload_vision,
                             port=c.server_port, ctx=c.context_tokens, gpu_layers=gpu_layers,
                             sleep_idle_s=c.idle_unload_min * 60, extra_args=extra)

    # ------------------------------------------------------------------ lifecycle
    def start(self, wait: bool = True) -> None:
        if self._client is not None:
            return
        with self._lock:
            spec = self.spec or self.select()
            if not is_installed(spec, self.ctx.paths.models_dir, vision=False):
                raise LlmUnavailable(f"model {spec.id} is not downloaded yet ({setup_hint()})")
            if self.server is None:
                self.server = LlamaServer(self._options(spec), self.ctx.paths.logs_dir, self._on_state)
            try:
                self.server.start(wait=wait)
            except ServerError as e:
                raise LlmUnavailable(str(e)) from None
        if wait:
            self.warmup()

    def start_background(self) -> None:
        def go() -> None:
            try:
                self.start(wait=True)
            except LLMError as e:
                log.warning("LLM not started: %s", e)
                self.reason = str(e)
                msg = str(e)
                if isinstance(e, LlmUnavailable) and ("not installed" in msg or "not downloaded" in msg):
                    self.ctx.bus.publish(Topic.NOTIFY, title="Language model not installed",
                                         message="Simple commands work. For everything else run "
                                                 "scripts\\setup-models.bat once (downloads ~9 GB).",
                                         level="warning")

        threading.Thread(target=go, name="llm-start", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            if self.server is not None:
                self.server.stop()
            self._warm = False

    def switch_model(self, model_id: str) -> None:
        spec = get_model(model_id)
        self.stop()
        with self._lock:
            self.spec, self.reason, self.server = spec, "chosen manually", None
        self.start(wait=True)

    def client(self) -> LlamaClient:
        if self._client is not None:
            return self._client
        if not self.ready:
            self.start(wait=True)
        assert self.server is not None
        return self.server.client()

    def warmup(self) -> None:
        """Process system prompt + tools once so the first real request is fast."""
        if self._warm or self.warmup_messages is None:
            return
        try:
            msgs, tools = self.warmup_messages()
            self.chat(msgs, tools=tools, max_tokens=1)
            self._warm = True
            log.info("LLM warmed up (prompt cache primed)")
        except LLMError as e:
            log.warning("warmup failed: %s", e)

    # ------------------------------------------------------------------ chat
    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
             thinking: bool = False, max_tokens: int = 1024, on_content: Callable[[str], None] | None = None,
             cancel: CancelToken | None = None) -> ChatResult:
        spec = self.spec or (self.select() if self._client is None else None)
        sampling: dict[str, Any] = {}
        style = "template"
        if spec is not None:
            sampling = dict(spec.sampling_thinking if thinking else spec.sampling)
            style = spec.thinking
        temp = self.ctx.config.data.llm.temperature
        if temp >= 0:
            sampling["temperature"] = temp
        result = self.client().chat(messages, tools=tools, thinking=thinking if style != "none" else None,
                                    thinking_style=style, sampling=sampling,
                                    max_tokens=max_tokens if not thinking else max(max_tokens, 4096),
                                    on_content=on_content, cancel=cancel)
        if result.timings:
            t = result.timings
            log.debug("TIMING stage=llm_call prompt_n=%s cache_n=%s prompt_ms=%.1f predicted_n=%s gen_tps=%.1f "
                      "ttft_ms=%.1f total_ms=%.1f", t.get("prompt_n"), t.get("cache_n"), t.get("prompt_ms", 0),
                      t.get("predicted_n"), t.get("predicted_per_second", 0), result.ttft_ms or -1,
                      result.total_ms)
        return result

    # ------------------------------------------------------------------ vision
    def describe_image(self, png: bytes, question: str, *, cancel: CancelToken | None = None) -> str:
        if self.spec is not None and not self.spec.vision:
            raise LLMError(f"{self.spec.title} can't see images")
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                             {"type": "text", "text": question}]}]
        res = self.chat(msgs, max_tokens=400, cancel=cancel)
        return res.content.strip()
