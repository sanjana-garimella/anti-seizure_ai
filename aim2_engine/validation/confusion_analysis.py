"""
confusion_analysis.py
---------------------
TP / FP / TN / FN analysis of the rule engine against Dr. Barbour's 63 gold
cases. Two evaluations:

  (1) SAFETY-FLAG detection (binary) — the clinically critical one. Positive =
      a safety concern SHOULD be raised (answer key safety_flag == YES). A FALSE
      NEGATIVE here is the dangerous error (missed safety flag).
  (2) RECOMMENDATION category — multiclass; reported as overall accuracy plus
      a one-vs-rest TP/FP/TN/FN table per category.

CAVEAT: gold labels are the PI's picks on SYNTHETIC cases, not a held-out chart
review — this is a self-consistency check, not clinical accuracy evidence.

Run:  cd aim2_engine && python3 validation/confusion_analysis.py
"""
from __future__ import annotations
import csv, json, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from engine_core import (run_engine, snapshot_from_interval, PatientContext,
                         MedicationContext, PhysicianContingency,
                         RecommendationCategory, ENGINE_VERSION)
from engine_core.asm_rules import load_asm_rules

GOLD = json.load(open(os.path.join(_HERE, "gold_cases.json")))["cases"]
KEY = {r["case_id"]: r for r in
       csv.DictReader(open(os.path.join(_HERE, "private",
                       "INTERNAL_engine_answer_key_DO_NOT_SEND.csv")))}
REG = load_asm_rules(os.path.join(_ROOT, "data", "asm_rules.json"))


def predict(case):
    snap = snapshot_from_interval(
        PatientContext(**case.get("patient", {"external_id": "c"})),
        MedicationContext(**case["medication"]),
        PhysicianContingency(**case.get("contingency", {})),
        case.get("answers", {}))
    rec = run_engine(snap, asm_registry=REG)
    return rec.category, bool(rec.safety_concern_flag)


def binary_metrics(tp, fp, tn, fn):
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    spec = tn / (tn + fp) if (tn + fp) else float("nan")
    acc = (tp + tn) / (tp + fp + tn + fn)
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else float("nan")
    return prec, rec, spec, acc, f1


# ---- run engine over all cases --------------------------------------------
rows = []
for c in GOLD:
    cid = c["case_id"]
    pred_cat, pred_flag = predict(c)
    gold_cat = c["gold_standard_category"]
    gold_flag = (KEY.get(cid, {}).get("safety_flag", "") or "").strip().upper() == "YES"
    rows.append((cid, c.get("label", ""), gold_cat, pred_cat, gold_flag, pred_flag))

print(f"Engine {ENGINE_VERSION} — {len(rows)} gold cases\n")

# ===== (1) SAFETY-FLAG confusion matrix ====================================
tp = fp = tn = fn = 0
fn_cases, fp_cases = [], []
for cid, lbl, gc, pc, gflag, pflag in rows:
    if gflag and pflag: tp += 1
    elif (not gflag) and pflag: fp += 1; fp_cases.append((cid, lbl))
    elif (not gflag) and (not pflag): tn += 1
    else: fn += 1; fn_cases.append((cid, lbl))

prec, recl, spec, acc, f1 = binary_metrics(tp, fp, tn, fn)
print("=" * 64)
print("(1) SAFETY-FLAG DETECTION  (positive = safety concern should fire)")
print("=" * 64)
print(f"  Confusion matrix:")
print(f"                     predicted YES   predicted NO")
print(f"    actual YES (5) :      TP={tp:<3d}          FN={fn:<3d}")
print(f"    actual NO (58) :      FP={fp:<3d}          TN={tn:<3d}")
print(f"\n  precision={prec:.3f}  recall/sensitivity={recl:.3f}  "
      f"specificity={spec:.3f}  accuracy={acc:.3f}  F1={f1:.3f}")
if fn_cases:
    print(f"  ⚠ FALSE NEGATIVES (missed safety flag — dangerous): {fn_cases}")
else:
    print("  ✓ Zero false negatives — every gold safety case was flagged.")
if fp_cases:
    print(f"  FALSE POSITIVES (over-flagged): {fp_cases}")

# ===== (2) RECOMMENDATION category (multiclass) ============================
cats = [c.value for c in RecommendationCategory]
exact = sum(1 for r in rows if r[2] == r[3])
print("\n" + "=" * 64)
print("(2) RECOMMENDATION CATEGORY  (multiclass, one-vs-rest)")
print("=" * 64)
print(f"  overall exact-match accuracy: {exact}/{len(rows)} = {exact/len(rows):.3f}\n")
print(f"  {'category':<22s} {'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3}  "
      f"{'prec':>5} {'rec':>5} {'F1':>5}  support")
for cat in cats:
    ctp = sum(1 for r in rows if r[2] == cat and r[3] == cat)
    cfp = sum(1 for r in rows if r[2] != cat and r[3] == cat)
    cfn = sum(1 for r in rows if r[2] == cat and r[3] != cat)
    ctn = len(rows) - ctp - cfp - cfn
    support = sum(1 for r in rows if r[2] == cat)
    p, rc, _, _, f = binary_metrics(ctp, cfp, ctn, cfn)
    pf = f"{p:.2f}" if p == p else "  - "
    rf = f"{rc:.2f}" if rc == rc else "  - "
    ff = f"{f:.2f}" if f == f else "  - "
    print(f"  {cat:<22s} {ctp:>3} {cfp:>3} {cfn:>3} {ctn:>3}  "
          f"{pf:>5} {rf:>5} {ff:>5}  {support}")

# disagreements
print("\n  recommendation disagreements (gold != engine):")
any_d = False
for cid, lbl, gc, pc, gflag, pflag in rows:
    if gc != pc:
        any_d = True
        print(f"    {cid:<4s} gold={gc:<20s} engine={pc:<20s} | {lbl[:46]}")
if not any_d:
    print("    (none)")

print("\nCAVEAT: synthetic gold labels — self-consistency check, not clinical "
      "accuracy. Needs physician chart-review coding for definitive validation.")
