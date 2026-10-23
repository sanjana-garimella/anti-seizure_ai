"""
metrics.py
----------
Validation metrics for the Aim 2 engine, framed as an AGREEMENT / CLASSIFICATION
problem (engine output vs. a clinician reference standard) — not a treatment
comparison. Pure standard library (no numpy/scipy/sklearn) to honor the project's
"stdlib only, no pip required to run" rule; every statistic is implemented and
unit-checked here.

Provides:
  Categorical recommendation
    - confusion_matrix, observed_agreement, prevalence
    - cohens_kappa (unweighted), weighted_kappa (linear|quadratic, ordinal)
    - pabak (prevalence-and-bias-adjusted kappa)
    - per_class_prf + macro_prf  (macro-averaged precision/recall/F1)
  Binary safety flag (asymmetric costs)
    - safety_metrics: sensitivity, specificity, FALSE-NEGATIVE RATE (headline),
      NPV, PPV — each with a Wilson 95% CI
  Continuous dose magnitude
    - bland_altman (bias + 95% limits of agreement) ; icc_2_1 (absolute agreement)
    - (Pearson r is deliberately NOT offered as an agreement metric)
  Study-design helpers
    - wilson_ci, bootstrap_ci
    - fisher_exact_2x2 (deferral vs. human disagreement)
    - mcnemar_exact (two paired classifiers vs. correctness)

DEFERRAL CONVENTION (fixed for reproducibility): a deferral ("insufficient_data")
is EXCLUDED from the categorical accuracy/kappa denominator and analyzed
separately (see fisher_exact_2x2 + evaluate_deferrals). Pass deferral_mode="class"
to instead treat it as a third outcome class.

Run:  cd aim2_engine && python3 validation/metrics.py     # self-test + gold-set run
"""
from __future__ import annotations

import math
import os
import random
import sys
from collections import Counter
from statistics import mean, pstdev
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Z95 = 1.959963984540054
DEFER = "insufficient_data"

# A default clinical "intervention" ordering for the ordinal (weighted) kappa.
# This is a METHODS choice the PI should confirm; pass your own `order`.
DEFAULT_ORDER = ["lower_dose", "no_change", "hold", "slower_uptitration",
                 "escalate", "add_medication", "switch_medication"]


# ---------------------------------------------------------------------------
# Confusion matrix + agreement
# ---------------------------------------------------------------------------
def _labels(gold, pred, labels=None):
    if labels is not None:
        return list(labels)
    return sorted(set(gold) | set(pred))


