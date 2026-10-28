"""
demo_seed.py - load a few SYNTHETIC patients + check-ins so the app looks
populated for a demo. Idempotent: only seeds if there are no patients yet.
Sets a known demo login (doctor / demo1234).

Run:  python3 demo_seed.py     (then start the app)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clinic_store as db
import clinic
from engine_core import run_engine

DEMO_PW = os.environ.get("ASM_DOCTOR_PASSWORD", "demo1234")

# Curated SYNTHETIC check-ins (2026-07-14; 15 more added 2026-10-28 so every engine
# outcome has an example - see the end of the list): a compact set that populates the
# review queue with one clean example of each engine decision path + the three
# priorities (urgent / review / routine) + the formulation variety (tablet /
# liquid / mg) + the over-max safety flag. Replaces the old 63-case validation
# dump that cluttered the queue with stale (pre-2026-06) cases. Each was run
# through the current engine and lands on the labeled outcome.
PATIENTS = [
    # -- routine: doing well --
    # Noah - TABLET dosing (shows tablet -> mg); seizure-free, no SE, at goal -> no change.
    ({"external_id": "Noah (demo)", "age": 10, "weight": 34, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 1, "dose_unit": "tablets",
      "concentration": 500, "frequency": 2, "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),
    # Eli - short/infrequent breakthrough while titrating -> continue the plan (no change).
    ({"external_id": "Eli (demo)", "age": 11, "weight": 38, "epilepsy_type": "focal",
      "medication": "oxcarbazepine", "dose_amount": 150, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [{"look": ["focal"], "duration": "under_1min", "frequency_bucket": "monthly"}]}),
    # Nora - seizure-free with a small, manageable side effect -> no change.
    ({"external_id": "Nora (demo)", "age": 9, "weight": 30, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q9": ["sleepiness"], "Q10c_impact": "small",
      "Q10a": "at_goal", "Q10c_time": "over_4_weeks"}),
    # Kai - breakthrough coincides with missed doses -> address adherence, no dose change.
    ({"external_id": "Kai (demo)", "age": 11, "weight": 36, "epilepsy_type": "focal",
      "medication": "oxcarbazepine", "dose_amount": 300, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "sometimes", "Q3": "yes", "Q7": "no", "Q10a": "still_increasing",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly"}]}),
    # -- review: a change or a check is suggested --
    # Ivy - frequent, longer breakthrough, no SE -> increase the dose faster.
    ({"external_id": "Ivy (demo)", "age": 8, "weight": 26, "epilepsy_type": "focal",
      "medication": "valproic_acid", "dose_amount": 250, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly", "count_4wk": 20}]}),
    # Theo - breakthrough + medium-impact SE while titrating -> slow the increase.
    ({"external_id": "Theo (demo)", "age": 12, "weight": 44, "epilepsy_type": "focal",
      "medication": "topiramate", "dose_amount": 100, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["behavior"], "Q10b_impact": "medium_big",
      "Q10a": "still_increasing",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "monthly"}]}),
    # Ava - at the maximum dose, medicine helping, still seizing -> add a second medicine.
    ({"external_id": "Ava (demo)", "age": 8, "weight": 26, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 780, "dose_unit": "mg", "frequency": 2,
      "can_increase": False, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q11": "better", "Q10a": "at_goal",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly", "count_4wk": 20}]}),
    # Sam - breakthrough + intolerable SE -> switch medicine.
    ({"external_id": "Sam (demo)", "age": 13, "weight": 50, "epilepsy_type": "focal",
      "medication": "carbamazepine", "dose_amount": 300, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": "oxcarbazepine"},
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["behavior"], "Q10b_impact": "big_wants_change",
      "Q10a": "still_increasing",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly"}]}),
    # Ben - seizure-free but a big-impact SE, family wants a switch -> switch medicine.
    ({"external_id": "Ben (demo)", "age": 12, "weight": 46, "epilepsy_type": "focal",
      "medication": "carbamazepine", "dose_amount": 300, "dose_unit": "mg", "frequency": 2,
      "can_increase": False, "switch_to": "oxcarbazepine"},
     {"Q2": "almost_never", "Q3": "no", "Q9": ["dizziness"], "Q10b_impact": "big_wants_change",
      "Q10a": "at_goal", "Q12": "different_medicine"}),
    # Mia - stopped the medicine because of side effects -> switch to a better-tolerated option.
    ({"external_id": "Mia (demo)", "age": 10, "weight": 30, "epilepsy_type": "focal",
      "medication": "zonisamide", "dose_amount": 100, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": "levetiracetam"},
     {"Q2": "stopped", "stopped_reason": "side_effects", "Q3": "no", "Q10a": "at_goal"}),
    # Leo - ethosuximide (absence-only) reported with focal seizures -> indication mismatch, switch.
    ({"external_id": "Leo (demo)", "age": 9, "weight": 28, "epilepsy_type": "focal",
      "medication": "ethosuximide", "dose_amount": 250, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": "levetiracetam"},
     {"Q2": "almost_never", "Q3": "yes", "Q4": ["focal"], "Q10a": "still_increasing",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly"}]}),
    # Zoe - reported dose is ABOVE the recommended maximum for her weight -> over-max safety flag.
    ({"external_id": "Zoe (demo)", "age": 8, "weight": 20, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 1300, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),
    # Rey - breakthrough but no seizure duration captured -> need more info (defer).
    ({"external_id": "Rey (demo)", "age": 10, "weight": 32, "epilepsy_type": "focal",
      "medication": "topiramate", "dose_amount": 100, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q5": 3, "Q5_unit": "per_week", "Q10a": "still_increasing"}),
    # Gia - seizure-free but family would like to try a different medicine -> offer options (defer).
    ({"external_id": "Gia (demo)", "age": 9, "weight": 31, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": "brivaracetam"},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q12": "different_medicine"}),
    # -- urgent: safety --
    # Avery - LIQUID dosing (mL -> mg); a spreading/blistering rash flags urgent SJS review.
    ({"external_id": "Avery (demo)", "age": 7, "weight": 24, "epilepsy_type": "generalized",
      "medication": "lamotrigine", "dose_amount": 5, "dose_unit": "mL",
      "concentration": 5, "frequency": 2, "can_increase": True, "switch_to": "levetiracetam"},
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "blisters", "Q10a": "at_goal",
      "Q13": "the rash looks like it is spreading and blistering"}),

    # -- 2026-10-28: one example of each outcome added by Dr. Barbour's 2026-10-25
    # answers (engine 0.4.x), so every path the engine can take is in the demo. --
    # Pia - seizure-free, Level 2 side effects while still increasing -> slow the increases.
    ({"external_id": "Pia (demo)", "age": 9, "weight": 28, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 250, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing", "Q9": ["sleepiness"],
      "Q10b_impact": "medium_tolerable"}),
    # Rosa - seizure-free, Level 3, family chose UNEVEN dosing -> hold; the same total is
    # re-split as 500 mg morning + 1000 mg night (whole 250 mg tablets).
    ({"external_id": "Rosa (demo)", "age": 12, "weight": 40, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 3, "dose_unit": "tablets",
      "concentration": 250, "frequency": 2, "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q9": ["sleepiness"], "Q10b_impact": "medium_alot", "Q10g_dosing_choice": "uneven"}),
    # Finn - seizure-free, Level 3 (nausea) while still increasing, family chose THREE
    # TIMES A DAY (allowed while increasing; her template H: helps stomach upset on
    # valproate). 3 x 250 mg capsules twice a day -> 2 capsules three times a day, same
    # 1500 mg/day (41.7 mg/kg/day). Labs ordered (valproate).
    ({"external_id": "Finn (demo)", "age": 11, "weight": 36, "sex": "male",
      "epilepsy_type": "generalized",
      "medication": "valproic_acid", "dose_amount": 3, "dose_unit": "tablets",
      "concentration": 250, "frequency": 2, "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing", "Q9": ["nausea"],
      "Q10b_impact": "medium_alot", "Q10g_dosing_choice": "tid"}),
    # Lucy - seizure-free, Level 3, no choice yet -> hold; the draft lists the options.
    ({"external_id": "Lucy (demo)", "age": 8, "weight": 26, "epilepsy_type": "focal",
      "medication": "oxcarbazepine", "dose_amount": 450, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q9": ["dizziness"], "Q10b_impact": "medium_alot"}),
    # Omar - seizure-free, Level 4 (big impact) -> lower the dose + labs.
    ({"external_id": "Omar (demo)", "age": 10, "weight": 32, "sex": "male",
      "epilepsy_type": "generalized",
      "medication": "valproic_acid", "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q9": ["sleepiness"], "Q10b_impact": "big"}),
    # Tess - seizure-free, Level 5 (not a fit) on tablets -> switch; the summary shows the
    # new medicine's starting schedule and the old-medicine taper in tablets.
    ({"external_id": "Tess (demo)", "age": 13, "weight": 45, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 4, "dose_unit": "tablets",
      "concentration": 250, "frequency": 2, "can_increase": True, "switch_to": "oxcarbazepine"},
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q9": ["mood_changes"], "Q10b_impact": "not_a_fit"}),
    # Max - urgent breakthrough + Level 3, family chose "lower the dose and add a second
    # medicine" (offered by the urgent table cell) -> add.
    ({"external_id": "Max (demo)", "age": 10, "weight": 34, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 600, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q5": 2, "Q5_unit": "per_week", "Q7": True, "Q9": ["mood_changes"],
      "Q10b_impact": "medium_alot", "Q10g_dosing_choice": "add",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                            "frequency_bucket": "weekly", "unresponsive": False}]}),
    # Ella - urgent breakthrough + Level 4 -> lower the dose and add a second medicine.
    ({"external_id": "Ella (demo)", "age": 9, "weight": 30, "epilepsy_type": "focal",
      "medication": "topiramate", "dose_amount": 100, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q5": 2, "Q5_unit": "per_week", "Q7": True, "Q9": ["mood_changes"], "Q10b_impact": "big",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                            "frequency_bucket": "weekly", "unresponsive": False}]}),
    # Gus - non-urgent breakthrough + Level 3 while increasing -> stay 2 weeks, then one
    # increase (liquid, rounded to a measurable mL).
    ({"external_id": "Gus (demo)", "age": 7, "weight": 24, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 300, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing", "Q5": 1,
      "Q5_unit": "per_month", "Q7": True, "Q9": ["sleepiness"], "Q10b_impact": "medium_alot",
      "seizure_episodes": [{"look": ["focal"], "duration": "under_1min",
                            "frequency": 1, "frequency_unit": "per_month"}]}),
    # Ana - EMERGENT tier from frequency (daily 1-3 min focal seizures), no safety flag.
    ({"external_id": "Ana (demo)", "age": 14, "weight": 50, "epilepsy_type": "focal",
      "medication": "oxcarbazepine", "dose_amount": 600, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q5": 1, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                            "frequency": 1, "frequency_unit": "per_day"}]}),
    # Cal - whole-body seizure over 3 minutes -> urgent safety; NO family message (#51).
    ({"external_id": "Cal (demo)", "age": 9, "weight": 30, "epilepsy_type": "generalized",
      "medication": "levetiracetam", "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing", "Q5": 1,
      "Q5_unit": "per_week", "Q5b": "under_2_weeks", "Q7": True,
      "seizure_episodes": [{"look": ["whole_body"], "duration": "over_3min",
                            "frequency": 1, "frequency_unit": "per_week"}]}),
    # Ray - breakthrough with neither frequency nor length -> defer to the physician.
    ({"external_id": "Ray (demo)", "age": 6, "weight": 21, "epilepsy_type": "focal",
      "medication": "clobazam", "dose_amount": 5, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "at_goal", "Q11": "not_sure"}),
    # Nia - at the goal dose, ongoing seizures, difficult-but-tolerable side effects ->
    # ask the family: keep increasing, or add a second medicine.
    ({"external_id": "Nia (demo)", "age": 12, "weight": 42, "epilepsy_type": "focal",
      "medication": "lamotrigine", "dose_amount": 125, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q5": 1, "Q5_unit": "per_month", "Q7": True, "Q9": ["dizziness"],
      "Q10b_impact": "medium_big",
      "seizure_episodes": [{"look": ["focal"], "duration": "under_1min",
                            "frequency": 1, "frequency_unit": "per_month"}]}),
    # Iris - Dr. Barbour's worked example on 250 mg tablets: two increases, uneven
    # AM/PM with a half tablet on the climb step, worded "500 mg (2 tablets)...".
    ({"external_id": "Iris (demo)", "age": 12, "weight": 30, "epilepsy_type": "focal",
      "medication": "levetiracetam", "dose_amount": 2, "dose_unit": "tablets",
      "concentration": 250, "frequency": 2, "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q5": 2, "Q5_unit": "per_week",
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                            "frequency": 2, "frequency_unit": "per_week"}]}),
    # Hazel - ethosuximide for ABSENCE seizures (the only type it treats): staring
    # spells of 30-90 s, 12 a day (urgent for absence: 10+ a day). 1000 mg/day =
    # 20 mg/kg/day, the start of her typical range at 50 kg. Two increases in 250 mg
    # CAPSULES (worded "capsules", never split); step 2 lands above the usual max and
    # is flagged for the clinician (never above the absolute max).
    ({"external_id": "Hazel (demo)", "age": 12, "weight": 50, "epilepsy_type": "absence",
      "medication": "ethosuximide", "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
      "can_increase": True, "switch_to": None},
     {"Q2": "almost_never", "Q3": "yes", "Q4": ["brief_staring"], "Q10a": "at_goal",
      "Q10c_time": "over_2_weeks", "Q5": 12, "Q5_unit": "per_day",
      "seizure_episodes": [{"look": ["brief_staring"], "duration": "30_90s",
                            "frequency": 12, "frequency_unit": "per_day",
                            "unresponsive": True}]}),
]


def main():
    # `--reset` clears the existing demo database first, so the curated set below
    # replaces any stale rows (e.g. the old 63-case validation dump) instead of
    # being skipped by the idempotency guard. Synthetic data only, so this is safe.
    if "--reset" in sys.argv:
        try:
            os.remove(db.DB_PATH)
            print("Reset: cleared the existing demo database.")
        except FileNotFoundError:
            pass
    db.init_db()
    db.update_password("doctor", DEMO_PW)  # known demo login

    if db.list_patients():
        print("Demo data already present - leaving it as is.")
    else:
        rf = clinic.current_redflag()
        for p, ans in PATIENTS:
            pid = db.add_patient(p)
            inst = db.create_instance(pid)
            rec = run_engine(clinic.build_snapshot(db.get_patient(pid), ans),
                             asm_registry=clinic.current_asm(), drug_classifier=clinic.CLASSIFIER,
                             physician_name="your care team")
            rf_cats = sorted({fl.category for fl in rf.scan(ans["Q13"])}) if rf and ans.get("Q13") else []
            pri, reasons = clinic.triage(rec, rf_cats)
            sub = db.add_submission(inst["id"], pid, ans, pri, reasons)
            db.add_recommendation(sub, rec.to_dict())
            db.mark_submitted(inst["id"])
            # Log demo runs too, so the audit store isn't empty for seeded patients.
            clinic._run_nlp_audit(ans, patient_id=str(pid), session_id=f"sub_{sub}")
            clinic._log_recommendation_audit(rec, db.get_patient(pid), session_id=f"sub_{sub}")
        # One patient with a ready, un-submitted survey - for the LIVE demo:
        # open it, fill it, and watch it appear in the doctor's queue.
        lia = {"external_id": "Lia (demo)", "age": 9, "weight": 31, "epilepsy_type": "focal",
               "medication": "oxcarbazepine", "dose_amount": 300, "dose_unit": "mg",
               "frequency": 2, "can_increase": True, "switch_to": None}
        linst = db.create_instance(db.add_patient(lia))
        db.audit("system", "demo_seed", f"{len(PATIENTS)} submitted + 1 pending")
        print(f"Seeded {len(PATIENTS)} submitted check-ins + a ready-to-fill survey for Lia (demo).")
        print(f"  Lia's blank survey link: http://127.0.0.1:8000/s/{linst['token']}")

    # NOTE (2026-07-14): the old bulk-seed of demo_validation_cases.json (63
    # pre-2026-06 cases) was REMOVED from the queue - it was stale (predated the
    # June/July engine changes) and buried the queue in outdated items. The
    # curated PATIENTS above give one current example of each decision path. The
    # validation JSON still lives on disk for the offline harness; it just isn't
    # dumped into the review queue anymore.

    print("\n  Log in as:  doctor  /  " + DEMO_PW)
    print("  Then start the app and open http://127.0.0.1:8000/login\n")


if __name__ == "__main__":
    main()
