"""
score_gold.py
-------------
Compare the clinician's gold-standard labels (filled in
gold_standard_labeling_sheet.csv) to the engine's recommendation and report
Cohen's kappa (target >= 0.8), concordance, disagreements, and safety counts.

Run (after the clinician fills the sheet):
    python3 validation/score_gold.py
"""

import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from engine_core import (run_engine, snapshot_from_interval, PatientContext,
                         MedicationContext, PhysicianContingency, DrugClassifier,
                         ENGINE_VERSION)
from engine_core.asm_rules import load_asm_rules
from harness import cohens_kappa

_ASM = os.path.join(os.path.dirname(_HERE), "data", "asm_rules.json")
ASM_REGISTRY = load_asm_rules(_ASM) if os.path.isfile(_ASM) else {}
CLASSIFIER = DrugClassifier(default_by="mechanism")

# Plain clinician labels -> engine category codes (case-insensitive, trimmed).
GOLD_MAP = {
    "increase the dose": "escalate", "increase": "escalate", "escalate": "escalate", "dose increase": "escalate",
    "increase more slowly": "slower_uptitration", "slower": "slower_uptitration", "slow down": "slower_uptitration",
    "slower uptitration": "slower_uptitration", "increase slowly": "slower_uptitration",
    "switch medicine": "switch_medication", "switch": "switch_medication", "switch medication": "switch_medication",
    "change medicine": "switch_medication", "change medication": "switch_medication",
    "no change": "no_change", "keep same": "no_change", "keep the same": "no_change", "continue": "no_change",
    "no medication change": "no_change",
    "need more info": "insufficient_data", "more info": "insufficient_data", "insufficient": "insufficient_data",
    "insufficient data": "insufficient_data", "review": "insufficient_data", "unsure": "insufficient_data",
    "not sure": "insufficient_data",
}


def build(pc, ans):
    return snapshot_from_interval(
        PatientContext(external_id=pc["external_id"], age_years=pc.get("age_years"),
                       weight_kg=pc.get("weight_kg"), epilepsy_etiology=pc.get("epilepsy_type")),
        MedicationContext(medication=pc.get("medication"), dose_amount=pc.get("dose_amount"),
                          dose_unit=pc.get("dose_unit", "mg"), frequency_per_day=pc.get("frequency_per_day")),
        PhysicianContingency(can_increase_dose=pc.get("can_increase_dose"), can_switch_to=pc.get("can_switch_to")),
        ans)


def main():
    cases = json.load(open(os.path.join(_HERE, "cases.json")))["cases"]
    sheet = os.path.join(_HERE, "gold_standard_labeling_sheet.csv")
    if not os.path.isfile(sheet):
        print("No labeling sheet found. Run make_gold_sheet.py first."); return 1
    labels = {}
    with open(sheet, newline="") as f:
        for row in csv.DictReader(f):
            labels[row["case_id"]] = (row.get("your_recommendation") or "").strip()

    predicted, gold, rows, unrecognized, unlabeled, safety = [], [], [], [], [], 0
    for c in cases:
        cid = c["case_id"]
        raw = labels.get(cid, "")
        rec = run_engine(build(c["patient_context"], c["answers"]),
                         asm_registry=ASM_REGISTRY, drug_classifier=CLASSIFIER)
        if rec.safety_concern_flag:
            safety += 1
        if not raw:
            unlabeled.append(cid); continue
        g = GOLD_MAP.get(raw.lower())
        if not g:
            unrecognized.append((cid, raw)); continue
        predicted.append(rec.category); gold.append(g)
        rows.append((cid, g, rec.category, g == rec.category))

    n = len(predicted)
    report = ["AIM 2 VALIDATION REPORT (clinician gold standard vs. engine)",
              f"engine version: {ENGINE_VERSION}",
              f"cases total: {len(cases)} | labeled+scored: {n} | unlabeled: {len(unlabeled)} | "
              f"unrecognized labels: {len(unrecognized)}", ""]
    if n:
        matches = sum(1 for _, g, p, ok in rows if ok)
        conc = matches / n
        kappa = cohens_kappa(predicted, gold)
        report += [f"concordance (exact agreement): {conc:.3f}  ({matches}/{n})",
                   f"Cohen's kappa: {kappa:.3f}   (target >= 0.80 -> "
                   f"{'MET' if kappa >= 0.8 else 'NOT MET'})",
                   f"safety-concern outputs among all cases: {safety}", ""]
        dis = [r for r in rows if not r[3]]
        if dis:
            report.append("Disagreements (clinician -> engine):")
            report += [f"  {cid}: clinician={g}  engine={p}" for cid, g, p, _ in dis]
        else:
            report.append("No disagreements on scored cases.")
    else:
        report.append("No scored cases yet - fill 'your_recommendation' in the sheet.")
    if unrecognized:
        report += ["", "Unrecognized labels (please use an allowed value):"]
        report += [f"  {cid}: '{raw}'" for cid, raw in unrecognized]
    if unlabeled:
        report += ["", f"Unlabeled cases: {', '.join(unlabeled)}"]

    text = "\n".join(report)
    out = os.path.join(_HERE, "validation_report.txt")
    with open(out, "w") as f:
        f.write(text + "\n")
    print(text)
    print(f"\n(report saved to {os.path.basename(out)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
