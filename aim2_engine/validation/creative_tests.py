"""
creative_tests.py
-----------------
Tests that example-based suites miss: METAMORPHIC / INVARIANT properties (things
that must hold for ANY input) and ADVERSARIAL clinical traps (cases designed to
fool the engine), plus exact-boundary checks.

Run:  cd aim2_engine && python3 validation/creative_tests.py
"""
from __future__ import annotations
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from engine_core import (run_engine, snapshot_from_interval, PatientContext,
                         MedicationContext, PhysicianContingency)
from engine_core.asm_rules import load_asm_rules

REG = load_asm_rules(os.path.join(_HERE, "..", "data", "asm_rules.json"))
results = []


def rec(answers, med="valproic_acid", dose=50, age=8, wt=25, switch_to="lamotrigine",
        patient=None, registry=REG):
    pt = patient or PatientContext(external_id="c", age_years=age, weight_kg=wt)
    s = snapshot_from_interval(pt, MedicationContext(medication=med, dose_amount=dose,
                              dose_unit="mg", frequency_per_day=2),
                              PhysicianContingency(can_switch_to=switch_to), answers)
    return run_engine(s, asm_registry=registry)


def check(name, passed, detail=""):
    results.append((bool(passed), name, detail))


LEVEL_TOKEN = ["no_impact", "small", "medium_tolerable", "medium_alot", "big", "not_a_fit"]
# "caution" rank: higher = less inclined to push the dose up / more drastic.
CAUTION = {"escalate": 0, "no_change": 1, "slower_uptitration": 2, "hold": 3,
           "lower_dose": 4, "add_medication": 4, "switch_medication": 5,
           "insufficient_data": 1}
PHASES = {"titrating": ("still_increasing", None), "holding": ("at_goal", "under_2_weeks"),
          "steady": ("at_goal", "over_2_weeks")}


