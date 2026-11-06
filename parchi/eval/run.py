"""Run the test set across conditions × configs and write per-note results (reuses the STT cache)."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from parchi import audio, config, gnani_client, pipeline
from parchi.catalog import load_catalog, stt_keywords
from parchi.eval import channel, metrics, synth

CONFIGS = {"A": "A_baseline", "B": "B_parchi"}  # A: no keywords/topic · B: catalog keywords + topic


@dataclass
class Job:
    note_id: str
    source: str
    condition: str
    config: str
    wav: Path
    gold: list[metrics.Triple]


def load_testset() -> dict[str, tuple[str, list[metrics.Triple]]]:
    notes: dict[str, tuple[str, list[metrics.Triple]]] = {}
    for r in synth.existing_labels():
        src, lines = notes.setdefault(r["note_id"], (r["source"], []))
        lines.append((r["sku_id"], float(r["qty"]), r["unit"]))
    return notes


def audio_file(note_id: str) -> Path | None:
    hits = sorted((synth.testset_dir() / "audio").glob(f"{note_id}.*"))
    return hits[0] if hits else None


def bias(cfg: str) -> tuple[list[str] | None, str | None]:
    return (stt_keywords(load_catalog()), config.STT_TOPIC) if cfg == "B_parchi" else (None, None)


def plan(conditions: list[str], configs: list[str], limit: int | None = None) -> tuple[list[Job], list[str]]:
    """Build jobs (degraded audio is made locally here). Returns (jobs, notes missing audio)."""
    jobs, missing = [], []
    notes = sorted(load_testset().items())[: limit or None]
    for note_id, (source, gold) in notes:
        src = audio_file(note_id)
        if src is None:
            missing.append(note_id)
            continue
        clean = audio.normalise(src)
        for cond in conditions:
            wav = channel.degrade(clean, cond, note_id)
            jobs.extend(Job(note_id, source, cond, CONFIGS[c], wav, gold) for c in configs)
    return jobs, missing


def estimate_calls(jobs: list[Job]) -> int:
    """New STT calls needed: every chunk of a job whose first chunk is not cached yet."""
    n = 0
    for j in jobs:
        kw, topic = bias(j.config)
        chunks = audio.chunk(audio.normalise(j.wav))
        if not gnani_client.stt_is_cached(chunks[0], keywords=kw, topic=topic):
            n += len(chunks)
    return n


def run_jobs(jobs: list[Job], progress=None) -> list[dict[str, object]]:
    cat = load_catalog()
    rows = []
    for i, j in enumerate(jobs):
        kw, topic = bias(j.config)
        row: dict[str, object] = {"note_id": j.note_id, "source": j.source, "condition": j.condition,
                                  "config": j.config, "labels": json.dumps(j.gold)}
        try:
            tr = pipeline.transcribe(j.wav, keywords=kw, topic=topic)
            an = pipeline.analyse(tr.text, cat)
            pred = [((m.sku_id, m.qty, m.unit), m.status) for _, m in an.lines]
            row.update(transcript=tr.text, api_calls=tr.api_calls, error="")
        except gnani_client.GnaniError as exc:  # failed note: every gold line counts as missed
            pred = []
            row.update(transcript="", api_calls=0, error=str(exc))
        row["pred"] = json.dumps(pred, ensure_ascii=False)
        row.update(asdict(metrics.score_note(j.gold, pred)))
        rows.append(row)
        if progress:
            progress(i + 1, len(jobs))
    return rows


def new_run_dir() -> Path:
    d = config.REPORTS_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_rows(run_dir: Path, rows: list[dict[str, object]], meta: dict[str, object]) -> None:
    with open(run_dir / "per_note.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["note_id"])
        w.writeheader()
        w.writerows(rows)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def latest_run() -> Path | None:
    runs = sorted(config.REPORTS_DIR.glob("eval_*/per_note.csv"))
    return runs[-1].parent if runs else None
