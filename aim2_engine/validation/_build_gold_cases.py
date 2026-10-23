"""
_build_gold_cases.py
---------------------
Reproducibly builds validation/gold_cases.json: the 63 synthetic cases Dr.
Barbour reviewed (A1-J10), encoded in the harness's snapshot format with her
GOLD-STANDARD recommendation as `gold_standard_category`.

Source of truth = her review transcribed in ../../PROJECT_LOG.md (gold table +
themes T1-T18). Categories map: increase->escalate, increase slower->
slower_uptitration, switch->switch_medication, no change->no_change, need more
info->insufficient_data. Cases she noted but left the box unchecked are tagged
`"inferred": true` (engine vs gold still scored, but flagged for her).

No clinical values live here - dosing/ceilings/thresholds come from
data/asm_rules.json + data/titration_thresholds.json. Run:
    python validation/_build_gold_cases.py
"""
import json, os

_HERE = os.path.dirname(os.path.abspath(__file__))

# Exact weights (kg) as given in Dr. Barbour's reviewed case document.
_EXACT_WT = {
    "A1": 30, "A2": 38, "A3": 55, "A4": 55, "A5": 44, "A6": 21, "A7": 60, "A8": 19,
    "B1": 38, "B2": 40, "B3": 50, "B4": 47, "B5": 31, "B6": 48, "B7": 43,
    "C1": 31, "C2": 30, "C3": 31, "C4": 30, "C5": 54, "C6": 19, "C7": 62,
    "D1": 35, "D2": 40, "D3": 28, "D4": 43, "D5": 48,
    "E1": 34, "E2": 33, "E3": 27, "E4": 25, "E5": 33,
    "F1": 45, "F2": 52, "F3": 33, "F4": 46, "F5": 64,
    "G1": 60, "G2": 62, "G3": 25, "G4": 27, "G5": 29,
    "H1": 24, "H2": 31, "H3": 34, "H4": 21, "H5": 40, "H6": 40,
    "I1": 18, "I2": 41, "I3": 42, "I4": 30, "I5": 44,
    "J1": 30, "J2": 24, "J3": 22, "J4": 25, "J5": 52, "J6": 44, "J7": 50,
    "J8": 47, "J9": 34, "J10": 20,
}
# fallback age->weight only if a case id is missing above
_WT = {5: 18, 6: 20, 7: 23, 8: 26, 9: 29, 10: 32, 11: 36, 12: 40, 13: 45,
       14: 50, 15: 55, 16: 60}


