"""Routine sounds played by the assistant: real MP3/WAV/FLAC/OGG files are decoded; the output device is faked.

The MP3s in tests/data were encoded with LAME (ffmpeg), like real-world MP3s; tone_vbr_albumart_2s.mp3 is a VBR
file that libsndfile garbles when it is read in pieces (silence from 1 s on) - the engine reads every file in one
call, so it must play completely. (MP3s written by libsndfile itself can't serve as test files: it reads them back
wrongly.)
"""

import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from assistant.audio.sound import BLOCK, SoundEngine, play_track
from assistant.tools.base import ToolError

RATE = 44100
DATA = Path(__file__).parent / "data"


class FakeStream:
    """Stands in for sounddevice.OutputStream and keeps everything that would reach the speakers."""

    instances: list = []
    delay = 0.0

    def __init__(self, samplerate, channels, dtype, device=None, blocksize=0, **kw):
        self.samplerate, self.channels, self.dtype = samplerate, channels, dtype
        self.blocks, self.stopped, self.aborted, self.closed = [], False, False, False
        FakeStream.instances.append(self)

    def start(self):
        pass

    def write(self, block):
        assert block.dtype == np.float32 and block.ndim == 2 and block.shape[1] == self.channels
        self.blocks.append(block.copy())
        if FakeStream.delay:
            time.sleep(FakeStream.delay)

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    @property
    def audio(self):
        return np.concatenate(self.blocks) if self.blocks else np.zeros((0, self.channels), np.float32)


@pytest.fixture
def out(monkeypatch):
    """The output device. (A stand-in sounddevice module: this machine may not even have PortAudio.)"""
    import sys
    import types

    fake = types.SimpleNamespace(OutputStream=FakeStream, query_devices=lambda *a, **k: [],
                                 default=types.SimpleNamespace(device=(None, None)))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    FakeStream.instances, FakeStream.delay = [], 0.0
    return FakeStream


def tone(path, seconds, rate=RATE, channels=2, fmt=None):
    t = np.arange(int(rate * seconds)) / rate
    x = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(path), np.stack([x] * channels, axis=1) if channels > 1 else x, rate, format=fmt)
    return str(path)


def secs(stream):
    return len(stream.audio) / stream.samplerate


def mp3(tmp_path, name):
    """A copy of a test MP3 (under a path with a space and an umlaut, like real music folders)."""
    folder = tmp_path / "Meine Musik"
    folder.mkdir(exist_ok=True)
    return str(shutil.copy(DATA / name, folder / name.replace("tone", "Intro-Größe")))


def loud_everywhere(audio, rate=RATE, window=0.25):
    """No silent stretch anywhere (a broken decoder goes quiet part-way)."""
    w = int(rate * window)
    return all(float(np.sqrt(np.mean(audio[i:i + w] ** 2))) > 0.01 for i in range(0, len(audio) - w, w))


