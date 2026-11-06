"""Voice model catalog (all CPU, all via sherpa-onnx; checked Nov 2026).

* Wake word: sherpa-onnx keyword spotter, zipformer GigaSpeech 3.3M (English BPE).
  Open vocabulary: any name works without training (~1.5% of one core).
* VAD: Silero v5 (2 MB).
* STT: NVIDIA Parakeet TDT 0.6B v3, int8 - 25 European languages incl. English + German.
* TTS: **Supertonic 3** int8 (Apr 2026) for English *and* German - replaces Kokoro + Piper:
  sherpa-onnx 2.0 wheels no longer ship the espeak-ng phonemizer, so Piper doesn't run and
  Kokoro silently drops words missing from its lexicon (app names!). Supertonic reads
  characters directly, was 4-5x faster than Kokoro on CPU and had the lowest round-trip WER.
* Voice lock: 3D-Speaker CAM++ speaker embeddings.

Files come from the official k2-fsa/sherpa-onnx GitHub releases; SHA-256 values were
computed from those downloads.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ..core.cancel import CancelToken
from ..llm.download import Progress, download

log = logging.getLogger(__name__)

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class VoiceModel:
    id: str
    title: str
    url: str
    size: int
    sha256: str
    folder: str              # directory inside models/voice after extraction ("" for single files)
    check: str               # a file that must exist when installed (relative to models/voice)

    @property
    def archive(self) -> bool:
        return self.url.endswith(".tar.bz2")


VOICE_MODELS: dict[str, VoiceModel] = {m.id: m for m in (
    VoiceModel("kws", "Wake word (keyword spotter)",
               f"{RELEASES}/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2", 17626723,
               "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a",
               "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01",
               "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/tokens.txt"),
    VoiceModel("vad", "Voice activity detection (Silero v5)", f"{RELEASES}/asr-models/silero_vad_v5.onnx", 2313101,
               "6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396", "", "silero_vad_v5.onnx"),
    VoiceModel("stt", "Speech recognition (Parakeet TDT 0.6B v3)",
               f"{RELEASES}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2", 487170055,
               "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
               "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/tokens.txt"),
    VoiceModel("tts", "Speech output (Supertonic 3, 31 languages)",
               f"{RELEASES}/tts-models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2", 128774318,
               "82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427",
               "sherpa-onnx-supertonic-3-tts-int8-2026-05-11", "sherpa-onnx-supertonic-3-tts-int8-2026-05-11/voice.bin"),
    VoiceModel("speaker", "Voice lock (speaker embeddings, CAM++)",
               f"{RELEASES}/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx", 29596978,
               "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b", "",
               "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"),
)}

#: Required for voice control; "speaker" only when voice lock is enabled.
CORE_VOICE = ("kws", "vad", "stt", "tts")

#: Supertonic 3 preset voices -> speaker id (order measured by pitch: F1-F5 then M1-M5)
TTS_VOICES = {"F1": 0, "F2": 1, "F3": 2, "F4": 3, "F5": 4, "M1": 5, "M2": 6, "M3": 7, "M4": 8, "M5": 9}


def voice_dir(models_dir: Path) -> Path:
    return Path(models_dir) / "voice"


def installed(model_id: str, models_dir: Path) -> bool:
    return (voice_dir(models_dir) / VOICE_MODELS[model_id].check).exists()


def missing(models_dir: Path, ids: tuple[str, ...] = CORE_VOICE) -> list[str]:
    return [m for m in ids if not installed(m, models_dir)]


def ensure(model_id: str, models_dir: Path, cache_dir: Path, *, progress: Progress | None = None,
           cancel: CancelToken | None = None) -> Path:
    """Download (resumable, verified) and install one voice model. Returns its path."""
    m = VOICE_MODELS[model_id]
    root = voice_dir(models_dir)
    target = root / (m.folder or m.check)
    if installed(model_id, models_dir):
        return target
    root.mkdir(parents=True, exist_ok=True)
    if not m.archive:
        return download(m.url, root / m.check, sha256=m.sha256, size=m.size, progress=progress, cancel=cancel)
    arc = download(m.url, Path(cache_dir) / Path(m.url).name, sha256=m.sha256, size=m.size, progress=progress,
                   cancel=cancel)
    tmp = root / (m.folder + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    with tarfile.open(arc) as tf:
        tf.extractall(tmp, filter="data")  # rejects absolute paths, "..", links outside, devices
    inner = tmp / m.folder
    if not inner.is_dir():
        raise RuntimeError(f"{arc.name} did not contain {m.folder}/")
    shutil.rmtree(target, ignore_errors=True)
    inner.replace(target)
    shutil.rmtree(tmp, ignore_errors=True)
    arc.unlink(missing_ok=True)  # keep disk usage low; the checksum lets a re-download resume cleanly
    log.info("installed voice model %s", m.id)
    return target
