"""per_note.csv → results.csv, report.md (tables + top-10 failures) and PNG charts."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from parchi.catalog import load_catalog  # noqa: E402
from parchi.eval.channel import CONDITIONS  # noqa: E402
from parchi.eval.metrics import NoteScore, aggregate  # noqa: E402

SCORE_COLS = list(NoteScore.__dataclass_fields__)
CONFIG_ORDER = ["A_baseline", "B_parchi"]
COLOURS = {"A_baseline": "#2a78d6", "B_parchi": "#eb6834"}  # categorical slots 1, 2 (validated pair)
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SHOW = [("f1", "F1"), ("precision", "Precision"), ("recall", "Recall"), ("sku_accuracy", "SKU acc"),
        ("qty_accuracy", "Qty acc"), ("unsafe_auto_approve_rate", "Unsafe auto-approve"),
        ("clarification_rate", "Clarification rate")]


def results_table(per_note: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (src, cond, cfg), g in per_note.groupby(["source", "condition", "config"]):
        scores = [NoteScore(**{k: int(r[k]) for k in SCORE_COLS}) for _, r in g.iterrows()]
        rows.append({"source": src, "condition": cond, "config": cfg, **aggregate(scores),
                     "api_calls": int(g["api_calls"].sum()), "stt_failed": int((g["error"].fillna("") != "").sum())})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["condition"] = pd.Categorical(df["condition"], [c for c in CONDITIONS if c in set(df["condition"])], ordered=True)
    return df.sort_values(["source", "condition", "config"]).reset_index(drop=True)


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _md_table(df: pd.DataFrame) -> str:
    head = "| Condition | Config | Notes | Lines | " + " | ".join(lbl for _, lbl in SHOW) + " |"
    sep = "|" + "---|" * (4 + len(SHOW))
    body = [f"| {r.condition} | {r.config} | {r.notes} | {r.gold_lines} | "
            + " | ".join(f"{r[k]:.3f}" if k == "f1" else _pct(r[k]) for k, _ in SHOW) + " |"
            for _, r in df.iterrows()]
    return "\n".join([head, sep, *body])


def _lines(triples: list, cat) -> str:
    out = []
    for item in triples:
        (sku, qty, unit), status = (item, None) if not isinstance(item[0], list) else item
        name = cat.by_id[sku].label if sku in cat.by_id else "?"
        q = "–" if qty is None else f"{qty:g}"
        out.append(f"{name} {q} {unit or ''}".strip() + (f" [{status}]" if status else ""))
    return "; ".join(out) or "(none)"


def failures(per_note: pd.DataFrame, k: int = 10) -> pd.DataFrame:
    df = per_note.copy()
    df["missed"] = df["gold"] - df["correct"]
    df["wrong_green"] = df["green_wrong"]
    df = df[(df["missed"] > 0) | (df["wrong_green"] > 0)]
    return df.sort_values(["wrong_green", "missed"], ascending=False).head(k)


def chart(df: pd.DataFrame, metric: str, title: str, path: Path, lower_is_better: bool = False) -> None:
    sources = sorted(df["source"].unique())
    fig, axes = plt.subplots(1, len(sources), figsize=(max(6.4, 4.8 * len(sources)), 3.8), sharey=True,
                             squeeze=False, facecolor=SURFACE)
    top = 1.08 if not lower_is_better else max(0.1, float(df[metric].max()) * 1.35)
    fmt = (lambda v: f"{v:.2f}") if not lower_is_better else (lambda v: f"{100 * v:.1f}%")
    for ax, src in zip(axes[0], sources):
        d = df[df["source"] == src]
        conds = [c for c in CONDITIONS if c in set(d["condition"].astype(str))]
        width = 0.36
        for i, cfg in enumerate(CONFIG_ORDER):
            vals = [float(d[(d["condition"].astype(str) == c) & (d["config"] == cfg)][metric].sum()) for c in conds]
            xs = [x + (i - 0.5) * (width + 0.02) for x in range(len(conds))]
            ax.bar(xs, vals, width=width, color=COLOURS[cfg], label=cfg, zorder=2)
            for x, v in zip(xs, vals):
                ax.text(x, v + top * 0.015, fmt(v), ha="center", va="bottom", fontsize=8, color=INK_2)
        ax.set_xticks(range(len(conds)), conds, fontsize=9, color=INK)
        ax.set_title(f"{src} (n={int(d['notes'].max())} notes)", fontsize=10, color=INK, loc="left")
        ax.set_ylim(0, top)
        if lower_is_better:
            ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
        ax.tick_params(colors=INK_2, length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
    axes[0][0].set_ylabel(title + (" (lower is better)" if lower_is_better else ""), color=INK_2, fontsize=9)
    axes[0][-1].legend(frameon=False, fontsize=9, labelcolor=INK, loc="upper right")
    fig.suptitle(f"{title} by condition\nA_baseline vs B_parchi · Gnani Prisma v3.0", fontsize=11, color=INK,
                 x=0.02, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def build(run_dir: Path) -> Path:
    per_note = pd.read_csv(run_dir / "per_note.csv", dtype={"error": str, "transcript": str})
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) if (run_dir / "meta.json").exists() else {}
    res = results_table(per_note)
    res.to_csv(run_dir / "results.csv", index=False)
    cat = load_catalog()

    md = [f"# Parchi evaluation — {run_dir.name}", "",
          "STT: **Gnani Prisma v3.0** (`indic-auto`, candidates hi-IN/en-IN, `codemix`). "
          "Configs: **A_baseline** = no keywords/topic; **B_parchi** = catalog keywords (≤200) + topic. "
          "A line counts as correct only if SKU, quantity and unit all match the label.", ""]
    md += ["> **Caveat:** synthetic notes are generated from the same catalog/lexicon the parser uses, and parser "
           "rules were tuned after inspecting synthetic failures — treat synthetic numbers as a development set. "
           "Human recordings (`python -m parchi.eval add-human`) are the held-out check.", ""]
    if meta.get("skipped_conditions"):
        md += [f"> Skipped conditions: {', '.join(meta['skipped_conditions'])} — no noise files in `data/noise/`.", ""]
    md += [f"New API calls in this run: {meta.get('api_calls', int(per_note['api_calls'].sum()))} "
           f"(everything else came from cache).", ""]
    for src in ("human", "synthetic"):
        d = res[res["source"] == src] if not res.empty else res
        md += [f"## {src.title()} recordings", ""]
        if d.empty:
            md += [f"_No {src} notes in the test set yet._", ""]
            continue
        md += [_md_table(d), ""]
        piv = d.pivot_table(index="condition", columns="config", values=["f1", "unsafe_auto_approve_rate"],
                            observed=True)
        if {"A_baseline", "B_parchi"} <= set(d["config"]):
            md += ["**B − A (keywords + topic effect):**", ""]
            for cond in piv.index:
                df1 = piv.loc[cond, ("f1", "B_parchi")] - piv.loc[cond, ("f1", "A_baseline")]
                du = piv.loc[cond, ("unsafe_auto_approve_rate", "B_parchi")] - piv.loc[cond, ("unsafe_auto_approve_rate", "A_baseline")]
                md.append(f"- {cond}: F1 {df1:+.3f}, unsafe auto-approve {100 * du:+.1f} pts")
            md.append("")
    md += ["## Charts", "", "![F1 by condition](f1_by_condition.png)", "",
           "![Unsafe auto-approve rate](unsafe_auto_approve.png)", "",
           "## Top 10 failure cases", "",
           "Sorted by wrong GREEN lines (unsafe), then missed lines.", ""]
    for _, r in failures(per_note).iterrows():
        md += [f"**{r.note_id}** · {r.source} · {r.condition} · {r.config} — "
               f"{int(r.correct)}/{int(r.gold)} lines right, {int(r.green_wrong)} wrong GREEN",
               f"- Transcript: {r.transcript if isinstance(r.transcript, str) and r.transcript else '(STT failed: ' + str(r.error) + ')'}",
               f"- Label: {_lines(json.loads(r.labels), cat)}",
               f"- Parchi: {_lines(json.loads(r.pred), cat)}", ""]
    md += ["## Metric definitions", "",
           "- **Precision / recall / F1**: over order lines; correct = SKU + qty + unit all match.",
           "- **SKU accuracy**: share of labelled lines whose SKU was predicted. **Qty accuracy**: of those, share "
           "with the right qty + unit.",
           "- **Unsafe auto-approve rate**: share of GREEN (auto-confident) lines that are wrong — the safety number.",
           "- **Clarification rate**: share of predicted lines marked AMBER (sent to a human / retailer).", ""]
    (run_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    if not res.empty:
        chart(res, "f1", "Line F1", run_dir / "f1_by_condition.png")
        chart(res, "unsafe_auto_approve_rate", "Unsafe auto-approve rate", run_dir / "unsafe_auto_approve.png",
              lower_is_better=True)
    return run_dir / "report.md"
