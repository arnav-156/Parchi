"""Phase 0 smoke test: one Timbre v2.5 call (-> .ogg) and one Prisma v3.0 call.

Usage: python scripts/smoke_test.py [--force]
Results are saved in cache/smoke/; reruns reuse them (zero API calls) unless --force.
"""
from __future__ import annotations

import inspect
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from gnani.stt import GnaniSTTClient  # noqa: E402
from gnani.tts import AudioConfig, GnaniTTSClient  # noqa: E402

logging.getLogger("gnani").setLevel(logging.WARNING)  # SDK logs DEBUG by default
sys.stdout.reconfigure(encoding="utf-8")  # Windows console default cp1252 chokes on Devanagari

OUT = ROOT / "cache" / "smoke"
SAMPLE = ROOT / "data" / "samples" / "sample1.ogg"
TTS_VOICE = "Poorvi"  # Hinglish voice per docs
TTS_TEXT = (
    "Bhaiya, Parle-G ke do carton, Maggi chhota wala paanch dabba, "
    "Surf Excel ek kilo teen packet, aur Tata namak bees packet bhej dena."
)
KEYWORDS = ["Parle-G", "Maggi", "Surf Excel", "Tata Salt", "Colgate"]
TOPIC = "Kirana retailer placing an FMCG order with a distributor: product names, quantities, cartons, packets."


def main(force: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    print("== SDK signatures ==")
    print("GnaniSTTClient.transcribe", inspect.signature(GnaniSTTClient.transcribe))
    print("GnaniTTSClient.synthesize", inspect.signature(GnaniTTSClient.synthesize))
    print("AudioConfig", inspect.signature(AudioConfig))
    print("\n== Timbre v2.5 voices ==")
    print(GnaniTTSClient.supported_voices(model="timbre-v2.5"))

    # --- TTS (Timbre v2.5 -> ogg/opus) ---
    tts_out = OUT / "tts_sample.ogg"
    sample = SAMPLE if SAMPLE.exists() else tts_out
    if not SAMPLE.exists():
        if force or not tts_out.exists():
            print(f"\n== TTS call: voice={TTS_VOICE}, model=timbre-v2.5, ogg/oggopus ==")
            audio = GnaniTTSClient().synthesize(
                TTS_TEXT,
                voice=TTS_VOICE,
                model="timbre-v2.5",
                language="hi-IN",
                speed=0.95,
                audio_config=AudioConfig(sample_rate=48000, encoding="oggopus", container="ogg"),
            )
            tts_out.write_bytes(audio)
            print(f"TTS returned {type(audio).__name__}, {len(audio)} bytes, head={audio[:4]!r}")
        else:
            print(f"\n== TTS: reusing {tts_out.relative_to(ROOT)} (no API call) ==")
    print(f"STT input: {sample.relative_to(ROOT)}")

    # --- STT (Prisma v3.0) ---
    stt_out = OUT / f"stt_{sample.stem}.json"
    if force or not stt_out.exists():
        print("\n== STT call: prisma-v3.0, indic-auto, codemix, 5 keywords ==")
        resp = GnaniSTTClient().transcribe(
            sample,
            language_code="indic-auto",
            model="prisma-v3.0",
            language_candidates=["hi-IN", "en-IN"],
            format="codemix",
            keywords=KEYWORDS,
            topic=TOPIC,
        )
        stt_out.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(f"\n== STT: reusing {stt_out.relative_to(ROOT)} (no API call) ==")
    print(stt_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main(force="--force" in sys.argv)
