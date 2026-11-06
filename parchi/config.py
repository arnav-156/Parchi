"""Paths, env vars and thresholds. Read env once at import; tests monkeypatch attributes."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
REPORTS_DIR = ROOT / "reports"
LEXICON_DIR = Path(__file__).resolve().parent / "lexicon"
CATALOG_CSV = DATA_DIR / "catalog.csv"
RETAILERS_CSV = DATA_DIR / "retailers.csv"
DB_PATH = Path(os.getenv("PARCHI_DB", str(DATA_DIR / "parchi.db")))

MAX_API_CALLS = int(os.getenv("PARCHI_MAX_API_CALLS", "600"))
DISTRIBUTOR_NAME = os.getenv("PARCHI_DISTRIBUTOR", "Shree Ganesh Traders")

# Gnani models (named explicitly; SDK defaults are not relied on)
STT_MODEL = "prisma-v3.0"
STT_LANGUAGE = "indic-auto"
STT_CANDIDATES = ("hi-IN", "en-IN")
STT_FORMAT = "codemix"
STT_MAX_CHUNK_S = 28.0  # REST limit is 30 s
STT_TOPIC = (
    "Kirana retailer ordering FMCG goods from a distributor over a WhatsApp voice note, "
    "in Hinglish: brand and product names, pack sizes, quantities in Hindi numbers, "
    "units like carton, peti, dabba, packet, darjan, piece, kilo."
)
TTS_MODEL = "timbre-v2.5"
TTS_VOICE = os.getenv("PARCHI_TTS_VOICE", "Poorvi")
TTS_VOICE_EN = os.getenv("PARCHI_TTS_VOICE_EN", "Kaveri")  # en-IN replies
TTS_LANGUAGE = "hi-IN"
TTS_SPEED = float(os.getenv("PARCHI_TTS_SPEED", "0.95"))

# Matching thresholds
GREEN_SCORE = 85.0
AMBER_SCORE = 70.0
QTY_SUSPICIOUS = 500.0
