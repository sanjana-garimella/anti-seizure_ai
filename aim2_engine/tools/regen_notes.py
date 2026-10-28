#!/usr/bin/env python3
"""
regen_notes.py - re-run the engine over stored submissions and refresh their
cached clinician notes.

WHY THIS EXISTS
    /review renders CACHED summaries: the clinical note is generated once at
    submission time and stored in recommendations.clinical_summary. After an engine
    or output-format change, existing rows keep the old text until they are
    regenerated. Doing that by hand is risky in two specific ways this script
    handles for you.

SAFETY 1 - never write SQLite in place through the Cowork/FUSE mount.
    A bulk UPDATE straight onto clinic_data/clinic.db once threw `disk I/O error`
    mid-commit and left a corrupt b-tree (clinic.db.CORRUPT-20260728-162740 is
    still on disk as the evidence). This script copies the DB OFF-mount with
    sqlite3.Connection.backup(), does all work there, runs PRAGMA integrity_check,
    writes a timestamped backup, stages the new file beside the target, and swaps
    it in with an ATOMIC RENAME.

SAFETY 2 - never rewrite a row whose inputs cannot be reproduced.
    A submission is only rewritten when the rebuilt snapshot reproduces what was
    stored: same medication dose line AND same category. Submissions recorded
    before submissions.med_context_json existed can lose the family's Q1a dose (a
    liquid's mg/mL in particular) and would regenerate against the doctor's intake
    dose instead - a changed clinical input. Those are REPORTED AND SKIPPED, never
    silently rewritten.

    The family draft message is preserved by default: a note refresh should not
    quietly replace text a clinician may be about to send. --with-drafts opts in.

USAGE
    cd aim2_engine
    python3 tools/regen_notes.py                 # dry run, report only
    python3 tools/regen_notes.py --apply         # regenerate + swap
    python3 tools/regen_notes.py --apply --with-drafts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Recommendation columns refreshed from the re-run. draft_message, created_at and
# submission_id are deliberately absent - see the module docstring.
_REFRESH = ("category", "safety_flag", "recognized", "urgent_flags_json",
            "rationale_json", "clinical_summary", "titration_json",
            "switch_target", "engine_version", "sources_json",
            "urgency_tier", "dose_exceeds_max", "reason_summary")
_ALLOWED_COLUMNS = set(_REFRESH) | {"draft_message"}
_UPDATE_SQL = (
    "UPDATE recommendations SET category=?, safety_flag=?, recognized=?, "
    "urgent_flags_json=?, rationale_json=?, clinical_summary=?, titration_json=?, "
    "switch_target=?, engine_version=?, sources_json=?, urgency_tier=?, "
    "dose_exceeds_max=?, reason_summary=? WHERE id=?"
)
_UPDATE_SQL_WITH_DRAFT = (
    "UPDATE recommendations SET category=?, safety_flag=?, recognized=?, "
    "urgent_flags_json=?, rationale_json=?, clinical_summary=?, titration_json=?, "
    "switch_target=?, engine_version=?, sources_json=?, urgency_tier=?, "
    "dose_exceeds_max=?, reason_summary=?, draft_message=? WHERE id=?"
)


def _stored_total_mg_per_day(summary):
    """The total daily dose a stored note reports, or None when it says the mg total
    was not computable. Read as a NUMBER, not as a rendered string, so the check
    survives note-format changes: the old layout wrote "250 mg x 2/day = 500 mg/day"
    and the current one writes "500 mg/day (16.7 mg/kg/day)". Both yield 500.
    (mg/kg/day figures do not match the pattern - "16.7 mg/kg/day" is not "N mg/day".)
    """
    line = re.search(r"Current dose: (.+)", summary or "")
    if not line:
        return None
    hits = re.findall(r"([\d.]+) mg/day", line.group(1))
    return float(hits[-1]) if hits else None


def _rec_values(d):
    return {
        "category": d.get("category"),
        "safety_flag": 1 if d.get("safety_concern_flag") else 0,
        "recognized": 1 if d.get("medication_recognized") else 0,
        "urgent_flags_json": json.dumps(d.get("urgent_flags", [])),
        "rationale_json": json.dumps(d.get("rationale", [])),
        "clinical_summary": d.get("clinical_summary"),
        "titration_json": json.dumps(d.get("titration_schedule")),
        "switch_target": d.get("switch_target"),
        "engine_version": d.get("engine_version"),
        "sources_json": json.dumps(d.get("sources")),
        "urgency_tier": d.get("urgency_tier"),
        "dose_exceeds_max": 1 if d.get("dose_exceeds_max") else 0,
        "reason_summary": d.get("reason_summary"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    ap.add_argument("--with-drafts", action="store_true",
                    help="also replace the stored family draft message")
    ap.add_argument("--db", default=None, help="path to clinic.db (default: clinic_data/clinic.db)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    live = args.db or os.path.join(here, "clinic_data", "clinic.db")
    if not os.path.isfile(live):
        print(f"no database at {live}")
        return 1

    work_dir = tempfile.mkdtemp(prefix="regen_notes_")     # OFF the mount
    work = os.path.join(work_dir, "clinic.db")
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(work)
    src.backup(dst)                                        # consistent copy, no locks left behind
    dst.close()
    src.close()

    # Point clinic_store at the working copy BEFORE importing it.
    os.environ["ASM_DB"] = work
    os.chdir(here)
    import clinic_store as db          # noqa: E402
    import clinic                      # noqa: E402
    from engine_core import run_engine                      # noqa: E402
    from engine_core.outputs import _dose_line              # noqa: E402

    db.init_db()                       # applies pending column migrations to the copy

    c = sqlite3.connect(work)
    c.row_factory = sqlite3.Row
    rows = list(c.execute(
        "SELECT r.id rid, r.category cat, r.clinical_summary cs, s.patient_id pid,"
        " s.answers_json aj, s.med_context_json mc"
        " FROM recommendations r JOIN submissions s ON s.id = r.submission_id ORDER BY r.id"))

    drafts_before = {r["id"]: r["draft_message"] for r in
                     c.execute("SELECT id, draft_message FROM recommendations")}
    done, skipped = [], []
    for r in rows:
        answers = json.loads(r["aj"] or "{}")
        med_ctx = json.loads(r["mc"]) if r["mc"] else None
        patient = db.get_patient(r["pid"])
        snap = clinic.build_snapshot(patient, answers, med_context=med_ctx)
        rec = run_engine(snap, asm_registry=clinic.current_asm(),
                         drug_classifier=clinic.CLASSIFIER, physician_name="your care team")
        # Same clinical INPUT, compared as numbers: the total daily dose the stored
        # note reports must equal the one the rebuilt snapshot carries. A mismatch
        # means the rebuild is running on a different dose than the family submitted.
        stored_dose = _stored_total_mg_per_day(r["cs"])
        rebuilt_dose = snap.medication.total_daily_dose
        why = []
        same = (stored_dose is None and rebuilt_dose is None) or (
            stored_dose is not None and rebuilt_dose is not None
            and abs(stored_dose - rebuilt_dose) < 0.01)
        if not same:
            why.append(f"dose differs (stored {stored_dose}, rebuilt {rebuilt_dose};"
                       f" rebuilt reads {_dose_line(snap.medication)!r})"
                       + ("" if med_ctx else " - submission predates med_context_json"))
        if r["cat"] != rec.category:
            why.append(f"category differs ({r['cat']} -> {rec.category})")
        if why:
            skipped.append((r["rid"], patient["external_id"], why))
            continue
        vals = _rec_values(rec.to_dict())
        cols = list(_REFRESH)
        if args.with_drafts:
            vals["draft_message"] = rec.to_dict().get("draft_patient_message")
            cols.append("draft_message")
        if any(col not in _ALLOWED_COLUMNS for col in cols):
            raise RuntimeError("refresh columns left the allowlist")
        sql = _UPDATE_SQL_WITH_DRAFT if args.with_drafts else _UPDATE_SQL
        c.execute(sql, [vals[k] for k in cols] + [r["rid"]])
        done.append((r["rid"], patient["external_id"]))
    c.commit()

    integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    if not args.with_drafts:
        for rid, dm in c.execute("SELECT id, draft_message FROM recommendations"):
            assert dm == drafts_before[rid], f"draft_message changed on rid={rid}"
    c.close()

    print(f"database      : {live}")
    print(f"regenerated   : {len(done)} of {len(rows)}")
    print(f"skipped       : {len(skipped)}")
    for rid, ext, why in skipped:
        print(f"  rid={rid} {ext}: " + "; ".join(why))
    print(f"integrity     : {integrity}")
    print(f"family drafts : {'REPLACED' if args.with_drafts else 'preserved'}")

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply to swap the database in.")
        return 0
    if integrity != "ok":
        print("\nintegrity_check FAILED on the working copy - refusing to swap.")
        return 1

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(live, f"{live}.bak-{ts}")     # new file, not an in-place write
    staged = f"{live}.new-{ts}"
    shutil.copy2(work, staged)                 # stage on the same filesystem
    os.replace(staged, live)                   # ATOMIC RENAME - never cp over the live file
    print(f"\nswapped in. backup: {os.path.basename(live)}.bak-{ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
