"""
_build_dose_ladder.py
---------------------
Rebuild data/dose_titration_ladder.json from Dr. Barbour's "Final Doses" sheet
(kristen/final_doses_titration_vN.xlsx). Previously the JSON was transcribed by
hand from v6; this reads the sheet directly so a new version can't be mis-copied.

Columns are found by HEADER NAME (v9 inserted/renamed columns vs v6), cells are
copied VERBATIM (raw text kept next to the parsed number), nothing is computed or
corrected. Hand-curated metadata in the existing JSON (conmed restrictions,
high-dose labs, data-quality flags) is carried over unchanged - re-check the
flags against the new sheet by hand.

Stdlib only (reads the .xlsx as a zip of XML; uses the cached cell values).

Run (from aim2_engine/):
    python3 data/_build_dose_ladder.py ../kristen/final_doses_titration_v9.xlsx          # dry run: diff summary
    python3 data/_build_dose_ladder.py ../kristen/final_doses_titration_v9.xlsx --apply  # write the JSON
    python3 data/_build_dose_ladder.py ../kristen/final_doses_titration_v6.xlsx --check  # must reproduce today's JSON
Then rebuild asm_rules.json:  python3 data/_build_asm_rules.py
"""
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dose_titration_ladder.json")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


# ---------------------------------------------------------------- xlsx reader
def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_sheet(path, sheet_name):
    """Rows of the named sheet as lists of cached values (str | float | None)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    target = None
    for s in wb.find("m:sheets", _NS):
        if s.get("name") == sheet_name:
            target = targets[s.get(_R)]
    if target is None:
        raise SystemExit(f"no sheet named {sheet_name!r} in {path}")
    target = target.lstrip("/")
    target = target if target.startswith("xl/") else "xl/" + target
    root = ET.fromstring(z.read(target))
    rows = []
    for row in root.iter("{%s}row" % _NS["m"]):
        cells = {}
        for c in row.findall("m:c", _NS):
            t, v = c.get("t"), c.find("m:v", _NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
            elif t == "str" and v is not None:
                val = v.text
            elif v is not None:
                val = float(v.text)
            else:
                val = None
            cells[_col_index(c.get("r"))] = val
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i) for i in range(width)])
    return rows


# ---------------------------------------------------------------- parsing
_UNITS = {"mg/kg/day": "mg_per_kg_per_day", "mg/day": "mg_per_day"}


def _txt(v):
    if v is None:
        return None
    if isinstance(v, float):
        return f"{v:g}"
    s = str(v).strip()
    return s or None


def _num(v):
    if isinstance(v, float):
        return v
    s = _txt(v)
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _dose(v):
    """'10 mg/kg/day' -> {value, unit, raw}; None for blank or non-dose cells."""
    raw = _txt(v)
    if not raw:
        return None
    m = re.fullmatch(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(mg/kg/day|mg/day)", raw)
    if not m:
        return None
    return {"value": float(m.group(1).replace(",", "")), "unit": _UNITS[m.group(2)], "raw": raw}


def _step(v):
    """An increment cell: a dose, or a stray non-dose text kept + flagged (never dropped
    silently, never guessed); None when blank."""
    d = _dose(v)
    if d or not _txt(v):
        return d
    return {"raw": _txt(v), "value": None, "unit": None, "flag": "non-numeric/stray"}


def _drug_key(name):
    s = re.sub(r"[*]", "", str(name)).strip().lower()
    return re.sub(r"\s+", "_", s)


def _headers(row):
    """Header name -> list of column indexes (names repeat; v6/v9 spellings unified)."""
    alias = {"minimum maintenanance dose dose": "minimum maintenance dose",
             "effective max": "usual effective max",
             "ai": "initial dose cap (mg/day)"}       # v9 renamed the cap column 'Ai'
    out = {}
    for i, h in enumerate(row):
        if h is None:
            continue
        k = re.sub(r"\s+", " ", str(h)).strip().lower()
        k = alias.get(k, k)
        out.setdefault(k, []).append(i)
    return out


def build(path):
    rows = read_sheet(path, "Final Doses")
    H = _headers(rows[0])

    def col(r, name, nth=0):
        idx = H.get(name, [])
        return r[idx[nth]] if nth < len(idx) and idx[nth] < len(r) else None

    def series(r, prefix, n):
        return [col(r, f"{prefix} {i}") for i in range(1, n + 1)]

    bands, drug = {}, None
    for r in rows[1:]:
        if not r or all(v is None for v in r):
            continue
        if col(r, "drug name"):
            drug = _drug_key(col(r, "drug name"))
        label = _txt(col(r, "age/weight group"))
        if not drug or not label:
            continue
        pre = [d for d in (_step(v) for v in reversed(series(r, "preincrem", 9))) if d]
        up = [d for d in (_step(v) for v in series(r, "increm", 9)) if d]
        high = [d for d in (_step(v) for v in series(r, "postincrem", 7)) if d]
        eff = col(r, "usual effective max")
        band = {
            "band_label": label,
            "initial_dose": _dose(col(r, "initial dose")),
            "initial_dose_cap_mg_per_day": _num(col(r, "initial dose cap (mg/day)")),
            "maintenance_range": _txt(col(r, "maintenance dose")),
            "max_absolute_mg_per_day": _num(col(r, "max absolute dose (mg/day)")),
            "dose_increase_amount": _dose(col(r, "dose increase amount")),
            "dose_increase_interval": _txt(col(r, "dose increase interval")),
            "min_maintenance_dose": _dose(col(r, "minimum maintenance dose", 1)),
            "effective_max": ({"value": _num(eff), "raw": _txt(eff)} if _txt(eff) else None),
            "pre_maintenance_increments": pre,
            "up_increments": up,
            "high_increments_often_not_tolerated": high,
            "forms": {"oral_solution_mg_per_ml": _txt(col(r, "oral solution (mg/ml)")),
                      "ir_tablet_capsule_mg": _txt(col(r, "ir tablet/capsule (mg)")),
                      "er_tablet_capsule_mg": _txt(col(r, "er tablet/capsule (mg)"))},
            "_helper_columns": {"absolute_range_J": _num(col(r, "absolute range")),
                                "divide_by_K": _num(col(r, "divide by")),
                                "one_step_increment_L": _num(col(r, "1 step increment")),
                                "percent_cap_M": _num(col(r, "common percent cap")),
                                "effective_range_N": _num(col(r, "effective range")),
                                "min_maintenance_O": _num(col(r, "minimum maintenance dose", 0))},
        }
        tpd = _num(col(r, "times per day (ir)"))       # new in v9
        if tpd is not None:
            band["times_per_day_ir"] = tpd
        band["_helper_columns"] = {k: v for k, v in band["_helper_columns"].items() if v is not None}
        bands.setdefault(drug, []).append({k: v for k, v in band.items() if v is not None})
    return bands


def _strip_new(bands):
    """Drop v9-only fields so a v6 rebuild can be compared to the hand-made JSON."""
    return {d: [{k: v for k, v in b.items() if k != "times_per_day_ir"} for b in bs]
            for d, bs in bands.items()}


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    src = argv[1]
    with open(OUT) as fh:
        cur = json.load(fh)
    new_bands = build(src)
    name = os.path.basename(src)

    if "--check" in argv:
        a, b = _strip_new(new_bands), cur["bands_by_drug"]
        bad = []
        for drug in sorted(set(a) | set(b)):
            la, lb = a.get(drug, []), b.get(drug, [])
            if len(la) != len(lb):
                bad.append(f"{drug}: {len(la)} bands vs {len(lb)}")
                continue
            for x, y in zip(la, lb):
                for k in sorted(set(x) | set(y)):
                    if x.get(k) != y.get(k):
                        bad.append(f"{drug} [{y.get('band_label')}] {k}: built {x.get(k)!r} != json {y.get(k)!r}")
        print("\n".join(bad) if bad else f"OK - {name} rebuilds data/dose_titration_ladder.json exactly")
        return 1 if bad else 0

    out = dict(cur)
    out["bands_by_drug"] = new_bands
    out["source"] = f"clinician (Barbour {name}, rebuilt by data/_build_dose_ladder.py)"
    out["_README"] = re.sub(r"final_doses_titration_v\d+\.xlsx( \([0-9-]+\))?", name, cur["_README"])
    for drug in sorted(set(new_bands) | set(cur["bands_by_drug"])):
        old = [b["band_label"] for b in cur["bands_by_drug"].get(drug, [])]
        new = [b["band_label"] for b in new_bands.get(drug, [])]
        changed = sum(1 for b in new_bands.get(drug, [])
                      if b not in cur["bands_by_drug"].get(drug, []))
        print(f"{drug:15s} bands {len(old)} -> {len(new)}  changed/new bands: {changed}"
              + ("" if old == new else f"  labels {old} -> {new}"))
    if "--apply" not in argv:
        print("\ndry run - re-run with --apply to write data/dose_titration_ladder.json")
        return 0
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