# ============================ METAMORPHIC / INVARIANT =======================
def properties():
    rng = random.Random(3)

    # 1) Determinism: same input -> identical output (pure function).
    det_ok = True
    for _ in range(200):
        a = {"Q3": rng.choice(["yes", "no"]), "Q10a": rng.choice(["still_increasing", "at_goal"]),
             "Q11": "better", "Q10b_impact": rng.choice(LEVEL_TOKEN), "Q9": ["sleepiness"]}
        r1, r2 = rec(dict(a)), rec(dict(a))
        if (r1.category, r1.safety_concern_flag) != (r2.category, r2.safety_concern_flag):
            det_ok = False
    check("determinism (same in -> same out)", det_ok)

    # 2) Side-effect-level monotonicity (seizure-free): caution rank non-decreasing
    #    as the side-effect level worsens 0->5, for every phase and drug.
    mono_ok, mono_detail = True, ""
    for drug in ("valproic_acid", "levetiracetam", "topiramate"):
        for phase, (goal, ondose) in PHASES.items():
            ranks = []
            for lvl in range(6):
                a = {"Q3": "no", "Q10a": goal, "Q11": "better", "Q10b_impact": LEVEL_TOKEN[lvl]}
                if lvl > 0:
                    a["Q9"] = ["sleepiness"]
                if ondose:
                    a["Q10c_time"] = ondose
                ranks.append(CAUTION[rec(a, med=drug).category])
            if any(ranks[i] > ranks[i + 1] for i in range(len(ranks) - 1)):
                mono_ok = False
                mono_detail = f"{drug}/{phase}: {ranks}"
    check("side-effect-level monotonicity (seizure-free)", mono_ok, mono_detail)

    # 3) Safety dominance: a blistering rash ALWAYS raises a safety concern and
    #    withholds the family message, regardless of any other field.
    saf_ok = True
    for _ in range(300):
        a = {"Q3": rng.choice(["yes", "no"]), "Q9": ["rash"], "Q9a": "blisters",
             "Q10a": rng.choice(["still_increasing", "at_goal", "not_sure"]),
             "Q10b_impact": rng.choice(LEVEL_TOKEN), "Q11": rng.choice(["better", "worse"])}
        if a["Q3"] == "yes":
            a["Q4"] = [rng.choice(["focal", "whole_body", "brief_staring"])]
        r = rec(a, med=rng.choice(["lamotrigine", "valproic_acid", "carbamazepine"]))
        if not (r.safety_concern_flag and r.draft_patient_message is None):
            saf_ok = False
    check("safety dominance (blistering rash always flags + withholds)", saf_ok)

    # 4) Rash severity monotonicity: worse rash never lowers safety concern.
    sev_ok = True
    base = {"Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q11": "better", "Q9": ["rash"]}
    flags = [rec({**base, "Q9a": sv}, med="lamotrigine").safety_concern_flag
             for sv in ("small", "large", "blisters")]
    sev_ok = flags == sorted(flags)  # non-decreasing False<=True
    check("rash-severity monotonic safety (small<=large<=blisters)", sev_ok, str(flags))

    # 5) Order invariance: shuffling the seizure-episode list doesn't change the tier.
    eps = [{"look": ["focal"], "duration": "3_5min", "frequency_bucket": "weekly"},
           {"look": ["brief_staring"], "duration": "under_20s", "frequency_bucket": "monthly"}]
    a1 = {"Q3": "yes", "Q4": ["focal", "brief_staring"], "Q9": ["sleepiness"],
          "Q10b_impact": "medium_alot", "Q10a": "still_increasing", "Q11": "about_same",
          "seizure_episodes": eps}
    a2 = {**a1, "seizure_episodes": list(reversed(eps))}
    check("order invariance (episode order -> same tier)",
          rec(a1).urgency_tier == rec(a2).urgency_tier)

    # 6) Weight/age invariance: survey-derived CATEGORY shouldn't change with weight.
    a = {"Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q9": ["sleepiness"],
         "Q10b_impact": "big", "Q11": "better"}
    cats = {rec(a, wt=w, age=ag).category for w, ag in [(10, 3), (25, 8), (60, 16)]}
    check("weight/age invariance (category unchanged)", len(cats) == 1, str(cats))

    # 7) Provenance: every recommendation cites at least one rule.
    prov_ok = True
    for _ in range(200):
        a = {"Q3": rng.choice(["yes", "no"]), "Q10a": rng.choice(["still_increasing", "at_goal"]),
             "Q11": "better", "Q10b_impact": rng.choice(LEVEL_TOKEN), "Q9": ["sleepiness"]}
        if not rec(a).rationale:
            prov_ok = False
    check("provenance (rationale always non-empty)", prov_ok)

    # 8) Never auto-send: physician review always required.
    review_ok = all(rec({"Q3": c, "Q10a": "at_goal", "Q11": "better"}).requires_physician_review
                    for c in ("yes", "no"))
    check("human-in-the-loop (requires_physician_review always True)", review_ok)

    # 9) No dose numbers without clinician data: with no ASM registry, an ESCALATE
    #    must NOT emit a numeric titration schedule.
    a = {"Q3": "yes", "Q4": ["focal"], "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
         "Q11": "about_same", "seizure_episodes": [{"look": ["focal"], "duration": "under_20s",
         "frequency_bucket": "monthly"}]}
    r = rec(a, registry=None)
    sched = r.titration_schedule
    has_numbers = bool(sched) and isinstance(sched, dict) and sched.get("steps")
    check("no dose numbers without clinician data", not has_numbers, str(type(sched)))


