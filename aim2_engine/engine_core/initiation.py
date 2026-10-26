"""
initiation.py
-------------
Look up Dr. Barbour's week-by-week STARTING schedule for a new medicine (add or
switch) from data/initiation_schedules.json (her '<Drug> Initiation' tabs, v9).

Display only: returns her cells verbatim for the clinician; never computes, rounds
or changes a dose, and never feeds the decision. Defers (returns no schedule, with
a reason) whenever the band is unclear, the drug has no tab, or the tab says it
does not apply (lamotrigine with valproate / an enzyme inducer). The tabs are
verified. The switch taper in the same file stays verified:false.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import dose_ladder, formulation

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_FILE = os.path.join(_DATA, "initiation_schedules.json")
_CACHE: Optional[Dict[str, Any]] = None


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """The schedules file, or {} if absent/corrupt (callers then show nothing)."""
    p = path or _FILE
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def default() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        _CACHE = load()
    return _CACHE


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


def _in_band(band: Optional[Dict[str, Any]], age: Optional[float], weight: Optional[float]) -> bool:
    if not band:
        return False
    v = weight if band.get("basis") == "weight_kg" else age
    if v is None:
        return False
    lo, hi = band.get("min"), band.get("max")
    if lo is not None and (v < lo or (v == lo and not band.get("min_inclusive", True))):
        return False
    if hi is not None and (v > hi or (v == hi and not band.get("max_inclusive", False))):
        return False
    return True


def _prefer_tablets(age: Optional[float], current_unit: Optional[str] = None) -> Optional[bool]:
    """Same form rule as the tablet/liquid conversion (formulation_rules.json): the
    form the family already uses (Dr. Barbour 2026-10-25, #61); only when that is
    unknown, the age rule (older than the cutoff -> tablets, else liquid). None
    when neither is known."""
    u = (current_unit or "").lower()
    if u == "tablets":
        return True
    if u == "ml":
        return False
    afr = (formulation.load_rules() or {}).get("age_form_rule") or {}
    if not afr.get("enabled") or age is None:
        return None
    return age > float(afr.get("tablet_if_age_greater_than", 10))


def schedule_for(drug_key: Optional[str], age_years: Optional[float], weight_kg: Optional[float],
                 concomitant_asms: Optional[List[str]] = None,
                 data: Optional[Dict[str, Any]] = None,
                 current_unit: Optional[str] = None) -> Dict[str, Any]:
    """{"drug", "available", "reason", "notes", "forms": [{form_title, kind, columns,
    band_label, cells, no_tablet}], "preferred_kind", "verified", "source"}.
    `available` is False (with a human-readable `reason`) whenever we defer."""
    data = data if data is not None else default()
    drug = (drug_key or "").lower()
    out: Dict[str, Any] = {"drug": drug, "available": False, "reason": None, "notes": [],
                           "forms": [], "preferred_kind": None,
                           "verified": bool(data.get("verified", False)),
                           "source": data.get("source")}
    never = ((data.get("switch_taper") or {}).get("not_started_as_new_medicine") or {}).get(drug)
    if never:
        out["reason"] = f"Not started as a new medicine ({never})"
        return out
    tab = (data.get("by_drug") or {}).get(drug)
    if not tab:
        out["reason"] = "No starting schedule from Dr. Barbour for this medicine yet."
        return out
    out["notes"] = list(tab.get("notes") or [])
    # Her lamotrigine tab: "for patients not on valproate and not on enzyme-inducing
    # drugs, which need different, lower dosing" - the ladder carries the same rule.
    sup = dose_ladder.conmed_suppression_reason(drug, concomitant_asms)
    if sup:
        out["reason"] = ("This starting schedule does not apply with the child's other medicine ("
                         f"{sup['regimen'].replace('_', ' ')}); clinician to set the starting doses.")
        return out
    if weight_kg is None and age_years is None:
        out["reason"] = "Weight and age not reported - band cannot be chosen."
        return out
    for t in tab.get("tables") or []:
        hits = [b for b in t.get("bands") or [] if _in_band(b.get("band"), age_years, weight_kg)]
        if len(hits) != 1:
            continue
        b = hits[0]
        cells = list(b.get("cells") or [])
        out["forms"].append({
            "form_title": t.get("form_title") or "",
            "kind": t.get("kind"),
            "columns": list(t.get("columns") or []),
            "band_label": b.get("band_label"),
            "cells": cells,
            "no_tablet": t.get("kind") == "solid" and all(c in (None, "—", "-") for c in cells[:1]),
        })
    if not out["forms"]:
        out["reason"] = ("No single band in Dr. Barbour's starting schedule fits this child "
                         f"(age {age_years if age_years is not None else '?'}, weight "
                         f"{weight_kg if weight_kg is not None else '?'} kg) - clinician to choose.")
        return out
    pref = _prefer_tablets(age_years, current_unit)
    if pref is not None:
        want = "solid" if pref else "liquid"
        out["preferred_kind"] = want
        out["forms"].sort(key=lambda f: 0 if f["kind"] in (want, "combined") else 1)
    out["available"] = True
    return out


def schedule_lines(sched: Dict[str, Any], max_forms: int = 2) -> List[str]:
    """Plain-text lines for the clinician summary: one line per form, weeks joined."""
    if not sched.get("available"):
        return [sched.get("reason") or "No starting schedule available."]
    lines = []
    for f in sched["forms"][:max_forms]:
        pairs = [f"{c}: {v}" for c, v in zip(f["columns"], f["cells"]) if v]
        title = f["form_title"] or "Starting dose"
        tag = " (preferred)" if f["kind"] == sched.get("preferred_kind") else ""
        if f.get("no_tablet"):
            lines.append(f"{title} [{f['band_label']}]{tag}: no tablet schedule fits this band - "
                         "use the liquid.")
            continue
        lines.append(f"{title} [{f['band_label']}]{tag}: " + "; ".join(pairs))
    return lines


def taper_lines(old_med: Optional[str], current_total_mg_per_day: Optional[float],
                freq: Optional[int], dose_unit: Optional[str] = None,
                concentration: Optional[float] = None,
                data: Optional[Dict[str, Any]] = None) -> List[str]:
    """Stopping the OLD medicine during a switch (Dr. Barbour's template F taper +
    her 2026-10-25 answer, #69): week-by-week fraction of the current dose, then off.
    The new medicine starts in week 1 alongside it. Display only, DRAFT. Amounts are
    shown as tablets of the child's own strength only when they come out to whole or
    half tablets, and as liquid rounded to the liquid step; otherwise mg only."""
    data = data if data is not None else default()
    tp = data.get("switch_taper") or {}
    mults = tp.get("old_medicine_multipliers_by_week") or []
    off = tp.get("old_medicine_off_after_week")
    name = (old_med or "the current medicine").replace("_", " ")
    tot = formulation._num(current_total_mg_per_day)
    fq = formulation._num(freq)
    if not mults or tot is None or not fq:
        return [f"Stopping {name}: current dose or doses per day not known - clinician to set "
                "the taper."]
    fq = int(fq)
    unit = (dose_unit or "").lower()
    strength = formulation._num(concentration)
    rules = formulation.load_rules() or {}
    step = formulation._num((rules.get("liquid_rounding") or {}).get("ml_per_dose_step"))
    how = {1: "once a day", 2: "twice a day"}.get(fq, f"{fq} times a day")
    parts = []
    for wk, mlt in enumerate(mults, start=1):
        per = tot * float(mlt) / fq
        txt = f"{round(per, 2):g} mg {how}"
        if unit == "tablets" and strength:
            n = per / strength
            if abs(n * 2 - round(n * 2)) < 1e-6 and n > 0:
                cnt = f"{n:g}".replace(".5", "½") if n % 1 else f"{int(n)}"
                cnt = "½" if abs(n - 0.5) < 1e-6 else cnt
                txt = f"{round(per, 2):g} mg ({cnt} × {strength:g} mg) {how}"
            else:
                txt += " (not a whole or half tablet - clinician to set the amount)"
        elif unit == "ml" and strength and step:
            ml = round(round(per / strength / step) * step, 6)
            txt = f"{ml * strength:g} mg ({ml:g} mL) {how}"
        label = "current dose" if abs(float(mlt) - 1.0) < 1e-9 else f"x{float(mlt):g}"
        parts.append(f"week {wk} ({label}): {txt}")
    tail = (f"; off after week {off}" if off else "")
    return [f"Stopping {name} (Dr. Barbour's template F taper, DRAFT): " + "; ".join(parts) + tail
            + f". Start the new medicine in week {tp.get('new_medicine_starts_week', 1)} alongside it."]
