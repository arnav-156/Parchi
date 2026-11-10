"""Synthetic retailer calls for end-to-end testing (Timbre v2.5 voices → phone line → .ogg).

Each call is a scripted order spoken by a Timbre voice, passed through the eval's phone-line simulation
(8 kHz, μ-law, 300–3400 Hz) and, for one call, synthetic traffic noise. Expected order lines are written
to data/samples/calls/calls.csv for the test plan. One Timbre call per new script (cached afterwards).

Usage: python scripts/make_test_calls.py
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parchi import audio, config, gnani_client  # noqa: E402
from parchi.eval import channel  # noqa: E402

OUT = config.DATA_DIR / "samples" / "calls"

# expected: (sku_id, qty, unit, status after first pass)
CALLS = [
    dict(id="C1_happy_path", retailer="R001", voice="Deepak", lang="hi-IN", speed=1.0, channel="phone",
         text="Namaste bhaiya, Sharma General Store se bol raha hoon. Tide ke do carton, Vim bar teen darjan, "
              "Bisleri ek peti aur Kurkure bees packet bhej dena.",
         expected=[("DET005", 2, "CARTON", "GREEN"), ("DET007", 36, "PIECE", "GREEN"),
                   ("WAT001", 1, "CARTON", "GREEN"), ("SNK005", 20, "PACKET", "GREEN")],
         purpose="All-green order over a phone line; greeting must not become a line; dozen → pieces"),
    dict(id="C2_ambiguous_complaint", retailer="R002", voice="Nalini", lang="hi-IN", speed=1.0, channel="phone",
         text="Haan ji, Gupta Kirana se. Pichli baar Surf Excel kharab aaya tha. Is baar Surf Excel teen carton, "
              "Tata Tea do dabba aur Maggi bhi bhej dena.",
         expected=[(None, 3, "CARTON", "AMBER"), (None, 2, "BOX", "AMBER"), (None, None, None, "AMBER")],
         purpose="Complaint note; two ambiguous variants; Maggi with no quantity → clarification loop"),
    dict(id="C2R_reply", retailer="R002", voice="Nalini", lang="hi-IN", speed=1.0, channel="clean",
         text="Surf Excel ek kilo wala, Tata Tea aadha kilo wala, aur Maggi chhota wala das packet.",
         expected=[("DET002", 3, "CARTON", "GREEN"), ("TEA002", 2, "BOX", "GREEN"), ("NOO001", 10, "PACKET", "GREEN")],
         purpose="Retailer's WhatsApp answer to C2's question; resolves all three lines"),
    dict(id="C3_noisy_fractions", retailer="R004", voice="Roopesh", lang="hi-IN", speed=1.1,
         channel="phone+traffic@10dB",
         text="Bhai saadhe teen peti Thums Up, paune do peti Sprite, aur Parle-G ke pachees dabbe.",
         expected=[("BEV001", 3.5, "CARTON", "GREEN"), ("BEV003", 1.75, "CARTON", "GREEN"),
                   ("BIS001", 25, "BOX", "GREEN")],
         purpose="Fractions (saadhe, paune) on a noisy phone line, fast speaker"),
    dict(id="C4_english", retailer="R006", voice="Kaveri", lang="en-IN", speed=1.0, channel="phone",
         text="Hello, this is Joseph Daily Needs. Please send two cartons of Coca-Cola, five packets of Good Day "
              "and one dozen Dove soap.",
         expected=[("BEV002", 2, "CARTON", "GREEN"), ("BIS002", 5, "PACKET", "GREEN"), ("SOP005", 12, "PIECE", "GREEN")],
         purpose="English order; reply should come back in English (en-IN)"),
]


def to_ogg(wav: Path, dst: Path) -> None:
    subprocess.run([audio.ffmpeg_bin(), "-v", "error", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k",
                    "-ar", "16000", "-ac", "1", str(dst)], check=True)


def render(call: dict) -> Path:
    dst = OUT / f"{call['id']}.ogg"
    if dst.exists():
        return dst
    src = gnani_client.tts(call["text"], voice=call["voice"], speed=call["speed"], language=call["lang"])
    clean = audio.normalise(src)
    if call["channel"] == "clean":
        to_ogg(clean, dst)
        return dst
    x = channel.phone(channel._load(clean))
    if "traffic" in call["channel"]:
        snr = float(call["channel"].split("@")[1].removesuffix("dB"))
        noise = channel._load(config.DATA_DIR / "noise" / "synthetic_traffic.wav")
        x = channel.add_noise(x, noise, snr, np.random.default_rng(7))
    tmp = OUT / f"{call['id']}.wav"
    sf.write(str(tmp), np.clip(x, -1, 1).astype(np.float32), channel.SR, subtype="PCM_16")
    to_ogg(tmp, dst)
    tmp.unlink()
    return dst


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    before = gnani_client.usage()["total"]
    for c in CALLS:
        print("ready", render(c).relative_to(config.ROOT))
    with open(OUT / "calls.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "retailer", "voice", "lang", "speed", "channel", "purpose", "text",
                                          "expected"])
        w.writeheader()
        for c in CALLS:
            w.writerow({**{k: c[k] for k in w.fieldnames if k != "expected"}, "expected": json.dumps(c["expected"])})
    print("new API calls:", gnani_client.usage()["total"] - before)