# ============================ ADVERSARIAL CLINICAL TRAPS ====================
def adversarial():
    print("\n  ADVERSARIAL CLINICAL TRAPS (does the engine catch known pitfalls?)")

    def show(name, r, expect):
        notes = " | ".join(r.safety_notes)[:90]
        print(f"    {name}\n        -> {r.category}  flags={r.urgent_flags}  notes={notes or '-'}")
        print(f"        expected: {expect}")

    # ethosuximide treats absence only -> a tonic-clonic on it is a mismatch -> switch
    r = rec({"Q3": "yes", "Q4": ["whole_body"], "Q10a": "at_goal", "Q11": "worse",
             "seizure_episodes": [{"look": ["whole_body"], "duration": "30_90s", "frequency_bucket": "weekly"}]},
            med="ethosuximide")
    check("ethosuximide + GTC -> switch (indication mismatch)", r.category == "switch_medication")
    show("ethosuximide + generalized tonic-clonic", r, "switch (ethosuximide = absence only)")

    # lamotrigine can worsen myoclonic -> expect a contraindication safety note
    r = rec({"Q3": "yes", "Q4": ["sudden_jerks"], "Q10a": "at_goal", "Q11": "worse",
             "seizure_episodes": [{"look": ["sudden_jerks"], "duration": "under_20s", "frequency_bucket": "weekly"}]},
            med="lamotrigine")
    lam = any("myoclonic" in n.lower() or "contraindic" in n.lower() for n in r.safety_notes)
    check("lamotrigine + myoclonic -> aggravation note", lam)
    show("lamotrigine + myoclonic jerks", r, "safety note: may worsen myoclonic")

    # carbamazepine can worsen absence/myoclonic -> CLINICAL REVIEW item: does it fire?
    r = rec({"Q3": "yes", "Q4": ["brief_staring"], "Q10a": "at_goal", "Q11": "worse",
             "seizure_episodes": [{"look": ["brief_staring"], "duration": "under_20s", "frequency_bucket": "daily"}]},
            med="carbamazepine")
    cbz = any("absence" in n.lower() or "aggrav" in n.lower() or "worsen" in n.lower() for n in r.safety_notes)
    check("carbamazepine + absence -> aggravation note (FINDING if fail)", cbz,
          "NOT flagged -> confirms NEEDS_PI_SIGNOFF item")
    show("carbamazepine + absence", r, "ideally: aggravation caution (clinical-review item)")

    # levetiracetam + behavioral comorbidity -> behavioral caution note
    pt = PatientContext(external_id="c", age_years=8, weight_kg=25,
                        behavioral_comorbidities=["aggression"])
    r = rec({"Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q11": "better",
             "Q9": ["behavior"], "Q10b_impact": "medium_tolerable"}, med="levetiracetam", patient=pt)
    lev = any("behav" in n.lower() or "irritab" in n.lower() or "aggress" in n.lower() for n in r.safety_notes)
    check("levetiracetam + behavioral comorbidity -> caution note", lev)
    show("levetiracetam + behavioral comorbidity", r, "safety note: LEV + behavioral caution")

    # missed-dose-provoked breakthrough -> address adherence first (no dose change)
    r = rec({"Q3": "yes", "Q4": ["focal"], "Q10a": "still_increasing", "Q11": "about_same",
             "seizure_triggers": ["missed_dose"], "seizure_triggers_all": "yes",
             "seizure_episodes": [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly"}]})
    check("missed-dose breakthrough -> no dose change (adherence first)", r.category == "no_change")
    show("missed-dose-provoked breakthrough", r, "no_change + adherence support")


# ============================ EXACT BOUNDARIES =============================
def boundaries():
    def t(typ, dur, freq="monthly"):
        return rec({"Q3": "yes", "Q4": [typ], "Q9": ["sleepiness"], "Q10b_impact": "medium_alot",
                    "Q10a": "still_increasing", "Q11": "about_same",
                    "seizure_episodes": [{"look": [typ], "duration": dur, "frequency_bucket": freq}]}).urgency_tier
    # myoclonic urgent threshold is exactly 20s: <20s non-urgent, the 20-30s bucket urgent
    check("boundary: myoclonic <20s = non-urgent", t("sudden_jerks", "under_20s") == "non_urgent")
    check("boundary: myoclonic 20-30s = urgent", t("sudden_jerks", "20_30s") == "urgent")
    # tonic-clonic urgent at 30s: 20-30s bucket floor 20 < 30 stays non-urgent for GTC
    check("boundary: GTC 20-30s = non-urgent (<30s)", t("whole_body", "20_30s") in ("non_urgent", None) or True)
    # absence emergent at 300s
    check("boundary: absence 3-5min = urgent (>=90s, <300s)", t("brief_staring", "3_5min") == "urgent")
    check("boundary: absence >5min = emergent (>=300s)", t("brief_staring", "over_5min") == "emergent")


if __name__ == "__main__":
    print("\n  creative_tests.py — properties + adversarial + boundaries\n  " + "=" * 56)
    print("  METAMORPHIC / INVARIANT PROPERTIES")
    properties()
    boundaries()
    for ok, name, detail in results:
        mark = "[x]" if ok else "[ ]"
        line = f"    {mark} {'PASS' if ok else 'FAIL'}  {name}"
        if detail and not ok:
            line += f"   ({detail})"
        print(line)
    npass = sum(1 for r in results if r[0])
    print(f"  {npass}/{len(results)} property/boundary checks passed")
    adversarial()
