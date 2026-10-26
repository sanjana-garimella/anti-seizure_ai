#!/usr/bin/env python3
"""
paraphrase_100.py
-----------------
100 new synthetic cases, labeled before anything is scored.

Engine labels come from the decision-table JSON (or, for the 10 safety probes,
from the written safety rule), not from run_engine. Caregiver sentences are
written to express a fact list that was fixed first. The lexicon is then scored
against that list. Three wording styles:

  lexical     uses words the lexicon lists
  paraphrase  caregiver wording that avoids those words
  negation    "sleeping normally / no rash" on seizure-free, no-symptom cases

What a match means: the code agrees with the tables, and the word list finds
the facts when the sentence uses its own words. It is not chart review, and it
is not a parent-language accuracy claim. The paraphrase slice is the one that
can fail.

Run:  cd aim2_engine && python3 validation/paraphrase_100.py
Writes paraphrase_100_cases.json and paraphrase_100_report.json beside this file.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

from engine_core import (  # noqa: E402
    run_engine, snapshot_from_interval, PatientContext, MedicationContext,
    PhysicianContingency, RecommendationCategory, ENGINE_VERSION,
)
from engine_core.asm_rules import load_asm_rules  # noqa: E402
from engine_core.categorize import categorize  # noqa: E402
from nlp.structurer import LexiconStructurer  # noqa: E402
import metrics as M  # noqa: E402

REG = load_asm_rules(os.path.join(_ROOT, "data", "asm_rules.json"))
DATA = os.path.join(_ROOT, "data")
NS = json.load(open(os.path.join(DATA, "no_seizures_decision_table.json")))
BT = json.load(open(os.path.join(DATA, "breakthrough_decision_tables.json")))

LEVEL_TOKEN = ["no_impact", "small", "medium_tolerable", "medium_alot", "big", "not_a_fit"]
SYMPTOM = {1: "sleepiness", 2: "dizziness", 3: "irritability", 4: "nausea", 5: "behavior"}
_ACT = {
    "continue": "no_change", "no_change": "no_change", "slower": "slower_uptitration",
    "hold": "hold", "lower": "lower_dose", "add_medication": "add_medication",
    "switch": "switch_medication", "switch_medication": "switch_medication",
    "escalate": "escalate",
}
PHASES = {
    "titrating": ("still_increasing", None),
    "holding": ("at_goal", "under_2_weeks"),
    "steady": ("at_goal", "over_2_weeks"),
}
TIERS = {
    "non_urgent": ("focal", "under_20s", "monthly"),
    "urgent": ("focal", "3_5min", "weekly"),
    "emergent": ("focal", "over_5min", "weekly"),
}
LEXICAL_SX = {
    "sleepiness": "She has been drowsy and tired since the last visit.",
    "dizziness": "He feels dizzy and unsteady when he stands up.",
    "irritability": "She has been irritable and cranky in the afternoons.",
    "nausea": "He has been nauseous and vomited after the morning dose.",
    "behavior": "He has been aggressive and hyper at school.",
}
# Caregiver wording chosen to avoid the lexicon's symptom patterns.
PARAPHRASE_SX = {
    "sleepiness": "She can barely keep her eyes open after lunch.",
    "dizziness": "The room spins when he gets out of bed.",
    "irritability": "She snaps at everyone and cries more than usual.",
    "nausea": "His stomach turns and he leaves most of the plate.",
    "behavior": "He has been hitting and yelling at his sister.",
}
VALID = {c.value for c in RecommendationCategory}


def table_category(kind, phase, level, tier=None):
    if kind == "no_seizures":
        return _ACT[NS["actions_by_phase_level"][phase][level]]
    cell = BT["tables"][tier][phase][level]
    return _ACT[cell["category"]]


def build_cases():
    """Facts and labels first. Sentence text is chosen from the fact list."""
    cases = []

    def add_table(kind, phase, level, tier, age, weight):
        goal, ondose = PHASES[phase]
        answers = {"Q2": "almost_never", "Q3": "no" if kind == "no_seizures" else "yes",
                   "Q10a": goal, "Q10b_impact": LEVEL_TOKEN[level], "Q11": "better"}
        if level > 0:
            answers["Q9"] = [SYMPTOM[level]]
        if ondose:
            answers["Q10c_time"] = ondose
        if kind == "breakthrough":
            typ, dur, freq = TIERS[tier]
            answers["Q4"] = [typ]
            answers["seizure_episodes"] = [{
                "look": [typ], "duration": dur, "frequency_bucket": freq,
            }]
        symptom = SYMPTOM.get(level)
        cases.append({
            "kind": kind,
            "phase": phase,
            "level": level,
            "tier": tier,
            "age_years": age,
            "weight_kg": weight,
            "drug": "levetiracetam",
            "dose_amount": 250,
            "answers": answers,
            "gold_category": table_category(kind, phase, level, tier),
            "label_source": "decision table JSON, read before run_engine",
            "expect_safety": False,
            "symptom": symptom,
            "breakthrough": kind == "breakthrough",
        })

    for age, weight in ((8, 30), (11, 40)):
        for phase in PHASES:
            for level in range(6):
                add_table("no_seizures", phase, level, None, age, weight)
    for tier in TIERS:
        for phase in PHASES:
            for level in range(6):
                add_table("breakthrough", phase, level, tier, 8, 30)

    # Safety probes. The label is the written rule, fixed before the call.
    for i in range(6):
        cases.append({
            "kind": "safety_rash",
            "phase": None, "level": None, "tier": None,
            "age_years": 8 + i, "weight_kg": 28, "drug": "lamotrigine",
            "dose_amount": 25,
            "answers": {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "blisters",
                        "Q10a": "still_increasing", "Q11": "better"},
            "gold_category": "switch_medication",
            "label_source": "R0 spec: blistering rash -> stop and switch, withhold the family message",
            "expect_safety": True,
            "symptom": "rash",
            "breakthrough": False,
        })
    for i in range(4):
        cases.append({
            "kind": "safety_prolonged",
            "phase": None, "level": None, "tier": None,
            "age_years": 9, "weight_kg": 32, "drug": "levetiracetam",
            "dose_amount": 250,
            "answers": {
                "Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing", "Q11": "better",
                "Q4": ["whole_body"],
                "seizure_episodes": [{"look": ["whole_body"], "duration": "over_3min",
                                      "frequency": 1, "frequency_unit": "per_week"}],
            },
            "gold_category": "escalate",
            "label_source": "R0b spec: whole-body over 3 min while the medicine is helping -> escalate, withhold the family message",
            "expect_safety": True,
            "symptom": None,
            "breakthrough": True,
            "prolonged": True,
        })

    assert len(cases) == 100, len(cases)
    for i, case in enumerate(cases, start=1):
        case["case_id"] = f"P{i:03d}"
        style, text, gold_ents = _wording(i, case)
        case["wording_style"] = style
        case["free_text"] = text
        case["gold_entities"] = sorted(gold_ents)
    return cases


def _wording(i, case):
    """Pick a sentence for a fact list that is already fixed. The entity gold
    is that fact list, not a reading of the sentence."""
    drug = case["drug"]
    symptom = case["symptom"]
    prolonged = case.get("prolonged")
    seizure_free_quiet = (not case["breakthrough"]) and symptom is None and not case["expect_safety"]
    if seizure_free_quiet:
        style = "negation"
        text = ("He has been eating and sleeping normally. No rash and no dizziness. "
                f"Still giving {drug}.")
        gold = {("drug_mention", drug)}
        return style, text, gold
    style = "lexical" if i % 2 == 0 else "paraphrase"
    parts = []
    gold = set()
    if style == "lexical":
        parts.append(f"Still giving {drug} twice a day.")
        gold.add(("drug_mention", drug))
        if symptom == "rash":
            parts.append("He broke out in a rash with blisters.")
            gold.add(("side_effect", "rash"))
        elif symptom:
            parts.append(LEXICAL_SX[symptom])
            gold.add(("side_effect", symptom))
        if prolonged:
            parts.append("He had a whole body shaking seizure for several minutes.")
            gold.add(("seizure_mention", "seizure_reported"))
        elif case["breakthrough"]:
            parts.append("She had a seizure yesterday.")
            gold.add(("seizure_mention", "seizure_reported"))
    else:
        parts.append("Still giving the medicine twice a day.")
        if symptom == "rash":
            parts.append("Red patches and open sores showed up on his cheeks.")
            gold.add(("side_effect", "rash"))
        elif symptom:
            parts.append(PARAPHRASE_SX[symptom])
            gold.add(("side_effect", symptom))
        if prolonged or case["breakthrough"]:
            parts.append("She went stiff and was not responding for a short time.")
            gold.add(("seizure_mention", "seizure_reported"))
    return style, " ".join(parts), gold


def _snap(case):
    return snapshot_from_interval(
        PatientContext(external_id=case["case_id"], age_years=case["age_years"],
                       weight_kg=case["weight_kg"]),
        MedicationContext(medication=case["drug"], dose_amount=case["dose_amount"],
                          dose_unit="mg", frequency_per_day=2,
                          time_on_medication="4_12_weeks"),
        PhysicianContingency(can_switch_to="lamotrigine"),
        case["answers"],
    )


def _entities(text):
    found = set()
    for f in LexiconStructurer().structure(text):
        if f.field == "drug_mention":
            found.add(("drug_mention", f.suggested_value.lower()))
        elif f.field == "side_effect":
            found.add(("side_effect", f.suggested_value))
        elif f.field == "seizure_mention":
            found.add(("seizure_mention", f.suggested_value))
    return found


def _prf(cases, key):
    tp = fp = fn = 0
    for case in cases:
        gold = set(map(tuple, case["gold_entities"]))
        pred = set(map(tuple, case[key]))
        tp += len(gold & pred)
        fp += len(pred - gold)
        fn += len(gold - pred)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = (2 * prec * rec / (prec + rec)) if prec and rec else (0.0 if fn or fp else None)
    def r(x):
        return None if x is None else round(x, 4)
    return {"n": len(cases), "tp": tp, "fp": fp, "fn": fn,
            "precision": r(prec), "recall": r(rec), "f1": r(f1)}


def main():
    cases = build_cases()
    leaks = landing_misses = review_misses = safety_misses = 0
    gold, pred = [], []
    for case in cases:
        snap = _snap(case)
        status = categorize(snap, REG.get(case["drug"]))
        rec = run_engine(snap, asm_registry=REG)
        case["engine_category"] = rec.category
        case["engine_rule"] = rec.rationale[-1]["rule"] if rec.rationale else None
        case["safety_flag"] = bool(rec.safety_concern_flag)
        case["family_message_present"] = rec.draft_patient_message is not None
        case["requires_physician_review"] = bool(rec.requires_physician_review)
        case["landed"] = {
            "phase": status.phase,
            "level": status.side_effect_level,
            "tier": status.breakthrough_tier,
        }
        if case["kind"] in ("no_seizures", "breakthrough"):
            ok = (status.phase == case["phase"] and status.side_effect_level == case["level"]
                  and (case["tier"] is None or status.breakthrough_tier == case["tier"]))
            if not ok:
                landing_misses += 1
                case["landing_miss"] = True
        if not rec.requires_physician_review:
            review_misses += 1
        if rec.safety_concern_flag and rec.draft_patient_message is not None:
            leaks += 1
        if case["expect_safety"] and not rec.safety_concern_flag:
            safety_misses += 1
        if rec.category not in VALID:
            case["bad_category"] = True
        pred_ents = sorted(_entities(case["free_text"]))
        case["lexicon_entities"] = pred_ents
        gold.append(case["gold_category"])
        pred.append(rec.category)

    table = [c for c in cases if c["kind"] in ("no_seizures", "breakthrough")]
    probes = [c for c in cases if c not in table]
    table_gold = [c["gold_category"] for c in table]
    table_pred = [c["engine_category"] for c in table]
    rep = M.evaluate_categorical(table_gold, table_pred, n_boot=1000)
    mismatches = [
        {"case_id": c["case_id"], "kind": c["kind"], "cell": [c["tier"], c["phase"], c["level"]],
         "gold": c["gold_category"], "engine": c["engine_category"], "rule": c["engine_rule"]}
        for c in table if c["gold_category"] != c["engine_category"]
    ]
    probe_miss = [
        {"case_id": c["case_id"], "kind": c["kind"], "gold": c["gold_category"],
         "engine": c["engine_category"], "safety": c["safety_flag"]}
        for c in probes
        if c["gold_category"] != c["engine_category"] or c["safety_flag"] != c["expect_safety"]
    ]
    by_style = {}
    for style in ("lexical", "paraphrase", "negation"):
        by_style[style] = _prf([c for c in cases if c["wording_style"] == style], "lexicon_entities")

    report = {
        "engine_version": ENGINE_VERSION,
        "n_cases": 100,
        "what_this_is": (
            "Labels were fixed from the decision-table JSON (90 cases) or the written "
            "safety rule (10 probes) before run_engine. Caregiver sentences express that "
            "fact list. Lexicon F1 on the paraphrase slice is robustness to wording the "
            "word list does not contain. This is not chart-review validation."
        ),
        "engine_vs_table": {
            "n": len(table),
            "concordance": round(rep["observed_agreement"], 4),
            "cohens_kappa": round(rep["cohens_kappa"], 4),
            "cohens_kappa_ci": [round(x, 3) for x in rep["cohens_kappa_ci"]],
            "n_excluded_deferrals": rep["n_excluded_deferrals"],
            "weighted_kappa_linear": round(rep["weighted_kappa_linear"], 4),
            "macro_f1": round(rep["macro_prf"]["f1"], 4),
            "mismatches": mismatches,
        },
        "safety_probes": {
            "n": len(probes),
            "misses": probe_miss,
            "safety_flag_misses": safety_misses,
            "family_message_leaks": leaks,
        },
        "properties": {
            "landing_misses": landing_misses,
            "physician_review_misses": review_misses,
            "family_message_leaks_on_safety": leaks,
        },
        "lexicon_vs_preassigned_facts": {
            "overall": _prf(cases, "lexicon_entities"),
            "by_style": by_style,
        },
    }

    # Drop nothing clinical from the case file: these are synthetic inputs.
    case_path = os.path.join(_HERE, "paraphrase_100_cases.json")
    report_path = os.path.join(_HERE, "paraphrase_100_report.json")
    with open(case_path, "w") as f:
        json.dump({"_README": report["what_this_is"], "n_cases": 100, "cases": cases}, f, indent=2)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    eng = report["engine_vs_table"]
    lex = report["lexicon_vs_preassigned_facts"]
    print(f"engine {ENGINE_VERSION}  cases 100")
    print(f"table cells {eng['n']}  concordance {eng['concordance']}  "
          f"kappa {eng['cohens_kappa']}  CI {tuple(eng['cohens_kappa_ci'])}  "
          f"mismatches {len(eng['mismatches'])}")
    print(f"landing misses {landing_misses}  safety leaks {leaks}  "
          f"review misses {review_misses}  probe misses {len(probe_miss)}")
    def _fmt(x):
        return "n/a" if x is None else x
    print(f"lexicon overall F1 {_fmt(lex['overall']['f1'])}  "
          f"P {_fmt(lex['overall']['precision'])}  R {_fmt(lex['overall']['recall'])}")
    for style, row in by_style.items():
        print(f"  {style:12} n={row['n']:3}  F1 {_fmt(row['f1'])}  P {_fmt(row['precision'])}  "
              f"R {_fmt(row['recall'])}  tp {row['tp']} fp {row['fp']} fn {row['fn']}")
    if mismatches:
        print("table mismatches:")
        for row in mismatches:
            print(" ", row)
    if probe_miss:
        print("probe misses:")
        for row in probe_miss:
            print(" ", row)
    return 0 if not mismatches and not probe_miss and landing_misses == 0 and leaks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
