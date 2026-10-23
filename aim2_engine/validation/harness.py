"""
harness.py
----------
Validation harness for the Aim 2 rules engine (spec §9, §13).

Runs the engine over a labeled dataset and reports:
  * concordance (exact-match rate with the gold-standard category)
  * Cohen's kappa  (target >= 0.8 per the proposal)
  * an agreement-based approval proxy + rejection reasons
  * count of safety-concern outputs
  * the engine version (so runs are reproducible)

GOLD LABELS: the shipped `simulated_cases.json` carries PROVISIONAL labels
(the proposal's worked examples + logical expectations) so the pipeline can be
exercised. They are NOT a clinical gold standard. Real validation requires the
physician's chart-review coding (NEEDS_PI_SIGNOFF.md). With provisional labels
this is a self-consistency check of the engine, not evidence of clinical
accuracy.

Run:  python validation/harness.py            # DEFAULT: gold_cases.json (Dr. Barbour's
                                              #   63 reviewed A1-J10 cases) + data/asm_rules.json.
                                              #   This is the headline run (kappa ~0.976).
      python validation/harness.py simulated_cases.json --no-asm
                                              #   the 12 PROVISIONAL proposal examples
                                              #   (self-consistency only; expect low kappa).
      python validation/harness.py cases.json --asm ../data/asm_rules.json
"""

from __future__ import annotations

import json
import os
import sys

# Make engine_core importable when run from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine_core import (                                          # noqa: E402
    run_engine, snapshot_from_interval, PatientContext,
    MedicationContext, PhysicianContingency, RecommendationCategory,
    ENGINE_VERSION,
)
from engine_core.asm_rules import load_asm_rules                  # noqa: E402

CATEGORIES = [c.value for c in RecommendationCategory]


