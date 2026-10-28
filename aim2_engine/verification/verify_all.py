#!/usr/bin/env python3
"""
verify_all.py  -  one build-time gate for every machine check
=============================================================

Runs all offline verification in one shot and writes the proof artifacts:

  1. asm_rules.json      -> verify_asm_rules  (band order-independence,
                            coverage, ladder monotonicity, ceiling reachability)
  2. dose ladder + thresholds -> verify_ladder (rung monotonicity, tier/range,
                            cross-table, threshold ordering)
  3. red-flag safety net -> redteam_safety   (adversarial miss probing)

Exit code is nonzero if ANY hard property fails (a rule/ladder FAIL, or a
must-catch red-flag miss), so this can gate a build / pre-commit. It stays a
BUILD-TIME tool: nothing here runs in engine_core/ at runtime.

USAGE
  python3 verification/verify_all.py            # run all, write artifacts
  python3 verification/verify_all.py --quiet     # summary only
  (or:  make verify)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification import verify_asm_rules as VR      # noqa: E402
from verification import verify_ladder as VL          # noqa: E402
from verification import redteam_safety as RT         # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "..", "data")
_ART = os.path.join(_HERE, "artifacts")


def run_all(write: bool = True) -> dict:
    os.makedirs(_ART, exist_ok=True)

    # 1. asm_rules
    rules_path = os.path.join(_DATA, "asm_rules.json")
    backend = VR.get_backend("auto")
    rules_summary = VR.summarize(VR.verify(VR.load_bands(rules_path), backend),
                                 backend, os.path.relpath(rules_path))

    # 2. ladder + thresholds
    ladder_summary = VL.summarize(VL.verify_all(
        os.path.join(_DATA, "dose_titration_ladder.json"),
        os.path.join(_DATA, "titration_thresholds.json"),
        rules_path))

    # 3. red-team
    redteam_summary = RT.run(RT.LexiconRedFlagDetector())

    if write:
        import json
        for name, s in (("asm_rules_proof.json", rules_summary),
                        ("ladder_proof.json", ladder_summary),
                        ("redflag_redteam.json", redteam_summary)):
            with open(os.path.join(_ART, name), "w") as fh:
                json.dump(s, fh, indent=2)

    hard_fail = (not rules_summary["hard_properties_proved"]
                 or not ladder_summary["hard_properties_proved"]
                 or redteam_summary["must_catch_misses"] > 0)
    return {
        "rules": rules_summary,
        "ladder": ladder_summary,
        "redteam": redteam_summary,
        "clinical_signoff": clinical_signoff_status(),
        "ok": not hard_fail,
    }


def clinical_signoff_status(data_dir: str = None) -> dict:
    """Report false, absent, and unreadable verification metadata.

    Verification markers may be file-level or nested on drugs/rules/bands.
    This is report-only: machine consistency and clinical sign-off are separate.
    """
    import glob
    import json
    data_dir = data_dir or _DATA
    out = {"verified_false": {}, "unmarked": [], "unreadable": []}

    def count_verified(value) -> tuple:
        if isinstance(value, dict):
            markers = false_markers = 0
            for key, child in value.items():
                if key == "verified":
                    markers += 1
                    false_markers += int(child is False)
                nested_markers, nested_false = count_verified(child)
                markers += nested_markers
                false_markers += nested_false
            return markers, false_markers
        if isinstance(value, list):
            totals = [count_verified(child) for child in value]
            return sum(x[0] for x in totals), sum(x[1] for x in totals)
        return 0, 0

    for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        if path.endswith(".template.json"):
            continue
        name = os.path.basename(path)
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            out["unreadable"].append(name)
            continue
        markers, false_markers = count_verified(doc)
        if not markers:
            out["unmarked"].append(name)
        elif false_markers:
            out["verified_false"][name] = false_markers
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Run all build-time verification.")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    res = run_all(write=True)
    r, l, t = res["rules"], res["ladder"], res["redteam"]

    if not args.quiet:
        VR.print_report(r)
        VL.print_report(l)
        RT.print_report(t)

    print("\n" + "#" * 74)
    print("  VERIFY-ALL SUMMARY")
    print(f"    asm_rules : {r['counts']['FAIL']} FAIL / {r['counts']['WARN']} WARN / "
          f"{r['counts']['INFO']} INFO   proved={r['hard_properties_proved']}")
    print(f"    ladder    : {l['counts']['FAIL']} FAIL / {l['counts']['WARN']} WARN / "
          f"{l['counts']['INFO']} INFO   proved={l['hard_properties_proved']}")
    print(f"    red-team  : {t['misses']} misses "
          f"(must-catch={t['must_catch_misses']}) / {t['false_alarms']} false-alarms")
    print(f"    artifacts -> {os.path.relpath(_ART)}/")
    print(f"  RESULT: {'PASS' if res['ok'] else 'FAIL (hard defect - PI review)'}")
    signoff = res["clinical_signoff"]
    print("  PASS/FAIL covers internal consistency only. Clinical sign-off is separate:")
    unsigned = signoff["verified_false"]
    if unsigned:
        details = ", ".join(f"{name} ({count})" for name, count in unsigned.items())
        print(f"    verified:false -> {details}  (open items: NEEDS_PI_SIGNOFF.md)")
    if signoff["unmarked"]:
        print(f"    no verified marker -> {', '.join(signoff['unmarked'])}")
    if signoff["unreadable"]:
        print(f"    unreadable JSON -> {', '.join(signoff['unreadable'])}")
    if not any(signoff.values()):
        print("    no verified:false, unmarked, or unreadable clinical JSON found")
    print("#" * 74)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
