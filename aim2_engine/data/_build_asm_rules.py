"""
_build_asm_rules.py
-------------------
Reproducibly assemble data/asm_rules.json (the clinician-supplied dosing file
the engine loads).

DOSE BANDS are RECONCILED to Dr. Barbour's source of truth, the exact dose
ladder in data/dose_titration_ladder.json (itself transcribed verbatim from
kristen/final_doses_titration_v6.xlsx). Every initial / maintenance / max /
increment number is derived directly from that ladder, so asm_rules.json can
never drift from the sheet. Stdlib-only (reads the in-repo JSON, not the xlsx).

DRUG FACTS (indications, formulations, rxcui, key_interactions, etc.) come from
the reviewed DRAFT in ../clinician_verification/asm_rules.draft.json; the per-drug
review FACTS below (ethosuximide = absence-only; lamotrigine SJS + conmed-
dependent titration; SJS risk; do-not-switch targets; lab monitoring) are
layered on top.

LAMOTRIGINE (special): the sheet's ladder footnote applies ONLY to patients NOT
on valproate and NOT on an enzyme inducer. So only that 'none' regimen is
reconciled to the sheet (split into the sheet's 3 age bands); the WITH-valproate
and WITH-enzyme-inducer bands are PRESERVED from the draft unchanged (SJS/TEN
safety logic the sheet does not cover).

PROVENANCE: dose bands copied from her v9 ladder are verified:true, except a
band named in the ladder's data_quality_flags and the lamotrigine
WITH-valproate / WITH-inducer bands (those are not on the sheet). NOTE for PI: the sheet sets levetiracetam's effective max at
60 mg/kg/day; an earlier 2026-06 note had said 80 mg/kg/day. The sheet (source
of truth) wins here (60); flagged for PI confirmation.

Run:  python3 data/_build_asm_rules.py     # writes data/asm_rules.json
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DRAFT = os.path.normpath(os.path.join(HERE, "..", "clinician_verification", "asm_rules.draft.json"))
LADDER = os.path.join(HERE, "dose_titration_ladder.json")
OUT = os.path.join(HERE, "asm_rules.json")

# Per-drug facts from Kristen's review (themes T6/T11/T12/T14). Survey seizure
# tokens are used where a seizure type is named (mapped to clinical classes in
# the engine): brief_staring=absence, sudden_jerks=myoclonic.
FACTS = {
    "levetiracetam": {
        "avoid_if_comorbidity": ["autism", "depression", "anxiety",
                                  "emotional_outbursts", "aggression"],
        "key_safety": "Behavioral/psychiatric effects (irritability, mood, aggression). "
                      "Avoid in significant behavioral comorbidity (Barbour). Renally cleared.",
    },
    "brivaracetam": {
        # Kristen: do not switch briviact -> keppra (keppra has more behavioral SE).
        "no_switch_targets": ["levetiracetam"],
        "key_safety": "Same SV2A family as levetiracetam; behavioral effects possible.",
    },
    "lamotrigine": {
        "sjs_risk": True,
        "avoid_if_seizure_type": ["myoclonic"],
        "conmed_dependent_titration": True,
        "key_safety": "BOXED WARNING serious rash (SJS/TEN): risk up with valproate, with "
                      "exceeding the start dose, and with fast escalation. Slow titration "
                      "essential. Avoid in myoclonic seizures (may worsen). SJS risk on "
                      "abrupt stop-start (missed doses).",
    },
    "carbamazepine": {
        "sjs_risk": True,
        "requires_lab_monitoring": True,
        "labs": ["CBC", "LFTs", "sodium"],
        # Advisory only (CLINICAL_REVIEW.md item 3, NEEDS_PI_SIGNOFF.md #18).
        # Sets a safety note. Does not change the category or the dose.
        "avoid_if_seizure_type": ["absence", "myoclonic"],
        "key_safety": "Serious rash (SJS/TEN), esp. with HLA-B*1502 (Southeast/East Asian "
                      "ancestry). Hyponatremia; blood dyscrasias; enzyme inducer. "
                      "May aggravate absence and myoclonic seizures.",
    },
    "oxcarbazepine": {
        # Kristen: do not switch trileptal -> tegretol (carbamazepine has more SE).
        "no_switch_targets": ["carbamazepine"],
        "sjs_risk": True,
        # Same advisory caution as carbamazepine. Note only; category unchanged.
        "avoid_if_seizure_type": ["absence", "myoclonic"],
        "key_safety": "Hyponatremia; rash with HLA-B*1502 cross-reactivity. Spreading/"
                      "blistering rash -> stop + switch. "
                      "May aggravate absence and myoclonic seizures.",
    },
    "ethosuximide": {
        # Kristen: ethosuximide only treats staring-spell (absence) seizures.
        "treats_only": ["absence"],
        "key_safety": "Absence (staring-spell) seizures ONLY. Any other seizure type -> switch.",
    },
    "valproic_acid": {
        "requires_lab_monitoring": True,
        "labs": ["LFTs", "platelets", "ammonia"],
        "avoid_if_comorbidity": [],
        "key_safety": "Hepatotoxicity; pancreatitis; thrombocytopenia; teratogenic. "
                      "Raises lamotrigine levels (rash risk).",
    },
    "zonisamide": {
        "requires_lab_monitoring": False,
        "key_safety": "Sulfonamide; off-label <16y in this study; oligohidrosis/metabolic acidosis.",
    },
    "topiramate": {
        "key_safety": "Cognitive/word-finding; metabolic acidosis; oligohidrosis; nephrolithiasis.",
    },
    "lacosamide": {"key_safety": "PR prolongation; dizziness."},
    "clobazam": {"key_safety": "Sedation; benzodiazepine - avoid stacking sedating meds."},
}

with open(LADDER) as _fh:                                   # sheet version, from the ladder's own source
    _m = re.search(r"final_doses_titration_v\d+", json.load(_fh).get("source", ""))
SHEET = _m.group(0) if _m else "final_doses_titration"
SRC = f"Barbour {SHEET}.xlsx via data/dose_titration_ladder.json"


# ---------------------------------------------------------------------------
# Ladder-band -> asm_rules dose-band conversion (numbers straight from the sheet)
# ---------------------------------------------------------------------------
def _num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.match(r"^\s*\d+\.?\d*", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def _unit(s):
    return "kg" if "kg" in str(s).lower() else "mg"


def _parse_range(e):
    """maintenance range string -> (low, high, is_kg). The dash is a separator,
    not a minus sign, so only unsigned numbers are matched."""
    t = str(e).replace(",", "")
    is_kg = "kg" in t.lower()
    nums = re.findall(r"\d+\.?\d*", t)
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1]), is_kg
    if len(nums) == 1:
        return float(nums[0]), float(nums[0]), is_kg
    return None, None, is_kg


def _interval_weeks(h):
    h = str(h).lower()
    if "2 week" in h or "two week" in h:
        return 2
    if "week" in h:
        return 1
    return None


def _parse_edges(label):
    """band label -> {min_age,max_age,min_weight,max_weight}, is_kg. Mirrors the
    sheet; contiguity is applied per drug afterwards."""
    t = (label.replace(",", " ").replace("–", "-").replace("—", "-")
         .replace("≥", ">=").replace("≤", "<="))
    tl = t.lower()
    r = {"min_age_years": None, "max_age_years": None,
         "min_weight_kg": None, "max_weight_kg": None}
    is_kg = "kg" in tl
    lo_key, hi_key = (("min_weight_kg", "max_weight_kg") if is_kg
                      else ("min_age_years", "max_age_years"))
    nums = [float(n) for n in re.findall(r"\d+\.?\d*", t)]
    m = re.search(r"(\d+\.?\d*)\s*(?:to|-)\s*(<?)\s*(\d+\.?\d*)", t)
    if m:
        lo = float(m.group(1)); hi = float(m.group(3)); strict = (m.group(2) == "<")
        r[lo_key] = lo
        r[hi_key] = round(hi - 0.01, 2) if strict else hi
        return r, is_kg
    if not nums:
        if ">=2" in t or ">= 2" in t:
            r["min_age_years"] = 2
        return r, is_kg
    x = nums[0]
    if ">=" in t or "at least" in tl:
        r[lo_key] = x
    elif ">" in t:
        r[lo_key] = round(x + 0.01, 2)
    elif "<=" in t:
        r[hi_key] = x
    elif "<" in t:
        r[hi_key] = round(x - 0.01, 2)
    else:
        r[lo_key] = x
    return r, is_kg


def _make_contiguous(specs, is_kg):
    """Extend each lower band's max up to just below the next band's floor, so a
    borderline patient gets the LOWER (safer) ceiling instead of a no-band gap."""
    hi_key = "max_weight_kg" if is_kg else "max_age_years"
    lo_key = "min_weight_kg" if is_kg else "min_age_years"
    ordered = sorted(specs, key=lambda s: (s["edges"][lo_key]
                     if s["edges"][lo_key] is not None else -1))
    for i in range(len(ordered) - 1):
        cur, nxt = ordered[i]["edges"], ordered[i + 1]["edges"]
        nlo = nxt[lo_key]
        if nlo is None:
            continue
        target = round(nlo - 0.01, 2)
        if cur[hi_key] is None or cur[hi_key] < target:
            cur[hi_key] = target
    return specs


def _norm_label(s):
    s = str(s or "").replace("–", "-").replace("≥", ">=").replace("≤", "<=")
    return re.sub(r"\s+", " ", s).strip().lower()


def _band_flagged(flags, drug, label):
    lbl = _norm_label(label)
    for f in flags or []:
        if (str(f.get("drug", "")).lower() == str(drug).lower()
                and lbl in {_norm_label(x) for x in str(f.get("band", "")).split(" / ")}):
            return True
    return False


def _band_from_ladder(lb, freq, source_tag, label_override=None, regimen_note="",
                      verified=True):
    """Convert one dose_titration_ladder band into an asm_rules DoseBand dict."""
    label = label_override or lb.get("band_label", "")
    lo, hi, is_kg_m = _parse_range(lb.get("maintenance_range", ""))
    edges, _ = _parse_edges(lb.get("band_label", ""))
    init_raw = (lb.get("initial_dose") or {}).get("raw")
    init_v = _num(init_raw); init_u = _unit(init_raw)
    incr_raw = (lb.get("dose_increase_amount") or {}).get("raw")
    incr_v = _num(incr_raw); incr_u = _unit(incr_raw)
    iv = _interval_weeks(lb.get("dose_increase_interval"))
    init_cap = lb.get("initial_dose_cap_mg_per_day")
    max_abs = lb.get("max_absolute_mg_per_day")
    weight_based = is_kg_m
    cap_txt = f" (start cap {init_cap} mg/day)" if init_cap not in (None, "") else ""
    note = (f"Reconciled from Barbour {SHEET} [{lb.get('band_label')}]: "
            f"initial {init_raw}{cap_txt}, maintenance {lb.get('maintenance_range')}, "
            f"max {max_abs} mg/day, increase {incr_raw} "
            f"{str(lb.get('dose_increase_interval')).lower()}.")
    if regimen_note:
        note = regimen_note + " " + note
    b = {
        "label": label,
        "dose_basis": ("weight_based_mg_per_kg_per_day" if weight_based
                       else "weight_band_fixed_mg_per_day"),
        "min_age_years": edges["min_age_years"], "max_age_years": edges["max_age_years"],
        "min_weight_kg": edges["min_weight_kg"], "max_weight_kg": edges["max_weight_kg"],
    }
    if weight_based:
        b.update({
            "start_mg_per_kg_per_day": init_v if init_u == "kg" else None,
            "target_mg_per_kg_per_day_min": lo, "target_mg_per_kg_per_day_max": hi,
            "max_mg_per_kg_per_day": hi,
            "uptitration_step_mg_per_kg_per_day": incr_v if incr_u == "kg" else None,
            "uptitration_interval_weeks": iv,
            "absolute_max_mg_per_day": max_abs,
        })
    else:
        b.update({
            "dose_range_min_mg_per_day": lo, "dose_range_max_mg_per_day": hi,
            "max_daily_mg": max_abs, "absolute_max_mg_per_day": max_abs,
            "standard_uptitration_step_mg": incr_v,
            "standard_uptitration_interval_weeks": iv,
        })
    b.update({
        "frequency_per_day": freq, "source": source_tag, "verified": bool(verified),
        "initial_dose_cap_mg_per_day": _num(init_cap),
        "clinician_note": note,
    })
    return b


def _reconciled_bands(drug, ladder_bands, draft_bands, flags=None):
    """Sheet-derived bands for `drug`. Lamotrigine keeps its WITH-valproate /
    WITH-enzyme-inducer bands from the draft (not covered by the sheet)."""
    freq = (draft_bands[0].get("frequency_per_day") if draft_bands else 2) or 2
    if drug != "lamotrigine":
        specs = []
        for lb in ladder_bands:
            band = _band_from_ladder(
                lb, freq, SRC,
                verified=not _band_flagged(flags, drug, lb.get("band_label")))
            specs.append({"band": band, "edges": {k: band[k] for k in
                ("min_age_years", "max_age_years", "min_weight_kg", "max_weight_kg")}})
        is_kg = any(s["edges"]["min_weight_kg"] is not None
                    or s["edges"]["max_weight_kg"] is not None for s in specs)
        specs = _make_contiguous(specs, is_kg)
        return _flatten(specs)

    # lamotrigine: reconcile only the no-valproate/no-inducer regimen
    specs = []
    for lb in ladder_bands:
        lbl = lb.get("band_label", "") + " - WITHOUT valproate and WITHOUT enzyme inducer"
        band = _band_from_ladder(lb, freq, SRC, label_override=lbl,
            verified=not _band_flagged(flags, drug, lb.get("band_label")),
            regimen_note="Sheet footnote: applies ONLY to patients NOT on valproate "
                         "and NOT on an enzyme inducer.")
        specs.append({"band": band, "edges": {k: band[k] for k in
            ("min_age_years", "max_age_years", "min_weight_kg", "max_weight_kg")}})
    specs = _make_contiguous(specs, is_kg=False)
    none_bands = _flatten(specs)
    preserved = []
    for b in draft_bands:
        lab = b.get("label", "").lower()
        if ("with valproate" in lab or "with enzyme inducer" in lab
                or "with an enzyme inducer" in lab):
            pb = json.loads(json.dumps(b))
            pb["verified"] = False
            pb["clinician_note"] = ("PRESERVED (not in Barbour sheet; the sheet's ladder "
                "covers only the no-valproate/no-inducer regimen). DRAFT conmed-dependent "
                "SJS/TEN safety band. " + pb.get("clinician_note", "")).strip()
            preserved.append(pb)
    return none_bands + preserved


def _flatten(specs):
    out = []
    for s in specs:
        b = s["band"]
        for k in ("min_age_years", "max_age_years", "min_weight_kg", "max_weight_kg"):
            b[k] = s["edges"][k]
        out.append(b)
    return out


def build():
    with open(DRAFT) as fh:
        draft = json.load(fh)
    with open(LADDER) as fh:
        ladder_doc = json.load(fh)
    ladder = ladder_doc.get("bands_by_drug", {})
    flags = ladder_doc.get("data_quality_flags") or []

    out = {
        "_README": (
            "CLINICIAN-SUPPLIED ASM dosing rules + drug facts, loaded by "
            "engine_core.asm_rules.load_asm_rules(). DOSE BANDS are RECONCILED to Dr. "
            "Barbour's source of truth (data/dose_titration_ladder.json == "
            f"kristen/{SHEET}.xlsx): every initial/maintenance/max/"
            "increment number is derived from that ladder, so this file matches the sheet "
            "band-for-band and cannot silently drift. Rebuild with data/_build_asm_rules.py. "
            "Dose bands from her v9 sheet are verified:true, except data_quality_flags and "
            "the lamotrigine concomitant bands the sheet does not cover. Drug FACTS "
            "(treats_only, avoid_if_*, no_switch_targets, sjs_risk, conmed_dependent_titration) "
            "encode her written notes. CONVENTIONS: (1) dose_basis follows the sheet's "
            "maintenance unit; (2) each band's ceiling = its own sheet 'Max Absolute Dose' so "
            "weight tiers keep their specific caps; (3) adjacent bands are made contiguous by "
            "extending the lower band up to just below the next band's floor (borderline "
            "patient -> lower/safer ceiling, no no-band gap); (4) frequency_per_day is carried "
            "from the prior DRAFT (the sheet does not specify it); (5) LAMOTRIGINE: only the "
            "no-valproate/no-inducer regimen is reconciled to the sheet (its footnote scope), "
            "split into the sheet's 3 age bands; the WITH-valproate and WITH-enzyme-inducer "
            "bands are PRESERVED from the draft (SJS/TEN safety, not covered by the sheet)."),
        "_provenance": {
            "dose_bands_source": SRC,
            "reconciled_on": "2026-07-14",
            "reconciled_to": f"kristen/{SHEET}.xlsx == data/dose_titration_ladder.json",
            "drug_facts_source": "clinician_verification/asm_rules.draft.json (RxNorm+FDA PI) + Barbour 2026-06 review",
            "status": "v9 dose bands verified, except data_quality_flags and lamotrigine concomitant bands",
            "pi_flags": [
                f"levetiracetam effective max: {SHEET} = 60 mg/kg/day; earlier 2026-06 note said 80. "
                "Reconciled to the sheet (60); still open.",
                "See dose_titration_ladder.json data_quality_flags (zonisamide child step; "
                "brivaracetam >=50 kg initial dose).",
            ],
        },
    }

    for name, entry in draft.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        e = json.loads(json.dumps(entry))  # deep copy (keeps formulations, rxcui, etc.)
        if name in ladder:
            e["dose_bands"] = _reconciled_bands(
                name, ladder[name], entry.get("dose_bands", []), flags)
        # else: no ladder entry -> keep draft bands (should not happen for the 11 study drugs)

        facts = FACTS.get(name, {})
        for k, v in facts.items():
            e[k] = v
        e.setdefault("in_study", True)
        bands = e.get("dose_bands") or []
        # The absence/myoclonic caution on these two is not in her sheet.
        if name in ("carbamazepine", "oxcarbazepine"):
            e["verified"] = False
        else:
            e["verified"] = bool(bands) and all(b.get("verified") for b in bands)
        out[name] = e

    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"wrote {OUT} with {len([k for k in out if not k.startswith('_')])} drugs "
          f"(dose bands reconciled to dose_titration_ladder.json)")


if __name__ == "__main__":
    build()
