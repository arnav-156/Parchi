"""The ONLY module that talks to Gnani (Prisma v3.0 STT, Timbre v2.5 TTS).

Every call is cached on disk and counted in cache/usage.json; cache hits make zero API
calls. Real calls are refused once the counter reaches config.MAX_API_CALLS.
Transient failures (429, 5xx, network) are retried 3x with exponential backoff.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import requests
from gnani.stt import GnaniSTTClient
from gnani.tts import AudioConfig, GnaniTTSClient

from parchi import config

logging.getLogger("gnani").setLevel(logging.WARNING)  # after SDK import: it logs every call at INFO

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MIN_GAP_S = 0.5
_sleep: Callable[[float], None] = time.sleep  # patched in tests


class GnaniError(Exception):
    """Final failure after retries (or a non-retryable API error)."""


class CreditGuardError(GnaniError):
    """Refused: PARCHI_MAX_API_CALLS reached."""


# ---------- usage counter ----------

def _usage_path() -> Path:
    return config.CACHE_DIR / "usage.json"


def usage() -> dict[str, int]:
    p = _usage_path()
    return json.loads(p.read_text()) if p.exists() else {"stt": 0, "tts": 0, "total": 0}


def _count_call(kind: str) -> None:
    u = usage()
    if u["total"] >= config.MAX_API_CALLS:
        raise CreditGuardError(
            f"API call limit reached ({u['total']}/{config.MAX_API_CALLS}). "
            "Raise PARCHI_MAX_API_CALLS in .env to allow more."
        )
    u[kind] = u.get(kind, 0) + 1
    u["total"] += 1
    _usage_path().parent.mkdir(parents=True, exist_ok=True)
    _usage_path().write_text(json.dumps(u))


# ---------- raw SDK calls (monkeypatched in tests) ----------

def _call_stt(audio_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    return GnaniSTTClient().transcribe(audio_path, **params)


def _call_tts(text: str, params: dict[str, Any]) -> bytes:
    p = dict(params)
    container = p.pop("container")
    ac = (
        AudioConfig(sample_rate=48000, encoding="oggopus", container="ogg")
        if container == "ogg"
        else AudioConfig(sample_rate=24000, encoding="linear_pcm", container="wav")
    )
    return GnaniTTSClient().synthesize(text, audio_config=ac, **p)


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status in RETRY_STATUS
    return isinstance(exc, requests.RequestException)


def _with_retries(kind: str, fn: Callable[[], Any]) -> Any:
    for attempt in range(MAX_ATTEMPTS):
        _count_call(kind)  # every attempt hits the API, so every attempt is counted
        try:
            return fn()
        except Exception as exc:  # SDK raises its own APIError / requests errors
            if isinstance(exc, (ValueError, TypeError)):
                raise  # programming error: bad params, never retry
            if not _is_transient(exc) or attempt == MAX_ATTEMPTS - 1:
                raise GnaniError(f"{kind.upper()} failed: {exc}") from exc
            # 429 = "only 1 active request at a time": the previous one is still finishing server-side
            _sleep(5 * 2**attempt if getattr(exc, "status_code", None) == 429 else 2**attempt)
    raise AssertionError("unreachable")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------- public API ----------

def stt(
    audio_path: Path,
    *,
    keywords: list[str] | None = None,
    topic: str | None = None,
    previous_text: str | None = None,
    model: str = config.STT_MODEL,
    language_code: str = config.STT_LANGUAGE,
    language_candidates: tuple[str, ...] = config.STT_CANDIDATES,
    fmt: str = config.STT_FORMAT,
) -> dict[str, Any]:
    """Transcribe one ≤30 s audio file with Prisma. Returns the API JSON plus `_cached`."""
    audio_path = Path(audio_path)
    params = _stt_params(keywords, topic, previous_text, model, language_code, language_candidates, fmt)
    cache_file = stt_cache_path(audio_path, params)
    if cache_file.exists():
        return {**json.loads(cache_file.read_text(encoding="utf-8")), "_cached": True}

    resp = _with_retries("stt", lambda: _call_stt(audio_path, params))
    _sleep(MIN_GAP_S)  # breathing room so the next request isn't refused as concurrent
    if not resp.get("success", True):
        raise GnaniError(f"STT unsuccessful: {resp}")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    return {**resp, "_cached": False}


def _stt_params(keywords: list[str] | None, topic: str | None, previous_text: str | None, model: str,
                language_code: str, language_candidates: tuple[str, ...], fmt: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": model,
        "language_code": language_code,
        "language_candidates": list(language_candidates) if language_code == "indic-auto" else None,
        "format": fmt,
        "keywords": keywords or None,
        "topic": topic or None,
        "previous_text": previous_text or None,
    }
    return {k: v for k, v in params.items() if v is not None}


def stt_cache_path(audio_path: Path, params: dict[str, Any]) -> Path:
    key = _sha(Path(audio_path).read_bytes())[:32] + "_" + _sha(json.dumps(params, sort_keys=True).encode())[:16]
    return config.CACHE_DIR / "stt" / f"{key}.json"


def stt_is_cached(audio_path: Path, *, keywords: list[str] | None = None, topic: str | None = None,
                  previous_text: str | None = None) -> bool:
    params = _stt_params(keywords, topic, previous_text, config.STT_MODEL, config.STT_LANGUAGE,
                         config.STT_CANDIDATES, config.STT_FORMAT)
    return stt_cache_path(audio_path, params).exists()


def tts(
    text: str,
    *,
    voice: str = config.TTS_VOICE,
    speed: float = config.TTS_SPEED,
    container: str = "ogg",
    language: str = config.TTS_LANGUAGE,
    model: str = config.TTS_MODEL,
) -> Path:
    """Synthesize text with Timbre; returns path of the cached audio file (.ogg or .wav)."""
    if container not in ("ogg", "wav"):
        raise ValueError(f"container must be ogg or wav, got {container!r}")
    params = {"voice": voice, "speed": speed, "language": language, "model": model, "container": container}
    key = _sha(json.dumps({"text": text, **params}, sort_keys=True, ensure_ascii=False).encode())[:40]
    out = config.CACHE_DIR / "tts" / f"{key}.{container}"
    if out.exists():
        return out
    audio = _with_retries("tts", lambda: _call_tts(text, params))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    return out