# ---------------------------------------------------------------------- real files through the engine
def _pitch(audio, start=RATE // 2, n=8192):
    spectrum = np.abs(np.fft.rfft(audio[start:start + n, 0]))
    return np.argmax(spectrum) * RATE / n


def test_an_mp3_plays(out, tmp_path):
    SoundEngine().play(mp3(tmp_path, "tone_44k_stereo_2s.mp3"), wait=True)
    s = out.instances[0]
    assert (s.samplerate, s.channels) == (RATE, 2) and s.stopped and s.closed and not s.aborted
    assert 1.95 <= secs(s) <= 2.2                       # (MP3 adds a few ms of encoder padding)
    assert loud_everywhere(s.audio) and abs(_pitch(s.audio) - 440) < 10


@pytest.mark.parametrize("name,channels", [("tone_48k_mono_2s.mp3", 1), ("tone_vbr_albumart_2s.mp3", 1)])
def test_48khz_vbr_and_album_art_mp3s(out, tmp_path, name, channels):
    SoundEngine().play(mp3(tmp_path, name), wait=True)
    s = out.instances[0]
    assert (s.samplerate, s.channels) == (RATE, channels) and 1.9 <= secs(s) <= 2.2   # resampled to 44.1 kHz
    assert loud_everywhere(s.audio) and abs(_pitch(s.audio) - 440) < 10              # still the 440 Hz tone


@pytest.mark.parametrize("ext", ["wav", "flac", "ogg"])
def test_other_formats_play(out, tmp_path, ext):
    SoundEngine().play(tone(tmp_path / f"s.{ext}", 1.0), wait=True)
    assert 0.95 <= secs(out.instances[0]) <= 1.1


def test_an_mp3_repeats_for_the_minimum_time(out, tmp_path):
    SoundEngine().play(mp3(tmp_path, "tone_44k_stereo_1s.mp3"), wait=True, min_s=3.5)
    s = out.instances[0]
    assert 3.9 <= secs(s) <= 4.3 and loud_everywhere(s.audio)   # 4 rounds, the last one to its end


def test_until_the_routine_is_done_with_an_mp3(out, tmp_path):
    finished = threading.Event()
    eng = SoundEngine()
    FakeStream.delay = 0.002
    eng.play(mp3(tmp_path, "tone_44k_stereo_1s.mp3"), wait=False, until=finished.is_set)
    time.sleep(0.3)
    finished.set()                                       # the routine's last step is done
    deadline = time.time() + 5
    while time.time() < deadline and not (out.instances and out.instances[0].closed):
        time.sleep(0.01)
    s = out.instances[0]
    assert s.closed and s.stopped and not s.aborted
    peak = float(np.abs(s.audio[:RATE // 2]).max())
    tail = float(np.abs(s.audio[-RATE // 20:]).max())   # the last 50 ms: faded to (almost) nothing
    assert tail < peak * 0.1 and secs(s) >= 1.0


def test_stop_cuts_a_sound_at_once(out, tmp_path):
    eng = SoundEngine()
    FakeStream.delay = 0.002
    FakeStream.delay = 0.03                              # close to real time (one block = 46 ms)
    eng.play(tone(tmp_path / "long.wav", 10.0), wait=False)
    time.sleep(0.2)
    eng.stop_all()
    deadline = time.time() + 5
    while time.time() < deadline and not out.instances[0].closed:
        time.sleep(0.01)
    s = out.instances[0]
    assert s.aborted and secs(s) < 5


def test_other_formats_and_broken_files_go_to_the_fallback(out, tmp_path):
    class Mci:
        def __init__(self):
            self.calls, self.stopped = [], 0

        def play(self, path, wait, cancelled=None, *, until=None, min_s=0.0, volume=1.0, speaking=None,
                 duck_level=None):
            assert callable(speaking) and callable(duck_level)          # ducking reaches the MCI player too
            self.calls.append((path, wait, min_s))

        def stop_all(self):
            self.stopped += 1

    wma = tmp_path / "jingle.wma"
    wma.write_bytes(b"ASF fake")
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"this is not audio" * 100)
    with pytest.raises(ToolError, match="MP3, WAV, FLAC or OGG"):
        SoundEngine().play(str(wma), wait=False)
    with pytest.raises(ToolError, match="broken.mp3"):
        SoundEngine().play(str(broken), wait=False)
    mci = Mci()
    eng = SoundEngine(fallback=mci)
    eng.play(str(wma), wait=True, min_s=5)
    eng.play(str(broken), wait=False)
    eng.stop_all()
    assert mci.calls == [(str(wma), True, 5), (str(broken), False, 0.0)] and mci.stopped == 1
    assert out.instances == []                           # nothing played by us


def test_sounds_work_on_every_os():
    from assistant.tools.platform import create_platform

    assert isinstance(create_platform().sound, SoundEngine)


# ---------------------------------------------------------------------- the playing logic (exact)
def _source(seconds, rate=RATE):
    data = np.ones((int(seconds * rate), 1), np.float32)
    pos = [0]

    def read(n):
        block = data[pos[0]:pos[0] + n]
        pos[0] += len(block)
        return block

    def rewind():
        pos[0] = 0

    return read, rewind


def _run(seconds, *, until_after=None, min_s=0.0, stop_after=None):
    read, rewind = _source(seconds)
    written, stop = [], threading.Event()

    def write(block):
        written.append(block)
        t = sum(len(b) for b in written) / RATE
        if stop_after is not None and t >= stop_after:
            stop.set()

    def until():
        return sum(len(b) for b in written) / RATE >= until_after

    played = play_track(read, rewind, write, RATE, stop=stop, min_s=min_s,
                        until=until if until_after is not None else None)
    return played, np.concatenate(written)[:, 0] if written else np.zeros(0)


def test_play_track_once_and_minimum():
    assert _run(1.0)[0] == pytest.approx(1.0)
    assert _run(1.0, min_s=2.5)[0] == pytest.approx(3.0)     # whole rounds


def test_play_track_until_done_fades_out():
    played, audio = _run(1.0, until_after=2.5)
    assert 3.4 <= played <= 3.6                             # repeat until 2.5 s, then a 1 s fade
    fade = audio[-RATE:]
    assert fade[0] > 0.9 and fade[-1] < 0.01 and np.all(np.diff(fade) <= 1e-6)
    played, _ = _run(10.0, until_after=0.0, min_s=1.5)       # done at once, but at least 1.5 s
    assert 2.4 <= played <= 2.6


def test_play_track_stop_and_empty_files():
    assert _run(10.0, stop_after=0.5)[0] < 0.6
    assert _run(0.0, min_s=5.0)[0] == 0.0                   # an empty file isn't repeated forever
    assert BLOCK == 2048


# ---------------------------------------------------------------------- the voice stays audible: ducking + volume
def test_ducker_lowers_smoothly_while_talking_and_comes_back():
    from assistant.audio.sound import Ducker

    talking = [False]
    d = Ducker(lambda: talking[0], lambda: 0.25, RATE)
    one = np.ones((BLOCK, 1), np.float32)
    assert np.array_equal(d(one), one)                           # nobody talks: untouched
    talking[0] = True
    gains = np.concatenate([d(one)[:, 0] for _ in range(10)])    # 10 blocks = 0.46 s
    assert gains[0] == pytest.approx(1.0) and gains[-1] == pytest.approx(0.25)
    assert np.flatnonzero(gains <= 0.2501)[0] / RATE < 0.16      # down within 0.15 s
    assert np.abs(np.diff(gains)).max() < 0.001                  # a ramp, no click
    talking[0] = False
    up = np.concatenate([d(one)[:, 0] for _ in range(20)])
    assert up[-1] == pytest.approx(1.0) and np.flatnonzero(up >= 0.9999)[0] / RATE < 0.62
    assert np.abs(np.diff(up)).max() < 0.001
    half = Ducker(lambda: False, lambda: 0.25, RATE, volume=0.5)
    assert np.allclose(half(one), 0.5)                           # a quieter sound (the step's volume)


def test_a_sound_ducks_under_the_voice(out, tmp_path):
    eng = SoundEngine()
    talking = [False]
    eng.speaking = lambda: talking[0]
    eng.duck_level = lambda: 0.25
    orig = FakeStream.write

    def write(self, block):                                     # the voice talks from 1.0 s to 2.0 s of the sound
        t = sum(len(b) for b in self.blocks) / RATE
        talking[0] = 1.0 <= t < 2.0
        orig(self, block)

    FakeStream.write = write
    try:
        eng.play(mp3(tmp_path, "tone_44k_stereo_1s.mp3"), wait=True, min_s=3.0, volume=0.8)
    finally:
        FakeStream.write = orig
    a = out.instances[0].audio[:, 0]

    def rms(t0, t1):
        return float(np.sqrt(np.mean(a[int(t0 * RATE):int(t1 * RATE)] ** 2)))

    before, during, after = rms(0.3, 0.9), rms(1.4, 1.9), rms(2.9, 3.0)
    assert during == pytest.approx(before * 0.25, rel=0.15)      # ducked to 25 % while talking
    assert after == pytest.approx(before, rel=0.1)               # and back up afterwards
    ref = SoundEngine()
    ref.play(mp3(tmp_path, "tone_44k_stereo_1s.mp3"), wait=True)
    full = out.instances[-1].audio[:, 0]
    assert before == pytest.approx(float(np.sqrt(np.mean(full[int(0.3 * RATE):int(0.9 * RATE)] ** 2))) * 0.8, rel=0.05)


def test_the_mci_player_ducks_too():
    from assistant.audio.mci import play_session

    class Clock:
        t = 0.0

        def __call__(self):
            return self.t

    clock = Clock()

    class Ticks:
        def wait(self, dt):
            clock.t = round(clock.t + dt, 3)
            return False

    volumes, started = [], [0.0]

    def mci(cmd):
        if cmd.startswith("play "):
            started[0] = clock.t
        elif cmd.startswith("status "):
            return "playing" if clock.t - started[0] < 4.0 else "stopped"
        elif cmd.startswith("setaudio "):
            volumes.append((round(clock.t, 1), int(cmd.rsplit(" ", 1)[1])))
        return ""

    play_session(mci, "a", "x.wma", stop=Ticks(), clock=clock, volume=0.8,
                 speaking=lambda: 1.0 <= clock.t < 2.0, duck_level=lambda: 0.25)
    assert volumes[0] == (0.0, 800)                              # the step's volume from the start
    assert (1.0, 200) in volumes and (2.0, 800) in volumes       # ducked while talking, back afterwards


# ---------------------------------------------------------------------- the voice itself: evened out, louder
def test_voice_sentences_are_evened_out_to_near_full_level():
    from assistant.voice.tts import loudness

    t = np.arange(RATE) / RATE
    quiet = (0.34 * np.sin(2 * np.pi * 180 * t) * np.sin(2 * np.pi * 3 * t) ** 2).astype(np.float32)  # like raw TTS
    y = loudness(quiet)
    assert float(np.abs(y).max()) == pytest.approx(0.89, abs=0.01)
    gain_db = 20 * np.log10(np.sqrt(np.mean(y ** 2)) / np.sqrt(np.mean(quiet ** 2)))
    assert 8 < gain_db < 9                                       # +8.4 dB for this one
    louder = loudness(quiet, 2.0)
    assert float(np.abs(louder).max()) < 0.98 and np.sqrt(np.mean(louder ** 2)) > np.sqrt(np.mean(y ** 2)) * 1.3
    assert np.all(np.isfinite(louder))
    assert np.array_equal(loudness(np.zeros(100, np.float32)), np.zeros(100, np.float32))   # silence stays silence
    hiss = (np.random.default_rng(0).standard_normal(1000) * 0.001).astype(np.float32)
    assert float(np.abs(loudness(hiss)).max()) <= float(np.abs(hiss).max()) * 6.01          # gain capped
