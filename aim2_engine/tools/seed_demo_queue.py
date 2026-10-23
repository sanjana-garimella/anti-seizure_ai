#!/usr/bin/env python3
"""
seed_demo_queue.py - shape the demo review queue: one submission per patient, and
5-8 worked examples of every queue tab (Emergent / Urgent / Non-urgent / Safety /
No seizures), matching the tabs `clinic.py` actually renders.

WHY
    The queue is the first thing anyone sees, and it was uneven: two patients
    appeared twice, `escalate` had 2 examples and `insufficient_data` 1, and
    **no case was emergent at all**, so the Emergent tier/chip/filter had nothing
    real behind it. The Safety and No-seizures tabs had no dedicated cases either -
    a safety-flagged row only ever showed up incidentally, never guaranteed.

HOW
    Every row is produced by running the ACTUAL engine over survey answers - the
    same `build_snapshot` + `run_engine` + `triage` path a family submission takes.
    Nothing is hand-written into the recommendation columns, so a seeded case is
    indistinguishable from a real check-in and stays consistent with the rules.
    Each new submission also records `med_context_json`, so it can be re-run
    losslessly later (tools/regen_notes.py).

SAFETY
    SIMULATED DATA ONLY (NON_NEGOTIABLE_RULES.md - no real PHI until IRB +
    HIPAA/CITI). Names are obvious placeholders and ids are prefixed `sim-`.
    All SQLite work happens on an OFF-mount copy, verified with
    PRAGMA integrity_check, then swapped in by atomic rename after a timestamped
    backup - writing the mounted DB in place has corrupted it before.

USAGE
    cd aim2_engine
    python3 tools/seed_demo_queue.py            # dry run, report only
    python3 tools/seed_demo_queue.py --apply
    python3 tools/seed_demo_queue.py --only tablets --apply   # just one group
"""

from __future__ import annotations

