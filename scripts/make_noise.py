"""Synthetic background noise for the eval's noise conditions (no API calls, no real recordings).

Writes data/noise/synthetic_{fan,traffic,crowd}.wav (16 kHz mono, 30 s):
  fan     – pink noise + 50 Hz mains hum harmonics
  traffic – brown-noise rumble with slow swells and a few horn beeps
  crowd   – babble: several reversed, time-shifted synthetic test notes overlapped (unintelligible speech)

Usage: python scripts/make_noise.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parchi import audio, config  # noqa: E402
from parchi.eval.channel import _load  # noqa: E402

SR, SECONDS = 16000, 30
N = SR * SECONDS
rng = np.random.default_rng(2026)


def norm(x: np.ndarray, peak: float = 0.5) -> np.ndarray:
    return (x / (np.max(np.abs(x)) + 1e-9) * peak).astype(np.float32)


def pink(n: int) -> np.ndarray:
    spec = np.fft.rfft(rng.normal(size=n))
    f = np.arange(len(spec))
    spec[1:] /= np.sqrt(f[1:])
    return np.fft.irfft(spec, n)


def fan() -> np.ndarray:
    t = np.arange(N) / SR
    hum = sum(0.3 / k * np.sin(2 * np.pi * 50 * k * t) for k in (1, 2, 3, 4))
    return norm(norm(pink(N)) + 0.4 * hum)


def traffic() -> np.ndarray:
    t = np.arange(N) / SR
    brown = np.cumsum(rng.normal(size=N))
    brown -= np.convolve(brown, np.ones(SR) / SR, mode="same")  # remove drift
    x = norm(brown) * (0.6 + 0.4 * np.sin(2 * np.pi * t / 7.0))  # cars passing
    for start in rng.uniform(1, SECONDS - 2, size=4):  # horns
        i = slice(int(start * SR), int((start + 0.4) * SR))
        x[i] += 0.25 * np.sign(np.sin(2 * np.pi * 420 * t[i]))
    return norm(x)


def crowd() -> np.ndarray:
    notes = sorted((config.DATA_DIR / "testset" / "audio").glob("syn_*.ogg"))[:8]
    if not notes:
        raise SystemExit("No synthetic test notes to build babble from: run `python -m parchi.eval synth` first.")
    x = np.zeros(N)
    for p in notes:
        v = _load(audio.normalise(p))[::-1]  # reversed speech: speech-like spectrum, no words
        v = np.tile(v, N // len(v) + 2)
        off = int(rng.integers(0, len(v) - N))
        x += v[off:off + N] * rng.uniform(0.5, 1.0)
    return norm(x)


if __name__ == "__main__":
    out = config.DATA_DIR / "noise"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("fan", fan), ("traffic", traffic), ("crowd", crowd)):
        sf.write(str(out / f"synthetic_{name}.wav"), fn(), SR, subtype="PCM_16")
        print("wrote", out / f"synthetic_{name}.wav")
