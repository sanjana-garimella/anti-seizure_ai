"""
demo.py - see the Aim 2 engine work on a few synthetic cases.

Run from the aim2_engine/ directory:
    python3 demo.py

Uses SYNTHETIC patient data only. Dosing numbers shown in Case 4 use a clearly
labeled ILLUSTRATIVE rule (not clinical values) just to demonstrate the shape;
the real engine ships with empty dosing tables until the PI fills them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine_core import (
    run_engine, snapshot_from_interval, PatientContext, MedicationContext,
    PhysicianContingency, DrugClassifier, ASMRule, DoseBand,
)


def make(med, answers, cont=None, amt=250, freq=2):
    return snapshot_from_interval(
        PatientContext(external_id="DEMO", age_years=8, weight_kg=30),
        MedicationContext(medication=med, dose_amount=amt, dose_unit="mg", frequency_per_day=freq),
        PhysicianContingency(**(cont or {"can_increase_dose": True})),
        answers,
    )


def show(title, rec):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)
    print("Category :", rec.category)
    print("Rules    :", ", ".join(r["rule"] for r in rec.rationale))
    if rec.titration_schedule:
        ts = rec.titration_schedule
        if ts.get("status") == "ok":
            print("Titration:", [f"{s['target_total_mg_per_day']} mg/day" for s in ts["steps"]])
        else:
            print("Titration:", ts.get("status"), "-", ts.get("note", ""))
        # NEW 2026-07-08: exact next-dose rung(s) from Dr. Barbour's dose ladder.
        lad = ts.get("ladder")
        if lad:
            nxt = ", ".join(f"{x['target_mg_per_day']} mg/day ({x['tier']})" for x in lad["next_up"]) or "at top rung"
            rf = f" (rounded from {lad['rounded_from']})" if lad.get("rounded_from") is not None else ""
            print(f"Ladder   : {lad['band_label']} | level {lad.get('current_level_index')}/{lad.get('total_levels')} "
                  f"~{lad.get('current_rung_mg_per_day')} mg/day{rf} | next: {nxt}"
                  + (f" | † labs: {', '.join(lad['high_dose_labs'])}" if lad["reaches_high_tier"] else "")
                  + ("  [DRAFT]" if not lad["verified"] else ""))
    if rec.switch_family_note:
        print("Family   :", rec.switch_family_note)
    if rec.medication_flag:
        print("Med check:", rec.medication_flag)
    if rec.safety_concern_flag:
        print("SAFETY   : urgent review; patient message withheld =", rec.draft_patient_message is None)


def main():
    dc = DrugClassifier(default_by="mechanism")

    show("Case 1 - Breakthrough + no side effects (expect: escalate)",
         run_engine(make("levetiracetam", {"Q3": "yes", "Q10a": "still_increasing"})))

    show("Case 2 - Seizure-free + intolerable side effects (expect: switch)",
         run_engine(make("carbamazepine", {"Q3": "no", "Q9": ["dizziness"], "Q10a": "at_goal",
                                            "Q10c_impact": "big_wants_change"},
                         cont={"can_increase_dose": False, "can_switch_to": "oxcarbazepine"}),
                    drug_classifier=dc))

    show("Case 3 - Blistering rash reported (expect: safety stop)",
         run_engine(make("lamotrigine", {"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"})))

    # Case 4: same as Case 1 but WITH an illustrative clinician dosing rule, to
    # show the titration schedule that appears once the PI fills the tables.
    illustrative = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
        label="ILLUSTRATIVE-not-clinical", min_age_years=2, max_age_years=18,
        dose_range_max_mg_per_day=1500, max_daily_mg=1500,
        standard_uptitration_step_mg=500, standard_uptitration_interval_weeks=2)])
    show("Case 4 - Escalate WITH illustrative dosing data (shows titration steps)",
         run_engine(make("levetiracetam", {"Q3": "yes", "Q10a": "still_increasing"}),
                    asm_rule=illustrative))

    # Case 5 (NEW 2026-07-08): frequent/long breakthrough (duration captured) ->
    # escalate faster, and the engine now shows the EXACT next dose rung(s) from
    # Dr. Barbour's dose ladder (final_doses v6). DRAFT until PI sign-off.
    show("Case 5 - Frequent breakthrough, duration captured (expect: escalate + exact ladder rung)",
         run_engine(make("levetiracetam",
                         {"Q3": "yes", "Q10a": "still_increasing",
                          "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                                "frequency": 3, "frequency_unit": "per_week"}]})))

    print("\n" + "-" * 68)
    print("Full clinical note for Case 1:")
    print("-" * 68)
    print(run_engine(make("levetiracetam", {"Q3": "yes", "Q10a": "still_increasing"})).clinical_summary)


if __name__ == "__main__":
    main()