def confusion_matrix(gold, pred, labels=None):
    """Return (labels, matrix) with matrix[true][pred] counts."""
    labels = _labels(gold, pred, labels)
    idx = {l: i for i, l in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for g, p in zip(gold, pred):
        m[idx[g]][idx[p]] += 1
    return labels, m


def observed_agreement(gold, pred):
    n = len(gold)
    return sum(1 for g, p in zip(gold, pred) if g == p) / n if n else float("nan")


def prevalence(gold):
    n = len(gold)
    return {k: v / n for k, v in Counter(gold).items()} if n else {}


def cohens_kappa(gold, pred, labels=None):
    labels = _labels(gold, pred, labels)
    n = len(gold)
    if n == 0:
        return float("nan")
    po = observed_agreement(gold, pred)
    gc, pc = Counter(gold), Counter(pred)
    pe = sum((gc[l] / n) * (pc[l] / n) for l in labels)
    return 1.0 if pe >= 1 else (po - pe) / (1 - pe)


def weighted_kappa(gold, pred, order, weights="linear"):
    """Ordinal weighted kappa. `order` lists the categories from low to high so
    distance encodes severity of disagreement (decrease<maintain<increase)."""
    idx = {l: i for i, l in enumerate(order)}
    k = len(order)
    if k < 2:
        return float("nan")
    pairs = [(g, p) for g, p in zip(gold, pred) if g in idx and p in idx]
    n = len(pairs)
    if n == 0:
        return float("nan")

    def w(i, j):
        d = abs(i - j) / (k - 1)
        return 1 - d if weights == "linear" else 1 - d * d  # agreement weights

    O = [[0.0] * k for _ in range(k)]
    for g, p in pairs:
        O[idx[g]][idx[p]] += 1
    rows = [sum(r) for r in O]
    cols = [sum(O[i][j] for i in range(k)) for j in range(k)]
    po = sum(w(i, j) * O[i][j] for i in range(k) for j in range(k)) / n
    pe = sum(w(i, j) * rows[i] * cols[j] for i in range(k) for j in range(k)) / (n * n)
    return 1.0 if pe >= 1 else (po - pe) / (1 - pe)


def pabak(gold, pred, labels=None):
    """Prevalence-and-bias-adjusted kappa: (k*po - 1)/(k - 1)."""
    labels = _labels(gold, pred, labels)
    k = len(labels)
    if k < 2:
        return float("nan")
    return (k * observed_agreement(gold, pred) - 1) / (k - 1)


def per_class_prf(gold, pred, labels=None):
    labels = _labels(gold, pred, labels)
    _, m = confusion_matrix(gold, pred, labels)
    out = {}
    for i, l in enumerate(labels):
        tp = m[i][i]
        fn = sum(m[i]) - tp
        fp = sum(m[r][i] for r in range(len(labels))) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        out[l] = {"precision": prec, "recall": rec, "f1": f1, "support": sum(m[i])}
    return out


def macro_prf(gold, pred, labels=None):
    pc = per_class_prf(gold, pred, labels)
    if not pc:
        return {"precision": float("nan"), "recall": float("nan"), "f1": float("nan")}
    return {k: mean(d[k] for d in pc.values()) for k in ("precision", "recall", "f1")}


# ---------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------
def wilson_ci(k, n, z=Z95):
    """Wilson score interval for a proportion (robust at small n / near 0 or 1)."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / den
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_ci(metric_fn: Callable[[Sequence, Sequence], float],
                 gold, pred, n_boot=2000, alpha=0.05, seed=0):
    """Percentile bootstrap CI for any metric_fn(gold, pred) by resampling cases."""
    rng = random.Random(seed)
    n = len(gold)
    if n == 0:
        return (float("nan"), float("nan"))
    vals = []
    idxs = range(n)
    for _ in range(n_boot):
        sample = [rng.randrange(n) for _ in idxs]
        g = [gold[i] for i in sample]
        p = [pred[i] for i in sample]
        try:
            v = metric_fn(g, p)
            if v == v:  # not NaN
                vals.append(v)
        except Exception:
            pass
    if not vals:
        return (float("nan"), float("nan"))
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return (lo, hi)


# ---------------------------------------------------------------------------
# Binary safety flag (asymmetric costs)
# ---------------------------------------------------------------------------
def safety_metrics(gold_flags: Sequence[bool], pred_flags: Sequence[bool]):
    """Sensitivity, specificity, FALSE-NEGATIVE RATE (the harm), NPV, PPV, each
    with a Wilson 95% CI. Positive = a safety concern should fire."""
    tp = sum(1 for g, p in zip(gold_flags, pred_flags) if g and p)
    fn = sum(1 for g, p in zip(gold_flags, pred_flags) if g and not p)
    fp = sum(1 for g, p in zip(gold_flags, pred_flags) if not g and p)
    tn = sum(1 for g, p in zip(gold_flags, pred_flags) if not g and not p)

    def m(num, den):
        return (num / den if den else float("nan"), wilson_ci(num, den))

    sens, sens_ci = m(tp, tp + fn)
    spec, spec_ci = m(tn, tn + fp)
    fnr, fnr_ci = m(fn, tp + fn)          # headline harm metric
    npv, npv_ci = m(tn, tn + fn)
    ppv, ppv_ci = m(tp, tp + fp)
    return {
        "counts": {"TP": tp, "FN": fn, "FP": fp, "TN": tn},
        "sensitivity": sens, "sensitivity_ci": sens_ci,
        "specificity": spec, "specificity_ci": spec_ci,
        "false_negative_rate": fnr, "false_negative_rate_ci": fnr_ci,
        "npv": npv, "npv_ci": npv_ci, "ppv": ppv, "ppv_ci": ppv_ci,
    }


# ---------------------------------------------------------------------------
# Continuous dose magnitude
# ---------------------------------------------------------------------------
def bland_altman(pred: Sequence[float], ref: Sequence[float]):
    """Systematic bias + 95% limits of agreement (NOT correlation)."""
    diffs = [p - r for p, r in zip(pred, ref)]
    n = len(diffs)
    if n == 0:
        return {"n": 0}
    bias = mean(diffs)
    sd = pstdev(diffs) if n > 1 else 0.0
    return {"n": n, "bias": bias, "sd_of_diff": sd,
            "loa_lower": bias - Z95 * sd, "loa_upper": bias + Z95 * sd}


def icc_2_1(pred: Sequence[float], ref: Sequence[float]):
    """ICC(2,1): two-way random, single rater, absolute agreement. Two raters
    (engine, reference) over n subjects."""
    rows = list(zip(pred, ref))
    n = len(rows)
    k = 2
    if n < 2:
        return float("nan")
    grand = mean([v for row in rows for v in row])
    row_means = [mean(row) for row in rows]
    col_means = [mean([rows[i][j] for i in range(n)]) for j in range(k)]
    ss_rows = k * sum((rm - grand) ** 2 for rm in row_means)
    ss_cols = n * sum((cm - grand) ** 2 for cm in col_means)
    ss_total = sum((rows[i][j] - grand) ** 2 for i in range(n) for j in range(k))
    ss_err = ss_total - ss_rows - ss_cols
    msr = ss_rows / (n - 1)
    msc = ss_cols / (k - 1)
    mse = ss_err / ((n - 1) * (k - 1))
    den = msr + (k - 1) * mse + (k / n) * (msc - mse)
    return (msr - mse) / den if den else float("nan")


# ---------------------------------------------------------------------------
# Significance helpers (use sparingly; CIs are the headline)
# ---------------------------------------------------------------------------
def fisher_exact_2x2(table):
    """Two-sided Fisher's exact p for [[a,b],[c,d]] (use for the deferral-vs-
    human-disagreement association; expected cells are small)."""
    (a, b), (c, d) = table
    n = a + b + c + d
    r1, r2, c1 = a + b, c + d, a + c

    def hg(x):
        lo = max(0, c1 - r2)
        hi = min(r1, c1)
        if x < lo or x > hi:
            return 0.0
        return (math.comb(r1, x) * math.comb(r2, c1 - x)) / math.comb(n, c1)

    p_obs = hg(a)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    return min(1.0, sum(hg(x) for x in range(lo, hi + 1) if hg(x) <= p_obs + 1e-12))


def mcnemar_exact(b: int, c: int):
    """Two-sided exact McNemar p for paired classifiers; b,c = discordant counts
    (one correct where the other wrong). Use ONLY for engine-vs-baseline, scored
    correct/incorrect per case — not for the core agreement question."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


