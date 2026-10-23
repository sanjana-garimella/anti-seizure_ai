"""
build_review_cases_v3.py  (2026-07-16)
--------------------------------------
Round-3 SYNTHETIC interval-survey cases for Dr. Barbour's review, generated AFTER
the 2026-07-16 changes. 100% synthetic - no PHI. NEW patients/scenarios (no repeats
of the v1/v2 A1-K3 set); deliberately weighted to the areas where her feedback /
sign-off is most useful:

  * the reconciled breakthrough tables across urgency tier x phase x 6-level side
    effect (esp. Level 3 "medium, a lot", Level 4 "big", Level 5 "not a fit", and
    the at-max fallbacks) - the cells the earlier rounds barely touched;
  * F1 "assume urgent when a frequency is given but no duration" (+ the bare
    no-info boundary that still defers);
  * B3 "end +1/+2 higher" computed from the family-reported planned end dose;
  * Q3 no-seizures Level-3 dosing switch by phase (TID only at steady state);
  * D1 concise no-change / adherence summaries (+ HLA advisory dropped on no-change);
  * emergent-tier protocols, dose-ceiling, and safety (SJS / seizure emergency).

NON-NEGOTIABLE: every recommendation, AI summary, family message, and safety flag
is produced by the RULE-BASED engine (engine_core) via the exact production path
(clinic.build_snapshot -> run_engine -> redflag scan -> triage). This file
fabricates NO clinical decision; every output is a DRAFT (verified:false) pending
PI sign-off (NEEDS_PI_SIGNOFF.md). In-folder only (confidentiality rule #1).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "aim2_engine")
sys.path.insert(0, ENGINE)

import clinic                       # noqa: E402
from engine_core import run_engine  # noqa: E402


def epi(look, duration, freq_bucket, **extra):
    d = {"look": list(look), "duration": duration, "frequency_bucket": freq_bucket}
    d.update(extra)
    return d


def pt(cid, age, wt, drug, amt, freq, *, can_inc=True, unit="mg", conc=None,
       etype="focal", switch=None):
    d = dict(external_id=cid, age=age, weight=wt, epilepsy_type=etype, medication=drug,
             dose_amount=amt, dose_unit=unit, frequency=freq, can_increase=can_inc,
             switch_to=switch)
    if conc is not None:
        d["concentration"] = conc
    return d


# Frequency->tier reference (per Barbour's thresholds): focal/other >=1/wk urgent,
# >=1/day emergent; generalized >=1/4wk urgent, >=1/wk emergent. Phase: titrating =
# still_increasing; holding = at goal, on dose <2 wk; steady = at goal, >2 wk.
BT = {"Q2": "almost_never", "Q3": "yes"}
TIT = {"Q10a": "still_increasing"}
HOLD = {"Q10a": "at_goal", "Q10c_time": "under_2_weeks"}
STDY = {"Q10a": "at_goal", "Q10c_time": "over_2_weeks"}

CASES = [
    # ============ U. URGENT tier x phase x 6-level side effect ================
    ("U1", "titration", "Urgent bt (focal weekly), titrating, no SE -> quicken, end +1",
     pt("U1", 9, 28, "levetiracetam", 400, 2), {**BT, **TIT,
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U2", "critical_se", "Urgent bt + medium-tolerable SE (L2), titrating -> quicken (urgency drives)",
     pt("U2", 11, 34, "oxcarbazepine", 300, 2), {**BT, **TIT, "Q9": ["dizziness"],
      "Q10b_impact": "medium_tolerable", "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U3", "critical_se", "Urgent bt + medium-A-LOT SE (L3), titrating -> switch dosing + end +1, labs",
     pt("U3", 13, 44, "valproic_acid", 250, 2), {**BT, **TIT, "Q9": ["sleepiness"],
      "Q10b_impact": "medium_alot", "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U4", "critical_se", "Urgent bt + BIG SE (L4), titrating -> lower + add second med",
     pt("U4", 10, 30, "topiramate", 100, 2), {**BT, **TIT, "Q9": ["acting_confused"],
      "Q10b_impact": "big", "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U5", "critical_se", "Urgent bt + NOT-A-FIT SE (L5) -> switch medicine",
     pt("U5", 8, 26, "zonisamide", 100, 2), {**BT, **TIT, "Q9": ["irritability"],
      "Q10b_impact": "not_a_fit", "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U6", "titration", "Urgent bt, HOLDING (at goal <2wk), no SE -> make 2 increases",
     pt("U6", 12, 40, "lamotrigine", 100, 2), {**BT, **HOLD,
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U7", "titration", "Urgent bt, STEADY (at goal >2wk), no SE -> make 2; if max add",
     pt("U7", 14, 55, "brivaracetam", 50, 2), {**BT, **STDY,
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("U8", "safety_ceiling", "Urgent bt at MAX dose, helping -> add second med (fallback)",
     pt("U8", 8, 26, "levetiracetam", 780, 2, can_inc=False), {**BT, **STDY, "Q11": "better",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly", count_4wk=12)]}),

    # ============ E. EMERGENT tier (daily / generalized) =====================
    ("EM1", "titration", "Emergent bt (generalized daily), titrating, no SE -> quicken, end +2",
     pt("EM1", 7, 22, "valproic_acid", 200, 2, etype="generalized"),
     {**BT, **TIT, "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("EM2", "critical_se", "Emergent bt + medium-A-LOT SE (L3), titrating -> switch dosing + end +2",
     pt("EM2", 9, 28, "oxcarbazepine", 300, 2, etype="generalized"),
     {**BT, **TIT, "Q9": ["sleepiness"], "Q10b_impact": "medium_alot",
      "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("EM3", "critical_se", "Emergent bt + BIG SE (L4) -> dose decreases + add second med",
     pt("EM3", 11, 36, "topiramate", 150, 2, etype="generalized"),
     {**BT, **TIT, "Q9": ["acting_confused"], "Q10b_impact": "big",
      "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("EM4", "critical_se", "Emergent bt + NOT-A-FIT (L5) -> switch + urgent visit",
     pt("EM4", 6, 20, "levetiracetam", 300, 2, etype="generalized"),
     {**BT, **TIT, "Q9": ["irritability"], "Q10b_impact": "not_a_fit",
      "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("EM5", "safety_ceiling", "Emergent bt at MAX, no SE -> add second med (fallback)",
     pt("EM5", 8, 26, "valproic_acid", 500, 2, can_inc=False, etype="generalized"),
     {**BT, **TIT, "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("EM6", "titration", "Emergent bt, STEADY (at goal >2wk) -> make 2; if exceeds max keep/add",
     pt("EM6", 13, 48, "lamotrigine", 150, 2, etype="generalized"),
     {**BT, **STDY, "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("EM7", "safety_ceiling", "Emergent whole-body >3 min -> seizure-emergency safety flag",
     pt("EM7", 10, 32, "valproic_acid", 300, 2, etype="generalized"),
     {**BT, **TIT, "seizure_episodes": [epi(["whole_body"], "over_3min", "weekly")],
      "urgent_safety": ["whole_body_over_3min"]}),

    # ============ N. NO-SEIZURES 6-level (Q3 TID by phase) ====================
    ("N1", "critical_se", "No seizures + medium-tolerable (L2), titrating -> slow increases",
     pt("N1", 9, 28, "topiramate", 75, 2), {"Q2": "almost_never", "Q3": "no", **TIT,
      "Q9": ["sleepiness"], "Q10b_impact": "medium_tolerable"}),
    ("N2", "critical_se", "No seizures + medium-A-LOT (L3), TITRATING -> pause + switch dosing (uneven/wait, NO TID)",
     pt("N2", 12, 40, "levetiracetam", 500, 2), {"Q2": "almost_never", "Q3": "no", **TIT,
      "Q9": ["irritability"], "Q10b_impact": "medium_alot"}),
    ("N3", "critical_se", "No seizures + medium-A-LOT (L3), HOLDING -> stay + switch dosing (NO TID)",
     pt("N3", 12, 40, "levetiracetam", 500, 2), {"Q2": "almost_never", "Q3": "no", **HOLD,
      "Q9": ["irritability"], "Q10b_impact": "medium_alot"}),
    ("N4", "critical_se", "No seizures + medium-A-LOT (L3), STEADY -> stay + switch dosing (TID OK)",
     pt("N4", 12, 40, "levetiracetam", 500, 2), {"Q2": "almost_never", "Q3": "no", **STDY,
      "Q9": ["irritability"], "Q10b_impact": "medium_alot"}),
    ("N5", "critical_se", "No seizures + BIG (L4) -> lower dose + labs",
     pt("N5", 10, 32, "valproic_acid", 400, 2), {"Q2": "almost_never", "Q3": "no", **STDY,
      "Q9": ["sleepiness"], "Q10b_impact": "big"}),
    ("N6", "critical_se", "No seizures + NOT-A-FIT (L5) -> switch medicine",
     pt("N6", 8, 26, "zonisamide", 100, 2), {"Q2": "almost_never", "Q3": "no", **STDY,
      "Q9": ["irritability"], "Q10b_impact": "not_a_fit"}),
    ("N7", "critical_se", "No seizures + medium-A-LOT (L3) on uneven-eligible drug -> uneven dosing",
     pt("N7", 11, 36, "oxcarbazepine", 300, 2), {"Q2": "almost_never", "Q3": "no", **TIT,
      "Q9": ["dizziness"], "Q10b_impact": "medium_alot"}),
    ("N8", "titration", "No seizures + small SE (L1), titrating -> continue on schedule",
     pt("N8", 7, 23, "lamotrigine", 25, 2), {"Q2": "almost_never", "Q3": "no", **TIT,
      "Q9": ["sleepiness"], "Q10b_impact": "small"}),

    # ============ M. MISSING-DURATION (F1 assume-urgent) + boundary ==========
    ("M1", "defer", "Bt frequency given (focal weekly), NO duration, titrating -> assume urgent",
     pt("M1", 10, 30, "oxcarbazepine", 300, 2), {**BT, **TIT, "Q4": ["focal"],
      "Q5": 2, "Q5_unit": "per_week"}),
    ("M2", "defer", "Bt frequency given, AT GOAL, no duration -> assume urgent (steady row)",
     pt("M2", 12, 40, "levetiracetam", 750, 2), {**BT, **STDY, "Q4": ["focal"],
      "Q5": 3, "Q5_unit": "per_week"}),
    ("M3", "defer", "Bt frequency given, at MAX, no duration -> assume urgent; at max fallback",
     pt("M3", 8, 26, "levetiracetam", 780, 2, can_inc=False), {**BT, **STDY, "Q4": ["focal"],
      "Q5": 2, "Q5_unit": "per_week"}),
    ("M4", "defer", "Bt reported but NEITHER frequency NOR duration -> still defer (safe boundary)",
     pt("M4", 9, 28, "topiramate", 100, 2), {**BT, **TIT, "Q4": ["focal"]}),
    ("M5", "defer", "Bt frequency given + medium SE, no duration -> (medium-SE missing-duration path)",
     pt("M5", 11, 36, "valproic_acid", 250, 2), {**BT, **TIT, "Q4": ["focal"],
      "Q5": 2, "Q5_unit": "per_week", "Q9": ["sleepiness"], "Q10b_impact": "medium_tolerable"}),

    # ============ P. PLANNED END DOSE (B3 compute) ===========================
    ("P1", "titration", "Urgent titrating + planned end dose 1500 -> end +1 higher (computed)",
     pt("P1", 10, 32, "levetiracetam", 500, 2), {**BT, **TIT,
      "Q10f_planned_end_mg_per_day": 1500, "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("P2", "titration", "Emergent titrating + planned end dose 900 -> end +2 higher (computed)",
     pt("P2", 8, 26, "valproic_acid", 300, 2, etype="generalized"), {**BT, **TIT,
      "Q10f_planned_end_mg_per_day": 900,
      "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=30)]}),
    ("P3", "titration", "Urgent titrating + planned end dose near max -> end +1 reaches ceiling",
     pt("P3", 12, 40, "oxcarbazepine", 600, 2), {**BT, **TIT,
      "Q10f_planned_end_mg_per_day": 1800, "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("P4", "titration", "Urgent titrating + planned end dose, medium-tolerable SE -> end +1 higher",
     pt("P4", 11, 34, "levetiracetam", 500, 2), {**BT, **TIT, "Q9": ["dizziness"],
      "Q10b_impact": "medium_tolerable", "Q10f_planned_end_mg_per_day": 1200,
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    # ============ K. CONCISE no-change / adherence (D1) ======================
    ("CN1", "adherence", "Bt coincides with missed doses -> support adherence, concise no-change",
     pt("CN1", 11, 36, "oxcarbazepine", 300, 2), {"Q2": "sometimes", "Q3": "yes", **TIT,
      "Q7": "no", "seizure_episodes": [epi(["focal"], "1_3min", "weekly")],
      "seizure_triggers": ["missed_dose"], "seizure_triggers_all": True}),
    ("CN2", "titration", "Seizure-free, no SE, titrating -> concise no-change (continue)",
     pt("CN2", 9, 28, "lamotrigine", 50, 2), {"Q2": "almost_never", "Q3": "no", **TIT, "Q11": "better"}),
    ("CN3", "titration", "Seizure-free at goal, small manageable SE -> no change",
     pt("CN3", 13, 45, "levetiracetam", 750, 2), {"Q2": "almost_never", "Q3": "no", **STDY,
      "Q9": ["sleepiness"], "Q10b_impact": "small"}),
    ("CN4", "adherence", "Partial adherence, seizure-free -> no change + adherence support",
     pt("CN4", 10, 30, "valproic_acid", 300, 2), {"Q2": "sometimes", "Q3": "no", **TIT}),

    # ============ Z. AT-MAX fallbacks, ceilings, safety, edges ===============
    ("Z1", "safety_ceiling", "At max, helping, ongoing bt (no SE) -> add second med",
     pt("Z1", 9, 28, "topiramate", 200, 2, can_inc=False), {**BT, **STDY, "Q11": "better",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly", count_4wk=8)]}),
    ("Z2", "safety_ceiling", "At max, NOT helping, urgent bt -> engine ADDS per table (FEEDBACK: add vs switch a not-helping drug at max?)",
     pt("Z2", 12, 40, "topiramate", 200, 2, can_inc=False, switch="valproic_acid"),
     {**BT, **STDY, "Q11": "about_same", "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("Z3", "critical_se", "Bt + intolerable SE (family wants change) -> switch",
     pt("Z3", 10, 32, "levetiracetam", 500, 2, switch="lamotrigine"),
     {**BT, **TIT, "Q9": ["irritability"], "Q10c_impact": "big_wants_change", "Q12": "different_medicine",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("Z4", "titration", "Bt, family prefers a slower/changed dose -> slower uptitration",
     pt("Z4", 11, 34, "oxcarbazepine", 300, 2), {**BT, **TIT, "Q12": "change_dose",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("Z5", "defer", "Side-effect impact answer not interpretable -> defer to physician",
     pt("Z5", 9, 28, "lamotrigine", 50, 2), {"Q2": "almost_never", "Q3": "no", **TIT,
      "Q9": ["sleepiness"], "Q10b_impact": "not_sure_weird_token"}),
    ("Z6", "safety_ceiling", "Reported dose ABOVE the recommended maximum -> dose-over-max safety note",
     pt("Z6", 8, 26, "levetiracetam", 2000, 2), {"Q2": "almost_never", "Q3": "no", **STDY}),
    ("Z7", "critical_se", "Spreading/blistering rash on lamotrigine -> STOP + switch (SJS safety)",
     pt("Z7", 10, 32, "lamotrigine", 50, 2, switch="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", **TIT, "Q9": ["rash"], "Q9a": "blisters"}),
    ("Z8", "critical_se", "Small rash on oxcarbazepine + missed doses -> small-rash workup / switch",
     pt("Z8", 9, 28, "oxcarbazepine", 300, 2, switch="levetiracetam"),
     {"Q2": "sometimes", "Q3": "no", **TIT, "Q9": ["rash"], "Q9a": "small"}),
    ("Z9", "switch", "Ethosuximide (absence-only) reported with focal seizures -> switch (indication)",
     pt("Z9", 7, 23, "ethosuximide", 250, 2, etype="absence", switch="valproic_acid"),
     {**BT, **TIT, "Q4": ["focal"], "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),
    ("Z10", "adherence", "Stopped medicine due to side effects -> switch + adherence support",
     pt("Z10", 12, 40, "topiramate", 100, 2, switch="lamotrigine"),
     {"Q2": "stopped", "Q2b": "side_effects", "Q3": "no", **TIT}),
    ("Z11", "titration", "Absence, short/infrequent bt while titrating -> continue plan",
     pt("Z11", 8, 26, "ethosuximide", 250, 2, etype="absence"),
     {**BT, **TIT, "Q4": ["absence"], "seizure_episodes": [epi(["absence"], "under_1min", "monthly")]}),
    ("Z12", "critical_se", "Helping but dose-limited by SE, ongoing bt -> add second med",
     pt("Z12", 11, 36, "lamotrigine", 100, 2), {**BT, **TIT, "Q11": "better", "Q9": ["dizziness"],
      "Q10b_impact": "medium_tolerable", "Q10d_dose_limited_by_se": "yes",
      "seizure_episodes": [epi(["focal"], "under_1min", "weekly")]}),
]


def run_all():
    asm = clinic.current_asm()
    rf = clinic.current_redflag()
    out, counts = [], {}
    for cid, group, scenario, patient, answers in CASES:
        rec = run_engine(clinic.build_snapshot(patient, answers),
                         asm_registry=asm, drug_classifier=clinic.CLASSIFIER,
                         physician_name="your care team")
        rf_cats = sorted({fl.category for fl in rf.scan(answers["Q13"])}) \
            if rf and answers.get("Q13") else []
        priority, reasons = clinic.triage(rec, rf_cats)
        d = rec.to_dict()
        counts[d["category"]] = counts.get(d["category"], 0) + 1
        out.append({"case_id": cid, "group": group, "scenario": scenario,
                    "priority": priority, "triage_reasons": reasons,
                    "redflag_categories": rf_cats, "patient": patient,
                    "answers": answers, "engine_output": d})
    return out, counts


def _by(cases, key):
    c = {}
    for x in cases:
        c[x[key]] = c.get(x[key], 0) + 1
    return c


def main():
    cases, counts = run_all()
    payload = {
        "_README": ("Round-3 synthetic interval-survey cases for Dr. Barbour's review of the "
                    "Aim 2 rule-based engine, generated AFTER the 2026-07-16 changes. 100% "
                    "synthetic - no PHI. NEW patients/scenarios (no repeats of v1/v2). "
                    "Recommendation, AI summary, family message, and safety flags are produced "
                    "by engine_core via the production path; this file fabricates no clinical "
                    "decision. Every output is a DRAFT (verified:false), pending PI sign-off."),
        "generated": "2026-07-16",
        "focus": "reconciled tier x phase x 6-level tables; F1 assume-urgent; B3 end-dose; "
                 "Q3 TID-by-phase; D1 concise; emergent/at-max/safety",
        "engine_version": cases[0]["engine_output"]["engine_version"] if cases else None,
        "n_cases": len(cases),
        "category_counts": counts,
        "priority_counts": _by(cases, "priority"),
        "cases": cases,
    }
    path = os.path.join(HERE, "review_cases_v3.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(cases)} cases -> {path}")
    print("category_counts:", json.dumps(counts))
    print("priority_counts:", json.dumps(payload["priority_counts"]))


if __name__ == "__main__":
    main()
