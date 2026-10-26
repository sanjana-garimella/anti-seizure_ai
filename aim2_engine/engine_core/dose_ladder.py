"""
dose_ladder.py
--------------
Loads the clinician-supplied per-drug/weight-band DOSE TITRATION LADDER
(data/dose_titration_ladder.json, transcribed VERBATIM from Dr. Barbour's
final_doses_titration_v9.xlsx, built by data/_build_dose_ladder.py) and resolves the EXACT next dose(s) for a patient
from the explicit rungs she specified.

HARD RULES (spec §11.1 + clinical-safety.md):
  * NO clinical numbers are defined in this module. Every dose comes from the
    JSON file. If the file/band is absent or a value is null, the resolver
    returns None and the caller falls back to its existing behavior (defer).
  * Bands from her confirmed sheet carry verified:true. A band named in
    data_quality_flags stays verified:false and keeps the DRAFT note.
  * The engine READS the explicit rungs; it never recomputes or interpolates a
    dose the clinician did not list.

Rung tiers (from the sheet's columns):
  pre         - below the minimum maintenance / goal   (cols Q-Y)
  maintenance - the minimum maintenance / goal dose     (col  Z)
  up          - tolerated increases above goal          (cols AA-AI, <= effective max)
  high        - higher increases often NOT tolerated     (cols AJ-AP); reaching these
                triggers the dagger (high_dose) labs, if applicable to the drug.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_LADDER = os.path.join(_DATA, "dose_titration_ladder.json")

_TOL = 1e-6


def _clean(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if not k.startswith("_")}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


def load_ladder(path: Optional[str] = None) -> Dict[str, Any]:
    """The dose-titration ladder, or {} if the file is absent/corrupt - in which
    case the caller keeps its existing (asm_rules-based) schedule behavior."""
    p = path or _LADDER
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as fh:
            return _clean(json.load(fh))
    except (json.JSONDecodeError, OSError):
        return {}


_CACHE: Optional[Dict[str, Any]] = None


def default_ladder() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_ladder()
    return _CACHE


def reset_cache() -> None:
    """Clear the cache so the next default_ladder() reloads from disk (PI edits
    data/dose_titration_ladder.json). Mirrors titration_config.reset_caches()."""
    global _CACHE
    _CACHE = None


def conmed_suppression_reason(drug_key: Optional[str],
                              concomitant_asms: Optional[List[str]],
                              ladder: Optional[Dict[str, Any]] = None
                              ) -> Optional[Dict[str, str]]:
    """If this drug's ladder is transcribed only for patients NOT on certain
    concomitant meds (lamotrigine: '*for patients NOT receiving valproate and NOT
    receiving enzyme-inducing antiseizure medications') and the patient IS on such
    a med, return {"regimen", "note"} so the caller SUPPRESSES the (too-aggressive)
    ladder and points to the conmed-specific regimen (asm_rules.json). Else None.

    All drug-name knowledge lives in data/dose_titration_ladder.json
    (conmed_restrictions_by_drug); this function only matches strings - no
    clinical value is hardcoded (clinical-safety.md / spec 11.1)."""
    ladder = ladder if ladder is not None else default_ladder()
    restr = (ladder.get("conmed_restrictions_by_drug") or {}).get((drug_key or "").lower())
    if not restr or not concomitant_asms:
        return None
    meds = [str(m).lower() for m in concomitant_asms]
    not_valid = restr.get("not_valid_with") or []
    vpa = restr.get("valproate_synonyms") or []
    ind = restr.get("enzyme_inducers") or []
    if "valproate" in not_valid and any(syn in m for m in meds for syn in vpa):
        return {"regimen": "valproate", "note": restr.get("note", "")}
    if "enzyme_inducer" in not_valid and any(name in m for m in meds for name in ind):
        return {"regimen": "enzyme_inducer", "note": restr.get("note", "")}
    return None


# ---------------------------------------------------------------------------
# Band-label range parsing.  A label is either weight-based (contains "kg") or
# age-based (years). We match the patient's weight OR age against the band's
# range. Returns (low, high, unit) with unit in {"kg","years"}; bounds are
# inclusive-low / exclusive-high to mirror the sheet's "X to <Y" convention,
# except the open-ended ">=X" / ">X" (high=inf) and "<X"/"<=X" (low=0) forms.
# ---------------------------------------------------------------------------
_NUM = r"(\d+\.?\d*)"


def parse_band_range(label: str) -> Optional[Tuple[float, float, str]]:
    if not label:
        return None
    t = label.replace(",", "").replace("–", "-").replace("—", "-")
    unit = "kg" if "kg" in t.lower() else ("years" if ("year" in t.lower()
           or "adult" in t.lower() or "adolescent" in t.lower()) else None)
    if unit is None:
        return None
    nums = [float(n) for n in re.findall(_NUM, t)]
    low, high = 0.0, float("inf")
    tl = t.lower()
    # explicit two-number ranges: "X to <Y", "X-Y"
    m = re.search(_NUM + r"\s*(?:to|-)\s*<?\s*" + _NUM, t)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        return (low, high, unit)
    if not nums:
        # e.g. "Children ... and Adults" with no number -> treat as all ages
        return (0.0, float("inf"), unit)
    x = nums[0]
    if "≥" in t or ">=" in t or "at least" in tl:
        return (x, float("inf"), unit)
    if ">" in t:               # ">18 years", ">16 years", ">38 kg"
        return (x, float("inf"), unit)
    if "≤" in t or "<=" in t:
        return (0.0, x, unit)
    if "<" in t:
        return (0.0, x, unit)
    return (x, float("inf"), unit)


def _matches(label: str, age_years: Optional[float], weight_kg: Optional[float]) -> bool:
    rng = parse_band_range(label)
    if rng is None:
        return False
    low, high, unit = rng
    val = weight_kg if unit == "kg" else age_years
    if val is None:
        return False
    return (val >= low - _TOL) and (val < high or high == float("inf"))


def band_for(drug_key: str, age_years: Optional[float], weight_kg: Optional[float],
             ladder: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The ladder band whose (weight- or age-) range contains the patient, or
    None. On multiple matches the tightest (smallest span) band wins."""
    ladder = ladder if ladder is not None else default_ladder()
    bands = (ladder.get("bands_by_drug") or {}).get((drug_key or "").lower())
    if not bands:
        return None
    matches = []
    for b in bands:
        if _matches(b.get("band_label", ""), age_years, weight_kg):
            rng = parse_band_range(b.get("band_label", ""))
            span = (rng[1] - rng[0]) if rng and rng[1] != float("inf") else float("inf")
            matches.append((span, b))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    return matches[0][1]


# ---------------------------------------------------------------------------
def _finite(x: Any) -> Optional[float]:
    """A finite, non-negative float, or None. Rejects None, bool, NaN, +/-inf,
    negatives and non-numerics so malformed/out-of-bounds cells degrade (defer)
    instead of poisoning downstream dose math."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    f = float(x)
    if not math.isfinite(f) or f < 0:
        return None
    return f


def _val(cell: Any) -> Optional[float]:
    if isinstance(cell, dict):
        return _finite(cell.get("value"))
    return _finite(cell)


def _unit(cell: Any) -> Optional[str]:
    return cell.get("unit") if isinstance(cell, dict) else None


def _to_mg_per_day(value: Optional[float], unit: Optional[str],
                   weight_kg: Optional[float]) -> Optional[float]:
    value = _finite(value)
    if value is None:
        return None
    if unit == "mg_per_kg_per_day":
        w = _finite(weight_kg)
        return value * w if (w and w > 0) else None
    # mg_per_day (or unspecified numeric already in mg/day)
    return value


def build_rungs(band: Dict[str, Any], weight_kg: Optional[float]) -> List[Dict[str, Any]]:
    """Ordered, de-duplicated rungs in mg/day with a tier tag. Skips stray/
    non-numeric cells (flagged in data_quality_flags) rather than guessing."""
    out: List[Dict[str, Any]] = []

    def add(cells, tier):
        for c in (cells or []):
            v = _val(c)
            mgday = _to_mg_per_day(v, _unit(c), weight_kg)
            if mgday is None:
                continue
            out.append({"mg_per_day": round(mgday, 3), "tier": tier,
                        "raw": (c.get("raw") if isinstance(c, dict) else str(c))})

    add(band.get("pre_maintenance_increments"), "pre")
    add([band.get("min_maintenance_dose")], "maintenance")
    add(band.get("up_increments"), "up")
    add(band.get("high_increments_often_not_tolerated"), "high")

    # order by dose; keep the first (lowest) tier label on exact duplicates so a
    # dose that appears in both an up- and high- column is not mislabeled "high".
    out.sort(key=lambda r: r["mg_per_day"])
    dedup: List[Dict[str, Any]] = []
    for r in out:
        if dedup and abs(dedup[-1]["mg_per_day"] - r["mg_per_day"]) <= _TOL:
            continue
        dedup.append(r)
    return dedup


def _norm_label(s: Any) -> str:
    s = str(s or "").replace("–", "-").replace("≥", ">=").replace("≤", "<=")
    return re.sub(r"\s+", " ", s).strip().lower()


def quality_flag(ladder: Dict[str, Any], drug_key: Optional[str],
                 band: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The data_quality_flags row for this band, if her sheet still has an open cell."""
    if not band:
        return None
    lbl = _norm_label(band.get("band_label"))
    for f in (ladder.get("data_quality_flags") or []):
        if (str(f.get("drug", "")).lower() == (drug_key or "").lower()
                and lbl in {_norm_label(x) for x in str(f.get("band", "")).split(" / ")}):
            return f
    return None


def _band_verified(ladder: Dict[str, Any], drug_key: Optional[str],
                   band: Optional[Dict[str, Any]]) -> bool:
    """True when the ladder file is her confirmed sheet and this band is not flagged."""
    return bool(ladder.get("verified", False)) and quality_flag(ladder, drug_key, band) is None


def reference_for(drug_key: str, age_years: Optional[float], weight_kg: Optional[float],
                  concomitant_asms: Optional[List[str]] = None,
                  ladder: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The clinician DRAFT REFERENCE dose for THIS child (age/weight), independent
    of the currently-entered dose. For the physician-facing report only (never the
    family message). Returns the goal (min maintenance), usual effective max, and
    absolute max from Dr. Barbour's ladder band, or:
      * None                       - no ladder / no matching band;
      * {"conmed_suppressed": True, ...} - the ladder does not apply to this
        patient's concomitant meds (lamotrigine + valproate/enzyme inducer);
        the report should point to the conmed-specific regimen instead.
    Values are hers. A flagged band stays unverified."""
    ladder = ladder if ladder is not None else default_ladder()
    if not ladder:
        return None
    supp = conmed_suppression_reason(drug_key, concomitant_asms, ladder)
    if supp:
        return {"conmed_suppressed": True, "regimen": supp["regimen"],
                "note": supp["note"], "band_label": None}
    band = band_for(drug_key, age_years, weight_kg, ladder)
    if band is None:
        return None
    basis = _unit(band.get("min_maintenance_dose")) or "mg_per_day"
    min_maint = _to_mg_per_day(_val(band.get("min_maintenance_dose")), basis, weight_kg)
    eff = band.get("effective_max") or {}
    eff_max = (_to_mg_per_day(eff.get("value"), basis, weight_kg)
               if basis == "mg_per_kg_per_day" else eff.get("value"))
    return {
        "conmed_suppressed": False,
        "band_label": band.get("band_label"),
        "dose_basis": basis,
        "weight_kg": _finite(weight_kg),   # for weight-based mg/kg/day display
        "min_maintenance_mg_per_day": round(min_maint, 1) if min_maint is not None else None,
        "effective_max_mg_per_day": round(eff_max, 1) if isinstance(eff_max, (int, float)) else None,
        "absolute_max_mg_per_day": band.get("max_absolute_mg_per_day"),
        # per-band titration pacing (same source of truth -> Dosing and pacing views
        # can't drift): the clinician's increment amount + interval for this band.
        "step_raw": (band.get("dose_increase_amount") or {}).get("raw"),
        "interval": band.get("dose_increase_interval"),
        "verified": _band_verified(ladder, drug_key, band),
    }


def resolve_plan(drug_key: str, age_years: Optional[float], weight_kg: Optional[float],
                 current_mg_per_day: Optional[float], n_up: int = 1,
                 ladder: Optional[Dict[str, Any]] = None,
                 concomitant_asms: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Resolve the exact next dose(s) UP from the patient's current dose, using
    only the clinician's explicit rungs.

    Returns None when the ladder / band / current dose is unavailable (caller
    falls back). Otherwise a DRAFT, provenance-tagged plan dict:
        {dose_basis, current_mg_per_day, min_maintenance_mg_per_day,
         effective_max_mg_per_day, absolute_max_mg_per_day, rungs,
         next_up:[{step,target_mg_per_day,tier,at_or_below_current?}],
         reaches_high_tier, high_dose_labs, band_label, verified, note,
         data_quality_flag}
    """
    ladder = ladder if ladder is not None else default_ladder()
    if not ladder:
        return None
    band = band_for(drug_key, age_years, weight_kg, ladder)
    current_mg_per_day = _finite(current_mg_per_day)
    if band is None or current_mg_per_day is None:
        return None
    # Concomitant-med restriction (lamotrigine ladder is transcribed *for patients
    # NOT on valproate / enzyme inducers). If the patient is on a restricted comed,
    # SUPPRESS this ladder (it would titrate far too fast -> SJS/TEN risk); the
    # caller falls back to the conmed-specific regimen (asm_rules.json) and surfaces
    # conmed_suppression_reason(...). Returning None mirrors the no-band fallback.
    if conmed_suppression_reason(drug_key, concomitant_asms, ladder):
        return None
    rungs = build_rungs(band, weight_kg)
    if not rungs:
        return None

    basis = (_unit(band.get("min_maintenance_dose"))
             or _unit((band.get("up_increments") or [None])[0]) or "mg_per_day")
    min_maint = _to_mg_per_day(_val(band.get("min_maintenance_dose")),
                               _unit(band.get("min_maintenance_dose")), weight_kg)
    eff = band.get("effective_max") or {}
    eff_max = _to_mg_per_day(eff.get("value"), basis, weight_kg) if _unit(band.get("min_maintenance_dose")) == "mg_per_kg_per_day" else eff.get("value")
    abs_max = band.get("max_absolute_mg_per_day")

    # Round the entered dose to the nearest ladder rung = the patient's "level"
    # (families/doctors enter off-grid values; this snaps to the tables' grid so
    # lookups are deterministic). The snap is for DISPLAY / level indexing only.
    cur_idx = min(range(len(rungs)),
                  key=lambda i: abs(rungs[i]["mg_per_day"] - current_mg_per_day))
    cur_rung = rungs[cur_idx]
    on_grid = abs(cur_rung["mg_per_day"] - current_mg_per_day) <= max(_TOL, 0.02 * cur_rung["mg_per_day"])

    # next rungs strictly above the ENTERED dose (never propose a dose <= what
    # they already take, even after rounding the displayed level) AND never above
    # this band's absolute maximum. NON_NEGOTIABLE_RULES #2 / clinical-safety.md:
    # "Never exceed maximum dose; enforce dose ceilings." For weight-based (mg/kg)
    # bands a heavy in-band patient can have computed rungs above the absolute cap
    # (e.g. levetiracetam 60 mg/kg x 60 kg = 3600 > 3000 mg/day max); those are
    # withheld here so the engine never proposes an over-ceiling next dose.
    above = [r for r in rungs
             if r["mg_per_day"] > current_mg_per_day + _TOL
             and (abs_max is None or r["mg_per_day"] <= abs_max + _TOL)]
    rungs_over_max_dropped = bool(
        abs_max is not None and any(r["mg_per_day"] > abs_max + _TOL for r in rungs))
    n_up = max(1, int(n_up or 1))
    picks = above[:n_up]
    next_up = []
    reaches_high = False
    for i, r in enumerate(picks, start=1):
        if r["tier"] == "high":
            reaches_high = True
        next_up.append({"step": i, "target_mg_per_day": r["mg_per_day"],
                        "tier": r["tier"], "raw": r["raw"]})

    labs = []
    if reaches_high:
        labs = ((ladder.get("high_dose_labs_by_drug") or {}).get((drug_key or "").lower()) or [])

    # A flag on one band must not mark every band of that drug unverified.
    dq = quality_flag(ladder, drug_key, band)
    verified = _band_verified(ladder, drug_key, band)

    _src = re.search(r"final_doses_titration_v\d+", str(ladder.get("source") or ""))
    src_name = _src.group(0) if _src else "dose ladder"
    if verified:
        note = (f"Exact rungs are clinician-supplied (Barbour {src_name}). "
                "The engine reads the listed rungs; it does not interpolate.")
    else:
        note = (f"Exact rungs are clinician-supplied (Barbour {src_name}). "
                "DRAFT - verified:false, pending PI sign-off (NEEDS_PI_SIGNOFF.md). "
                "The engine reads the listed rungs; it does not interpolate.")
    if reaches_high:
        note += (" Next increase enters the higher, often-less-tolerated range - order "
                 "the listed labs and review tolerability.")
    if rungs_over_max_dropped:
        note += (" One or more listed rungs exceed this band's absolute maximum "
                 f"({abs_max} mg/day) for the patient's weight and were withheld - "
                 "the engine never proposes a dose above the clinician maximum.")

    return {
        "dose_basis": basis,
        "band_label": band.get("band_label"),
        "weight_kg": _finite(weight_kg),   # for weight-based mg/kg/day display
        "current_mg_per_day": round(current_mg_per_day, 3),
        "min_maintenance_mg_per_day": round(min_maint, 3) if min_maint is not None else None,
        "effective_max_mg_per_day": round(eff_max, 3) if isinstance(eff_max, (int, float)) else None,
        "absolute_max_mg_per_day": abs_max,
        "rungs": rungs,
        "current_rung_mg_per_day": cur_rung["mg_per_day"],
        "current_tier": cur_rung["tier"],
        "current_level_index": cur_idx + 1,      # 1-based position on the ladder
        "total_levels": len(rungs),
        "rounded_from": None if on_grid else round(current_mg_per_day, 3),
        "next_up": next_up,
        "at_or_above_top_rung": len(above) == 0,
        "rungs_above_absolute_max_dropped": rungs_over_max_dropped,
        "reaches_high_tier": reaches_high,
        "high_dose_labs": labs,
        "verified": verified,
        "note": note,
        "data_quality_flag": (dq.get("issue") if dq else None),
    }


# ---------------------------------------------------------------------------
# DISPLAY FORMATTERS (physician-facing DRAFT reference; NEVER the family message)
#
# ONE standardized rendering of the age/weight dose reference so every surface -
# the clinician summary, the inbox card, the exact-dose-ladder card, and the
# clinical-reference table (and any future one) - shows the SAME fields, order,
# and units and cannot drift. Pure, deterministic string helpers over values
# already resolved from the clinician's table (reference_for()/resolve_plan()):
# they invent no clinical number, and they add NO provenance of their own - the
# DRAFT / verified:false tag stays with the caller so each surface keeps its own
# wording. Weight-based bands additionally show mg/kg/day; fixed/age-banded
# bands show mg/day only.
# ---------------------------------------------------------------------------

def dose_value_str(mg_per_day: Any, weight_kg: Optional[float] = None,
                   per_kg: bool = False) -> Optional[str]:
    """A single dose value as 'N mg/day', or 'N mg/day (M mg/kg/day)' when the
    band is weight-based (`per_kg`) and a positive weight is known. Returns None
    when the value is not a finite, non-negative number, so a missing/None cell
    renders nothing rather than a fabricated 0."""
    v = _finite(mg_per_day)
    if v is None:
        return None
    s = f"{v:g} mg/day"
    if per_kg:
        w = _finite(weight_kg)
        if w and w > 0:
            s += f" ({round(v / w, 1):g} mg/kg/day)"
    return s


def dose_range_str(low: Any, high: Any, weight_kg: Optional[float] = None,
                   per_kg: bool = False) -> Optional[str]:
    """A low-to-high dose RANGE as 'L–H mg/day' (plus 'L–H mg/kg/day' when the
    band is weight-based and a positive weight is known). Falls back to a single
    value when only one bound is present; None when neither is a finite number.
    Used for the 'typical dose' (goal-to-effective-max) span."""
    lo = _finite(low)
    hi = _finite(high)
    if lo is not None and hi is not None:
        s = f"{lo:g}–{hi:g} mg/day"
        if per_kg:
            w = _finite(weight_kg)
            if w and w > 0:
                s += f" ({round(lo / w, 1):g}–{round(hi / w, 1):g} mg/kg/day)"
        return s
    return dose_value_str(lo if lo is not None else hi, weight_kg, per_kg)


def dose_reference_parts(ref: Optional[Dict[str, Any]],
                         weight_kg: Optional[float] = None,
                         current_mg_per_day: Optional[float] = None
                         ) -> List[tuple]:
    """The dose reference as `[(key, label, value)]` in display order:

        current · typical range · absolute max

    This is the ONE definition of which fields the reference has, what order they
    come in, what they are called, and what units they carry. `format_dose_reference`
    joins these into the one-liner; a surface that wants to lay the fields out
    itself (the clinician summary renders them as three separate bullets) reads the
    parts instead of re-deriving them, so the surfaces cannot drift.

    Absent values are omitted (a missing cell renders nothing, never a fabricated
    0). Adds no provenance - the DRAFT / verified:false tag stays with the caller.
    """
    if not isinstance(ref, dict):
        return []
    # mg/kg/day is shown for every DOSE whenever the child's weight is known (not
    # only for weight-based bands - 2026-09-30). The absolute max is a flat mg cap,
    # so it is labelled as such and NOT converted: e.g. 3000 mg/day shown as
    # "250 mg/kg/day" for a 12 kg child read as if that much were allowed.
    per_kg = True
    w = weight_kg if weight_kg is not None else ref.get("weight_kg")
    cur = current_mg_per_day if current_mg_per_day is not None else ref.get("current_mg_per_day")
    _abs = dose_value_str(ref.get("absolute_max_mg_per_day"))
    out: List[tuple] = []
    for key, label, value in (
        ("current", "current", dose_value_str(cur, w, per_kg)),
        ("typical_range", "typical range",
         dose_range_str(ref.get("min_maintenance_mg_per_day"),
                        ref.get("effective_max_mg_per_day"), w, per_kg)),
        ("absolute_max", "absolute max", f"{_abs} (fixed cap)" if _abs else None),
    ):
        if value is not None:
            out.append((key, label, value))
    return out


def format_dose_reference(ref: Optional[Dict[str, Any]],
                          weight_kg: Optional[float] = None,
                          current_mg_per_day: Optional[float] = None,
                          sep: str = " · ") -> str:
    """Standardized one-line dose reference for THIS child, in display order
    (Kristen 2026-07, label confirmed 2026-08-04): current dose · typical RANGE
    (min maintenance to effective max) · absolute max - each in mg/day, plus
    mg/kg/day when the band is weight-based. The range is deliberately shown as a
    span with no endpoint labels; "typical range" names it, and the absolute max is
    a separate field so the two ceilings cannot be read as the same number.

    `ref` is a reference_for()/resolve_plan() dict. `current_mg_per_day` (the
    patient's reported dose) overrides ref['current_mg_per_day'] when given;
    weight falls back to ref['weight_kg']. Any absent value is omitted; returns
    '' when nothing is renderable. DRAFT/provenance is intentionally NOT added
    here - the caller appends it."""
    return sep.join(f"{label} {value}" for _, label, value
                    in dose_reference_parts(ref, weight_kg, current_mg_per_day))
