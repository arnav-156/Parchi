"""ffmpeg normalisation to 16 kHz mono s16 WAV, and silence-based chunking to ≤28 s."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from parchi import config

SAMPLE_RATE = 16000


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH. Install it (e.g. `winget install Gyan.FFmpeg`).")
    return exe


def normalise(src: Path, out_dir: Path | None = None) -> Path:
    """Convert any audio file to 16 kHz mono 16-bit WAV (cached by source hash)."""
    src = Path(src)
    out_dir = out_dir or config.CACHE_DIR / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{hashlib.sha256(src.read_bytes()).hexdigest()[:32]}.wav"
    if not out.exists():
        cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE),
               "-sample_fmt", "s16", "-map_metadata", "-1", "-bitexact", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg could not decode {src.name}: {r.stderr.strip()[:300]}")
    return out


def duration_s(wav: Path) -> float:
    info = sf.info(str(wav))
    return info.frames / info.samplerate


def split_points(samples: np.ndarray, sr: int, max_s: float = config.STT_MAX_CHUNK_S, min_s: float = 10.0) -> list[int]:
    """Sample indices to cut at: the quietest 30 ms frame in each [min_s, max_s] window (latest on ties)."""
    frame = int(0.03 * sr)
    n_frames = len(samples) // frame
    x = samples[: n_frames * frame].astype(np.float64).reshape(n_frames, frame)
    energy = np.sqrt((x**2).mean(axis=1))
    cuts: list[int] = []
    start = 0
    while len(samples) - start > max_s * sr:
        lo, hi = (start + int(min_s * sr)) // frame, (start + int(max_s * sr)) // frame
        quietest = hi - 1 - int(np.argmin(energy[lo:hi][::-1]))  # ties → latest, fewer chunks = fewer API calls
        cut = quietest * frame + frame // 2
        cuts.append(cut)
        start = cut
    return cuts


def chunk(wav: Path, max_s: float = config.STT_MAX_CHUNK_S) -> list[Path]:
    """Return [wav] if short enough, else ≤max_s chunk files split on silence."""
    data, sr = sf.read(str(wav), dtype="int16")
    if len(data) <= max_s * sr:
        return [wav]
    bounds = [0, *split_points(data, sr, max_s), len(data)]
    out = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        p = wav.with_name(f"{wav.stem}_c{i}.wav")
        if not p.exists():
            sf.write(str(p), data[a:b], sr, subtype="PCM_16")
        out.append(p)
    return out
