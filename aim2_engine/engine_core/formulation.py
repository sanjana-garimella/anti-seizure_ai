"""
formulation.py
--------------
DISPLAY-ONLY conversion of a ladder dose (mg/day) into what the family actually
gives: whole/half tablets per dose, or mL of the oral liquid.

NON-NEGOTIABLE POSTURE (see NON_NEGOTIABLE_RULES.md):
  * Runs AFTER the decision; it never changes the category, the ladder target or
    the dose ceiling. Rule-based, deterministic, no ML.
  * Every rule comes from data/formulation_rules.json (Dr. Barbour's answers in
    kristen/dose_to_tablet_conversion_scenarios.xlsx, 'Flags & clarifications');
    strengths come from data/asm_rules.json formulations_rxnorm. No clinical value
    is hardcoded here. All output is a DRAFT (verified:false) for the clinician.
  * The absolute maximum is never crossed, whatever the rounding window allows.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

_RULES_CACHE: Optional[Dict[str, Any]] = None
_EPS = 1e-6


def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    """Load formulation_rules.json. Missing/corrupt -> {} (callers then show no
    tablet/liquid line rather than inventing rules)."""
    global _RULES_CACHE
    if path is None and _RULES_CACHE is not None:
        return _RULES_CACHE
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             os.pardir, "data", "formulation_rules.json")
    try:
        with open(p, encoding="utf-8") as fh:
            rules = json.load(fh)
    except (OSError, json.JSONDecodeError):
        rules = {}
    if path is None:
        _RULES_CACHE = rules
    return rules


_FORMS_CACHE: Optional[Dict[str, Any]] = None


def formulations_for(drug_key: Optional[str]) -> List[Dict[str, Any]]:
    """formulations_rxnorm for a drug from data/asm_rules.json ([] if absent)."""
    global _FORMS_CACHE
    if _FORMS_CACHE is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         os.pardir, "data", "asm_rules.json")
        try:
            with open(p, encoding="utf-8") as fh:
                _FORMS_CACHE = json.load(fh)
        except (OSError, json.JSONDecodeError):
            _FORMS_CACHE = {}
    entry = _FORMS_CACHE.get(drug_key or "") if drug_key else None
    return list((entry or {}).get("formulations_rxnorm") or []) if isinstance(entry, dict) else []


# ---------------------------------------------------------------------------
# Product lines (from asm_rules formulations_rxnorm)
# ---------------------------------------------------------------------------

def _num(x: Any) -> Optional[float]:
    """Finite positive number, or None (strings like '250' are accepted)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _is_er(form: str) -> bool:
    f = form.lower()
    return "extended" in f or " xr" in f or "(er" in f


def _is_dr(form: str) -> bool:
    return "delayed" in form.lower()


def _halvable(form: str) -> bool:
    """Plain tablets only: not ER/DR, capsules, films or orally-disintegrating."""
    f = form.lower()
    return ("tablet" in f and not _is_er(f) and not _is_dr(f)
            and "disintegrating" not in f and "odt" not in f)


