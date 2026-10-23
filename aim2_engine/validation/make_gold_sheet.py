"""
make_gold_sheet.py
------------------
Generate a set of synthetic, de-identified interval cases and a clinician
labeling sheet for gold-standard validation (no patient data needed).

Writes:
  validation/cases.json                      - the cases (case_id + inputs)
  validation/gold_standard_labeling_sheet.csv - open in Excel; the clinician
                                                reads each case and writes their
                                                recommendation (BLIND - the
                                                engine's answer is not shown).

Then run validation/score_gold.py to compare the clinician's labels to the
engine and compute Cohen's kappa.

Run:  python3 validation/make_gold_sheet.py
"""

import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from engine_core.humanize import (humanize, humanize_list, ADHERENCE_HUMAN,
                                   SEIZURE_TYPE_HUMAN, IMPACT_HUMAN, OVERALL_HUMAN,
                                   PREFERENCE_HUMAN, RASH_HUMAN)

_AD = {"almost_never": "almost_never_misses", "sometimes": "sometimes_misses", "stopped": "stopped"}
_PREF = {"no_preference": "no_strong_preference", "change_dose": "change_dose",
         "different_medicine": "try_different_medicine", "other": "other"}


def C(cid, med, age, wt, ans, can_increase=True, switch_to=None, etiology="focal"):
    return {"case_id": cid,
            "patient_context": {"external_id": cid, "age_years": age, "weight_kg": wt,
                                "epilepsy_type": etiology, "medication": med,
                                "dose_amount": 250, "dose_unit": "mg", "frequency_per_day": 2,
                                "can_increase_dose": can_increase, "can_switch_to": switch_to},
            "answers": ans}


