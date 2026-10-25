"""
_build_initiation_schedules.py
------------------------------
Copy Dr. Barbour's per-medicine "<Drug> Initiation" tabs (final_doses_titration_v9.xlsx,
2026-10) into data/initiation_schedules.json: the week-by-week STARTING schedule for a
new medicine, per weight (or age) band, for each product form (liquid / tablet).

Her note (2026-10-23): "When your medication tool decides to add or switch medications,
you can use this plan to provide the families with a recommended new medication
schedule."

Every cell is copied VERBATIM as text ("½ × 250 mg AM, 1 × 250 mg PM"); nothing is
computed, rounded or converted. A dash cell ("—") means no tablet schedule fits that
band (her note: use the solution). The tab's header notes are kept with the drug.
DRAFT - verified:false until she signs off.

Run (from aim2_engine/):
    python3 data/_build_initiation_schedules.py ../kristen/final_doses_titration_v9.xlsx          # dry run
    python3 data/_build_initiation_schedules.py ../kristen/final_doses_titration_v9.xlsx --apply  # write
"""
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _build_dose_ladder import read_sheet, _drug_key  # noqa: E402

OUT = os.path.join(HERE, "initiation_schedules.json")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_LIQUID = re.compile(r"solution|suspension", re.I)


def _txt(v):
    if v is None:
        return None
    if isinstance(v, float):
        return f"{v:g}"
    s = re.sub(r"[ \t]+", " ", str(v)).strip()
    return s or None


def _is_header(row):
    first = (_txt(row[0]) or "").lower() if row else ""
    return first.startswith(("weight band", "weight group")) and len([v for v in row if v]) > 2


def parse_band(label):
    """'10–12.5 kg' / '< 20 kg' / '≥50 kg (adult)' / '12–16 y (adolescent)' /
    '> 16 years (adult)' -> {"basis": "weight_kg"|"age_years", "min", "max",
    "min_inclusive", "max_inclusive"}; None if the label can't be read."""
    s = (label or "").replace("–", "-").replace("—", "-")
    s = re.sub(r"\(.*?\)", "", s).strip().lower()
    m = re.fullmatch(r"(.+?)\s*(kg|y|years?)", s)
    if not m:
        return None
    body, unit = m.group(1).strip(), m.group(2)
    basis = "weight_kg" if unit == "kg" else "age_years"
    num = r"([0-9]+(?:\.[0-9]+)?)"
    if re.fullmatch(num + r"\s*-\s*" + num, body):
        a, b = map(float, re.findall(r"[0-9.]+", body))
        return {"basis": basis, "min": a, "max": b, "min_inclusive": True, "max_inclusive": False}
    m2 = re.fullmatch(r"(<=|≤|<|>=|≥|>)\s*" + num, body)
    if m2:
        op, v = m2.group(1), float(m2.group(2))
        if op in ("<", "<=", "≤"):
            return {"basis": basis, "min": None, "max": v, "min_inclusive": True,
                    "max_inclusive": op != "<"}
        return {"basis": basis, "min": v, "max": None, "min_inclusive": op != ">",
                "max_inclusive": True}
    return None


def _sheet_names(path):
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name") for s in wb.find(_NS + "sheets")]


def build_tab(path, name):
    rows = read_sheet(path, name)
    notes, tables, title, cur = [], [], None, None
    for r in rows:
        vals = [v for v in r if _txt(v)]
        if not vals:
            cur = None
            continue
        if _is_header(r):
            cols = [_txt(v) for v in r]
            while cols and cols[-1] is None:
                cols.pop()
            cur = {"form_title": title or "", "columns": cols[1:], "bands": []}
            kinds = {"liquid" if _LIQUID.search(title or "") else "solid"} if title else set()
            cur["kind"] = kinds.pop() if kinds else "combined"
            tables.append(cur)
            continue
        if len(vals) == 1:
            t = _txt(vals[0])
            if re.search(r"\(\s*[0-9]", t) and len(t) < 90:      # a form title, e.g. "Oral solution  (100 mg/mL)"
                title, cur = t, None                           # (may follow a table with no blank row)
                continue
            if cur is None:
                if not notes or t != notes[-1]:
                    notes.append(t)
                continue
        if cur is not None:
            label = _txt(r[0])
            cells = [_txt(r[i]) if i < len(r) else None for i in range(1, len(cur["columns"]) + 1)]
            cur["bands"].append({"band_label": label, "band": parse_band(label), "cells": cells})
    return {"sheet": name, "notes": notes, "tables": tables}


def build(path):
    out = {}
    for name in _sheet_names(path):
        if not name.lower().endswith(" initiation"):
            continue
        drug = _drug_key(name[: -len(" initiation")])
        out[drug] = build_tab(path, name)
    return out


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    src = argv[1]
    by_drug = build(src)
    problems = []
    for drug, d in sorted(by_drug.items()):
        nb = sum(len(t["bands"]) for t in d["tables"])
        unread = [b["band_label"] for t in d["tables"] for b in t["bands"] if not b["band"]]
        print(f"{drug:15s} tables={[(t['kind'], len(t['bands'])) for t in d['tables']]} bands={nb}"
              + (f"  UNREAD BAND LABELS: {unread}" if unread else ""))
        if unread:
            problems.append(drug)
    data = {
        "_README": ("Week-by-week STARTING schedules for a new medicine (add or switch), copied "
                    "VERBATIM from Dr. Barbour's '<Drug> Initiation' tabs. Cells are text exactly as "
                    "she wrote them; '—' means no tablet schedule fits (use the liquid). The engine "
                    "only DISPLAYS these to the clinician; it never computes or changes a dose from "
                    "them. DRAFT - verified:false, pending PI sign-off."),
        "verified": False,
        "source": f"clinician (Barbour {os.path.basename(src)}, '<Drug> Initiation' tabs; "
                  "built by data/_build_initiation_schedules.py)",
        "by_drug": by_drug,
        # Stopping the OLD medicine during a switch (cross-taper). Dr. Barbour's template F
        # table (kb edits): week 1 current dose, week 2 x0.75, week 3 x0.50, week 4 x0.25 of
        # the current dose, each dose of the day; her 2026-10-25 answer (#69): the old
        # medicine is OFF at week 4, and the new medicine starts in week 1 alongside it (on
        # its own starting schedule above). My reading: x0.25 is given during week 4 and the
        # old medicine stops after week 4 - FLAG FOR DR. BARBOUR to confirm.
        "switch_taper": {
            "verified": False,
            "source": "clinician (Barbour template F, kb edits; answer 2026-10-25, NEEDS_PI_SIGNOFF #69)",
            "old_medicine_multipliers_by_week": [1.0, 0.75, 0.5, 0.25],
            "old_medicine_off_after_week": 4,
            "new_medicine_starts_week": 1,
            "not_started_as_new_medicine": {
                "carbamazepine": "Dr. Barbour 2026-10-25: carbamazepine is never offered as a new medication."
            },
        },
    }
    if problems:
        print("\nrefusing to write: some band labels could not be read - fix the parser or the sheet")
        return 1
    if "--apply" not in argv:
        print("\ndry run - re-run with --apply to write data/initiation_schedules.json")
        return 0
    with open(OUT, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
