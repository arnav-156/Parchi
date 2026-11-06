"""Line-level scoring. A predicted line is correct only if sku_id AND qty AND unit all match a gold line."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

Triple = tuple[str | None, float | None, str | None]


@dataclass
class NoteScore:
    gold: int
    predicted: int
    correct: int  # predicted lines matching a gold triple (multiset)
    sku_hits: int  # gold lines whose SKU appears among predictions (multiset)
    qty_hits: int  # of those SKU hits, how many also have the right qty+unit
    green: int
    green_wrong: int
    amber: int
    red: int


def _norm(t: Triple) -> Triple:
    sku, qty, unit = t
    return sku, (round(float(qty), 3) if qty is not None else None), unit


def score_note(gold: list[Triple], pred: list[tuple[Triple, str]]) -> NoteScore:
    """gold: label triples; pred: (triple, status) per predicted line."""
    g = Counter(_norm(t) for t in gold)
    # GREEN lines claim gold triples first, so a wrong GREEN is never excused by a correct AMBER twin
    order = sorted(pred, key=lambda p: p[1] != "GREEN")
    remaining = g.copy()
    correct = green_wrong = 0
    for t, status in order:
        t = _norm(t)
        if t[0] is not None and remaining[t] > 0:
            remaining[t] -= 1
            correct += 1
        elif status == "GREEN":
            green_wrong += 1

    gold_by_sku = Counter(t[0] for t in g.elements())
    pred_by_sku = Counter(_norm(t)[0] for t, _ in pred if t[0] is not None)
    sku_hits = sum((gold_by_sku & pred_by_sku).values())
    qty_hits = min(correct, sku_hits)  # a full-triple match is a SKU match with the right qty+unit
    statuses = Counter(s for _, s in pred)
    return NoteScore(
        gold=sum(g.values()), predicted=len(pred), correct=correct, sku_hits=sku_hits, qty_hits=qty_hits,
        green=statuses["GREEN"], green_wrong=green_wrong, amber=statuses["AMBER"], red=statuses["RED"],
    )


def _ratio(a: float, b: float) -> float:
    return a / b if b else 0.0


def aggregate(scores: list[NoteScore]) -> dict[str, float]:
    s = {k: sum(getattr(n, k) for n in scores) for k in NoteScore.__dataclass_fields__}
    p, r = _ratio(s["correct"], s["predicted"]), _ratio(s["correct"], s["gold"])
    return {
        "notes": len(scores),
        "gold_lines": s["gold"],
        "pred_lines": s["predicted"],
        "precision": p,
        "recall": r,
        "f1": _ratio(2 * p * r, p + r),
        "sku_accuracy": _ratio(s["sku_hits"], s["gold"]),
        "qty_accuracy": _ratio(s["qty_hits"], s["sku_hits"]),
        "unsafe_auto_approve_rate": _ratio(s["green_wrong"], s["green"]),
        "clarification_rate": _ratio(s["amber"], s["predicted"]),
        "red_rate": _ratio(s["red"], s["predicted"]),
    }
