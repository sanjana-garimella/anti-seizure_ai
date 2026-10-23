"""
asm_rules.py
------------
Data STRUCTURE for the per-medication ASM rules the engine reasons over
(spec §5 ASMRule, §9).

HARD RULE (spec §11.1): this module ships EMPTY. It defines the shape of an
ASM rule and a loader, but contains NO dose ranges, uptitration rates,
interaction logic, or other clinical values. Those are clinician-supplied via
`data/asm_rules.json` (see `data/asm_rules.template.json`) and confirmed in
NEEDS_PI_SIGNOFF.md before any clinical use.

If a rule (or a needed field) is absent, the engine degrades gracefully:
it can still output a recommendation *category* from survey-derived states,
but it will NOT fabricate a titration schedule or dosing numbers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ---------------------------------------------------------------------------
# Concomitant-med REGIMEN selection for conmed_dependent_titration drugs
# (lamotrigine). This is ROUTING logic, not a clinical value: the regimen and
# the enzyme-inducer drug names are read from the clinician-authored band
# LABELS, so no dose or drug fact is hardcoded here. See clinical-safety.md.
# ---------------------------------------------------------------------------
_VALPROATE_SYNONYMS = ("valproate", "valproic", "divalproex", "depakote",
                       "depakene", "valproic_acid", "vpa")


def _band_regimen(label: str) -> str:
    """Classify a band label into a conmed regimen tag from its own wording:
    'valproate', 'inducer', or 'none'."""
    l = (label or "").lower()
    if "with valproate" in l:
        return "valproate"
    if "with enzyme inducer" in l or "with an enzyme inducer" in l:
        return "inducer"
    return "none"


def _inducer_names_from_bands(bands: List["DoseBand"]) -> List[str]:
    """Enzyme-inducer drug names from the inducer band's label parenthetical
    (e.g. '(carbamazepine/phenytoin/phenobarbital/primidone)')."""
    names: List[str] = []
    for b in bands:
        if _band_regimen(b.label) == "inducer":
            m = re.search(r"\(([^)]*)\)", b.label or "")
            if m:
                names += [t.strip().lower() for t in re.split(r"[\/,]", m.group(1))
                          if t.strip()]
    return names


def _patient_regimen(concomitant_asms: Optional[List[str]],
                     inducer_names: List[str]) -> str:
    """Patient's regimen from concomitant ASMs. Valproate takes precedence (the
    slowest, most cautious lamotrigine ladder; highest rash risk)."""
    meds = [str(m).lower() for m in (concomitant_asms or [])]
    if any(syn in m for m in meds for syn in _VALPROATE_SYNONYMS):
        return "valproate"
    if any(name in m for m in meds for name in inducer_names):
        return "inducer"
    return "none"


# Safest-first fallback order when the regimen is unknown/unrepresented: the
# valproate ladder is the slowest (lowest SJS/TEN risk), so it is the safe
# default for a boxed-warning drug when concomitant meds are not known.
_SAFEST_REGIMEN_ORDER = ("valproate", "none", "inducer")


@dataclass
class DoseBand:
    """One age/weight band's dosing parameters - ALL clinician-supplied.

    Two dosing styles are supported (most pediatric ASM dosing is weight-based):
      * fixed_mg                       - absolute mg/day fields below;
      * weight_based_mg_per_kg_per_day - mg/kg/day fields (× patient weight).
    `dose_basis` selects which. Every field defaults to None so an unpopulated
    band fabricates nothing (spec §11.1).
    """
    label: str = ""
    min_age_years: Optional[float] = None
    max_age_years: Optional[float] = None
    min_weight_kg: Optional[float] = None
    max_weight_kg: Optional[float] = None
    dose_basis: str = "fixed_mg"                        # or weight_based_mg_per_kg_per_day
    # --- fixed-mg fields ---
    dose_range_min_mg_per_day: Optional[float] = None   # clinician-supplied
    dose_range_max_mg_per_day: Optional[float] = None   # clinician-supplied
    max_daily_mg: Optional[float] = None                # absolute ceiling
    standard_uptitration_step_mg: Optional[float] = None
    standard_uptitration_interval_weeks: Optional[int] = None
    # --- weight-based (mg/kg/day) fields (Barbour review, all clinician-supplied) ---
    start_mg_per_kg_per_day: Optional[float] = None
    target_mg_per_kg_per_day_min: Optional[float] = None
    target_mg_per_kg_per_day_max: Optional[float] = None
    max_mg_per_kg_per_day: Optional[float] = None
    uptitration_step_mg_per_kg_per_day: Optional[float] = None
    uptitration_interval_weeks: Optional[int] = None
    absolute_max_mg_per_day: Optional[float] = None     # ceiling regardless of weight
    frequency_per_day: Optional[int] = None
    # --- pace overrides (Kristen T4 "increase faster", T16 slower) ---
    faster_uptitration_interval_days: Optional[int] = None    # e.g. 3 (vs weekly)
    slower_uptitration_interval_weeks: Optional[int] = None
    # --- provenance ---
    source: Optional[str] = None
    verified: bool = False
    clinician_note: str = ""

    def applies_to(self, age_years: Optional[float], weight_kg: Optional[float]) -> bool:
        """True if the patient falls in this band. Honors an upper bound even
        when the lower bound is unset (fixes the band-selection bug where an
        "<= 30 kg" band matched everyone). Unknown patient value -> not excluded."""
        def _in(v, lo, hi):
            if v is None:
                return True
            if lo is not None and v < lo:
                return False
            if hi is not None and v > hi:
                return False
            return True
        return (_in(age_years, self.min_age_years, self.max_age_years)
                and _in(weight_kg, self.min_weight_kg, self.max_weight_kg))

    def ceiling_mg_per_day(self, weight_kg: Optional[float]) -> Optional[float]:
        """The maximum total mg/day for this band+patient, from clinician values
        only. Weight-based: min(max_mg_per_kg_per_day × weight, absolute_max).
        Fixed: min(dose_range_max, max_daily_mg). None when not computable."""
        candidates: List[float] = []
        if self.dose_basis == "weight_based_mg_per_kg_per_day":
            kg_max = self.max_mg_per_kg_per_day or self.target_mg_per_kg_per_day_max
            if kg_max is not None and weight_kg is not None:
                candidates.append(kg_max * weight_kg)
            if self.absolute_max_mg_per_day is not None:
                candidates.append(self.absolute_max_mg_per_day)
        else:
            if self.dose_range_max_mg_per_day is not None:
                candidates.append(self.dose_range_max_mg_per_day)
            if self.max_daily_mg is not None:
                candidates.append(self.max_daily_mg)
            if self.absolute_max_mg_per_day is not None:
                candidates.append(self.absolute_max_mg_per_day)
        return min(candidates) if candidates else None

    def uptitration_step_mg(self, weight_kg: Optional[float]) -> Optional[float]:
        """Next-step size in mg/day from clinician values (weight-based × weight,
        or the fixed step). None when not computable."""
        if self.dose_basis == "weight_based_mg_per_kg_per_day":
            if self.uptitration_step_mg_per_kg_per_day is not None and weight_kg is not None:
                return self.uptitration_step_mg_per_kg_per_day * weight_kg
            return None
        return self.standard_uptitration_step_mg


@dataclass
class ASMRule:
    """Per-medication clinical rule. Clinical fields default to empty/None.

    The fields after `source_note` were added from Dr. Barbour's 2026-06 review
    (PROJECT_LOG.md themes T6, T11, T12, T14). They are clinician-supplied
    drug FACTS, each tagged with provenance via `verified`:
      * treats_only            - seizure type(s) the drug covers, if narrow
                                 (ethosuximide -> ["absence"]); else empty;
      * avoid_if_seizure_type  - relative contraindication by seizure type
                                 (lamotrigine -> ["myoclonic"]);
      * avoid_if_comorbidity   - behavioral comorbidities to avoid
                                 (levetiracetam -> autism/depression/anxiety/...);
      * no_switch_targets      - do NOT auto-suggest switching TO these
                                 (oxcarbazepine ->[carbamazepine]; brivaracetam ->[levetiracetam]);
      * sjs_risk               - carries a serious-rash (SJS/TEN) risk;
      * conmed_dependent_titration - titration depends on concomitant meds
                                 (lamotrigine); band labels name the regimen.
    """
    medication: str                                   # generic name (identifier)
    in_study: bool = True
    dose_bands: List[DoseBand] = field(default_factory=list)
    formulations: List[str] = field(default_factory=list)
    requires_lab_monitoring: bool = False
    labs: List[str] = field(default_factory=list)
    indications: List[str] = field(default_factory=list)   # seizure types
    interaction_exceptions: List[str] = field(default_factory=list)
    source_note: str = ""
    # --- drug facts from the Barbour review (clinician-supplied) ---
    display_name: str = ""
    treats_only: List[str] = field(default_factory=list)
    avoid_if_seizure_type: List[str] = field(default_factory=list)
    avoid_if_comorbidity: List[str] = field(default_factory=list)
    no_switch_targets: List[str] = field(default_factory=list)
    sjs_risk: bool = False
    conmed_dependent_titration: bool = False
    key_safety: str = ""
    verified: bool = False

    # ---- helpers (no clinical values introduced) ----
    def selected_regimen(self, concomitant_asms: Optional[List[str]]) -> Optional[str]:
        """The conmed regimen used to select a band (None for non-conmed drugs).
        Exposed for the provenance/output layer so the physician sees which
        regimen the engine assumed."""
        if not self.conmed_dependent_titration:
            return None
        return _patient_regimen(concomitant_asms,
                                _inducer_names_from_bands(self.dose_bands))

    def band_for(self, age_years: Optional[float], weight_kg: Optional[float],
                 concomitant_asms: Optional[List[str]] = None) -> Optional[DoseBand]:
        """Select the applicable dose band.

        For most drugs this is the first age/weight match (unchanged). For
        conmed_dependent_titration drugs (lamotrigine, whose 3 bands are
        distinguished by CONCOMITANT MED, not age/weight), we pick the band that
        matches the patient's regimen instead of the first age/weight match -
        the fix for the SJS/TEN risk where a valproate patient would otherwise
        get the faster non-valproate ladder. If concomitant meds are unknown
        (None), we default to the SAFEST (slowest) regimen present rather than
        guessing the faster one. DRAFT - pending PI sign-off (NEEDS_PI_SIGNOFF)."""
        candidates = [b for b in self.dose_bands if b.applies_to(age_years, weight_kg)]
        if not candidates:
            return None
        if not self.conmed_dependent_titration:
            return candidates[0]
        by_regimen: Dict[str, DoseBand] = {}
        for b in candidates:
            by_regimen.setdefault(_band_regimen(b.label), b)
        if concomitant_asms is not None:
            regimen = _patient_regimen(
                concomitant_asms, _inducer_names_from_bands(self.dose_bands))
            if regimen in by_regimen:
                return by_regimen[regimen]
        # unknown conmeds, or regimen not represented -> safest available
        for r in _SAFEST_REGIMEN_ORDER:
            if r in by_regimen:
                return by_regimen[r]
        return candidates[0]

    def has_dosing_data(self, age_years=None, weight_kg=None) -> bool:
        b = self.band_for(age_years, weight_kg)
        if not b:
            return False
        if b.dose_basis == "weight_based_mg_per_kg_per_day":
            return (b.uptitration_step_mg_per_kg_per_day is not None
                    and (b.max_mg_per_kg_per_day is not None
                         or b.target_mg_per_kg_per_day_max is not None
                         or b.absolute_max_mg_per_day is not None))
        return (b.dose_range_max_mg_per_day is not None
                and b.standard_uptitration_step_mg is not None)


# ---------------------------------------------------------------------------
# Loader / registry - empty unless a clinician-supplied JSON file is provided.
# ---------------------------------------------------------------------------

def _band_from_dict(d: Dict[str, Any]) -> DoseBand:
    return DoseBand(
        label=d.get("label", ""),
        min_age_years=d.get("min_age_years"),
        max_age_years=d.get("max_age_years"),
        min_weight_kg=d.get("min_weight_kg"),
        max_weight_kg=d.get("max_weight_kg"),
        dose_basis=d.get("dose_basis", "fixed_mg"),
        dose_range_min_mg_per_day=d.get("dose_range_min_mg_per_day"),
        dose_range_max_mg_per_day=d.get("dose_range_max_mg_per_day"),
        max_daily_mg=d.get("max_daily_mg"),
        standard_uptitration_step_mg=d.get("standard_uptitration_step_mg"),
        standard_uptitration_interval_weeks=d.get("standard_uptitration_interval_weeks"),
        start_mg_per_kg_per_day=d.get("start_mg_per_kg_per_day"),
        target_mg_per_kg_per_day_min=d.get("target_mg_per_kg_per_day_min"),
        target_mg_per_kg_per_day_max=d.get("target_mg_per_kg_per_day_max"),
        max_mg_per_kg_per_day=d.get("max_mg_per_kg_per_day"),
        uptitration_step_mg_per_kg_per_day=d.get("uptitration_step_mg_per_kg_per_day"),
        uptitration_interval_weeks=d.get("uptitration_interval_weeks"),
        absolute_max_mg_per_day=d.get("absolute_max_mg_per_day"),
        frequency_per_day=d.get("frequency_per_day"),
        faster_uptitration_interval_days=d.get("faster_uptitration_interval_days"),
        slower_uptitration_interval_weeks=d.get("slower_uptitration_interval_weeks"),
        source=d.get("source"),
        verified=bool(d.get("verified", False)),
        clinician_note=d.get("clinician_note", ""),
    )


def _rule_from_dict(name: str, d: Dict[str, Any]) -> ASMRule:
    return ASMRule(
        medication=name,
        in_study=d.get("in_study", True),
        dose_bands=[_band_from_dict(b) for b in d.get("dose_bands", [])],
        formulations=list(d.get("formulations", [])),
        requires_lab_monitoring=bool(d.get("requires_lab_monitoring", False)),
        labs=list(d.get("labs", [])),
        indications=list(d.get("indications", [])),
        interaction_exceptions=list(d.get("interaction_exceptions", [])),
        source_note=d.get("source_note", ""),
        display_name=d.get("display_name", ""),
        treats_only=list(d.get("treats_only", [])),
        avoid_if_seizure_type=list(d.get("avoid_if_seizure_type", [])),
        avoid_if_comorbidity=list(d.get("avoid_if_comorbidity", [])),
        no_switch_targets=[s.strip().lower() for s in d.get("no_switch_targets", [])],
        sjs_risk=bool(d.get("sjs_risk", False)),
        conmed_dependent_titration=bool(d.get("conmed_dependent_titration", False)),
        key_safety=d.get("key_safety", ""),
        verified=bool(d.get("verified", False)),
    )


def load_asm_rules(path: Optional[str] = None) -> Dict[str, ASMRule]:
    """
    Load clinician-supplied ASM rules from JSON. Returns {} when no file is
    provided or the file holds no populated rules. Keys are lowercased generic
    names. The file's `_README`/comment keys (starting with "_") are ignored.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # A corrupt/half-written rules file must degrade gracefully (same empty
        # registry as the missing-file case) so the engine still returns a
        # category from survey-derived state - it just won't have dosing facts.
        return {}
    if not isinstance(raw, dict):
        return {}
    rules: Dict[str, ASMRule] = {}
    for name, d in raw.items():
        if name.startswith("_") or not isinstance(d, dict):
            continue
        rules[name.strip().lower()] = _rule_from_dict(name.strip().lower(), d)
    return rules


# Default registry is EMPTY (no clinical values shipped).
ASM_RULES: Dict[str, ASMRule] = {}


def get_rule(name: Optional[str], registry: Optional[Dict[str, ASMRule]] = None) -> Optional[ASMRule]:
    if not name:
        return None
    reg = registry if registry is not None else ASM_RULES
    return reg.get(str(name).strip().lower())
