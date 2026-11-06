"""Open-vocabulary wake word: the assistant's name, spotted by sherpa-onnx's keyword spotter.

The name is turned into the model's BPE tokens at runtime, so any name works
without training. Measured (TTS voices, 4 vCPU): English pronunciation 12/12
detected, 0 false triggers in 18 tricky negatives, ~1.5% of one core.
German-style pronunciations of English names ("Javis") are harder: the
calibration step (say the name 3x in onboarding) learns how *you* say it and
picks the sensitivity; the STT double-check in the pipeline filters false triggers.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ENCODER = "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
DECODER = "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
JOINER = "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
LABEL = "WAKE"


def ascii_upper(text: str) -> str:
    """'Jürgen' -> 'JUERGEN', 'Zoë' -> 'ZOE' (the English model only knows A-Z and ')."""
    t = text.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    t = t.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip().upper()


def sensitivity_params(sensitivity: float) -> tuple[float, float]:
    """Slider 0..1 -> (keyword threshold, boost). 0.5 = sherpa-onnx's default threshold 0.25, boost 1.5."""
    s = min(1.0, max(0.0, sensitivity))
    threshold = 0.45 - 0.40 * s          # 0.45 (strict) .. 0.05 (very sensitive)
    boost = 0.5 + 2.0 * s                # 0.5 .. 2.5
    return round(threshold, 3), round(boost, 2)


@dataclass
class CalibrationResult:
    sensitivity: float
    variants: list[str]
    detected: int
    total: int
    heard: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.detected >= max(1, self.total - 1)


class WakeWord:
    def __init__(self, model_dir: Path, name: str, *, sensitivity: float = 0.5, variants: list[str] | None = None,
                 keywords_file: Path, threads: int = 1):
        import sentencepiece as spm
        import sherpa_onnx as so

        self.dir = Path(model_dir)
        self.keywords_file = Path(keywords_file)
        self.threads = threads
        self._so = so
        self._sp = spm.SentencePieceProcessor(model_file=str(self.dir / "bpe.model"))
        self._vocab = {line.split()[0] for line in (self.dir / "tokens.txt").read_text(encoding="utf-8").splitlines()
                       if line.strip()}
        self.name = name
        self.configure(name, sensitivity, variants or [])

    # ------------------------------------------------------------------ keywords
    def tokens(self, phrase: str) -> list[str] | None:
        text = ascii_upper(phrase)
        if not text:
            return None
        pieces = self._sp.encode(text, out_type=str)
        return pieces if pieces and all(p in self._vocab for p in pieces) else None

    def keyword_lines(self, phrases: list[str], threshold: float, boost: float) -> list[str]:
        lines, seen = [], set()
        for p in phrases:
            toks = self.tokens(p)
            if toks is None:
                log.debug("wake variant %r has no usable tokens; skipped", p)
                continue
            key = " ".join(toks)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{key} :{boost} #{threshold} @{LABEL}")
        return lines

    def configure(self, name: str, sensitivity: float, variants: list[str]) -> None:
        self.name, self.sensitivity, self.variants = name, sensitivity, list(variants)
        threshold, boost = sensitivity_params(sensitivity)
        lines = self.keyword_lines([name, *variants], threshold, boost)
        if not lines:
            raise ValueError(f"the name {name!r} can't be used as a wake word (no letters A-Z)")
        self.keywords_file.parent.mkdir(parents=True, exist_ok=True)
        self.keywords_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.spotter = self._so.KeywordSpotter(
            tokens=str(self.dir / "tokens.txt"), encoder=str(self.dir / ENCODER), decoder=str(self.dir / DECODER),
            joiner=str(self.dir / JOINER), keywords_file=str(self.keywords_file), num_threads=self.threads,
            keywords_threshold=threshold, keywords_score=boost, max_active_paths=4)
        self.stream = self.spotter.create_stream()
        log.info("wake word: %r (+%d variants) threshold=%s boost=%s", name, len(lines) - 1, threshold, boost)

    # ------------------------------------------------------------------ streaming
    def process(self, frame: np.ndarray, sample_rate: int = 16000) -> bool:
        """Feed audio; True when the wake word was just spoken."""
        self.stream.accept_waveform(sample_rate, frame)
        hit = False
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            if self.spotter.get_result(self.stream):
                self.spotter.reset_stream(self.stream)
                hit = True
        return hit

    def reset(self) -> None:
        self.stream = self.spotter.create_stream()

    def detect_in(self, audio: np.ndarray) -> bool:
        """Offline check of a whole clip (calibration)."""
        self.reset()
        hit = False
        for i in range(0, len(audio), 320):
            hit |= self.process(audio[i:i + 320])
        hit |= self.process(np.zeros(8000, np.float32))
        self.reset()
        return hit

    # ------------------------------------------------------------------ calibration
    def calibrate(self, recordings: list[np.ndarray], heard: list[str]) -> CalibrationResult:
        """Find the lowest sensitivity (fewest false triggers) that detects the user's recordings.

        ``heard`` are STT transcripts of the recordings; words similar to the name become variants
        ("Javis" for a German-sounding "Jarvis")."""
        from rapidfuzz import fuzz

        name_key = ascii_upper(self.name).replace(" ", "")
        extra: list[str] = []
        for text in heard:
            words = re.findall(r"[^\W\d_]+", text)
            cands = words[:3] + [a + b for a, b in zip(words[:3], words[1:4], strict=False)]
            for w in cands:
                k = ascii_upper(w).replace(" ", "")
                if k and k != name_key and fuzz.ratio(k, name_key) >= 70 and w not in extra:
                    extra.append(w)
        best: CalibrationResult | None = None
        for variants in ([], extra[:3]):
            for s in (0.5, 0.6, 0.7, 0.8, 0.9):
                self.configure(self.name, s, variants)
                n = sum(self.detect_in(r) for r in recordings)
                res = CalibrationResult(s, list(variants), n, len(recordings), list(heard))
                if best is None or n > best.detected:
                    best = res
                if n == len(recordings):
                    self.configure(self.name, s, variants)
                    return res
            if not extra:
                break
        assert best is not None
        self.configure(self.name, best.sensitivity, best.variants)
        return best
