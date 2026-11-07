"""Model catalog: which GGUF files to download, how to run them, how to talk to them.

Checked in Nov 2026 against Hugging Face (sizes and SHA-256 from the repos'
LFS metadata; all Apache-2.0, ungated):

* Qwen3.5 4B / 9B (Feb 2026): natively multimodal (separate mmproj file),
  hybrid thinking - *off by default* for the small sizes, enabled per request
  with ``chat_template_kwargs.enable_thinking``. Strong native tool calling.
* Gemma 4 E4B (Mar 2026): vision, configurable thinking, native function calling.
* Granite 4.2 8B (Aug 2026): tool-calling focused, 12 languages (incl. German), text only.
* Ministral 3 8B (Dec 2025): vision + function calling.

The benchmark (``bench/llm_bench.py``) measures latency on the user's GPU and
tool-calling accuracy on *our* tool schemas; ``profile = "auto"`` uses its result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


#: The Hugging Face commit each repository is downloaded from. A commit URL never changes, so a
#: re-upload on the repository's main branch (common for GGUF repos) can't break the pinned
#: size/SHA-256 below. Checked Nov 2026.
REVISIONS = {
    "unsloth/Qwen3.5-9B-GGUF": "3885219b6810b007914f3a7950a8d1b469d598a5",
    "unsloth/Qwen3.5-4B-GGUF": "e87f176479d0855a907a41277aca2f8ee7a09523",
    "unsloth/Qwen3.5-9B-MTP-GGUF": "9716a636ee4bddc3fed678220b7a33dd2a4160ae",
    "unsloth/gemma-4-E4B-it-GGUF": "bfc15c382204943c3a8fff0c750b94ae2364d7a3",
    "ibm-granite/granite-4.2-8b-GGUF": "93f3f6a8938ee922b784cf4e5b4203cd3428df8f",
    "mistralai/Ministral-3-8B-Instruct-2512-GGUF": "0102285ad796bd99af90f58de616092e5630e970",
    "ggml-org/Qwen3.5-0.8B-GGUF": "807dfd9a6fadbf703207aac3bfb0b2b3040d077c",
}


@dataclass(frozen=True)
class ModelFile:
    repo: str
    file: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{REVISIONS.get(self.repo, 'main')}/{self.file}"

    def local_name(self, model_id: str) -> str:
        # mmproj files share generic names across repos -> prefix with the model id
        return self.file if not self.file.lower().startswith("mmproj") else f"{model_id}-{self.file}"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    title: str
    family: str                     # qwen3.5 | gemma4 | granite4 | ministral3 | test
    params_b: float
    weights: ModelFile
    mmproj: ModelFile | None = None
    #: how to switch thinking per request: "template" (chat_template_kwargs.enable_thinking)
    #: | "effort" (reasoning_effort) | "none" (model has no thinking mode)
    thinking: str = "template"
    thinking_default: bool = False   # what the template does when we don't say anything
    sampling: dict[str, Any] = field(default_factory=dict)
    sampling_thinking: dict[str, Any] = field(default_factory=dict)
    #: extra llama-server arguments (e.g. speculative decoding)
    server_args: tuple[str, ...] = ()
    notes: str = ""
    hidden: bool = False             # not offered in the UI (tests)

    @property
    def vision(self) -> bool:
        return self.mmproj is not None

    @property
    def download_bytes(self) -> int:
        return self.weights.size + (self.mmproj.size if self.mmproj else 0)

    def vram_estimate_mb(self, ctx: int, with_vision: bool = True) -> int:
        """Rough VRAM need: weights + projector + KV cache/compute buffers (measured on similar models)."""
        mb = self.weights.size / 2**20
        if with_vision and self.mmproj:
            mb += self.mmproj.size / 2**20
        per_1k_ctx = {"qwen3.5": 18, "gemma4": 40, "granite4": 20, "ministral3": 70}.get(self.family, 60)
        return int(mb + 450 + per_1k_ctx * ctx / 1024)


# No presence penalty: it penalizes the quotes/braces that tool-call JSON must repeat.
QWEN_SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0}
QWEN_SAMPLING_THINK = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0}

CATALOG: dict[str, ModelSpec] = {m.id: m for m in (
    ModelSpec(
        "qwen3.5-9b", "Qwen3.5 9B (Q4_K_M)", "qwen3.5", 9.0,
        ModelFile("unsloth/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf", 5680522464,
                  "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"),
        ModelFile("unsloth/Qwen3.5-9B-GGUF", "mmproj-F16.gguf", 918166080,
                  "f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f"),
        sampling=QWEN_SAMPLING, sampling_thinking=QWEN_SAMPLING_THINK,
        notes="Smart default for 8 GB GPUs."),
    ModelSpec(
        "qwen3.5-4b", "Qwen3.5 4B (Q4_K_M)", "qwen3.5", 4.0,
        ModelFile("unsloth/Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf", 2740937888,
                  "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"),
        ModelFile("unsloth/Qwen3.5-4B-GGUF", "mmproj-F16.gguf", 672423616,
                  "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864"),
        sampling=QWEN_SAMPLING, sampling_thinking=QWEN_SAMPLING_THINK,
        notes="Fast default; leaves VRAM for other apps."),
    ModelSpec(
        "qwen3.5-9b-mtp", "Qwen3.5 9B MTP (faster decoding, experimental)", "qwen3.5", 9.0,
        ModelFile("unsloth/Qwen3.5-9B-MTP-GGUF", "Qwen3.5-9B-Q4_K_M.gguf", 5868826976,
                  "e8dd94817e95d6c0939102049d068418269978377b13616c4726235e232841fe"),
        ModelFile("unsloth/Qwen3.5-9B-GGUF", "mmproj-F16.gguf", 918166080,
                  "f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f"),
        sampling=QWEN_SAMPLING, sampling_thinking=QWEN_SAMPLING_THINK,
        server_args=("--spec-type", "draft-mtp"),
        notes="Same model with multi-token-prediction heads: speculative decoding without a draft model."),
    ModelSpec(
        "gemma-4-e4b", "Gemma 4 E4B (Q4_K_M)", "gemma4", 8.0,
        ModelFile("unsloth/gemma-4-E4B-it-GGUF", "gemma-4-E4B-it-Q4_K_M.gguf", 4977171584,
                  "85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87"),
        ModelFile("unsloth/gemma-4-E4B-it-GGUF", "mmproj-F16.gguf", 990372672,
                  "ddf46c21d7078e95338cfc22306b19b276a29a5ad089023449dd54d4b6170a51"),
        thinking="template",
        sampling={"temperature": 0.7, "top_p": 0.95, "top_k": 64, "min_p": 0.0},
        sampling_thinking={"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0},
        notes="Google's edge model (4B effective / 8B with per-layer embeddings)."),
    ModelSpec(
        "granite-4.2-8b", "Granite 4.2 8B (Q4_K_M, text only)", "granite4", 8.0,
        ModelFile("ibm-granite/granite-4.2-8b-GGUF", "granite-4.2-8b-Q4_K_M.gguf", 5347917952,
                  "16a9369d0805f80b7377d25d87f937a90c05dc04ad79173a52001e42c9aab311"),
        thinking="none", sampling={"temperature": 0.3, "top_p": 0.9, "top_k": 40},
        notes="IBM, tuned for tool calling; no vision."),
    ModelSpec(
        "ministral-3-8b", "Ministral 3 8B (Q4_K_M)", "ministral3", 8.0,
        ModelFile("mistralai/Ministral-3-8B-Instruct-2512-GGUF", "Ministral-3-8B-Instruct-2512-Q4_K_M.gguf",
                  5198911904, "33e7a72cf5e6e2cfc2f2847075acc013d68bba023e35310cef86b5cf8fdca761"),
        ModelFile("mistralai/Ministral-3-8B-Instruct-2512-GGUF", "Ministral-3-8B-Instruct-2512-BF16-mmproj.gguf",
                  858283168, "e799380f596d152ff4026a72d409ecc89c96cd437676804bc3f2ab3bd6b486ec"),
        thinking="none", sampling={"temperature": 0.15, "top_p": 0.95},
        notes="Mistral, vision + function calling."),
    ModelSpec(
        "qwen3.5-0.8b", "Qwen3.5 0.8B (tests only)", "qwen3.5", 0.8,
        ModelFile("ggml-org/Qwen3.5-0.8B-GGUF", "Qwen3.5-0.8B-Q4_0.gguf", 563036064,
                  "57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf"),
        sampling=QWEN_SAMPLING, sampling_thinking=QWEN_SAMPLING_THINK, hidden=True),
)}


def get_model(model_id: str) -> ModelSpec:
    try:
        return CATALOG[model_id]
    except KeyError:
        raise KeyError(f"unknown model '{model_id}'. Known: {', '.join(sorted(CATALOG))}") from None


def model_files(spec: ModelSpec, models_dir: Path, *, vision: bool = True) -> tuple[Path, Path | None]:
    w = Path(models_dir) / spec.weights.local_name(spec.id)
    mm = Path(models_dir) / spec.mmproj.local_name(spec.id) if (vision and spec.mmproj) else None
    return w, mm


def is_installed(spec: ModelSpec, models_dir: Path, *, vision: bool = True) -> bool:
    w, mm = model_files(spec, models_dir, vision=vision)
    ok = w.exists() and w.stat().st_size == spec.weights.size
    if mm is not None and spec.mmproj is not None:
        ok = ok and mm.exists() and mm.stat().st_size == spec.mmproj.size
    return ok


def choose_model(profile: str, smart: str, fast: str, *, vram_free_mb: int | None, bench: dict | None,
                 ctx: int) -> tuple[str, str]:
    """Pick the model for ``profile`` (auto|smart|fast). Returns (model_id, reason)."""
    if profile == "smart":
        return smart, "profile=smart"
    if profile == "fast":
        return fast, "profile=fast"
    if bench and bench.get("recommended") in (smart, fast):
        return bench["recommended"], f"benchmark ({bench.get('date', '?')}): {bench.get('reason', '')}"
    need = CATALOG[smart].vram_estimate_mb(ctx) if smart in CATALOG else 0
    if vram_free_mb is None:
        return fast, "no NVIDIA GPU detected"
    if vram_free_mb >= need:
        return smart, f"{vram_free_mb} MB VRAM free >= ~{need} MB needed (run the benchmark to confirm latency)"
    return fast, f"only {vram_free_mb} MB VRAM free (< ~{need} MB for {smart})"
