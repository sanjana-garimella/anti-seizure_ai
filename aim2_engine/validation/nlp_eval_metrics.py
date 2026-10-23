"""
nlp_eval_metrics.py
-------------------
Rigorous, stdlib-only evaluation for the advisory NLP layer:

  * span_prf              - micro precision/recall/F1 over span/finding sets.
  * bootstrap_ci          - document-level bootstrap confidence interval for any
                            metric (default F1), so results come with error bars.
  * per_field_prf         - precision/recall/F1 broken down by field.
  * red_flag_recall       - recall on the escalate-only red-flag class (target
                            ~1.0: a missed urgent signal is the worst error).
  * calibration_report    - reliability curve + ECE (wraps nlp.calibration).

A "finding" is any hashable item, e.g. (field, value) or (start, end, label).
Gold and predictions are lists (one set per document) so bootstrap can resample
whole documents.
"""
from __future__ import annotations

import os
import random
import sys
from typing import Callable, Dict, List, Sequence, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp.calibration import reliability_curve, expected_calibration_error  # noqa: E402


def _counts(gold: Set, pred: Set) -> Tuple[int, int, int]:
    tp = len(gold & pred)
    fp = len(pred - gold)
    fn = len(gold - pred)
    return tp, fp, fn


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    p = tp / (tp + fp) if (tp + fp) else (1.0 if fn == 0 else 0.0)
    r = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def span_prf(gold_docs: Sequence[Set], pred_docs: Sequence[Set]) -> Dict[str, float]:
    tp = fp = fn = 0
    for g, p in zip(gold_docs, pred_docs):
        a, b, c = _counts(set(g), set(p))
        tp += a; fp += b; fn += c
    return _prf(tp, fp, fn)


def bootstrap_ci(gold_docs: Sequence[Set], pred_docs: Sequence[Set],
                 metric: str = "f1", n_boot: int = 1000, alpha: float = 0.05,
                 seed: int = 0) -> Dict[str, float]:
    rng = random.Random(seed)
    n = len(gold_docs)
    if n == 0:
        return {"point": 0.0, "lo": 0.0, "hi": 0.0, "n_boot": n_boot}
    idx = list(range(n))
    vals = []
    for _ in range(n_boot):
        sample = [rng.choice(idx) for _ in range(n)]
        g = [gold_docs[i] for i in sample]
        p = [pred_docs[i] for i in sample]
        vals.append(span_prf(g, p)[metric])
    vals.sort()
    lo = vals[int((alpha / 2) * n_boot)]
    hi = vals[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    point = span_prf(gold_docs, pred_docs)[metric]
    return {"metric": metric, "point": round(point, 4),
            "lo": round(lo, 4), "hi": round(hi, 4),
            "ci": f"{round(point,3)} [{round(lo,3)}, {round(hi,3)}]", "n_boot": n_boot}


def per_field_prf(gold_docs: Sequence[Set], pred_docs: Sequence[Set],
                  field_of: Callable = lambda x: x[0]) -> Dict[str, Dict[str, float]]:
    fields = set()
    for d in list(gold_docs) + list(pred_docs):
        for item in d:
            fields.add(field_of(item))
    out = {}
    for f in sorted(fields):
        tp = fp = fn = 0
        for g, p in zip(gold_docs, pred_docs):
            gf = {x for x in g if field_of(x) == f}
            pf = {x for x in p if field_of(x) == f}
            a, b, c = _counts(gf, pf)
            tp += a; fp += b; fn += c
        out[f] = _prf(tp, fp, fn)
    return out


def red_flag_recall(gold_docs: Sequence[Set], pred_docs: Sequence[Set],
                    is_red_flag: Callable = lambda x: x[0] == "red_flag") -> Dict[str, float]:
    tp = fn = 0
    misses = []
    for i, (g, p) in enumerate(zip(gold_docs, pred_docs)):
        gr = {x for x in g if is_red_flag(x)}
        pr = {x for x in p if is_red_flag(x)}
        tp += len(gr & pr)
        missed = gr - pr
        fn += len(missed)
        if missed:
            misses.append({"doc": i, "missed": sorted(map(str, missed))})
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {"recall": round(recall, 4), "tp": tp, "missed": fn, "miss_detail": misses,
            "target": 1.0, "pass": recall >= 0.999 or (tp + fn) == 0}


def calibration_report(scores: Sequence[float], labels: Sequence[int],
                       bins: int = 10) -> Dict:
    return {"ece": expected_calibration_error(scores, labels, bins),
            "reliability": reliability_curve(scores, labels, bins)}


def full_report(gold_docs, pred_docs, scores=None, labels=None) -> Dict:
    rep = {"overall": span_prf(gold_docs, pred_docs),
           "f1_ci": bootstrap_ci(gold_docs, pred_docs, "f1"),
           "per_field": per_field_prf(gold_docs, pred_docs),
           "red_flag": red_flag_recall(gold_docs, pred_docs)}
    if scores is not None and labels is not None:
        rep["calibration"] = calibration_report(scores, labels)
    return rep


if __name__ == "__main__":
    import json
    gold = [{("side_effect", "somnolence"), ("red_flag", "breathing")},
            {("seizure_duration_bucket", "1_3min")},
            {("side_effect", "rash"), ("red_flag", "sjs")}]
    pred = [{("side_effect", "somnolence"), ("red_flag", "breathing")},
            {("seizure_duration_bucket", "over_3min")},
            {("side_effect", "rash"), ("red_flag", "sjs")}]
    print(json.dumps(full_report(gold, pred), indent=2))
