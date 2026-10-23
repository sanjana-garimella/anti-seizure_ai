"""
nlp_freetext_eval.py
--------------------
Scored benchmark for the advisory free-text NLP layer. Chains the 2026-07-08
modules into one pipeline and scores it against the annotated gold set
(validation/data/freetext_gold.json) with the rigorous metrics
(nlp_eval_metrics): micro P/R/F1 + bootstrap CIs, per-field breakdown, a
red-flag recall gate (target ~1.0), and a calibration report.

Pipeline (advisory only - never touches engine_core):
   typo.correct_text -> normalize_duration_frequency -> side-effect detection
   (entity synonyms + ConText assertion + abstention) -> red-flag detection
   (lexicon + negation, high-recall).

100% simulated data, CONFIDENTIAL - stays in this folder.
Run:  python3 validation/nlp_freetext_eval.py   (writes freetext_eval_report.json)
Import: from nlp_freetext_eval import run
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))       # aim2_engine/
sys.path.insert(0, _HERE)                         # validation/

from nlp.typo import correct_text                                     # noqa: E402
from nlp.normalize_duration_frequency import normalize as dfn         # noqa: E402
from nlp.normalize_entities import SIDE_EFFECT_SYNONYMS, normalize as enorm  # noqa: E402
from nlp.context_assertion import assess, Assertion                   # noqa: E402
import nlp_eval_metrics as M                                          # noqa: E402

_GOLD = os.path.join(_HERE, "data", "freetext_gold.json")

# red-flag lexicon: category -> surface phrases (high-recall escalate path)
_RED_FLAGS = {
    "breathing": ["stopped breathing", "not breathing", "trouble breathing",
                  "blue lips", "turned blue", "lips turned blue"],
    "prolonged_seizure": ["wouldn't stop", "would not stop", "didn't stop",
                          "did not stop", "over 5 minutes", "more than 5 minutes",
                          "over five minutes"],
    "spreading_rash": ["spreading rash", "blister", "blistering", "peeling skin"],
    "unresponsive": ["unresponsive", "wouldn't wake up", "would not wake up",
                     "won't wake up", "not responding"],
}


def structure(text: str) -> Tuple[Set[Tuple[str, str]], Dict[Tuple[str, str], float]]:
    """Return (findings, confidence_by_finding) for one free-text snippet."""
    fixed, _ = correct_text(text)
    low = fixed.lower()
    findings: Set[Tuple[str, str]] = set()
    conf: Dict[Tuple[str, str], float] = {}

    def add(field, value, c):
        key = (field, value)
        findings.add(key)
        conf[key] = max(conf.get(key, 0.0), c)

    # --- duration / frequency ---
    for f in dfn(fixed):
        add(f.field, f.suggested_value, f.confidence)

    # --- side effects: synonym match -> ConText assertion -> abstain gate ---
    for canon, syns in SIDE_EFFECT_SYNONYMS.items():
        for s in sorted(syns + [canon.replace("_", " ")], key=len, reverse=True):
            m = re.search(r"\b" + re.escape(s.lower()) + r"\b", low)
            if not m:
                continue
            a = assess(fixed, (m.start(), m.end()))
            if a.is_current_patient_positive:          # not negated/historical/other
                c = 0.9 - (0.2 if a.hedged else 0.0)
                add("side_effect", canon, c)
            break  # one hit per concept is enough

    # --- red flags: lexicon, suppress only on direct negation (still high-recall) ---
    for cat, phrases in _RED_FLAGS.items():
        for p in phrases:
            m = re.search(re.escape(p), low)
            if not m:
                continue
            a = assess(fixed, (m.start(), m.end()))
            if not a.negated:
                add("red_flag", cat, 0.95)
            break
    # prolonged also fires from a resolved duration >= over_5min
    if ("seizure_duration_bucket", "over_5min") in findings:
        add("red_flag", "prolonged_seizure", 0.9)

    return findings, conf


def run(write: bool = True) -> Dict:
    data = json.load(open(_GOLD))
    items = data["items"]
    gold_docs = [set(tuple(x) for x in it["findings"]) for it in items]
    pred_docs, cal_scores, cal_labels = [], [], []
    for it, gold in zip(items, gold_docs):
        pred, conf = structure(it["text"])
        pred_docs.append(pred)
        for key, c in conf.items():
            cal_scores.append(c)
            cal_labels.append(1 if key in gold else 0)

    report = M.full_report(gold_docs, pred_docs, cal_scores, cal_labels)
    report["dataset"] = {"source": os.path.basename(_GOLD), "n_items": len(items),
                         "simulated": True}
    if write:
        out = os.path.join(_HERE, "freetext_eval_report.json")
        json.dump(report, open(out, "w"), indent=2, ensure_ascii=False)
        report["_written_to"] = out
    return report


if __name__ == "__main__":
    r = run()
    o = r["overall"]
    print(f"\nFREE-TEXT NLP BENCHMARK  (n={r['dataset']['n_items']}, simulated)")
    print(f"  overall  P={o['precision']}  R={o['recall']}  F1={o['f1']}  "
          f"(tp={o['tp']} fp={o['fp']} fn={o['fn']})")
    print(f"  F1 95% CI: {r['f1_ci']['ci']}")
    print(f"  red-flag recall: {r['red_flag']['recall']}  "
          f"(missed={r['red_flag']['missed']}, gate {'PASS' if r['red_flag']['pass'] else 'FAIL'})")
    print(f"  calibration ECE: {r['calibration']['ece']}")
    print("  per-field:")
    for f, s in sorted(r["per_field"].items()):
        print(f"    {f:26} P={s['precision']:.2f} R={s['recall']:.2f} F1={s['f1']:.2f} "
              f"(tp{s['tp']} fp{s['fp']} fn{s['fn']})")
    if r["red_flag"]["miss_detail"]:
        print("  red-flag misses:", r["red_flag"]["miss_detail"])
