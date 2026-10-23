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
                      must_be_below: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Closest dose plan for `target` mg/day over `freq` doses/day, inside the
    -10/+30% window, never above the absolute max. Ranking: closest total first
    (ties go UP), then whole tablets over halves, then even over uneven, then
    fewest units. `must_exceed`: an increase step must land strictly above the
    previous dose (never round an increase back to the dose already taken);
    `must_be_below`: a climb step must stay under the NEXT step's target (never
    round a step past the one after it).
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
    """Compact family-giving line: 'morning 1 × 250 mg, night 2 × 250 mg = 750 mg/day
    (62.5 mg/kg/day, +25%)'."""
    doses = plan["doses"]
    names = _slot_names(freq)
    if len({d["mg"] for d in doses}) == 1:
        how = names[0] if freq == 1 else ("twice a day" if freq == 2 else f"{freq} times a day")
        body = f"{_fmt_recipe(doses[0]['recipe'])} {how}"
    else:
        body = ", ".join(f"{n} {_fmt_recipe(d['recipe'])}" for n, d in zip(names, doses))
    dev = plan["deviation_pct"]
    tot = plan["total_mg_per_day"]
    w = _num(weight_kg)
    bits = ([f"{round(tot / w, 1):g} mg/kg/day"] if w else []) + ([f"{dev:+.1f}%"] if abs(dev) >= 0.05 else [])
    extra = f" ({', '.join(bits)})" if bits else ""
    return f"{body} = {tot:g} mg/day{extra}" + _over_eff(tot, effective_max)


def _tablet_count(c: float) -> str:
    whole = int(c)
    if c == 0.5:
        return "½ tablet"
    n = f"{whole}½" if c % 1 else f"{whole}"
    return f"{n} tablet" if c == 1 else f"{n} tablets"