import argparse
import collections
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Each entry: (id suffix, age, weight kg, medication, dose, unit, freq, answers
# [, patient-record extras: {"concentration": mg per tablet, "name": display id}]).
# Answers are ordinary survey answers; the engine decides the category, so these
# are chosen to EXERCISE a path, not to assert an outcome.
FOCAL_LONG_WEEKLY = [{"look": ["focal"], "duration": "1_3min",
                      "frequency": 2, "frequency_unit": "per_week"}]
FOCAL_SHORT_RARE = [{"look": ["focal"], "duration": "under_1min",
                     "frequency": 1, "frequency_unit": "per_month"}]
FOCAL_WEEKLY_FORM = [{"look": ["focal"], "duration": "1_3min",       # family-form shape
                      "frequency_bucket": "weekly", "unresponsive": False}]
WHOLE_BODY_LONG = [{"look": ["whole_body"], "duration": "over_3min",
                    "frequency": 1, "frequency_unit": "per_week"}]

CASES = [
    # --- EMERGENT: whole-body seizure over 3 minutes (had zero coverage) --------
    ("emergent-1", 9, 30, "levetiracetam", 500, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q5": 1, "Q5_unit": "per_week",
      "Q5b": "under_2_weeks", "Q7": True, "seizure_episodes": WHOLE_BODY_LONG}),
    ("emergent-2", 6, 20, "valproic_acid", 300, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 2, "Q5_unit": "per_week",
      "Q7": True, "Q11": "worse", "seizure_episodes": WHOLE_BODY_LONG,
      "Q13": "The long one was frightening, it went on for several minutes."}),
    ("emergent-3", 13, 45, "oxcarbazepine", 600, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": WHOLE_BODY_LONG + FOCAL_LONG_WEEKLY}),
    ("emergent-4", 7, 24, "lamotrigine", 25, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q5": 1, "Q5_unit": "per_week",
      "Q7": True, "seizure_episodes": WHOLE_BODY_LONG}),
    ("emergent-5", 12, 40, "topiramate", 100, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 3, "Q5_unit": "per_week", "Q7": True,
      "Q11": "worse", "seizure_episodes": WHOLE_BODY_LONG}),
    # emergent-6/7/8 reach the tier through frequency, not the >3-min danger
    # rule, so they do NOT carry the safety flag - keeps Emergent from being a
    # strict subset of Safety (the danger-rule cases above always are both).
    ("emergent-6", 14, 50, "oxcarbazepine", 450, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                            "frequency": 1, "frequency_unit": "per_day"}]}),
    ("emergent-7", 13, 48, "clobazam", 10, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                            "frequency": 1, "frequency_unit": "per_day"}]}),
    ("emergent-8", 9, 28, "levetiracetam", 375, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 2, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": [{"look": ["whole_body"], "duration": "1_3min",
                            "frequency": 1, "frequency_unit": "per_week"}]}),

    # --- ESCALATE: urgent-tier breakthrough while titrating ---------------------
    ("escalate-1", 8, 26, "levetiracetam", 250, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q10f_planned_end_mg_per_day": 1200,
      "Q5": 2, "Q5_unit": "per_week", "Q7": True, "Q6": "more_frequent",
      "seizure_episodes": FOCAL_LONG_WEEKLY}),
    ("escalate-2", 11, 36, "topiramate", 50, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q5": 3, "Q5_unit": "per_week",
      "Q7": True, "Q9": ["sleepiness"], "Q10b_impact": "small",
      "seizure_episodes": FOCAL_LONG_WEEKLY}),
    ("escalate-3", 9, 28, "levetiracetam", 375, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": FOCAL_LONG_WEEKLY}),
    ("escalate-4", 6, 20, "lamotrigine", 25, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q5": 1, "Q5_unit": "per_week",
      "Q7": True, "seizure_episodes": FOCAL_LONG_WEEKLY}),
    ("escalate-5", 8, 27, "valproic_acid", 200, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 2, "Q5_unit": "per_week", "Q7": True,
      "seizure_episodes": FOCAL_LONG_WEEKLY}),

    # --- INSUFFICIENT DATA: breakthrough with no duration captured --------------
    ("defer-1", 7, 22, "levetiracetam", 250, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing"}),
    ("defer-2", 10, 32, "lamotrigine", 50, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q11": "not_sure"}),
    ("defer-3", 15, 55, "valproic_acid", 500, "mg", 2,
     {"Q3": "yes", "Q10a": "not_sure", "Q5": 4, "Q5_unit": "per_month"}),

    # --- NON-URGENT tier: short, infrequent breakthrough -----------------------
    ("nonurgent-1", 5, 18, "levetiracetam", 200, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_month", "Q7": True,
      "seizure_episodes": FOCAL_SHORT_RARE, "Q11": "better"}),
    ("nonurgent-2", 8, 25, "oxcarbazepine", 300, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 2, "Q5_unit": "per_month", "Q7": True,
      "seizure_episodes": FOCAL_SHORT_RARE, "Q11": "about_same"}),
    ("nonurgent-3", 12, 42, "lamotrigine", 50, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_month", "Q7": True,
      "seizure_episodes": FOCAL_SHORT_RARE}),
    ("nonurgent-4", 10, 33, "topiramate", 50, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_month", "Q7": True,
      "seizure_episodes": FOCAL_SHORT_RARE, "Q11": "better"}),
    ("nonurgent-5", 9, 31, "clobazam", 5, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 1, "Q5_unit": "per_month", "Q7": True,
      "seizure_episodes": FOCAL_SHORT_RARE, "Q11": "about_same"}),
    ("nonurgent-6", 15, 56, "carbamazepine", 300, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q5": 2, "Q5_unit": "per_month", "Q7": True,
      "seizure_episodes": FOCAL_SHORT_RARE}),

    # --- SAFETY: large/blistering rash on an SJS-risk drug -> stop + switch -----
    # (had zero dedicated coverage; only ever showed up incidentally)
    ("safety-1", 9, 30, "lamotrigine", 25, "mg", 2,
     {"Q3": "no", "Q10a": "still_increasing", "Q9a": "blisters"}),
    ("safety-2", 11, 40, "carbamazepine", 200, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q9a": "blisters",
      "southeast_asian_ancestry": True}),
    ("safety-3", 7, 24, "oxcarbazepine", 300, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q9a": "large"}),
    ("safety-4", 13, 45, "lamotrigine", 50, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q9a": "blisters", "Q5": 1,
      "Q5_unit": "per_week", "Q7": True, "seizure_episodes": FOCAL_SHORT_RARE}),
    ("safety-5", 5, 17, "carbamazepine", 100, "mg", 2,
     {"Q3": "no", "Q10a": "still_increasing", "Q9a": "large"}),
    ("safety-6", 15, 58, "lamotrigine", 75, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q9a": "blisters"}),

    # --- NO SEIZURES: nothing to report this interval ---------------------------
    # (had zero dedicated coverage; the tab counted whatever happened to land there)
    ("noseizure-1", 8, 26, "levetiracetam", 250, "mg", 2,
     {"Q2": "stopped", "Q3": "no", "Q10a": "still_increasing",
      "stopped_reason": "side_effects"}),
    ("noseizure-2", 10, 34, "valproic_acid", 300, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal"}),
    ("noseizure-3", 12, 44, "lamotrigine", 100, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q11": "better"}),
    ("noseizure-4", 6, 21, "topiramate", 50, "mg", 2,
     {"Q3": "no", "Q10a": "still_increasing"}),
    ("noseizure-5", 14, 51, "oxcarbazepine", 450, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q11": "about_same"}),
    ("noseizure-6", 9, 29, "clobazam", 10, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal"}),

    # --- TABLETS: dose reported in tablets, so the clinician summary shows the
    # tablet/liquid conversion of the next ladder step(s) (formulation layer,
    # Barbour 'Flags & clarifications' answers). Steady state + weekly 1-3 min focal
    # seizures -> urgent, two dose increases.
    # Her worked example: 30 kg, 2 x 250 mg twice a day -> uneven AM/PM, half tablet
    # on the climb step, ends at 500 mg morning + 750 mg night.
    ("tablets-1", 10, 30, "levetiracetam", 2, "tablets", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_LONG_WEEKLY},
     {"concentration": 250, "name": "Maya (demo)"}),
    # Small child: no 250 mg tablet dose fits step 1 -> liquid shown as the
    # fallback + "skip to step 2 in one move".
    ("tablets-2", 3, 12, "levetiracetam", 1, "tablets", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_LONG_WEEKLY},
     {"concentration": 250, "name": "Owen (demo)"}),
    # Lamotrigine 100 mg tablets: both steps land exactly, uneven AM/PM.
    ("tablets-3", 12, 40, "lamotrigine", 1, "tablets", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_LONG_WEEKLY},
     {"concentration": 100, "name": "Ruby (demo)"}),
    # 2026-10-15 changes: the family draft names the tablets, and levetiracetam uses
    # one strength (the family's own if it fits, else the standard 500 mg).
    # Over 10 on 250 mg tablets: keeps 250 mg; family draft gives tablet counts.
    ("tablets-4", 12, 30, "levetiracetam", 2, "tablets", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_LONG_WEEKLY},
     {"concentration": 250, "name": "Iris (demo)"}),
    # On 750 mg tablets and 750 mg fits both steps: keeps 750 mg (no switch).
    ("tablets-5", 13, 60, "levetiracetam", 1, "tablets", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_LONG_WEEKLY},
     {"concentration": 750, "name": "Jonah (demo)"}),
    # On 750 mg tablets but 750 mg doesn't fit: switches to the standard 500 mg and
    # the family draft says it's a different tablet.
    ("tablets-6", 13, 40, "levetiracetam", 1, "tablets", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_LONG_WEEKLY},
     {"concentration": 750, "name": "Lena (demo)"}),

    # --- ROUNDING (2026-10-23): ladder doses that are not round numbers. The
    # family draft prints them with ':g' (e.g. 1217.83 mg/day); the guardrail must
    # accept the printed form. The ethosuximide cases were wrongly flagged as
    # "possible fabrication" before the fix in family_message_allowed_doses.
    # Answers use the family form's episode shape (frequency_bucket).
    ("rounding-1", 12, 52.2, "ethosuximide", 500, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_WEEKLY_FORM},
     {"name": "Hazel (demo)"}),
    ("rounding-2", 8, 41.7, "ethosuximide", 500, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q5": 2, "Q5_unit": "per_week",
      "seizure_episodes": FOCAL_WEEKLY_FORM},
     {"name": "Felix (demo)"}),
    ("rounding-3", 4, 18.3, "ethosuximide", 100, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_WEEKLY_FORM},
     {"name": "June (demo)"}),
    ("rounding-4", 4, 18.3, "oxcarbazepine", 100, "mg", 2,
     {"Q3": "yes", "Q10a": "still_increasing", "Q5": 2, "Q5_unit": "per_week",
      "seizure_episodes": FOCAL_WEEKLY_FORM},
     {"name": "Milo (demo)"}),
    ("rounding-5", 4, 18.3, "levetiracetam", 250, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_WEEKLY_FORM},
     {"name": "Ada (demo)"}),
    ("rounding-6", 8, 33.4, "lamotrigine", 100, "mg", 2,
     {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
      "Q5_unit": "per_week", "seizure_episodes": FOCAL_WEEKLY_FORM},
     {"name": "Cole (demo)"}),
]