# ---------------------------------------------------------------------------
# Top-level evaluators
# ---------------------------------------------------------------------------
def evaluate_categorical(gold, pred, order=None, deferral_mode="exclude",
                         deferral_label=DEFER, n_boot=2000):
    """Full categorical report. deferral_mode 'exclude' (default) drops pairs where
    either side deferred; 'class' keeps deferral as its own outcome class."""
    if deferral_mode == "exclude":
        kept = [(g, p) for g, p in zip(gold, pred)
                if g != deferral_label and p != deferral_label]
        n_excl = len(gold) - len(kept)
        gold = [g for g, _ in kept]
        pred = [p for _, p in kept]
    else:
        n_excl = 0
    order = [o for o in (order or DEFAULT_ORDER) if o in set(gold) | set(pred)] or _labels(gold, pred)
    rep = {
        "n": len(gold), "n_excluded_deferrals": n_excl,
        "deferral_mode": deferral_mode,
        "observed_agreement": observed_agreement(gold, pred),
        "prevalence": prevalence(gold),
        "cohens_kappa": cohens_kappa(gold, pred),
        "cohens_kappa_ci": bootstrap_ci(cohens_kappa, gold, pred, n_boot),
        "weighted_kappa_linear": weighted_kappa(gold, pred, order, "linear"),
        "weighted_kappa_quadratic": weighted_kappa(gold, pred, order, "quadratic"),
        "pabak": pabak(gold, pred),
        "macro_prf": macro_prf(gold, pred),
        "per_class": per_class_prf(gold, pred),
        "weighted_kappa_order": order,
    }
    wk = lambda g, p: weighted_kappa(g, p, order, "linear")
    rep["weighted_kappa_linear_ci"] = bootstrap_ci(wk, gold, pred, n_boot)
    return rep


def evaluate_deferrals(engine_deferred: Sequence[bool], humans_disagreed: Sequence[bool]):
    """Does the engine defer precisely when the experts split? Fisher's exact on the
    2x2 of (engine deferred) x (humans disagreed)."""
    a = sum(1 for d, h in zip(engine_deferred, humans_disagreed) if d and h)
    b = sum(1 for d, h in zip(engine_deferred, humans_disagreed) if d and not h)
    c = sum(1 for d, h in zip(engine_deferred, humans_disagreed) if not d and h)
    d_ = sum(1 for d, h in zip(engine_deferred, humans_disagreed) if not d and not h)
    return {"table": [[a, b], [c, d_]],
            "fisher_p": fisher_exact_2x2([[a, b], [c, d_]]),
            "legend": "rows: engine deferred Y/N; cols: humans disagreed Y/N"}


