"""Phone-line degradation for evaluation: 8 kHz, μ-law, 300–3400 Hz band-pass, optional noise at an SNR.

Output is always 16 kHz mono 16-bit WAV (what the pipeline sends to Prisma), written once per
(source audio, condition) and cached under cache/eval/channel/.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, resample_poly, sosfiltfilt

from parchi import config

SR = 16000
PHONE_SR = 8000
MU = 255.0
CONDITIONS = ("clean", "phone", "phone+noise@10dB", "phone+noise@5dB")


def mulaw(x: np.ndarray) -> np.ndarray:
    """μ-law encode to 8 bits and decode back (the G.711 quantisation round trip)."""
    x = np.clip(x, -1.0, 1.0)
    enc = np.sign(x) * np.log1p(MU * np.abs(x)) / np.log1p(MU)
    q = np.round((enc + 1) / 2 * 255) / 255 * 2 - 1  # 8-bit code
    return np.sign(q) * (np.power(1 + MU, np.abs(q)) - 1) / MU


def phone(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """16 kHz float → 8 kHz → band-pass 300–3400 Hz → μ-law → back to 16 kHz."""
    y = resample_poly(x, PHONE_SR, sr)
    sos = butter(4, [300, 3400], btype="bandpass", fs=PHONE_SR, output="sos")
    y = mulaw(sosfiltfilt(sos, y))
    return resample_poly(y, sr, PHONE_SR)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + 1e-12))


def add_noise(x: np.ndarray, noise: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """Mix noise (looped / randomly cropped to length) so that RMS(signal)/RMS(noise) = snr_db."""
    if len(noise) < len(x):
        noise = np.tile(noise, int(np.ceil(len(x) / len(noise))) + 1)
    start = int(rng.integers(0, len(noise) - len(x) + 1))
    n = noise[start:start + len(x)]
    n = n * (rms(x) / (rms(n) * 10 ** (snr_db / 20)))
    y = x + n
    peak = np.max(np.abs(y))
    return y / peak * 0.99 if peak > 0.99 else y


def _load(path: Path) -> np.ndarray:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    return resample_poly(x, SR, sr).astype(np.float32) if sr != SR else x


def noise_files() -> list[Path]:
    return sorted((config.DATA_DIR / "noise").glob("*.wav"))


def available_conditions(requested: list[str]) -> tuple[list[str], list[str]]:
    """(runnable, skipped): noise conditions are skipped when data/noise/ has no .wav files."""
    has_noise = bool(noise_files())
    run = [c for c in requested if has_noise or "noise" not in c]
    return run, [c for c in requested if c not in run]


def degrade(clean_wav: Path, condition: str, note_id: str) -> Path:
    """Write (once) and return the 16 kHz WAV for this condition. Noise choice/offset is seeded by note_id."""
    if condition == "clean":
        return clean_wav
    seed = int(hashlib.sha256(f"{note_id}|{condition}".encode()).hexdigest()[:8], 16)
    tag = hashlib.sha256(clean_wav.read_bytes()).hexdigest()[:16]
    out = config.CACHE_DIR / "eval" / "channel" / f"{tag}_{condition.replace('@', '_').replace('+', '_')}.wav"
    if out.exists():
        return out
    y = phone(_load(clean_wav))
    if "noise" in condition:
        rng = np.random.default_rng(seed)
        files = noise_files()
        if not files:
            raise RuntimeError("Noise condition requested but data/noise/ has no .wav files.")
        snr = float(condition.split("@")[1].removesuffix("dB"))
        y = add_noise(y, _load(files[seed % len(files)]), snr, rng)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), np.clip(y, -1, 1).astype(np.float32), SR, subtype="PCM_16")
    return out