# A spread of clinically interpretable scenarios across all five categories,
# safety, adherence, preference, and a range of medications/ages.
CASES = [
    C("C01", "levetiracetam", 8, 30, {"Q2": "almost_never", "Q3": "yes", "Q4": ["sudden_jerks"], "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "still_increasing", "Q11": "worse"}),
    C("C02", "oxcarbazepine", 11, 38, {"Q2": "almost_never", "Q3": "yes", "Q4": ["focal"], "Q5": 1, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["sleepiness"], "Q10b_impact": "small", "Q10a": "still_increasing"}),
    C("C03", "carbamazepine", 13, 50, {"Q2": "almost_never", "Q3": "yes", "Q4": ["focal"], "Q5": 3, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["behavior"], "Q10b_impact": "big_wants_change", "Q10a": "still_increasing"}, switch_to="oxcarbazepine"),
    C("C04", "levetiracetam", 9, 31, {"Q2": "sometimes", "Q3": "yes", "Q7": "no", "Q10a": "still_increasing"}),
    C("C05", "valproic_acid", 6, 21, {"Q2": "almost_never", "Q3": "yes", "Q5": 1, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "still_increasing", "Q12": "change_dose"}),
    C("C06", "levetiracetam", 14, 55, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "at_goal"}, can_increase=False, switch_to="brivaracetam"),
    C("C07", "topiramate", 12, 44, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "at_goal"}, can_increase=False),
    C("C08", "levetiracetam", 10, 34, {"Q2": "almost_never", "Q3": "no", "Q9": ["sleepiness", "irritability"], "Q10b_impact": "small", "Q10a": "still_increasing"}),
    C("C09", "lamotrigine", 12, 45, {"Q2": "almost_never", "Q3": "no", "Q9": ["dizziness"], "Q10b_impact": "big_wants_change", "Q10a": "at_goal"}, switch_to="levetiracetam"),
    C("C10", "ethosuximide", 8, 28, {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing", "Q11": "better"}, etiology="absence"),
    C("C11", "lacosamide", 15, 60, {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),
    C("C12", "clobazam", 5, 18, {"Q2": "almost_never", "Q3": "no", "Q10a": "not_sure"}, etiology="generalized"),
    C("C13", "lamotrigine", 7, 24, {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "blisters", "Q10a": "at_goal"}, etiology="generalized"),
    C("C14", "oxcarbazepine", 9, 30, {"Q2": "stopped", "Q3": "yes", "Q7": "no", "Q10a": "still_increasing"}),
    C("C15", "zonisamide", 10, 33, {"Q2": "almost_never", "Q3": "no", "Q9": ["nausea"], "Q10b_impact": "small", "Q10a": "at_goal"}),
    C("C16", "valproic_acid", 11, 40, {"Q2": "almost_never", "Q3": "yes", "Q5": 4, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["sleepiness"], "Q10b_impact": "small", "Q10a": "still_increasing"}),
    C("C17", "carbamazepine", 14, 52, {"Q2": "almost_never", "Q3": "no", "Q9": ["dizziness"], "Q10b_impact": "medium_big", "Q10a": "still_increasing"}, switch_to="oxcarbazepine"),
    C("C18", "levetiracetam", 6, 20, {"Q2": "almost_never", "Q3": "yes", "Q5": 1, "Q5_unit": "per_month", "Q7": "yes", "Q10a": "still_increasing"}),
    C("C19", "topiramate", 13, 47, {"Q2": "sometimes", "Q3": "yes", "Q7": "no", "Q9": ["behavior"], "Q10b_impact": "small", "Q10a": "still_increasing"}),
    C("C20", "lamotrigine", 16, 62, {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "about_same", "Q12": "no_preference"}),
    C("C21", "brivaracetam", 12, 43, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "still_increasing"}),
    C("C22", "ethosuximide", 9, 29, {"Q2": "almost_never", "Q3": "yes", "Q5": 5, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["nausea"], "Q10b_impact": "big_wants_change", "Q10a": "at_goal"}, switch_to="valproic_acid", etiology="absence"),
    C("C23", "oxcarbazepine", 7, 25, {"Q2": "almost_never", "Q3": "no", "Q9": ["sleepiness"], "Q10b_impact": "no_impact", "Q10a": "at_goal"}),
    C("C24", "levetiracetam", 10, 35, {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing", "Q11": "better", "Q12": "no_preference"}),
    C("C25", "valproic_acid", 8, 27, {"Q2": "almost_never", "Q3": "no", "Q9": ["irritability"], "Q9a": "small", "Q10b_impact": "small", "Q10a": "at_goal"}),
    C("C26", "zonisamide", 12, 46, {"Q2": "almost_never", "Q3": "yes", "Q5": 1, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["dizziness"], "Q10b_impact": "big_wants_change", "Q10a": "at_goal"}, switch_to="lacosamide"),
    C("C27", "lacosamide", 14, 54, {"Q2": "sometimes", "Q3": "yes", "Q7": "no", "Q10a": "at_goal"}),
    C("C28", "clobazam", 6, 19, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "still_increasing", "Q12": "change_dose"}, etiology="generalized"),
    C("C29", "lamotrigine", 11, 41, {"Q2": "almost_never", "Q3": "no", "Q10a": "not_sure", "Q11": "not_sure"}),
    C("C30", "carbamazepine", 9, 31, {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "blisters", "Q10a": "still_increasing"}),
    C("C31", "levetiracetam", 5, 18, {"Q2": "almost_never", "Q3": "yes", "Q5": 1, "Q5_unit": "per_month", "Q7": "yes", "Q10a": "still_increasing"}, etiology="generalized"),
    C("C32", "brivaracetam", 16, 64, {"Q2": "almost_never", "Q3": "no", "Q9": ["irritability"], "Q10b_impact": "big_wants_change", "Q10a": "still_increasing"}, switch_to="lacosamide"),
    C("C33", "topiramate", 10, 33, {"Q2": "almost_never", "Q3": "no", "Q9": ["behavior"], "Q10b_impact": "medium_big", "Q10a": "at_goal"}, switch_to="valproic_acid"),
    C("C34", "carbamazepine", 12, 46, {"Q2": "almost_never", "Q3": "yes", "Q5": 3, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["dizziness"], "Q10b_impact": "big_wants_change", "Q10a": "still_increasing"}),
    C("C35", "lamotrigine", 14, 55, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "still_increasing", "Q12": "different_medicine"}),
    C("C36", "valproic_acid", 9, 30, {"Q2": "sometimes", "Q3": "yes", "Q7": "yes", "Q5": 1, "Q5_unit": "per_week", "Q10a": "still_increasing"}),
    C("C37", "oxcarbazepine", 8, 27, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "at_goal"}),
    C("C38", "zonisamide", 13, 48, {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing"}, can_increase=False),
    C("C39", "ethosuximide", 7, 25, {"Q2": "almost_never", "Q3": "no", "Q9": ["nausea"], "Q10b_impact": "small", "Q10a": "still_increasing"}, etiology="absence"),
    C("C40", "lacosamide", 15, 58, {"Q2": "almost_never", "Q3": "yes", "Q5": 1, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["sleepiness"], "Q10b_impact": "small", "Q10a": "still_increasing"}),
    C("C41", "clobazam", 6, 20, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q10a": "still_increasing"}, etiology="generalized"),
    C("C42", "lamotrigine", 11, 40, {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "small", "Q10b_impact": "small", "Q10a": "at_goal"}),
    C("C43", "levetiracetam", 10, 34, {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "worse", "Q12": "no_preference"}),
    C("C44", "carbamazepine", 9, 31, {"Q2": "stopped", "Q3": "yes", "Q7": "no", "Q10a": "still_increasing"}),
    C("C45", "topiramate", 13, 47, {"Q2": "almost_never", "Q3": "no", "Q9": ["behavior"], "Q10b_impact": "big_wants_change", "Q10a": "still_increasing"}),
    C("C46", "oxcarbazepine", 12, 44, {"Q2": "almost_never", "Q3": "yes", "Q4": ["focal"], "Q5": 1, "Q5_unit": "per_week", "Q6": "new_type", "Q7": "yes", "Q10a": "still_increasing"}),
    C("C47", "valproic_acid", 8, 28, {"Q2": "almost_never", "Q3": "yes", "Q5": 2, "Q5_unit": "per_week", "Q7": "yes", "Q9": ["sleepiness"], "Q10b_impact": "medium_big", "Q10a": "still_increasing"}),
    C("C48", "zonisamide", 11, 42, {"Q2": "almost_never", "Q3": "no", "Q10a": "not_sure", "Q11": "not_sure"}),
]


def summarize(case):
    a = case["answers"]
    parts = [f"Medication: {case['patient_context']['medication'].replace('_',' ').title()}",
             f"age {case['patient_context']['age_years']:g}"]
    parts.append("medicine taken: " + humanize(_AD.get(a.get("Q2", "almost_never")), ADHERENCE_HUMAN))
    if a.get("Q3") == "yes":
        s = "seizures: yes"
        if a.get("Q5"):
            s += f" ({a['Q5']} {humanize(a.get('Q5_unit'))})"
        if a.get("Q4"):
            s += "; " + humanize_list(a["Q4"], SEIZURE_TYPE_HUMAN)
        if a.get("Q7") == "no":
            s += "; doses were missed beforehand"
        parts.append(s)
    else:
        parts.append("seizures: none")
    if a.get("Q9") or a.get("Q10b_impact") or a.get("Q9a"):
        se = "side effects: " + (humanize_list(a.get("Q9", [])) or "yes")
        if a.get("Q9a"):
            se += f"; rash: {humanize(a['Q9a'], RASH_HUMAN)}"
        if a.get("Q10b_impact"):
            se += f"; impact: {humanize(a['Q10b_impact'], IMPACT_HUMAN)}"
        parts.append(se)
    else:
        parts.append("side effects: none")
    parts.append("dose status: " + humanize(a.get("Q10a", "not_sure")))
    if a.get("Q11"):
        parts.append("overall: " + humanize(a["Q11"], OVERALL_HUMAN))
    if a.get("Q12"):
        parts.append("family preference: " + humanize(_PREF.get(a["Q12"]), PREFERENCE_HUMAN))
    ctx = case["patient_context"]
    if ctx.get("can_increase_dose") is False:
        parts.append("note: physician has NOT approved a further increase")
    if ctx.get("can_switch_to"):
        parts.append(f"note: approved switch option = {ctx['can_switch_to'].replace('_',' ').title()}")
    return "; ".join(parts)


def main():
    cases_out = [{**c, "summary": summarize(c)} for c in CASES]
    with open(os.path.join(_HERE, "cases.json"), "w") as f:
        json.dump({"cases": cases_out}, f, indent=2)

    sheet = os.path.join(_HERE, "gold_standard_labeling_sheet.csv")
    with open(sheet, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "what_the_family_reported", "your_recommendation", "notes"])
        for c in CASES:
            w.writerow([c["case_id"], summarize(c), "", ""])
    print(f"Wrote {len(CASES)} cases -> cases.json")
    print(f"Wrote blank labeling sheet -> {os.path.basename(sheet)}")
    print("Allowed values for 'your_recommendation' (see README_VALIDATION.md):")
    print("  increase the dose | increase more slowly | switch medicine | no change | need more info")


if __name__ == "__main__":
    main()
