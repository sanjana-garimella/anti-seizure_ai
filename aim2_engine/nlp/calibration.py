"""
calibration.py
--------------
Confidence calibration + threshold selection for the advisory NLP layer (pure
stdlib). Because findings are human-reviewed, well-calibrated probabilities and
the RIGHT operating threshold matter more than raw accuracy:

  * IsotonicCalibrator  - non-parametric (PAV) monotone mapping raw->calibrated.
  * PlattCalibrator     - logistic (sigmoid) fit via gradient descent.
  * reliability_curve   - binned observed-vs-predicted (for reliability plots).
  * expected_calibration_error
  * pick_threshold      - choose the score cutoff meeting a target precision
                          (e.g. structured fields) OR target recall (e.g.
                          red flags: recall ~1.0, never miss an urgent signal).

No ML libraries, no network; deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple


# --------------------------- Isotonic (PAV) ---------------------------------
@dataclass
class IsotonicCalibrator:
    _x: List[float] = field(default_factory=list)
    _y: List[float] = field(default_factory=list)

    def fit(self, scores: Sequence[float], labels: Sequence[int]) -> "IsotonicCalibrator":
        pts = sorted(zip(scores, labels), key=lambda t: t[0])
        # Pool Adjacent Violators
        xs = [float(s) for s, _ in pts]
        ys = [float(l) for _, l in pts]
        w = [1.0] * len(ys)
        i = 0
        blocks = [[x, y, wi] for x, y, wi in zip(xs, ys, w)]
        merged = []
        for b in blocks:
            merged.append(b[:])
            while len(merged) > 1 and merged[-2][1] > merged[-1][1]:
                a = merged.pop()
                c = merged.pop()
                tw = c[2] + a[2]
                merged.append([c[0], (c[1] * c[2] + a[1] * a[2]) / tw, tw])
        # expand back to per-threshold step function
        self._x, self._y = [], []
        # reconstruct level per original x by walking merged pools in order
        idx = 0
        for pool in merged:
            self._x.append(pool[0])
            self._y.append(min(1.0, max(0.0, pool[1])))
        return self

    def predict(self, score: float) -> float:
        if not self._x:
            return float(score)
        # step: use the level of the largest fitted x <= score
        lvl = self._y[0]
        for x, y in zip(self._x, self._y):
            if x <= score:
                lvl = y
            else:
                break
        return lvl


# --------------------------- Platt (sigmoid) --------------------------------
@dataclass
class PlattCalibrator:
    a: float = 0.0
    b: float = 0.0

    def fit(self, scores, labels, lr=0.1, iters=2000) -> "PlattCalibrator":
        a, b = 0.0, 0.0
        n = len(scores)
        if n == 0:
            return self
        for _ in range(iters):
            ga = gb = 0.0
            for s, y in zip(scores, labels):
                p = 1.0 / (1.0 + math.exp(-(a * s + b)))
                ga += (p - y) * s
                gb += (p - y)
            a -= lr * ga / n
            b -= lr * gb / n
        self.a, self.b = a, b
        return self

    def predict(self, score: float) -> float:
        return 1.0 / (1.0 + math.exp(-(self.a * score + self.b)))


# --------------------------- reliability / ECE ------------------------------
def reliability_curve(scores, labels, bins=10) -> List[dict]:
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(s, y) for s, y in zip(scores, labels) if (s >= lo and (s < hi or (i == bins - 1 and s <= hi)))]
        if not sel:
            out.append({"bin": [round(lo, 2), round(hi, 2)], "n": 0,
                        "avg_pred": None, "frac_pos": None})
            continue
        avg_pred = sum(s for s, _ in sel) / len(sel)
        frac_pos = sum(y for _, y in sel) / len(sel)
        out.append({"bin": [round(lo, 2), round(hi, 2)], "n": len(sel),
                    "avg_pred": round(avg_pred, 3), "frac_pos": round(frac_pos, 3)})
    return out


def expected_calibration_error(scores, labels, bins=10) -> float:
    n = len(scores)
    if n == 0:
        return 0.0
    ece = 0.0
    for b in reliability_curve(scores, labels, bins):
        if b["n"]:
            ece += (b["n"] / n) * abs(b["avg_pred"] - b["frac_pos"])
    return round(ece, 4)


# --------------------------- threshold selection ----------------------------
def _pr_at(scores, labels, tau) -> Tuple[float, float]:
    tp = sum(1 for s, y in zip(scores, labels) if s >= tau and y == 1)
    fp = sum(1 for s, y in zip(scores, labels) if s >= tau and y == 0)
    fn = sum(1 for s, y in zip(scores, labels) if s < tau and y == 1)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    return prec, rec


def pick_threshold(scores, labels, target_precision=None, target_recall=None,
                   grid=101) -> dict:
    """Lowest threshold meeting target_precision, or highest meeting
    target_recall. For red flags pass target_recall≈1.0 (never miss); for
    structured fields pass target_precision (don't waste reviewer trust)."""
    taus = [i / (grid - 1) for i in range(grid)]
    best = None
    if target_recall is not None:
        # highest tau still >= target recall (fewest false positives)
        for tau in taus:
            p, r = _pr_at(scores, labels, tau)
            if r >= target_recall:
                best = {"tau": round(tau, 3), "precision": round(p, 3), "recall": round(r, 3)}
        return best or {"tau": 0.0, "precision": None, "recall": None}
    if target_precision is not None:
        for tau in taus:
            p, r = _pr_at(scores, labels, tau)
            if p >= target_precision:
                return {"tau": round(tau, 3), "precision": round(p, 3), "recall": round(r, 3)}
        return {"tau": 1.0, "precision": None, "recall": None}
    # default: maximize F1
    bestf = -1.0
    for tau in taus:
        p, r = _pr_at(scores, labels, tau)
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        if f1 > bestf:
            bestf, best = f1, {"tau": round(tau, 3), "precision": round(p, 3),
                               "recall": round(r, 3), "f1": round(f1, 3)}
    return best
