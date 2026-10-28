"""
holdout_test.py
---------------
A HELD-OUT generalization + stress test for the engine.

Why: the 63 gold cases are what the rules were written against, so scoring on them
is circular. Here we instead (1) GENERATE fresh cases across the input space the
engine was never tuned on, (2) compute the expected answer with an INDEPENDENT
"oracle" that re-derives it straight from Kristen's spec data files (NOT from
decision.py), and (3) score the engine against the oracle with metrics.py.

This tests SPEC-CONFORMANCE and ROBUSTNESS across a broad, independent set — and,
by generating many safety-positive cases, gives a tighter false-negative bound.
It is still not clinical truth (labels derive from the same clinical spec).

Plus a STRESS/FUZZ pass: thousands of random/malformed inputs to confirm the
engine never crashes, always returns a valid category, always requires physician
review, and NEVER emits a family message when a safety concern is flagged.

Run:  cd aim2_engine && python3 validation/holdout_test.py
"""
from __future__ import annotations

import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

from engine_core import (run_engine, snapshot_from_interval, PatientContext,
                         MedicationContext, PhysicianContingency, RecommendationCategory)
from engine_core.asm_rules import load_asm_rules
import metrics as M

REG = load_asm_rules(os.path.join(_ROOT, "data", "asm_rules.json"))
DATA = os.path.join(_ROOT, "data")
NS = json.load(open(os.path.join(DATA, "no_seizures_decision_table.json")))
BT = json.load(open(os.path.join(DATA, "breakthrough_decision_tables.json")))
URG = json.load(open(os.path.join(DATA, "breakthrough_urgency_thresholds.json")))
VALID = {c.value for c in RecommendationCategory}

LEVEL_TOKEN = ["no_impact", "small", "medium_tolerable", "medium_alot", "big", "not_a_fit"]
_ACT = {"continue": "no_change", "no_change": "no_change", "slower": "slower_uptitration",
        "hold": "hold", "lower": "lower_dose", "add_medication": "add_medication",
        "switch": "switch_medication", "switch_medication": "switch_medication",
        "escalate": "escalate"}

# ---- INDEPENDENT ORACLE (reads spec data; does not import decision.py) -------
_TYPE_CLASS = {"brief_staring": "absence", "sudden_jerks": "myoclonic",
               "whole_body": "generalized_tonic_clonic", "focal": "focal",
               "acting_off": "focal_impaired_awareness", "other": "other"}
_DUR_S = {"under_20s": 0, "20_30s": 20, "30_90s": 30, "90s_3min": 90, "3_5min": 180, "over_5min": 300}
_FREQ_W = {"daily": 7.0, "weekly": 2.0, "monthly": 0.25, "less_than_monthly": 0.1}


def oracle_tier(typ, dur, freq):
    th = URG["by_seizure_type"].get(_TYPE_CLASS.get(typ, "other"))
    if not th:
        return "non_urgent"
    secs = _DUR_S.get(dur)
    pw = _FREQ_W.get(freq)
    per_day = pw / 7.0 if pw is not None else None
    per_wk = pw
    per_4wk = pw * 4 if pw is not None else None

    def hits(pfx):
        if th.get(pfx + "_duration_s") is not None and secs is not None and secs >= th[pfx + "_duration_s"]:
            return True
        for u, v in (("per_day", per_day), ("per_week", per_wk), ("per_4wk", per_4wk)):
            t = th.get(f"{pfx}_freq_{u}")
            if t is not None and v is not None and v >= t:
                return True
        return False

    return "emergent" if hits("emergent") else ("urgent" if hits("urgent") else "non_urgent")


def oracle_no_seizures(phase, level):
    return _ACT[NS["actions_by_phase_level"][phase][level]]


def oracle_breakthrough(tier, phase, level):
    return _ACT[BT["tables"][tier][phase][level]["category"]]


# ---- case construction ------------------------------------------------------
def snap(answers, med="valproic_acid", dose=50, age=8, wt=25, switch_to="lamotrigine"):
    return snapshot_from_interval(
        PatientContext(external_id="h", age_years=age, weight_kg=wt),
        MedicationContext(medication=med, dose_amount=dose, dose_unit="mg", frequency_per_day=2),
        PhysicianContingency(can_switch_to=switch_to), answers)


PHASES = {"titrating": ("still_increasing", None),
          "holding": ("at_goal", "under_2_weeks"),
          "steady": ("at_goal", "over_2_weeks")}


def gen_no_seizures():
    cases = []
    for drug in ("valproic_acid", "levetiracetam", "lamotrigine", "topiramate"):
        for phase, (goal, ondose) in PHASES.items():
            for lvl in range(6):
                a = {"Q3": "no", "Q10a": goal, "Q11": "better", "Q10b_impact": LEVEL_TOKEN[lvl]}
                if lvl > 0:
                    a["Q9"] = ["sleepiness"]
                if ondose:
                    a["Q10c_time"] = ondose
                cases.append((oracle_no_seizures(phase, lvl), snap(a, med=drug)))
    return cases