def cohens_kappa(predicted, gold):
    """Cohen's kappa over the fixed category set. Pure stdlib."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must be the same length")
    n = len(predicted)
    if n == 0:
        return float("nan")
    p_o = sum(1 for a, b in zip(predicted, gold) if a == b) / n
    p_e = 0.0
    for cat in CATEGORIES:
        p_pred = sum(1 for a in predicted if a == cat) / n
        p_gold = sum(1 for b in gold if b == cat) / n
        p_e += p_pred * p_gold
    if p_e >= 1.0:
        return 1.0
    return (p_o - p_e) / (1.0 - p_e)


def _snapshot_from_case(case):
    patient = PatientContext(**case.get("patient", {"external_id": "case"}))
    medication = MedicationContext(**case["medication"])
    contingency = PhysicianContingency(**case.get("contingency", {}))
    return snapshot_from_interval(patient, medication, contingency, case.get("answers", {}))


def run_validation(cases, asm_registry=None, verbose=True):
    predicted, gold = [], []
    rejections, safety_count = [], 0

    for case in cases:
        snap = _snapshot_from_case(case)
        rec = run_engine(snap, asm_registry=asm_registry)
        pred = rec.category
        g = case.get("gold_standard_category")
        predicted.append(pred)
        gold.append(g)
        if rec.safety_concern_flag:
            safety_count += 1
        agree = (pred == g)
        if not agree:
            rejections.append({"label": case.get("label", "?"), "gold": g, "engine": pred,
                               "fired_rule": rec.rationale[-1]["rule"] if rec.rationale else None})
        if verbose:
            mark = "OK " if agree else "XX "
            print(f"  {mark} {case.get('label','?')[:54]:54s} gold={g:<20s} engine={pred}")

    n = len(cases)
    matches = sum(1 for a, b in zip(predicted, gold) if a == b)
    concordance = matches / n if n else float("nan")
    kappa = cohens_kappa(predicted, gold)

    # Richer agreement metrics (deferrals excluded; see docs/VALIDATION_PLAN.md).
    # Best-effort: never let the metrics block break the core run.
    metrics_block = {}
    try:
        if _HERE not in sys.path:
            sys.path.insert(0, _HERE)
        from metrics import evaluate_categorical  # noqa: E402 (same dir)
        m = evaluate_categorical(gold, predicted, n_boot=1000)
        metrics_block = {
            "n_excluded_deferrals": m["n_excluded_deferrals"],
            "weighted_kappa_linear": round(m["weighted_kappa_linear"], 4),
            "weighted_kappa_linear_ci": [round(x, 3) for x in m["weighted_kappa_linear_ci"]],
            "cohens_kappa_ci": [round(x, 3) for x in m["cohens_kappa_ci"]],
            "pabak": round(m["pabak"], 4),
            "macro_f1": round(m["macro_prf"]["f1"], 4),
        }
    except Exception as _e:
        metrics_block = {"unavailable": str(_e)}

    report = {
        "engine_version": ENGINE_VERSION,
        "n_cases": n,
        "concordance": round(concordance, 4),
        "cohens_kappa": round(kappa, 4),
        "kappa_target": 0.8,
        "kappa_meets_target": (kappa >= 0.8),
        "approval_proxy_rate": round(concordance, 4),  # agreement used as proxy
        "safety_concern_count": safety_count,
        "metrics": metrics_block,
        "rejections": rejections,
        "gold_label_caveat": ("Gold labels are the PI's picks on SYNTHETIC cases, not a "
                              "held-out chart review. Replace with physician chart-review "
                              "coding for definitive validation (NEEDS_PI_SIGNOFF.md)."),
    }
    if verbose:
        print("\n  ---- VALIDATION REPORT ----")
        print(f"  engine version    : {report['engine_version']}")
        print(f"  cases             : {report['n_cases']}")
        print(f"  concordance       : {report['concordance']}")
        print(f"  Cohen's kappa     : {report['cohens_kappa']}  (target >= 0.8 -> "
              f"{'MET' if report['kappa_meets_target'] else 'NOT MET'})")
        mb = report.get("metrics", {})
        if mb and "unavailable" not in mb:
            print(f"  weighted kappa    : {mb['weighted_kappa_linear']}  95% CI {tuple(mb['weighted_kappa_linear_ci'])}  "
                  f"(deferrals excluded: {mb['n_excluded_deferrals']})")
            print(f"  plain kappa 95% CI: {tuple(mb['cohens_kappa_ci'])}   PABAK {mb['pabak']}   macro-F1 {mb['macro_f1']}")
        print(f"  safety concerns   : {report['safety_concern_count']}")
        if rejections:
            print("  disagreements:")
            for r in rejections:
                print(f"    - {r['label']}: gold={r['gold']} engine={r['engine']} ({r['fired_rule']})")
        print(f"  NOTE: {report['gold_label_caveat']}")
    return report


def main(argv):
    # DEFAULT to the gold-standard set (Dr. Barbour's 63 reviewed cases) + the
    # clinician ASM rules, so the documented `python validation/harness.py`
    # reproduces the headline kappa instead of the 12 provisional proposal cases
    # (which are a self-consistency check only and score low by design).
    default_cases = os.path.join(_HERE, "gold_cases.json")
    default_asm = os.path.join(_ROOT, "data", "asm_rules.json")

    cases_path = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else default_cases

    # ASM registry: load data/asm_rules.json by default (the gold logic needs it);
    # `--asm PATH` overrides, `--no-asm` disables.
    if "--no-asm" in argv:
        asm_path = None
    elif "--asm" in argv:
        asm_path = argv[argv.index("--asm") + 1]
    else:
        asm_path = default_asm

    with open(cases_path) as fh:
        raw = json.load(fh)
    cases = raw["cases"] if isinstance(raw, dict) else raw
    asm_registry = load_asm_rules(asm_path) if asm_path else None

    print(f"Running validation harness (engine {ENGINE_VERSION}) over {len(cases)} "
          f"cases from {os.path.basename(cases_path)}...\n")
    report = run_validation(cases, asm_registry=asm_registry, verbose=True)
    return 0 if report["kappa_meets_target"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
