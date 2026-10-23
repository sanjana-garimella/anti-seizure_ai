"""
validation/nlp_eval_stats.py
-----------------------------
Small, stdlib-only significance/uncertainty helpers shared by the real-text
NLP evals (finding D2). Their purpose is to stop the eval scripts from reporting
bare point estimates ("F1 0.761 vs 0.720") as if a 4-point delta on a small,
possibly-unlabeled sample were a real, decisive difference.

Provided:
  * bootstrap_ci   - percentile bootstrap 95% CI for a rate (mean of 0/1s).
  * mcnemars_test  - McNemar's exact/chi-square test on PAIRED per-example
                     correctness for an A-vs-B comparison (needs per-example
                     gold correctness for both systems).
  * write_artifact - dump a timestamped JSON result artifact into validation/
                     so any cited number is reproducible and diffable.

DECISION-PATH NOTE: diagnostic/advisory only. Does not touch engine_core.
No external dependencies (no scipy/numpy) - project is stdlib-only.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from typing import Dict, List, Sequence, Tuple


def bootstrap_ci(values: Sequence[float], n_boot: int = 2000,
                 alpha: float = 0.05, seed: int = 0) -> Tuple[float, float, float]:
    """Percentile bootstrap CI for the mean of `values` (typically per-example
    0/1 correctness or hit indicators). Returns (point_estimate, lo, hi).
    Returns (nan, nan, nan) for an empty input."""
    n = len(values)
    if n == 0:
        nan = float("nan")
        return nan, nan, nan
    vals = list(values)
    point = sum(vals) / n
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return point, lo, hi


def mcnemars_test(correct_a: Sequence[bool],
                  correct_b: Sequence[bool]) -> Dict[str, float]:
    """McNemar's test on paired per-example correctness (same examples, two
    systems). `correct_a[i]`/`correct_b[i]` = whether system A/B got example i
    right. Returns discordant counts b, c (b = A right & B wrong, c = A wrong &
    B right), the test statistic and a p-value. Uses the exact binomial test
    when discordant pairs are few (<= 25), else the chi-square approximation
    with continuity correction. p is None-safe (nan) when there are no
    discordant pairs (systems tie)."""
    assert len(correct_a) == len(correct_b), "paired inputs must be equal length"
    b = sum(1 for a, bb in zip(correct_a, correct_b) if a and not bb)
    c = sum(1 for a, bb in zip(correct_a, correct_b) if (not a) and bb)
    n_disc = b + c
    if n_disc == 0:
        return {"b": b, "c": c, "statistic": float("nan"), "p_value": float("nan"),
                "method": "no-discordant-pairs"}
    if n_disc <= 25:
        # Exact two-sided binomial test, p=0.5.
        k = min(b, c)
        tail = sum(math.comb(n_disc, i) for i in range(0, k + 1)) / (2 ** n_disc)
        p = min(1.0, 2 * tail)
        return {"b": b, "c": c, "statistic": float(k), "p_value": p,
                "method": "exact-binomial"}
    # Chi-square with continuity correction.
    stat = (abs(b - c) - 1) ** 2 / (b + c)
    # survival function of chi-square with 1 df = erfc(sqrt(stat/2))
    p = math.erfc(math.sqrt(stat / 2.0))
    return {"b": b, "c": c, "statistic": stat, "p_value": p,
            "method": "chi2-continuity"}


def fmt_ci(point: float, lo: float, hi: float) -> str:
    if point != point:  # nan
        return "n/a"
    return f"{point:.3f} (95% CI {lo:.3f}-{hi:.3f})"


def write_artifact(basename: str, payload: dict,
                   out_dir: str = None) -> str:
    """Write `payload` as a timestamped JSON artifact into validation/ (finding
    D2/D8) so cited numbers are reproducible/diffable. Returns the path."""
    out_dir = out_dir or os.path.dirname(os.path.abspath(__file__))
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    payload = dict(payload)
    payload.setdefault("generated_utc", ts)
    path = os.path.join(out_dir, f"{basename}_{ts}.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return path