def gen_breakthrough():
    cases = []
    tier_inputs = {"non_urgent": ("focal", "under_20s", "monthly"),
                   "urgent": ("focal", "3_5min", "weekly"),
                   "emergent": ("focal", "over_5min", "weekly")}
    for tier, (typ, dur, freq) in tier_inputs.items():
        assert oracle_tier(typ, dur, freq) == tier, (typ, dur, freq, oracle_tier(typ, dur, freq))
        for phase, (goal, ondose) in PHASES.items():
            for lvl in range(0, 6):
                a = {"Q3": "yes", "Q4": [typ], "Q9": ["sleepiness"], "Q10b_impact": LEVEL_TOKEN[lvl],
                     "Q10a": goal, "Q11": "about_same",
                     "seizure_episodes": [{"look": [typ], "duration": dur, "frequency_bucket": freq}]}
                if ondose:
                    a["Q10c_time"] = ondose
                cases.append((oracle_breakthrough(tier, phase, lvl), snap(a)))
    return cases


def gen_safety():
    """Many blistering-rash positives (vary context) + benign negatives."""
    pos, neg = [], []
    rng = random.Random(1)
    drugs = ["lamotrigine", "carbamazepine", "valproic_acid", "levetiracetam", "oxcarbazepine"]
    for i in range(40):
        a = {"Q3": rng.choice(["no", "yes"]), "Q9": ["rash"], "Q9a": "blisters",
             "Q10a": rng.choice(["still_increasing", "at_goal"]), "Q11": "about_same"}
        if a["Q3"] == "yes":
            a["Q4"] = ["focal"]
        pos.append((True, snap(a, med=rng.choice(drugs), age=rng.randint(2, 17))))
    for i in range(40):
        a = {"Q3": "no", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q11": "better"}
        neg.append((False, snap(a, med=rng.choice(drugs))))
    return pos + neg


# ---- run held-out ------------------------------------------------------------
def held_out():
    print("\n  HELD-OUT GENERALIZATION TEST (fresh cases vs an independent spec-oracle)")
    cat_cases = gen_no_seizures() + gen_breakthrough()
    gold = [g for g, _ in cat_cases]
    pred = [run_engine(s, asm_registry=REG).category for _, s in cat_cases]
    rep = M.evaluate_categorical(gold, pred, deferral_mode="class", n_boot=1000)
    mism = [(g, p) for g, p in zip(gold, pred) if g != p]
    print(f"    categorical cases     : {len(gold)} (seizure-free + breakthrough L0-5)")
    print(f"    observed agreement    : {rep['observed_agreement']:.3f}")
    print(f"    Cohen's kappa         : {rep['cohens_kappa']:.3f}  95% CI {tuple(round(x,3) for x in rep['cohens_kappa_ci'])}")
    print(f"    macro F1              : {rep['macro_prf']['f1']:.3f}")
    print(f"    engine-vs-oracle mismatches: {len(mism)}" + (f"  {mism[:6]}" if mism else "  (none)"))

    # urgency tier conformance (engine's classifier vs the independent oracle)
    bt = gen_breakthrough()
    tier_inputs = {"non_urgent": ("focal", "under_20s", "monthly"),
                   "urgent": ("focal", "3_5min", "weekly"),
                   "emergent": ("focal", "over_5min", "weekly")}
    tier_ok = 0
    tier_cases = []
    for tier, (typ, dur, freq) in tier_inputs.items():
        for phase, (goal, ondose) in PHASES.items():
            a = {"Q3": "yes", "Q4": [typ], "Q9": ["sleepiness"], "Q10b_impact": "medium_alot",
                 "Q10a": goal, "Q11": "about_same",
                 "seizure_episodes": [{"look": [typ], "duration": dur, "frequency_bucket": freq}]}
            if ondose:
                a["Q10c_time"] = ondose
            eng_tier = run_engine(snap(a), asm_registry=REG).urgency_tier
            tier_cases.append((tier, eng_tier))
            tier_ok += (eng_tier == tier)
    print(f"    urgency-tier conformance: {tier_ok}/{len(tier_cases)} match the independent oracle")

    # safety on many generated positives -> tighter FNR bound
    saf = gen_safety()
    gflag = [g for g, _ in saf]
    pflag = [bool(run_engine(s, asm_registry=REG).safety_concern_flag) for _, s in saf]
    sm = M.safety_metrics(gflag, pflag)
    print(f"    safety positives generated: {sum(gflag)} ; sensitivity {sm['sensitivity']:.3f} "
          f"95% CI {tuple(round(x,3) for x in sm['sensitivity_ci'])}")
    print(f"    safety FNR (harm)        : {sm['false_negative_rate']:.3f} "
          f"95% CI {tuple(round(x,3) for x in sm['false_negative_rate_ci'])}  {sm['counts']}")
    return len(mism) == 0 and tier_ok == len(tier_cases) and sm["false_negative_rate"] == 0


# ---- stress / fuzz ----------------------------------------------------------
def stress(n=4000, seed=7):
    print(f"\n  STRESS / FUZZ TEST ({n} random + malformed inputs)")
    rng = random.Random(seed)
    meds = ["valproic_acid", "levetiracetam", "lamotrigine", "topiramate", "zonisamide",
            "clobazam", "ZONABLE", "", None, "Keppra", "made up drug", 12345]
    yn = ["yes", "no", "", None, "maybe", "YES", 1, 0, "true"]
    impacts = LEVEL_TOKEN + ["medium_big", "big_wants_change", "", None, "weird", 99]
    durs = list(_DUR_S) + ["over_3min", "1_3min", "under_1min", "not_sure", None, "", "forever"]
    freqs = list(_FREQ_W) + [None, "", "hourly", 5]
    types = ["brief_staring", "sudden_jerks", "whole_body", "focal", "acting_off", "other",
             "not_sure", "alien", None, ""]
    goals = ["still_increasing", "at_goal", "not_sure", None, "", "GOAL"]
    crashes = bad_cat = leaked = no_review = 0
    examples = []
    for _ in range(n):
        ans = {}
        if rng.random() < 0.9:
            ans["Q3"] = rng.choice(yn)
        if rng.random() < 0.85:
            ans["Q10a"] = rng.choice(goals)
        if rng.random() < 0.7:
            ans["Q11"] = rng.choice(["better", "worse", "about_same", None, "x"])
        if rng.random() < 0.7:
            ans["Q10b_impact"] = rng.choice(impacts)
            if rng.random() < 0.8:
                ans["Q9"] = rng.choice([["sleepiness"], ["rash"], [], ["x", "y"], "notalist"])
        if rng.random() < 0.5:
            ans["Q9a"] = rng.choice(["small", "large", "blisters", None, "huge"])
        if rng.random() < 0.6:
            neps = rng.randint(0, 3)
            eps = []
            for _ in range(neps):
                eps.append({"look": [rng.choice(types)], "duration": rng.choice(durs),
                            "frequency_bucket": rng.choice(freqs),
                            "count_4wk": rng.choice([None, 0, 3, 1200, -5, 1e9, "lots"])})
            ans["seizure_episodes"] = eps
            ans["Q4"] = [rng.choice(types) for _ in range(rng.randint(0, 3))]
        if rng.random() < 0.3:
            ans["seizure_triggers"] = rng.sample(["missed_dose", "lack_of_sleep", "fever_illness", "other"],
                                                 rng.randint(0, 3))
            ans["seizure_triggers_all"] = rng.choice(["yes", "no", None])
        try:
            s = snap(ans, med=rng.choice(meds),
                     dose=rng.choice([0, 50, 250, 999999, -10, None]),
                     age=rng.choice([0.5, 8, 17, 200, None]),
                     wt=rng.choice([3, 25, 300, None]),
                     switch_to=rng.choice(["lamotrigine", None, ""]))
            rec = run_engine(s, asm_registry=REG)
        except Exception as e:
            crashes += 1
            if len(examples) < 5:
                examples.append(f"CRASH {type(e).__name__}: {e} | {ans}")
            continue
        if rec.category not in VALID:
            bad_cat += 1
        if not rec.requires_physician_review:
            no_review += 1
        # CRITICAL invariant: a safety concern must withhold the family message.
        if rec.safety_concern_flag and rec.draft_patient_message is not None:
            leaked += 1
            if len(examples) < 5:
                examples.append(f"LEAK (safety+message): {ans}")
    print(f"    crashes                       : {crashes}")
    print(f"    invalid category returned     : {bad_cat}")
    print(f"    missing physician-review flag  : {no_review}")
    print(f"    SAFETY MESSAGE LEAKS (critical): {leaked}")
    for e in examples:
        print(f"      - {e}")
    return crashes == 0 and bad_cat == 0 and no_review == 0 and leaked == 0


if __name__ == "__main__":
    print("\n  holdout_test.py — held-out generalization + stress test\n  " + "=" * 58)
    ho = held_out()
    st = stress()
    print("\n  " + "=" * 58)
    print(f"  HELD-OUT: {'PASS' if ho else 'SEE MISMATCHES'}   |   STRESS/FUZZ: {'PASS' if st else 'FAILURES ABOVE'}")
    sys.exit(0 if ho and st else 1)