def solid_lines(formulations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Solid product lines usable for twice/three-times-daily steps. ER (once-daily)
    products are left out (flag #9); DR units are used whole, never halved."""
    out = []
    for f in formulations or []:
        strengths = f.get("strengths_mg")
        form = str(f.get("form", ""))
        if not strengths or _is_er(form):
            continue
        out.append({"form": form, "strengths": sorted(float(s) for s in strengths),
                    "halvable": _halvable(form)})
    return out


def liquid_mg_per_ml(drug_key: str, formulations: List[Dict[str, Any]],
                     rules: Dict[str, Any]) -> Optional[float]:
    default = (rules.get("liquid_default_mg_per_ml") or {}).get(drug_key)
    if isinstance(default, (int, float)):
        return float(default)
    for f in formulations or []:
        if isinstance(f.get("mg_per_ml"), (int, float)):
            return float(f["mg_per_ml"])
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _per_dose_amounts(strengths: List[float], allow_half: bool,
                      max_units: int) -> Dict[float, Tuple[Tuple[float, float], ...]]:
    """Every per-dose mg amount makeable from up to `max_units` units, mapped to
    the fewest-units recipe ((strength, count), ...). Half units count as one."""
    pieces = [(s, 1.0) for s in strengths]
    if allow_half:
        pieces += [(s, 0.5) for s in strengths]
    best: Dict[float, Tuple[int, Tuple[Tuple[float, float], ...]]] = {}
    for n in range(1, max_units + 1):
        for combo in itertools.combinations_with_replacement(pieces, n):
            if sum(1 for _s, k in combo if k == 0.5) > 1:
                continue   # at most one half tablet per dose
            mg = round(sum(s * k for s, k in combo), 4)
            if mg in best and best[mg][0] <= n:
                continue
            recipe: Dict[Tuple[float, float], int] = {}
            for piece in combo:
                recipe[piece] = recipe.get(piece, 0) + 1
            best[mg] = (n, tuple(sorted(((s, k * c) for (s, k), c in recipe.items()),
                                        reverse=True)))
    return {mg: r for mg, (_n, r) in best.items()}


def _units(recipe) -> float:
    return sum(c for _s, c in recipe)


def _halves(recipe) -> int:
    return sum(1 for _s, c in recipe if c % 1)


def best_tablet_split(target: float, freq: int, strengths: List[float],
                      allow_half: bool, rules: Dict[str, Any],
                      absolute_max: Optional[float],
                      must_exceed: Optional[float] = None,
                      must_be_below: Optional[float] = None,
                      require_uneven: bool = False) -> Optional[Dict[str, Any]]:
    """Closest dose plan for `target` mg/day over `freq` doses/day, inside the
    -10/+30% window, never above the absolute max. Ranking: closest total first
    (ties go UP), then whole tablets over halves, then even over uneven, then
    fewest units. `must_exceed`: an increase step must land strictly above the
    previous dose (never round an increase back to the dose already taken);
    `must_be_below`: a climb step must stay under the NEXT step's target (never
    round a step past the one after it). `require_uneven`: only unequal splits
    (the family chose uneven dosing at Level 3, #52).
    Dr. Barbour's worked example (flag #3/#4: 1300 mg/day target on
    250 mg tablets -> 500 mg morning + 750 mg night). None = no fit."""
    w = rules.get("window") or {}
    lo = target * (1 + (w.get("low_pct", -10)) / 100.0)
    hi = target * (1 + (w.get("high_pct", 30)) / 100.0)
    if absolute_max is not None and (rules.get("rounding") or {}).get("never_exceed_absolute_max", True):
        hi = min(hi, float(absolute_max))
    if hi + _EPS < lo:
        return None
    uneven = rules.get("uneven_dosing") or {}
    min_share = float(uneven.get("min_share_of_total_for_bid", 0.5))
    max_ratio = (1 - min_share) / min_share if min_share > 0 else 1.0
    amounts = _per_dose_amounts(strengths, allow_half,
                                int((rules.get("max_units_per_dose") or {}).get("value", 4)))
    keys = sorted(amounts)
    best = None
    for i, a in enumerate(keys):
        for b in keys[i:]:
            if b > a and (not uneven.get("all_medications", False) or b / a > max_ratio + _EPS):
                break
            if require_uneven and b == a:
                continue      # family chose uneven dosing: smaller morning, larger night
            for k in (range(0, 1) if b == a else range(1, freq)):
                total = (freq - k) * a + k * b
                if total < lo - _EPS or total > hi + _EPS:
                    continue
                if must_exceed is not None and total <= must_exceed + _EPS:
                    continue
                if must_be_below is not None and total >= must_be_below - _EPS:
                    continue
                units = (freq - k) * _units(amounts[a]) + k * _units(amounts[b])
                halves = ((freq - k) * _halves(amounts[a]) + k * _halves(amounts[b]))
                above = total >= target - _EPS
                rank = (round(abs(total - target), 6), 0 if above else 1, halves,
                        0 if a == b else 1, round(b - a, 6), units)
                if best is None or rank < best[0]:
                    doses = [a] * (freq - k) + [b] * k   # smaller first -> larger at night
                    best = (rank, total, doses)
    if best is None:
        return None
    _rank, total, doses = best
    return {"total_mg_per_day": round(total, 3),
            "deviation_pct": round((total - target) / target * 100.0, 1) if target else 0.0,
            "doses": [{"mg": d, "recipe": amounts[d]} for d in doses]}


def _slot_names(freq: int) -> List[str]:
    return {1: ["once a day"], 2: ["morning", "night"],
            3: ["morning", "afternoon", "night"]}.get(freq, [f"dose {i + 1}" for i in range(freq)])


def _fmt_recipe(recipe) -> str:
    merged: Dict[float, float] = {}
    for s, c in recipe:                  # one line per strength: "2½ × 250 mg"
        merged[s] = merged.get(s, 0) + c
    parts = []
    for s, c in sorted(merged.items(), reverse=True):
        cnt = "½" if c == 0.5 else (f"{int(c)}½" if c % 1 else f"{c:g}")
        parts.append(f"{cnt} × {s:g} mg")
    return " + ".join(parts)


def _per_kg(mg_day: float, weight_kg: Optional[float]) -> str:
    w = _num(weight_kg)
    return f" ({round(mg_day / w, 1):g} mg/kg/day)" if w else ""


def _over_eff(mg_day: float, effective_max: Optional[float]) -> str:
    e = _num(effective_max)
    return (f"; above the usual max (~{e:g} mg/day), clinician to confirm"
            if e and mg_day > e + _EPS else "")


def describe_tablets(plan: Dict[str, Any], freq: int,
                     weight_kg: Optional[float] = None,
                     effective_max: Optional[float] = None) -> str:
    """Compact clinician line: 'morning 1 × 250 mg, night 2 × 250 mg = 750 mg/day
    (62.5 mg/kg/day)'."""
    doses = plan["doses"]
    names = _slot_names(freq)
    if len({d["mg"] for d in doses}) == 1:
        how = names[0] if freq == 1 else ("twice a day" if freq == 2 else f"{freq} times a day")
        body = f"{_fmt_recipe(doses[0]['recipe'])} {how}"
    else:
        body = ", ".join(f"{n} {_fmt_recipe(d['recipe'])}" for n, d in zip(names, doses))
    tot = plan["total_mg_per_day"]
    w = _num(weight_kg)
    # Only the decided dose is shown (Dr. Barbour 2026-10-25) - no "% vs the exact
    # ladder value"; deviation_pct stays in the structured plan.
    bits = [f"{round(tot / w, 1):g} mg/kg/day"] if w else []
    extra = f" ({', '.join(bits)})" if bits else ""
    return f"{body} = {tot:g} mg/day{extra}" + _over_eff(tot, effective_max)


def unit_noun(form: Optional[str]) -> str:
    """What one unit is called for the family: capsule, film or tablet (from the
    product form in asm_rules formulations_rxnorm; e.g. ethosuximide 250 mg is a
    capsule - Dr. Barbour 2026-10-25)."""
    f = (form or "").lower()
    if "capsule" in f:
        return "capsule"
    if "film" in f:
        return "film"
    return "tablet"


def _unit_count(c: float, noun: str = "tablet") -> str:
    whole = int(c)
    if c == 0.5:
        return f"½ {noun}"
    n = f"{whole}½" if c % 1 else f"{whole}"
    return f"{n} {noun}" if c == 1 else f"{n} {noun}s"


def _tablet_count(c: float) -> str:
    return _unit_count(c, "tablet")


def family_tablet_text(plan: Dict[str, Any], freq: int, noun: str = "tablet") -> str:
    """Family wording, mg first with the unit count in brackets (Dr. Barbour
    2026-10-25, #67): '500 mg (1 tablet) twice a day', or for an uneven split
    '500 mg (2 tablets) in the morning and 750 mg (3 tablets) at night'. Same plan
    the clinician summary shows."""
    doses = plan["doses"]

    def _one(d):
        n = sum(c for _s, c in d["recipe"])
        return f"{d['mg']:g} mg ({_unit_count(n, noun)})"

    names = _slot_names(freq)
    if len({d["mg"] for d in doses}) == 1:
        how = "once a day" if freq == 1 else ("twice a day" if freq == 2 else f"{freq} times a day")
        return f"{_one(doses[0])} {how}"
    parts = [f"{_one(d)} in the {n}" if n != "night" else f"{_one(d)} at night"
             for n, d in zip(names, doses)]
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]


def family_liquid_text(total_mg: float, ml_per_dose: float, freq: int) -> str:
    """'530 mg (5.3 mL) twice a day' - the same mg-first pattern as tablets."""
    how = {1: "once a day", 2: "twice a day"}.get(freq, f"{freq} times a day")
    return f"{round(total_mg / freq, 3):g} mg ({ml_per_dose:g} mL) {how}"


def round_liquid(target: float, freq: int, conc: float, rules: Dict[str, Any],
                 absolute_max: Optional[float], must_exceed: Optional[float] = None
                 ) -> Optional[Tuple[float, float]]:
    """(mL per dose, mg/day) for the measurable liquid dose closest to `target`:
    mL rounded to liquid_rounding.ml_per_dose_step, inside the -10/+30% window,
    above `must_exceed`, never above the absolute max. None = no measurable fit."""
    step = _num((rules.get("liquid_rounding") or {}).get("ml_per_dose_step"))
    exact = target / freq / conc
    if step is None:
        return round(exact, 2), target
    w = rules.get("window") or {}
    lo = target * (1 + w.get("low_pct", -10) / 100.0)
    hi = target * (1 + w.get("high_pct", 30) / 100.0)
    if absolute_max is not None:
        hi = min(hi, float(absolute_max))
    base = round(exact / step)
    best = None
    for k in range(base - 3, base + 4):
        ml = round(k * step, 6)
        if ml <= 0:
            continue
        tot = round(ml * conc * freq, 3)
        if tot < lo - _EPS or tot > hi + _EPS:
            continue
        if must_exceed is not None and tot <= must_exceed + _EPS:
            continue
        key = (abs(tot - target), 0 if tot >= target else 1)
        if best is None or key < best[0]:
            best = (key, ml, tot)
    return (float(f"{best[1]:g}"), best[2]) if best else None


def resplit_tablets(total_mg_per_day: float, freq: int, strength: float,
                    formulations: List[Dict[str, Any]], how: str,
                    rules: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Same daily total, split differently (Level-3 family choice, #52): `how` is
    "uneven" (smaller morning, larger night, within the 30/70-40/60 range) or "tid"
    (three doses a day). Whole tablets of the child's current strength only, and the
    total must stay exactly the same. None when no such split exists (the team then
    sets the amounts)."""
    rules = rules if rules is not None else load_rules()
    tot, strength = _num(total_mg_per_day), _num(strength)
    if not rules or tot is None or strength is None:
        return None
    line = next((ln for ln in solid_lines(formulations)
                 if any(abs(s_ - strength) < _EPS for s_ in ln["strengths"])), None)
    if line is None:
        return None
    n = 3 if how == "tid" else freq
    if not n or (how == "uneven" and n < 2):
        return None
    exact = dict(rules, window={"low_pct": 0, "high_pct": 0})
    plan = best_tablet_split(tot, n, [strength], False, exact, None,
                             require_uneven=(how == "uneven"))
    if plan is None or abs(plan["total_mg_per_day"] - tot) > _EPS:
        return None
    noun = unit_noun(line["form"])
    return {"plan": plan, "freq": n, "unit_noun": noun,
            "family_text": family_tablet_text(plan, n, noun)}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def step_options(drug_key: str, targets: List[float], freq: Optional[int],
                 formulations: List[Dict[str, Any]], current_unit: Optional[str] = None,
                 current_strength: Optional[float] = None,
                 current_formulation: Optional[str] = None,
                 absolute_max: Optional[float] = None,
                 current_total_mg_per_day: Optional[float] = None,
                 weight_kg: Optional[float] = None,
                 effective_max: Optional[float] = None,
                 rules: Optional[Dict[str, Any]] = None,
                 age_years: Optional[float] = None,
                 split: Optional[str] = None) -> List[Dict[str, Any]]:
    """For each ladder target (mg/day, in step order; the last is where the child
    stays) return the DECIDED dose for that step: tablets or a measurable liquid
    volume. Tablets: the strength whose plan lands closest to the ladder dose; on a
    tie the child's current strength (or the previous step's), then the fewest
    tablets. One strength per step. Display only."""
    rules = rules if rules is not None else load_rules()
    if not rules or not targets:
        return []
    # Defensive coercion: survey/host values may arrive as strings or garbage.
    fq = _num(freq)
    freq = int(fq) if fq is not None and fq == int(fq) else None
    current_strength = _num(current_strength)
    absolute_max = _num(absolute_max)
    current_total_mg_per_day = _num(current_total_mg_per_day)
    unit = (current_unit or "").lower()
    on_tablets, on_liquid = unit == "tablets", unit in ("ml",)
    keep = (rules.get("form_continuity") or {}).get("keep_current_form", True)
    show_tab = on_tablets or not (keep and on_liquid)
    show_liq = on_liquid or not (keep and on_tablets)
    lines = solid_lines(formulations)
    conc = liquid_mg_per_ml(drug_key, formulations, rules)
    half_ok = (rules.get("half_tablets") or {}).get("allowed_on_climb_steps_only", False)
    form_notes: List[str] = []
    age_picks_liquid = False

    # Age rule (Dr. Barbour 2026-10-25, #61): ONLY when the family's current form is
    # unknown (simulated cases). Older than the cutoff -> tablets, otherwise liquid.
    # A known form is always kept. Falls back to the other form if no product exists.
    afr = rules.get("age_form_rule") or {}
    age = _num(age_years)
    form_known = on_tablets or on_liquid
    if (afr.get("enabled") and age is not None
            and (afr.get("overrides_current_form", False) or not form_known)):
        want_tab = age > float(afr.get("tablet_if_age_greater_than", 10)) + _EPS
        if want_tab and lines:
            show_tab, show_liq = True, False
        elif not want_tab and conc:
            show_tab, show_liq = False, True
            age_picks_liquid = True
        else:
            show_tab, show_liq = bool(lines), bool(conc)
            form_notes.append("Age rule prefers " + ("tablets" if want_tab else "liquid")
                              + ", but this medication has no usable "
                              + ("tablet" if want_tab else "oral liquid") + " product listed - "
                              "showing the other form; clinician to confirm.")
        if form_known and on_tablets != want_tab and show_tab == want_tab:
            form_notes.append(f"Age {age:g} y: showing "
                              + ("tablets" if want_tab else "liquid")
                              + f" per the age rule (family reports {'tablets' if on_tablets else 'liquid'}).")

    # Candidate strengths: one strength per step (Dr. Barbour 2026-10-25, #62/#66).
    # (label, [strength], halvable, form, is_current), product-list order.
    cand: List[Tuple[str, List[float], bool, str, bool]] = []
    for ln in lines:
        for s_ in ln["strengths"]:
            if any(abs(c[1][0] - s_) < _EPS and unit_noun(c[3]) == unit_noun(ln["form"]) for c in cand):
                continue   # same strength + same kind of unit on another line: keep the first
            is_cur = bool(on_tablets and current_strength is not None
                          and abs(s_ - current_strength) < _EPS)
            label = f"{s_:g} mg {ln['form']}" + (" (current)" if is_cur else "")
            cand.append((label, [s_], ln["halvable"], ln["form"], is_cur))
    prefer_cur = (rules.get("strength_choice") or {}).get("prefer_current_strength_on_tie", True)

    out: List[Dict[str, Any]] = []
    n = len(targets)
    unlisted = (on_tablets and current_strength is not None and lines
                and not any(abs(x - current_strength) < _EPS for ln in lines for x in ln["strengths"]))
    prev_tab = current_total_mg_per_day   # each step must go above the one before
    # Strengths that need no change: the child's own, plus the one the previous step used.
    own_strength = [(c[1][0], unit_noun(c[3])) for c in cand if c[4]]
    keep_strength = list(own_strength)
    for idx, tgt in enumerate(targets, start=1):
        final = idx == n
        row: Dict[str, Any] = {"step": idx, "target_mg_per_day": tgt, "final": final,
                               "tablet": None, "tablet_text": None, "liquid_text": None,
                               "family_tablet_text": None, "family_liquid_text": None,
                               "liquid_ml_per_dose": None, "liquid_mg_per_ml": None,
                               "liquid_total_mg_per_day": None,
                               "decided_mg_per_day": None, "unit_noun": None,
                               "strength_mg": None,
                               "age_rule_form": age_picks_liquid,
                               "notes": []}
        if not freq:
            row["notes"].append("Doses per day not reported - tablet/liquid split not computed.")
            out.append(row)
            continue
        tgt_ok = _num(tgt)
        if tgt_ok is None:
            row["notes"].append("Ladder dose is not a valid number - not converted.")
            out.append(row)
            continue
        tgt = tgt_ok
        if absolute_max is not None and tgt > absolute_max + _EPS:
            row["notes"].append(f"Ladder dose is above the absolute maximum ({absolute_max:g} "
                                "mg/day) - not converted; clinician to set the dose.")
            out.append(row)
            continue
        if prev_tab is not None and tgt <= prev_tab + _EPS:
            row["notes"].append(f"Ladder dose is not above the dose before it ({prev_tab:g} "
                                "mg/day) - not converted; clinician to check.")
            out.append(row)
            continue
        if show_tab and cand:
            best = None
            nxt = None if final else _num(targets[idx])   # next step's target
            for ci, (label, strengths, halvable, form, is_cur) in enumerate(cand):
                plan = None
                if split == "uneven" and freq > 1:
                    plan = best_tablet_split(tgt, freq, strengths, half_ok and halvable and not final,
                                             rules, absolute_max, must_exceed=prev_tab,
                                             must_be_below=nxt, require_uneven=True)
                if plan is None:
                    plan = best_tablet_split(tgt, freq, strengths, half_ok and halvable and not final,
                                             rules, absolute_max, must_exceed=prev_tab,
                                             must_be_below=nxt)
                if plan is None:
                    continue
                # Closest dose first (ties go up); on a tie stay on the strength the
                # child is on (or the one the previous step moved to) - change tablet
                # size only when the dose requires it (Dr. Barbour 2026-10-25); then
                # fewest half tablets, then fewest tablets.
                same = bool(prefer_cur and any(abs(strengths[0] - ks) < _EPS and unit_noun(form) == kn
                                               for ks, kn in keep_strength))
                key = (round(abs(plan["total_mg_per_day"] - tgt), 6),
                       0 if plan["deviation_pct"] >= 0 else 1,
                       0 if same else 1,
                       sum(_halves(d["recipe"]) for d in plan["doses"]),
                       sum(_units(d["recipe"]) for d in plan["doses"]), ci)
                if best is None or key < best[0]:
                    best = (key, label, plan, form, strengths[0])
            if best:
                _k, label, plan, form, strength = best
                keep_strength = own_strength + [(strength, unit_noun(form))]
                noun = unit_noun(form)
                row["tablet"] = plan
                row["unit_noun"] = noun
                row["strength_mg"] = strength
                row["decided_mg_per_day"] = plan["total_mg_per_day"]
                prev_tab = plan["total_mg_per_day"]
                row["family_tablet_text"] = family_tablet_text(plan, freq, noun)
                row["tablet_text"] = f"{label}: {describe_tablets(plan, freq, weight_kg, effective_max)}"
            else:
                row["notes"].append("No tablet fits the acceptable range.")
        # No tablet fits -> still show the liquid so the clinician has an option.
        if conc and (show_liq or (show_tab and row["tablet"] is None)):
            liq = round_liquid(tgt, freq, conc, rules, absolute_max, must_exceed=prev_tab)
            if liq is None:
                row["notes"].append("No measurable liquid dose fits the acceptable range.")
            else:
                ml, tot = liq
                if row["tablet"] is None:
                    prev_tab = tot
                    row["decided_mg_per_day"] = tot
                how = {1: "once a day", 2: "twice a day"}.get(freq, f"{freq} times a day")
                w_ = _num(weight_kg)
                kg_ = f" ({round(tot / w_, 1):g} mg/kg/day)" if w_ else ""
                row["liquid_ml_per_dose"] = ml
                row["liquid_mg_per_ml"] = conc
                row["liquid_total_mg_per_day"] = tot
                row["family_liquid_text"] = family_liquid_text(tot, ml, freq)
                row["liquid_text"] = (f"{ml:g} mL {how} ({conc:g} mg/mL) = {tot:g} mg/day{kg_}"
                                      + _over_eff(tot, effective_max))
        out.append(row)

    # Flag #3: a climb step with no tablet fit -> offer doing both increases at once.
    if (rules.get("combine_steps") or {}).get("offer_when_step_has_no_tablet_fit") and n > 1:
        for row in out[:-1]:
            if show_tab and row["tablet"] is None and out[-1]["tablet"] is not None and freq:
                row["notes"].append(f"Option: skip to step {n} "
                                    f"({out[-1]['decided_mg_per_day']:g} mg/day) in one move.")
    if form_notes and out:
        out[0]["notes"] = form_notes + out[0]["notes"]
    if unlisted and out:
        out[0]["notes"].append(f"Reported tablet strength {current_strength:g} mg is not a listed "
                               "product strength - verify with the family.")
    if current_formulation and (_is_er(current_formulation) or _is_dr(current_formulation)) and out:
        out[0]["notes"].append("Current product is extended/delayed-release - never split it; "
                               "if a partial unit is needed, switch to immediate-release, "
                               "twice a day.")
    return out