def family_tablet_text(plan: Dict[str, Any], freq: int) -> str:
    """Plain tablet counts for the family message: '2 tablets in the morning and
    3 tablets at night (250 mg each)'. Same plan the clinician summary shows."""
    doses = plan["doses"]
    strengths = sorted({s for d in doses for s, _c in d["recipe"]}, reverse=True)

    def _cnt(d):
        merged: Dict[float, float] = {}
        for s_, c in d["recipe"]:
            merged[s_] = merged.get(s_, 0) + c
        if len(merged) == 1:
            return _tablet_count(next(iter(merged.values())))
        return " + ".join(f"{_tablet_count(c)} of {s_:g} mg" for s_, c in sorted(merged.items(), reverse=True))

    names = _slot_names(freq)
    if len({d["mg"] for d in doses}) == 1:
        how = "once a day" if freq == 1 else ("twice a day" if freq == 2 else f"{freq} times a day")
        body = f"{_cnt(doses[0])} {how}"
    else:
        parts = [f"{_cnt(d)} in the {n}" if n != "night" else f"{_cnt(d)} at night"
                 for n, d in zip(names, doses)]
        body = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    each = f" ({strengths[0]:g} mg each)" if len(strengths) == 1 else ""
    return body + each


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
                 age_years: Optional[float] = None) -> List[Dict[str, Any]]:
    """For each ladder target (mg/day, in step order; the last is where the child
    stays) return {target, tablet, tablet_text, liquid_text, notes}. Display only."""
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

    # Age rule: older than the cutoff -> tablets, otherwise liquid (overrides the
    # form the child is on). Falls back to the other form if no product exists.
    afr = rules.get("age_form_rule") or {}
    age = _num(age_years)
    if afr.get("enabled") and age is not None and afr.get("overrides_current_form", True):
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
        if on_tablets != want_tab and (on_tablets or on_liquid) and show_tab == want_tab:
            form_notes.append(f"Age {age:g} y: showing "
                              + ("tablets" if want_tab else "liquid")
                              + f" per the age rule (family reports {'tablets' if on_tablets else 'liquid'}).")

    ov = rules.get("half_tablet_overrides") or {}
    ov_mg = [float(x) for x in (ov.get(drug_key) or []) if _num(x)]

    def _half(halvable: bool, strength: float) -> bool:
        """Halvable by product type, or explicitly cleared by the PI (half_tablet_overrides)."""
        return bool(halvable) or any(abs(strength - m) < _EPS for m in ov_mg)

    # Product lines to try. One tablet strength per plan when single_strength_per_plan
    # is on: the child's own strength first, then each other single strength.
    single = (rules.get("single_strength_per_plan") or {}).get("enabled", False)
    cand: List[Tuple[str, List[float], bool]] = []
    if single:
        if on_tablets and current_strength and (rules.get("form_continuity") or {}).get("prefer_current_strength", True):
            home = next((ln for ln in lines if any(abs(s_ - current_strength) < _EPS for s_ in ln["strengths"])), None)
            if home:
                cand.append((f"{current_strength:g} mg tablets (current)",
                             [float(current_strength)],
                             _half(home["halvable"], float(current_strength))))
        for ln in lines:
            for s_ in ln["strengths"]:
                if any(len(c[1]) == 1 and abs(c[1][0] - s_) < _EPS and ln["form"] in c[0] for c in cand):
                    continue
                cand.append((f"{s_:g} mg {ln['form']}", [s_], _half(ln["halvable"], s_)))
        # Standard strength for this drug: the only alternative to the child's own
        # strength, so every plan for this medicine names the same tablet.
        std = _num((rules.get("standard_tablet_strength") or {}).get(drug_key))
        if std is not None and any(abs(s_ - std) < _EPS for ln in lines for s_ in ln["strengths"]):
            cur_c = [c for c in cand if c[0].endswith("(current)")]
            std_c = next(c for c in cand if not c[0].endswith("(current)")
                         and abs(c[1][0] - std) < _EPS)
            cand = cur_c + ([] if any(abs(c[1][0] - std) < _EPS for c in cur_c) else [std_c])
    else:
        if on_tablets and current_strength and (rules.get("form_continuity") or {}).get("prefer_current_strength", True):
            home = next((ln for ln in lines if any(abs(s_ - current_strength) < _EPS for s_ in ln["strengths"])), None)
            cand.append((f"{current_strength:g} mg tablets (current)", [float(current_strength)],
                         bool(home and home["halvable"])))
            if home:
                cand.append((home["form"], home["strengths"], home["halvable"]))
        cand += [(ln["form"], ln["strengths"], ln["halvable"]) for ln in lines
                 if not any(c[1] == ln["strengths"] and c[0] == ln["form"] for c in cand)]

    # One bottle for the whole climb: pick the single strength that fits the most steps
    # (then closest totals, fewest halves, fewest tablets). The child's own strength wins
    # if it fits as many steps as the best one.
    if single and cand and show_tab and freq and len(cand) > 1:
        def _sim(c):
            label, strengths, halvable = c
            prev, nofit, dev, halves, units = current_total_mg_per_day, 0, 0.0, 0, 0.0
            for k, t in enumerate(targets):
                t = _num(t)
                if t is None or (absolute_max is not None and t > absolute_max + _EPS) \
                        or (prev is not None and t <= prev + _EPS):
                    continue
                last = k == len(targets) - 1
                nxt = None if last else _num(targets[k + 1])
                pl = best_tablet_split(t, freq, strengths, half_ok and halvable and not last,
                                       rules, absolute_max, must_exceed=prev, must_be_below=nxt)
                if pl is None:
                    nofit += 1
                    prev = t
                    continue
                prev = pl["total_mg_per_day"]
                dev += abs(pl["deviation_pct"])
                halves += sum(_halves(d["recipe"]) for d in pl["doses"])
                units += sum(_units(d["recipe"]) for d in pl["doses"])
            return (nofit, round(dev, 3), halves, units)
        scored = [(_sim(c), i, c) for i, c in enumerate(cand)]
        best_s = min(scored, key=lambda x: (x[0], x[1]))
        pick = best_s
        if on_tablets:
            cur = next((x for x in scored if x[2][0].endswith("(current)")), None)
            if cur and cur[0][0] == best_s[0][0]:
                pick = cur
        cand = [pick[2]]

    out: List[Dict[str, Any]] = []
    n = len(targets)
    unlisted = (on_tablets and current_strength is not None and lines
                and not any(abs(x - current_strength) < _EPS for ln in lines for x in ln["strengths"]))
    prev_tab = current_total_mg_per_day   # each step must go above the one before
    for idx, tgt in enumerate(targets, start=1):
        final = idx == n
        row: Dict[str, Any] = {"step": idx, "target_mg_per_day": tgt, "final": final,
                               "tablet": None, "tablet_text": None, "liquid_text": None,
                               "family_tablet_text": None, "liquid_ml_per_dose": None,
                               "liquid_mg_per_ml": None,
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
            for label, strengths, halvable in cand:
                nxt = None if final else _num(targets[idx])   # next step's target
                plan = best_tablet_split(tgt, freq, strengths, half_ok and halvable and not final,
                                         rules, absolute_max, must_exceed=prev_tab,
                                         must_be_below=nxt)
                if plan is None:
                    continue
                if on_tablets and (not single or label.endswith("(current)")):
                    best = (label, plan)      # first workable line in preference order
                    break
                key = (abs(plan["deviation_pct"]), 0 if plan["deviation_pct"] >= 0 else 1,
                       sum(_halves(d["recipe"]) for d in plan["doses"]),
                       sum(_units(d["recipe"]) for d in plan["doses"]))
                if best is None or len(best) < 3 or key < best[2]:
                    best = (label, plan, key)
            if best:
                row["tablet"] = best[1]
                prev_tab = best[1]["total_mg_per_day"]
                row["family_tablet_text"] = family_tablet_text(best[1], freq)
                row["tablet_text"] = (f"{best[0]}: "
                                      f"{describe_tablets(best[1], freq, weight_kg, effective_max)}")
            else:
                row["notes"].append("No tablet fits the acceptable range.")
        # No tablet fits -> still show the liquid so the clinician has an option.
        if conc and (show_liq or (show_tab and row["tablet"] is None)):
            if row["tablet"] is None:
                prev_tab = tgt
            per_ml = tgt / freq / conc            # tgt already checked <= absolute max
            how = {1: "once a day", 2: "twice a day"}.get(freq, f"{freq} times a day")
            w_ = _num(weight_kg)
            kg_ = f" ({round(tgt / w_, 1):g} mg/kg/day)" if w_ else ""
            row["liquid_ml_per_dose"] = round(per_ml, 2)
            row["liquid_mg_per_ml"] = conc
            row["liquid_text"] = (f"{per_ml:.2f} mL {how} ({conc:g} mg/mL) = {tgt:g} mg/day{kg_}"
                                  + _over_eff(tgt, effective_max))
        out.append(row)

    # Flag #3: a climb step with no tablet fit -> offer doing both increases at once.
    if (rules.get("combine_steps") or {}).get("offer_when_step_has_no_tablet_fit") and n > 1:
        for row in out[:-1]:
            if show_tab and row["tablet"] is None and out[-1]["tablet"] is not None and freq:
                row["notes"].append(f"Option: skip to step {n} "
                                    f"({out[-1]['target_mg_per_day']:g} mg/day) in one move.")
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