def C(cid, drug, age, gold, *, inferred=False, note="", seiz_types=None,
      comorbid=None, conmed_asms=None, prior=None, se_asian=None,
      ans=None, med=None):
    """Assemble one harness case. `ans` overrides survey answers; `med`
    overrides medication fields. Everything else defaults to a quiet snapshot."""
    answers = {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing"}
    answers.update(ans or {})
    medication = {"medication": drug, "dose_amount": 250, "dose_unit": "mg",
                  "frequency_per_day": 2, "time_on_medication": "4_12_weeks"}
    medication.update(med or {})
    return {
        "case_id": cid,
        "label": f"{cid} {drug} - {note}",
        "gold_standard_category": gold,
        "inferred": inferred,
        "patient": {"external_id": cid, "age_years": age,
                    "weight_kg": _EXACT_WT.get(cid, _WT.get(int(age), 30)),
                    "seizure_types": seiz_types or [],
                    "behavioral_comorbidities": comorbid or [],
                    "concomitant_asms": conmed_asms or [],
                    "prior_asms_tried": prior or [],
                    "southeast_asian_ancestry": se_asian},
        "medication": medication,
        "contingency": {},   # T2: engine reasons without chart context
        "answers": answers,
    }


# duration-captured breakthrough episode helper. Duration buckets (2026-06):
# under_1min (fine) | 1_3min | over_3min (whole-body = danger).  (short -> continue plan)
def ep(types, duration, freq, unit="per_week"):
    return [{"look": types, "duration": duration, "frequency": freq, "frequency_unit": unit}]


BT = {"Q3": "yes"}            # breakthrough shortcut
GOAL = {"Q10a": "at_goal"}
NOSURE = {"Q10a": "not_sure"}

cases = [
    # ---- A: breakthrough, no side effects ----
    C("A1", "levetiracetam", 8, "no_change", note="bt ~2/wk, short, titrating",
      ans={**BT, "Q4": ["sudden_jerks"], "seizure_episodes": ep(["sudden_jerks"], "under_1min", 2)}),
    C("A2", "oxcarbazepine", 11, "no_change", note="bt ~1/wk focal, titrating",
      ans={**BT, "Q4": ["focal"], "seizure_episodes": ep(["focal"], "under_1min", 1)}),
    C("A3", "lamotrigine", 14, "no_change", note="bt ~2/wk GTC, titrating",
      ans={**BT, "Q4": ["whole_body"], "Q6": "more", "seizure_episodes": ep(["whole_body"], "under_1min", 2)}),
    C("A4", "levetiracetam", 14, "escalate", inferred=True, note="bt, freq given, no duration -> assume urgent (F1 policy 2026-07-16)",
      ans={**BT, **GOAL, "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week"}),
    C("A5", "topiramate", 12, "add_medication", inferred=True, note="bt, freq given, no duration -> assume urgent; at max -> add (F1 policy 2026-07-16)",
      ans={**BT, **GOAL, "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week"}),
    C("A6", "valproic_acid", 6, "escalate", inferred=True, note="bt, freq given, no duration -> assume urgent (F1 policy 2026-07-16)",
      ans={**BT, "Q4": ["whole_body"], "Q5": 1, "Q5_unit": "per_week"}),
    C("A7", "lacosamide", 15, "no_change", inferred=True, note="bt ~3/wk, no duration -> assume urgent; at max -> no change (F1 policy 2026-07-16)",
      ans={**BT, "Q4": ["focal"], "Q6": "more", "Q5": 3, "Q5_unit": "per_week"}),
    C("A8", "clobazam", 6, "no_change", inferred=True, note="bt ~2/wk, no duration -> assume urgent; at max -> no change (F1 policy 2026-07-16)",
      ans={**BT, "Q4": ["whole_body"], "Q5": 2, "Q5_unit": "per_week"}),
    # ---- B: breakthrough + side effects ----
    C("B1", "oxcarbazepine", 11, "insufficient_data", note="bt + manageable, no duration",
      ans={**BT, "Q4": ["focal"], "Q5": 1, "Q5_unit": "per_week", "Q9": ["sleepiness"], "Q10b_impact": "small"}),
    C("B2", "valproic_acid", 11, "insufficient_data", note="bt ~4/wk + manageable, no duration",
      ans={**BT, "Q4": ["whole_body"], "Q5": 4, "Q5_unit": "per_week", "Q9": ["sleepiness"], "Q10b_impact": "small"}),
    C("B3", "carbamazepine", 13, "switch_medication", note="bt + intolerable behavior",
      ans={**BT, "Q4": ["focal"], "Q5": 3, "Q5_unit": "per_week", "Q9": ["behavior_change"], "Q10b_impact": "big_wants_change"}),
    C("B4", "topiramate", 13, "insufficient_data", note="bt + difficult-but-tolerable, no duration",
      ans={**BT, "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week", "Q9": ["word_finding"], "Q10b_impact": "medium_big"}),
    C("B5", "levetiracetam", 9, "insufficient_data", inferred=True, note="bt + manageable irritability, no duration",
      comorbid=["irritability"], ans={**BT, "Q4": ["whole_body"], "Q5": 2, "Q5_unit": "per_week", "Q9": ["irritability"], "Q10b_impact": "medium_big"}),
    C("B6", "lamotrigine", 13, "insufficient_data", inferred=True, note="bt + manageable dizziness, no duration",
      ans={**BT, "Q4": ["focal"], "Q5": 1, "Q5_unit": "per_week", "Q9": ["dizziness"], "Q10b_impact": "small"}),
    C("B7", "brivaracetam", 12, "switch_medication", inferred=True, note="bt + intolerable irritability",
      ans={**BT, "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week", "Q9": ["irritability"], "Q10b_impact": "big_wants_change"}),
    # ---- C: adherence / stopped / triggers ----
    C("C1", "levetiracetam", 9, "no_change", note="adherence-driven bt (missed doses)",
      ans={**BT, "Q2": "sometimes", "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week", "Q7": "no", "Q2a": ["missed_medication"]}),
    C("C2", "oxcarbazepine", 9, "switch_medication", note="stopped (side effects)",
      ans={"Q2": "stopped", "stopped_reason": "side_effects"}),
    C("C3", "carbamazepine", 9, "switch_medication", note="stopped (cost)",
      ans={"Q2": "stopped", "stopped_reason": "cost"}),
    C("C4", "valproic_acid", 9, "escalate", inferred=True, note="bt, freq given, no duration -> assume urgent (F1 policy 2026-07-16)",
      ans={**BT, "Q2": "sometimes", "Q4": ["whole_body"], "Q5": 1, "Q5_unit": "per_week", "Q7": "yes", "Q2a": ["illness"]}),
    C("C5", "lacosamide", 14, "no_change", note="adherence-driven bt, at goal",
      ans={**BT, **GOAL, "Q2": "sometimes", "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week", "Q7": "no", "Q2a": ["missed_medication"]}),
    C("C6", "clobazam", 6, "no_change", note="bt ~1/wk sleep trigger, short, titrating",
      ans={**BT, "Q4": ["whole_body"], "seizure_episodes": ep(["whole_body"], "under_1min", 1)}),
    C("C7", "lamotrigine", 16, "no_change", inferred=True, note="catamenial ~2/mo, short, at goal",
      ans={**BT, **GOAL, "Q4": ["whole_body"], "seizure_episodes": ep(["whole_body"], "under_1min", 2, "per_month")}),
    # ---- D: seizure-free, titrating ----
    C("D1", "levetiracetam", 10, "no_change", note="seizure-free titrating, better", ans={"Q11": "better"}),
    C("D2", "lamotrigine", 11, "no_change", note="seizure-free titrating"),
    C("D3", "ethosuximide", 8, "no_change", note="absence, seizure-free titrating, better",
      seiz_types=["brief_staring"], ans={"Q11": "better"}),
    C("D4", "brivaracetam", 12, "no_change", note="seizure-free titrating"),
    C("D5", "zonisamide", 13, "no_change", note="seizure-free titrating, better", ans={"Q11": "better"}),
    # ---- E: seizure-free + manageable SE ----
    C("E1", "levetiracetam", 10, "no_change", note="seizure-free, sleepy+mood manageable, titrating",
      ans={"Q9": ["sleepiness", "mood"], "Q10b_impact": "small"}),
    C("E2", "zonisamide", 10, "no_change", note="seizure-free, nausea manageable",
      ans={"Q9": ["nausea"], "Q10b_impact": "small"}),
    C("E3", "valproic_acid", 8, "no_change", note="seizure-free, mood manageable",
      ans={"Q9": ["mood"], "Q10b_impact": "small"}),
    C("E4", "oxcarbazepine", 7, "no_change", note="seizure-free, sleepy not-noticeable",
      ans={"Q9": ["sleepiness"], "Q10b_impact": "no_impact"}),
    C("E5", "topiramate", 10, "no_change", note="seizure-free, word-finding manageable",
      ans={"Q9": ["word_finding"], "Q10b_impact": "small"}),
    # ---- F: seizure-free + SE, switch boundary ----
    C("F1", "lamotrigine", 12, "switch_medication", note="intolerable dizziness, at goal",
      ans={**GOAL, "Q9": ["dizziness"], "Q10b_impact": "big_wants_change"}),
    C("F2", "carbamazepine", 14, "no_change", note="difficult-but-tolerable dizziness, titrating",
      ans={"Q9": ["dizziness"], "Q10b_impact": "medium_big"}),
    C("F3", "topiramate", 10, "no_change", note="difficult-but-tolerable behavior, at goal",
      ans={**GOAL, "Q9": ["behavior_change"], "Q10b_impact": "medium_big"}),
    C("F4", "zonisamide", 12, "switch_medication", note="intolerable dizziness, at goal",
      ans={**GOAL, "Q9": ["dizziness"], "Q10b_impact": "big_wants_change"}),
    C("F5", "brivaracetam", 16, "switch_medication", note="intolerable irritability, titrating",
      ans={"Q9": ["irritability"], "Q10b_impact": "big_wants_change"}),
    # ---- G: seizure-free, at goal ----
    C("G1", "lacosamide", 15, "no_change", note="seizure-free no SE at goal", ans={**GOAL}),
    C("G2", "lamotrigine", 16, "no_change", note="seizure-free no SE at goal", ans={**GOAL}),
    C("G3", "oxcarbazepine", 7, "no_change", note="sleepy not-noticeable at goal",
      ans={**GOAL, "Q9": ["sleepiness"], "Q10b_impact": "no_impact"}),
    C("G4", "valproic_acid", 8, "no_change", note="mood manageable at goal",
      ans={**GOAL, "Q9": ["mood"], "Q10b_impact": "small"}),
    C("G5", "ethosuximide", 9, "no_change", note="seizure-free no SE at goal",
      seiz_types=["brief_staring"], ans={**GOAL}),
    # ---- H: safety ----
    C("H1", "lamotrigine", 7, "switch_medication", note="spreading/blistering rash -> stop+switch",
      ans={"Q9": ["rash"], "Q9a": "blisters"}),
    C("H2", "carbamazepine", 9, "switch_medication", note="spreading/blistering rash -> stop+switch",
      ans={"Q9": ["rash"], "Q9a": "blisters"}),
    C("H3", "levetiracetam", 10, "escalate", note="seizure >5 min, med helping -> faster",
      ans={**BT, "Q4": ["whole_body"], "Q5": 1, "Q5_unit": "per_week", "Q11": "better",
           "urgent_safety": ["seizure_over_5_min"]}),
    C("H4", "valproic_acid", 6, "escalate", inferred=True, note="repeated seizures, helping -> faster",
      ans={**BT, "Q4": ["whole_body"], "Q5": 2, "Q5_unit": "per_week", "Q11": "better",
           "urgent_safety": ["repeated_seizures"]}),
    C("H5", "oxcarbazepine", 11, "switch_medication", note="ER + widespread rash/mouth sores -> stop+switch",
      ans={"Q9": ["rash"], "Q9a": "large", "urgent_safety": ["er_visit"]}),
    C("H6", "lamotrigine", 11, "switch_medication", inferred=True, note="small rash on LTG+VPA -> switch",
      conmed_asms=["valproic_acid"], ans={"Q9": ["rash"], "Q9a": "small"}),
    # ---- I: insufficient info ----
    C("I1", "clobazam", 5, "no_change", note="seizure-free, dose goal unknown", ans={**NOSURE}),
    C("I2", "lamotrigine", 11, "no_change", note="seizure-free, goal unknown, overall not sure",
      ans={**NOSURE, "Q11": "not_sure"}),
    C("I3", "zonisamide", 11, "no_change", note="seizure-free, goal unknown", ans={**NOSURE}),
    C("I4", "levetiracetam", 8, "insufficient_data", note="dose changed + ongoing seizures",
      med={"dose_changed_or_stopped": True}, ans={**BT, **NOSURE, "Q4": ["focal"], "Q5": 1, "Q5_unit": "per_week"}),
    C("I5", "topiramate", 12, "insufficient_data", inferred=True, note="bt type/freq unspecified, titrating",
      ans={**BT}),
    # ---- J ----
    C("J1", "lamotrigine", 9, "no_change", note="seizure-free titrating, on VPA (slow-titration note)",
      conmed_asms=["valproic_acid"]),
    C("J2", "levetiracetam", 7, "insufficient_data", note="LEV behavioral intolerable at goal (Kristen: offer switch options)",
      comorbid=["aggression"], ans={**GOAL, "Q9": ["behavior_change", "irritability"], "Q10b_impact": "big_wants_change"}),
    C("J3", "zonisamide", 6, "no_change", inferred=True, note="bt ~2/wk, no duration -> assume urgent; at max -> no change (F1 policy 2026-07-16)",
      ans={**BT, "Q4": ["whole_body"], "Q5": 2, "Q5_unit": "per_week"}),
    C("J4", "ethosuximide", 7, "switch_medication", note="focal seizures on absence-only drug -> switch",
      seiz_types=["brief_staring"], ans={**BT, "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week"}),
    C("J5", "valproic_acid", 15, "no_change", note="seizure-free no SE at goal, better", ans={**GOAL, "Q11": "better"}),
    C("J6", "oxcarbazepine", 12, "no_change", inferred=True, note="seizure-free, nausea manageable, titrating",
      ans={"Q9": ["nausea"], "Q10b_impact": "small"}),
    C("J7", "carbamazepine", 13, "insufficient_data", note="seizure-free at goal, family wants different med",
      ans={**GOAL, "Q12": "different_medicine"}),
    C("J8", "topiramate", 13, "insufficient_data", inferred=True, note="tolerable cognitive SE, family wants different med",
      ans={"Q9": ["word_finding"], "Q10b_impact": "medium_big", "Q12": "different_medicine"}),
    C("J9", "levetiracetam", 10, "slower_uptitration", inferred=True, note="seizure-free titrating, family prefers slower",
      ans={"Q12": "change_dose"}),
    C("J10", "clobazam", 6, "slower_uptitration", inferred=True, note="bt titrating, family prefers slower",
      ans={**BT, "Q4": ["whole_body"], "Q5": 1, "Q5_unit": "per_week", "Q12": "change_dose"}),
]

out = {
    "_README": ("63 synthetic cases Dr. Barbour reviewed (A1-J10), encoded for the "
                "validation harness. gold_standard_category = HER pick (PROJECT_LOG.md "
                "gold table). 'inferred' cases had a written note but unchecked box. "
                "Built by _build_gold_cases.py - do not hand-edit."),
    "source": "clinician (Barbour 2026-06 review), transcribed in PROJECT_LOG.md",
    "n_cases": len(cases),
    "cases": cases,
}

if __name__ == "__main__":
    path = os.path.join(_HERE, "gold_cases.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {len(cases)} cases -> {path}")
