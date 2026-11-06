"""Eval harness end to end with Gnani mocked: synth → run (clean+phone, A vs B) → report; rerun is free."""
import csv
import subprocess

import pytest

from parchi import audio, config, gnani_client
from parchi.eval import __main__ as cli
from parchi.eval import run, synth


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    ogg = tmp_path / "tone.ogg"
    subprocess.run([audio.ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=2",
                    "-c:a", "libopus", "-f", "ogg", str(ogg)], check=True)
    script = synth.make_scripts(1)[0]
    stt = []

    def fake_stt(path, params):
        stt.append(params)
        # biased config hears the script; baseline mishears every brand as "xyz"
        text = script.text if "keywords" in params else "xyz teen packet, abc do carton"
        return {"success": True, "transcript": text, "detected_language_code": "hi-IN"}

    monkeypatch.setattr(gnani_client, "_call_tts", lambda text, params: ogg.read_bytes())
    monkeypatch.setattr(gnani_client, "_call_stt", fake_stt)
    return script, stt


def test_synth_writes_audio_labels_scripts(env):
    script, _ = env
    assert cli.main(["synth", "--n", "1"]) == 0
    labels = list(csv.DictReader(open(config.DATA_DIR / "testset" / "labels.csv", encoding="utf-8")))
    assert {r["source"] for r in labels} == {"synthetic"} and len(labels) == len(script.lines)
    assert (config.DATA_DIR / "testset" / "audio" / "syn_001.ogg").exists()
    assert cli.main(["synth", "--n", "1"]) == 0  # idempotent: nothing pending
    assert gnani_client.usage()["tts"] == 1


def test_synth_uses_three_plus_voices_and_both_speeds():
    s = synth.make_scripts(8)
    assert len({x.voice for x in s}) >= 3 and {x.speed for x in s} == {0.9, 1.1}


def test_run_report_and_free_rerun(env, capsys):
    script, stt = env
    cli.main(["synth", "--n", "1"])
    assert cli.main(["run", "--conditions", "all", "--configs", "A,B"]) == 0
    out = capsys.readouterr().out
    assert "Skipping phone+noise@10dB, phone+noise@5dB" in out
    assert len(stt) == 4  # 1 note × (clean, phone) × (A, B)

    d = run.latest_run()
    res = list(csv.DictReader(open(d / "results.csv", encoding="utf-8")))
    f1 = {(r["condition"], r["config"]): float(r["f1"]) for r in res}
    assert f1[("clean", "B_parchi")] == 1.0 and f1[("clean", "A_baseline")] == 0.0
    assert all(r["source"] == "synthetic" for r in res)
    md = (d / "report.md").read_text(encoding="utf-8")
    assert "Prisma v3.0" in md and "_No human notes in the test set yet._" in md and "Skipped conditions" in md
    assert "Unsafe auto-approve" in md and "Top 10 failure cases" in md
    assert (d / "f1_by_condition.png").exists() and (d / "unsafe_auto_approve.png").exists()

    jobs, _ = run.plan(["clean", "phone"], ["A", "B"])
    assert run.estimate_calls(jobs) == 0
    assert cli.main(["run", "--configs", "A,B"]) == 0
    assert len(stt) == 4  # rerun: zero new STT calls
    assert cli.main(["report"]) == 0


def test_run_refuses_over_100_calls_without_yes(env, monkeypatch):
    cli.main(["synth", "--n", "1"])
    monkeypatch.setattr(run, "estimate_calls", lambda jobs: 150)
    assert cli.main(["run"]) == 2


def test_add_human_copies_labels(env, tmp_path):
    script, _ = env
    cli.main(["synth", "--n", "1"])
    take = tmp_path / "take.ogg"
    take.write_bytes((config.DATA_DIR / "testset" / "audio" / "syn_001.ogg").read_bytes())
    nid = synth.add_human("syn_001", take)
    notes = run.load_testset()
    assert notes[nid][0] == "human" and notes[nid][1] == notes["syn_001"][1]
