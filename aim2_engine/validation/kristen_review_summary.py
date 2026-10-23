"""
kristen_review_summary.py
-------------------------
Parses Dr. Barbour's PI-reviewed synthetic validation workbook
(validation/private/Aim2_Synthetic_Validation_Cases_reviewed_20260708.xlsx) and
emits a durable summary of her review: coverage, per-case agreement, and the
cases she flagged - annotated with how the 2026-07-08 engine changes address
each one.

100% SIMULATED data. CONFIDENTIAL - stays in this folder (NON_NEGOTIABLE_RULES.md §1).
Run:  python3 validation/kristen_review_summary.py
Out:  validation/kristen_review_summary.json
"""
import json
import os

try:
    import openpyxl
except ImportError:
    raise SystemExit("openpyxl required: pip install openpyxl --break-system-packages")

_HERE = os.path.dirname(os.path.abspath(__file__))
_XLSX = os.path.join(_HERE, "private",
                     "Aim2_Synthetic_Validation_Cases_reviewed_20260708.xlsx")
_OUT = os.path.join(_HERE, "kristen_review_summary.json")

# How each flagged case is resolved by the 2026-07-08 changes (provenance).
_RESOLUTIONS = {
    "B1": "Fixed: BT-table gate now routes non-urgent, titrating, level-1 breakthrough "
          "to 'continue on schedule' (no_change) instead of the legacy 'slower'. "
          "(decision.py gate; test_engine.test_breakthrough_level1_titrating_continues)",
    "B2": "Fixed: same non-urgent level-1 routing change as B1.",
    "B3": "Fixed: same non-urgent level-1 routing change as B1.",
    "D4": "Fixed: No-Seizures 'continue' path no longer auto-schedules a follow-up; "
          "follow-up is emitted only when the decision table lists one. "
          "(decision.py NS branch; test_dose_ladder.TestKristenValidationFixes)",
}


def main():
    wb = openpyxl.load_workbook(_XLSX, data_only=True)
    ws = wb["Cases"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    idx = {h: i for i, h in enumerate(hdr)}
    ca, ag, yr, cm = idx["Case"], idx["Agree?"], \
        idx["Your recommendation (if disagree)"], idx["Comments"]
    eng, sc = idx["ENGINE: Recommendation"], idx["Scenario"]

    agree = {"Yes": 0, "No": 0, "Unsure": 0, "blank": 0}
    flagged = []
    total = 0
    for r in rows[1:]:
        if r[ca] is None:
            continue
        total += 1
        a = (r[ag] or "").strip()
        agree[a if a in agree else "blank"] += 1
        if a.lower() not in ("yes", ""):
            flagged.append({
                "case": r[ca], "scenario": r[sc], "engine": r[eng],
                "agree": a, "clinician_recommendation": r[yr],
                "comment": r[cm],
                "resolution": _RESOLUTIONS.get(str(r[ca]),
                              "UNRESOLVED - review needed."),
            })

    summary = {
        "source": os.path.basename(_XLSX),
        "reviewed_by": "Dr. Kristen Barbour (PI)",
        "data": "100% simulated",
        "total_cases": total,
        "agreement": agree,
        "flagged_cases": flagged,
        "note": ("DRAFT engine outputs reviewed between-visit. Flagged cases are "
                 "addressed by the 2026-07-08 decision-table reconciliation + "
                 "engine changes; see each resolution. Pending final PI sign-off "
                 "(NEEDS_PI_SIGNOFF.md)."),
    }
    with open(_OUT, "w") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(f"{total} cases | agreement={agree} | flagged={len(flagged)}")
    for f in flagged:
        print(f"  [{f['case']}] {f['agree']}: engine={f['engine']!r}")
        print(f"      -> {f['resolution']}")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