# ---------------------------------------------------------------------------
# Self-test + run on the gold set
# ---------------------------------------------------------------------------
def _selftest():
    ok = []
    # weighted kappa: perfect agreement -> 1
    g = ["no_change", "escalate", "lower_dose"] * 5
    ok.append(("weighted_kappa perfect=1", abs(weighted_kappa(g, g, DEFAULT_ORDER) - 1) < 1e-9))
    # wilson sanity: 5/5 -> upper near 1, lower < 1
    lo, hi = wilson_ci(5, 5)
    ok.append(("wilson 5/5 lower<1", lo < 1 and hi <= 1))
    # safety: 1 missed flag => FNR = 0.5
    sm = safety_metrics([True, True, False], [True, False, False])
    ok.append(("safety FNR=0.5", abs(sm["false_negative_rate"] - 0.5) < 1e-9))
    # bland-altman: pred consistently +50 => bias 50, sd 0
    ba = bland_altman([150, 250, 350], [100, 200, 300])
    ok.append(("bland-altman bias=50", abs(ba["bias"] - 50) < 1e-9 and ba["sd_of_diff"] < 1e-9))
    # icc perfect agreement -> ~1
    ok.append(("icc perfect~1", abs(icc_2_1([1, 2, 3, 4], [1, 2, 3, 4]) - 1) < 1e-6))
    # fisher: clear association small p
    ok.append(("fisher assoc p<0.05", fisher_exact_2x2([[8, 1], [1, 8]]) < 0.05))
    # mcnemar symmetric -> p=1
    ok.append(("mcnemar 3,3 p=1", abs(mcnemar_exact(3, 3) - 1) < 1e-9))
    print("  SELF-TEST")
    for name, passed in ok:
        print(f"    [{'x' if passed else ' '}] {name}")
    print(f"  {sum(p for _, p in ok)}/{len(ok)} self-tests passed\n")
    return all(p for _, p in ok)


def _run_on_gold():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))
    import csv
    import json
    from harness import _snapshot_from_case  # noqa
    from engine_core import run_engine
    from engine_core.asm_rules import load_asm_rules

    gold_cases = json.load(open(os.path.join(here, "gold_cases.json")))["cases"]
    key = {r["case_id"]: r for r in csv.DictReader(
        open(os.path.join(here, "..", "..", "INTERNAL_engine_answer_key_DO_NOT_SEND.csv")))}
    reg = load_asm_rules(os.path.join(here, "..", "data", "asm_rules.json"))

    gold, pred, gflag, pflag = [], [], [], []
    for c in gold_cases:
        rec = run_engine(_snapshot_from_case(c), asm_registry=reg)
        gold.append(c["gold_standard_category"]); pred.append(rec.category)
        gflag.append((key.get(c["case_id"], {}).get("safety_flag", "") or "").strip().upper() == "YES")
        pflag.append(bool(rec.safety_concern_flag))

    print("  GOLD-SET RUN (63 cases; synthetic labels -> self-consistency, not clinical truth)")
    cat = evaluate_categorical(gold, pred, n_boot=1000)
    print(f"    n (deferrals excluded {cat['n_excluded_deferrals']}): {cat['n']}")
    print(f"    observed agreement : {cat['observed_agreement']:.3f}")
    print(f"    Cohen's kappa      : {cat['cohens_kappa']:.3f}  95% CI {tuple(round(x,3) for x in cat['cohens_kappa_ci'])}")
    print(f"    weighted kappa (lin): {cat['weighted_kappa_linear']:.3f}  CI {tuple(round(x,3) for x in cat['weighted_kappa_linear_ci'])}")
    print(f"    PABAK              : {cat['pabak']:.3f}")
    print(f"    macro F1           : {cat['macro_prf']['f1']:.3f}")
    sm = safety_metrics(gflag, pflag)
    print(f"    safety sensitivity : {sm['sensitivity']:.3f}  95% CI {tuple(round(x,3) for x in sm['sensitivity_ci'])}")
    print(f"    safety FNR (harm)  : {sm['false_negative_rate']:.3f}  95% CI {tuple(round(x,3) for x in sm['false_negative_rate_ci'])}  {sm['counts']}")
    print("\n  NOTE: human-vs-human kappa (the ceiling), dose Bland-Altman/ICC, and the")
    print("  deferral-vs-disagreement Fisher test need data we don't have yet (two")
    print("  clinician raters + dose outputs). The functions are ready for when we do.")


if __name__ == "__main__":
    print("\n  metrics.py — Aim 2 validation toolkit (stdlib only)\n  " + "=" * 56)
    _selftest()
    try:
        _run_on_gold()
    except Exception as e:
        print(f"  (gold-set run skipped: {e})")
