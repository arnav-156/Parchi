"""Evaluation CLI.

  python -m parchi.eval synth --n 20 [--yes]          # synthetic notes via Timbre v2.5 (1 call each)
  python -m parchi.eval add-human syn_003 my_take.ogg  # register a teammate reading script syn_003
  python -m parchi.eval run --conditions all --configs A,B [--limit N] [--yes]
  python -m parchi.eval report                         # rebuild the latest report (no API)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from parchi import gnani_client
from parchi.eval import channel, report, run, synth

CALL_LIMIT_WITHOUT_YES = 100  # brief: state the estimate and ask before >100 new calls in one run


def _confirm(n: int, yes: bool, what: str) -> bool:
    print(f"Estimated new Gnani API calls for {what}: {n}")
    if n > CALL_LIMIT_WITHOUT_YES and not yes:
        print(f"More than {CALL_LIMIT_WITHOUT_YES} calls: re-run with --yes to proceed.")
        return False
    return True


def cmd_synth(a: argparse.Namespace) -> int:
    scripts = synth.make_scripts(a.n, seed=a.seed)
    todo = synth.pending(scripts)
    if not _confirm(len(todo), a.yes, f"{len(todo)} synthetic notes (Timbre v2.5)"):
        return 2
    for p in synth.render(scripts):
        print("wrote", p.name)
    print("usage:", gnani_client.usage())
    return 0


def cmd_add_human(a: argparse.Namespace) -> int:
    print("registered", synth.add_human(a.script_id, Path(a.audio)))
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    requested = list(channel.CONDITIONS) if a.conditions == "all" else a.conditions.split(",")
    conditions, skipped = channel.available_conditions(requested)
    if skipped:
        print(f"Skipping {', '.join(skipped)}: data/noise/ has no .wav files.")
    configs = [c.strip().upper() for c in a.configs.split(",")]
    jobs, missing = run.plan(conditions, configs, a.limit)
    if missing:
        print(f"{len(missing)} labelled note(s) have no audio and are skipped: {', '.join(missing[:5])}…")
    if not jobs:
        print("Nothing to run: add audio + labels to data/testset/ (or `python -m parchi.eval synth`).")
        return 1
    if not _confirm(run.estimate_calls(jobs), a.yes, f"{len(jobs)} jobs"):
        return 2
    before = gnani_client.usage()["total"]
    rows = run.run_jobs(jobs, progress=lambda i, n: print(f"\r{i}/{n}", end="", flush=True))
    print()
    d = run.new_run_dir()
    run.write_rows(d, rows, {"conditions": conditions, "skipped_conditions": skipped,
                             "configs": [run.CONFIGS[c] for c in configs],
                             "api_calls": gnani_client.usage()["total"] - before})
    print("report:", report.build(d))
    print("usage:", gnani_client.usage())
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    d = run.latest_run()
    if d is None:
        print("No evaluation runs yet.")
        return 1
    print("report:", report.build(d))
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(prog="python -m parchi.eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth", help="generate synthetic notes with Timbre v2.5")
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_synth)
    h = sub.add_parser("add-human", help="register a human recording of a script")
    h.add_argument("script_id")
    h.add_argument("audio")
    h.set_defaults(fn=cmd_add_human)
    r = sub.add_parser("run", help="run conditions × configs over the test set")
    r.add_argument("--conditions", default="all", help="all or comma list: " + ",".join(channel.CONDITIONS))
    r.add_argument("--configs", default="A,B")
    r.add_argument("--limit", type=int, default=None, help="only the first N notes")
    r.add_argument("--yes", action="store_true", help="allow >100 new API calls")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("report", help="rebuild the latest report").set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
