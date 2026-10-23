#!/usr/bin/env python3
"""
redteam_safety.py  -  adversarial probing of the free-text safety net
=====================================================================

A red-team eval suite that stress-tests the escalate-only red-flag detector
(nlp/redflags.py) and, where relevant, the assertion layer. It complements the
agreement/kappa harness: kappa measures accuracy on normal text, this measures
ROBUSTNESS on inputs crafted to slip past the safety net.

Each case declares what SHOULD happen, so the suite reports:
  * MISS        - a case that should raise an urgent flag but did not
                  (the dangerous error for a safety net)
  * FALSE ALARM - a case that should NOT flag but did
                  (erodes reviewer trust; less dangerous)
  * OK          - behaved as expected

Attack families:
  typo/obfuscation, spacing/punctuation, negation-scope, family-attribution
  (experiencer), duration-phrasing, understatement, casing.

SCOPE / SAFETY (clinical-safety.md, NON_NEGOTIABLE_RULES.md):
  * PRE-HUMAN TRIAGE ONLY. This is an offline eval; it does not run in the
    decision path and changes no recommendation. It exists to show the PI/eng
    team WHERE the net leaks so terms/logic can be hardened before clinical use.
  * A MISS on a `must_catch` case is a hard failure (exit 1). Non-must_catch
    misses and false alarms are reported as findings for review, not failures,
    because some (e.g. family attribution) are judgment calls for the PI.

USAGE
  python3 verification/redteam_safety.py            # run, print report
  python3 verification/redteam_safety.py --json OUT # + write artifact
  exit 0 = no must_catch miss; 1 = a must_catch case slipped through.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Import the detector under test (nlp is advisory-layer; safe to import here).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp.redflags import LexiconRedFlagDetector  # noqa: E402

try:
    from nlp.assertion_secondopinion import AssertionSecondOpinion, SOMEONE_ELSE  # noqa: E402
    _HAS_ASSERT = True
except Exception:
    _HAS_ASSERT = False


# case = (text, should_flag, family, must_catch, note)
CASES = [
    # --- clean positives (sanity: these must fire) --------------------------
    ("her rash is spreading all over her face", True, "clean", True, "canonical SJS"),
    ("the seizure lasted 7 minutes", True, "clean", True, "prolonged seizure"),
    ("we called 911 and went to the ER", True, "clean", True, "emergency care"),
    ("he was unresponsive afterward", True, "clean", True, "unresponsive"),

    # --- clean negatives (must NOT fire) ------------------------------------
    ("small rash on his arm, looks fine now", False, "clean", False, "benign rash, no modifier"),
    ("eating and sleeping normally", False, "clean", False, "reassurance"),

    # --- negation scope -----------------------------------------------------
    # The net is ESCALATE-ONLY / HIGH-RECALL by design: it does NOT suppress a
    # danger token just because it is negated (a human still reads "no rash"),
    # so firing here is INTENDED, not a false alarm.
    ("no rash, no blistering, no peeling", True, "negation", False,
     "escalate-only: fires despite negation by design"),
    ("denies any trouble breathing", True, "negation", False,
     "escalate-only: fires despite negation by design"),
    # A danger phrased with 'no longer' that has NO matching term is a real leak
    # (hardening target, not must-catch: 'breathing well' isn't in the vocab):
    ("she is no longer breathing well", True, "negation", False,
     "leak: 'no longer breathing well' has no matching danger term"),

    # --- typo / obfuscation -------------------------------------------------
    ("the siezure went on for 7 minutes", True, "typo", True, "misspelled seizure, duration still there"),
    ("big rash blistring on her lips", True, "typo", False, "misspelled blistering"),
    ("rash spreadin fast and getting bigger", True, "typo", False, "dropped-g 'spreading'"),

    # --- spacing / punctuation obfuscation ----------------------------------
    ("she has s.j.s per the ER", True, "spacing", False, "punctuated SJS"),
    ("b l i s t e r s forming on his mouth", True, "spacing", False, "spaced letters"),

    # --- duration phrasing --------------------------------------------------
    ("it went on for a good six minutes", True, "duration", True, "number-word duration"),
    ("about 6 or 7 min, maybe longer", True, "duration", True, "range duration"),
    ("lasted only 30 seconds", False, "duration", False, "short seizure, must NOT flag"),

    # --- understatement -----------------------------------------------------
    ("just a little blister on his lip", True, "understatement", False, "blister downplayed"),
    ("tiny bit of skin peeling near his mouth", True, "understatement", False, "peeling downplayed"),

    # --- family attribution (experiencer) -----------------------------------
    # Escalate-only net fires (a human should confirm); the assertion layer
    # separately marks it as someone-else, an advisory suppression CANDIDATE.
    ("my other child once had SJS, not this patient", True, "family", False,
     "fires (escalate-only); assertion layer flags someone-else as advisory"),
]


def run(detector) -> dict:
    results = []
    misses = false_alarms = must_catch_misses = 0
    for text, should, family, must, note in CASES:
        flags = detector.scan(text)
        fired = any(f.severity == "urgent" for f in flags)
        status = "OK"
        if should and not fired:
            status = "MISS"
            misses += 1
            if must:
                must_catch_misses += 1
        elif not should and fired:
            status = "FALSE ALARM"
            false_alarms += 1
        results.append({
            "text": text, "family": family, "should_flag": should,
            "must_catch": must, "fired": fired, "status": status,
            "evidence": [f.evidence for f in flags], "note": note,
        })
    n = len(CASES)
    return {
        "artifact": "redflag_redteam_eval",
        "status": "DRAFT eval (pre-human triage; advisory only)",
        "n_cases": n,
        "misses": misses,
        "must_catch_misses": must_catch_misses,
        "false_alarms": false_alarms,
        "assertion_layer_available": _HAS_ASSERT,
        "results": results,
    }


def assertion_note(text: str) -> str:
    """If the assertion layer is present, note when a fired danger is actually
    attributed to someone else — a candidate for suppression BEFORE a human,
    but only as advisory context."""
    if not _HAS_ASSERT:
        return ""
    so = AssertionSecondOpinion()
    r = so.review_term(text, "SJS")
    return " [assertion: someone-else]" if r.label == SOMEONE_ELSE else ""


def print_report(summary: dict) -> None:
    print("=" * 74)
    print("  RED-FLAG SAFETY NET  -  ADVERSARIAL EVAL  (DRAFT, pre-human triage)")
    print("=" * 74)
    by_fam: dict = {}
    for r in summary["results"]:
        by_fam.setdefault(r["family"], []).append(r)
    for fam, rows in by_fam.items():
        print(f"\n[{fam}]")
        for r in rows:
            tag = {"OK": "  ok ", "MISS": ">MISS", "FALSE ALARM": "~FALSE"}[r["status"]]
            mc = " (must-catch)" if r["must_catch"] and r["status"] == "MISS" else ""
            extra = assertion_note(r["text"]) if r["family"] == "family" else ""
            print(f"  {tag}{mc}  {r['text']!r}")
            if r["status"] != "OK":
                print(f"         expected_flag={r['should_flag']} fired={r['fired']} "
                      f"evidence={r['evidence']} — {r['note']}{extra}")
    print("\n" + "-" * 74)
    print(f"  cases={summary['n_cases']}  misses={summary['misses']} "
          f"(must-catch={summary['must_catch_misses']})  "
          f"false_alarms={summary['false_alarms']}")
    if summary["must_catch_misses"] == 0:
        print("  PASS: no must-catch case slipped through.")
        print("  (Other misses/false-alarms are hardening targets for PI review.)")
    else:
        print("  FAIL: a MUST-CATCH danger phrase was missed — harden terms/logic.")
    print("=" * 74)


def main() -> int:
    p = argparse.ArgumentParser(description="Adversarial eval of the red-flag net.")
    p.add_argument("--json", metavar="OUT")
    args = p.parse_args()
    detector = LexiconRedFlagDetector()
    summary = run(detector)
    print_report(summary)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"  artifact -> {args.json}")
    return 1 if summary["must_catch_misses"] else 0


if __name__ == "__main__":
    sys.exit(main())