# --- MEDICATION SELECTION: non-urgent switch, family expresses a 2nd-choice
# preference among the engine's ranked options (had zero coverage - the queue
# never showed the "Medication options (DRAFT)" card with a family ranking on
# it). Each entry: (id suffix, age, sex, medication, dose, unit, freq, answers,
# selection attribute overrides for engine_core.med_selection).
MEDSEL_CASES = [
    ("medsel-1", 10, "female", "levetiracetam", 500, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q9": ["sleepiness"], "Q10b_impact": "big_wants_change"},
     {}),
    ("medsel-2", 9, "male", "valproic_acid", 250, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q9": ["mood_changes"], "Q10b_impact": "not_a_fit"},
     {"mood_difficulties": True}),
    ("medsel-3", 14, "male", "oxcarbazepine", 450, "mg", 2,
     {"Q3": "no", "Q10a": "at_goal", "Q9": ["sleepiness"], "Q10b_impact": "big_wants_change"},
     {}),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None, metavar="PREFIX",
                    help="seed only cases whose id starts with PREFIX (e.g. tablets)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    live = args.db or os.path.join(here, "clinic_data", "clinic.db")
    if not os.path.isfile(live):
        print(f"no database at {live}")
        return 1

    work_dir = tempfile.mkdtemp(prefix="seed_demo_")          # OFF the mount
    work = os.path.join(work_dir, "clinic.db")
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(work)
    src.backup(dst)
    dst.close()
    src.close()

    os.environ["ASM_DB"] = work
    os.chdir(here)
    import clinic_store as db          # noqa: E402
    import clinic                      # noqa: E402
    from engine_core import run_engine  # noqa: E402
    db.init_db()

    # ---- 1. one submission per patient: drop the older duplicates -------------
    c = sqlite3.connect(work)
    c.row_factory = sqlite3.Row
    seen, dropped = {}, []
    for r in c.execute("""SELECT s.id sub, p.external_id ext FROM submissions s
                          JOIN patients p ON p.id = s.patient_id ORDER BY s.id"""):
        seen.setdefault(r["ext"], []).append(r["sub"])
    for ext, subs in seen.items():
        for old in subs[:-1]:                      # keep the newest submission
            dropped.append((ext, old))
            c.execute("DELETE FROM recommendations WHERE submission_id=?", (old,))
            c.execute("DELETE FROM med_selections WHERE submission_id=?", (old,))
            c.execute("DELETE FROM submissions WHERE id=?", (old,))
    c.commit()
    existing = {r[0] for r in c.execute("SELECT external_id FROM patients")}
    c.close()          # release the lock: clinic_store opens its own connections

    # ---- 2. add the missing worked examples ----------------------------------
    added = []
    for case in CASES:
        suffix, age, wt, med, amt, unit, freq, answers = case[:8]
        extras = case[8] if len(case) > 8 else {}
        ext = extras.get("name") or f"sim-{suffix}"
        if ext in existing or (args.only and not suffix.startswith(args.only)):
            continue                                # idempotent: already seeded
        pid = db.add_patient({"external_id": ext, "age": age, "weight": wt,
                              "epilepsy_type": "unspecified", "medication": med,
                              "dose_amount": amt, "dose_unit": unit, "frequency": freq,
                              "concentration": extras.get("concentration"),
                              "can_increase": True, "switch_to": None})
        patient = db.get_patient(pid)
        inst = db.create_instance(pid)
        snap = clinic.build_snapshot(patient, answers)
        rec = run_engine(snap, asm_registry=clinic.current_asm(),
                         drug_classifier=clinic.CLASSIFIER,
                         physician_name="your care team")
        priority, reasons = clinic.triage(rec, [])
        sub = db.add_submission(inst["id"], pid, answers, priority, reasons,
                                med_context=clinic.med_context_of(snap))
        db.add_recommendation(sub, rec.to_dict())
        db.mark_submitted(inst["id"])
        added.append((ext, rec.category, rec.urgency_tier, priority))

    # ---- 2b. medication-selection cases: run the real selector + record a
    # family ranking (1st/2nd), exactly like /s/<token>/rank would ------------
    medsel_added = []
    _COMORBS = ["kidney_disease", "kidney_stones", "liver_disease", "diabetes", "obesity", "heart_issue",
                "bleeding_disorder", "sulfa_allergy", "prior_chemo"]
    for suffix, age, sex, med, amt, unit, freq, answers, sel_overrides in MEDSEL_CASES:
        ext = f"sim-{suffix}"
        if ext in existing or (args.only and not suffix.startswith(args.only)):
            continue
        pid = db.add_patient({"external_id": ext, "age": age, "weight": age * 3 + 5,
                              "sex": sex, "epilepsy_type": "unspecified", "medication": med,
                              "dose_amount": amt, "dose_unit": unit, "frequency": freq,
                              "concentration": None, "can_increase": True, "switch_to": None})
        patient = db.get_patient(pid)
        inst = db.create_instance(pid)
        snap = clinic.build_snapshot(patient, answers)
        rec = run_engine(snap, asm_registry=clinic.current_asm(),
                         drug_classifier=clinic.CLASSIFIER,
                         physician_name="your care team")
        if not clinic.phase2_applies(rec.to_dict()):
            print(f"   SKIP {ext}: engine returned {rec.category} "
                  f"(safety={rec.safety_concern_flag}) - not a phase-2 case, answers need updating")
            continue
        priority, reasons = clinic.triage(rec, [])
        sub = db.add_submission(inst["id"], pid, answers, priority, reasons,
                                med_context=clinic.med_context_of(snap))
        db.add_recommendation(sub, rec.to_dict())
        db.mark_submitted(inst["id"])

        cur_id = clinic._current_med_selection_id(med)
        attrs = {"age": age, "sex": sex, "staring_seizures": False,
                 "whole_body_single_jerk": False, "med_nonadherence": False,
                 "prior_tried": sorted({cur_id} if cur_id else set()),
                 "se_asian_han_chinese": False, "prefers_no_labs": False,
                 "mood_difficulties": False, "behavioral_difficulties": False,
                 "trouble_sleeping": False, "migraines": False,
                 **{c: False for c in _COMORBS}}
        attrs.update(sel_overrides)
        result = clinic.select_meds(attrs, clinic.SELECTION_RULESET)
        rd = result.to_dict()
        db.save_med_selection(sub, pid, attrs, rd)
        options = rd.get("options") or []
        # The family agrees with the engine's top two and marks them 1st/2nd
        # choice; anything ranked 3rd+ is left with no stated preference.
        ranking = {}
        if len(options) >= 1:
            ranking[options[0]["id"]] = "first"
        if len(options) >= 2:
            ranking[options[1]["id"]] = "second"
        db.save_med_ranking(sub, ranking)
        db.audit("patient", "med_ranking", f"submission {sub} choices {len(ranking)}")
        medsel_added.append((ext, rec.category, [o["id"] for o in options], ranking))

    # ---- 3. report ----------------------------------------------------------
    v = sqlite3.connect(work)
    v.row_factory = sqlite3.Row
    cats, tiers = collections.Counter(), collections.Counter()
    for r in v.execute("SELECT category, urgency_tier, safety_flag FROM recommendations"):
        cats[r["category"]] += 1
        tiers[r["urgency_tier"] or "no breakthrough"] += 1
        if r["safety_flag"]:
            tiers["safety"] += 1
    integrity = v.execute("PRAGMA integrity_check").fetchone()[0]
    total = v.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    dupes = v.execute("""SELECT COUNT(*) FROM (SELECT patient_id FROM submissions
                         GROUP BY patient_id HAVING COUNT(*) > 1)""").fetchone()[0]
    v.close()

    print(f"database        : {live}")
    print(f"duplicates dropped: {len(dropped)}  {[d[0] for d in dropped]}")
    print(f"cases added       : {len(added)}")
    for ext, cat, tier, pr in added:
        print(f"   {ext:20} {cat:18} tier={str(tier):11} priority={pr}")
    print(f"med-selection cases added: {len(medsel_added)}")
    for ext, cat, opt_ids, ranking in medsel_added:
        print(f"   {ext:20} {cat:18} options={opt_ids} family_ranking={ranking}")
    print(f"submissions       : {total} (patients with >1 submission: {dupes})")
    print(f"by category       : {dict(cats)}")
    print(f"by tier           : {dict(tiers)}")
    print(f"integrity         : {integrity}")

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return 0
    if integrity != "ok" or dupes:
        print("\nrefusing to swap: integrity or duplicate check failed.")
        return 1
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(live, f"{live}.bak-{ts}")
    staged = f"{live}.new-{ts}"
    shutil.copy2(work, staged)
    os.replace(staged, live)
    print(f"\nswapped in. backup: {os.path.basename(live)}.bak-{ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
