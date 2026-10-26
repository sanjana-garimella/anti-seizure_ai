"""
med_selection.py
----------------
Rule-based MEDICATION-SELECTION engine for the switch / add-a-second-medication
flow: given a patient's characteristics, it returns the top-ranked medications to
*switch to* or *add* (which drug), as a DRAFT for neurologist review.

NON-NEGOTIABLE POSTURE (see NON_NEGOTIABLE_RULES.md):
  * RULE-BASED ONLY. No ML/LLM anywhere in this path. Every avoid/favor signal
    traces to a specific, inspectable rule in
    `data/medication_selection_rules.json`, which itself encodes Dr. Barbour's
    workbook `kristen/Switching to a different medication.xlsx`.
  * This module NEVER sets a dose. It ranks preference only; titration numbers
    still come from `asm_rules.py` / `data/asm_rules.json`.
  * Output is a DRAFT: `requires_physician_review` is always True.
  * DEFER ON AMBIGUITY: if fewer than three medications remain eligible, the
    remaining slot(s) are left blank and flagged for the physician rather than
    guessed.
  * All clinical values are DRAFT / verified:false until PI sign-off
    (NEEDS_PI_SIGNOFF #41).

This module is deliberately SEPARATE from `engine_core.decision` (the titration
decision path). `decision.decide()` still chooses the *category*
(switch_medication / add_medication / ...); this module only fills in *which*
medication once that category has been chosen, so it cannot perturb the gold
titration validation set.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

try:
    from .version import ENGINE_VERSION
except Exception:  # pragma: no cover - allow standalone import
    ENGINE_VERSION = "unknown"


# ---------------------------------------------------------------------------
# Patient-attribute normalization
# ---------------------------------------------------------------------------

# Map the workbook's "Simulated Data" column headers to canonical attribute keys.
_HEADER_TO_ATTR = {
    "age": "age",
    "sex": "sex",
    "presence of staring seizures": "staring_seizures",
    "presence of whole body single jerk seizures": "whole_body_single_jerk",
    "presence of medication non-adherence": "med_nonadherence",
    "southeastern asian or han chinese": "se_asian_han_chinese",
    "no labs": "prefers_no_labs",
    "mood difficulties": "mood_difficulties",
    "behavioral difficulties": "behavioral_difficulties",
    "trouble sleeping": "trouble_sleeping",
    "migraines": "migraines",
    "kidney disease": "kidney_disease",
    "kidney stones": "kidney_stones",
    "liver disease": "liver_disease",
    "diabetes": "diabetes",
    "obesity": "obesity",
    "heart issue": "heart_issue",
    "bleeding disorder": "bleeding_disorder",
    "sulfa drug allergy": "sulfa_allergy",
    "prior chemotherapy": "prior_chemo",
}

# "Previously tried X" columns -> canonical study-drug id added to prior_tried.
_PRIOR_HEADER_TO_ID = {
    "previously tried keppra": "levetiracetam",
    "previously tried briviact": "brivaracetam",
    "previously tried clobazam": "clobazam",
    "previously tried depakote": "valproic_acid",
    "previously tried lacosamide": "lacosamide",
    "previously tried oxcarbazepine": "oxcarbazepine",
    "previously tried zonisamide": "zonisamide",
    "previously tried lamotrigine": "lamotrigine",
    "previously tried topiramate": "topiramate",
}

_BOOL_KEYS = {
    "staring_seizures", "whole_body_single_jerk", "med_nonadherence",
    "se_asian_han_chinese", "prefers_no_labs", "mood_difficulties",
    "behavioral_difficulties", "trouble_sleeping", "migraines",
    "kidney_disease", "kidney_stones", "liver_disease", "diabetes", "obesity",
    "heart_issue", "bleeding_disorder", "sulfa_allergy", "prior_chemo",
}


def _as_bool(v: Any) -> bool:
    """Yes/No, True/False, 1/0 -> bool. Anything unrecognized -> False (a missing
    answer must not silently trigger an avoid/favor rule)."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("yes", "y", "true", "1")


def normalize_patient(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either canonical keys or the workbook's column headers (case-
    insensitive) and return a canonical attribute dict.

    Output keys: age (int|None), sex ('male'|'female'|None), the boolean flags in
    _BOOL_KEYS, and prior_tried (a set of study-drug ids).
    """
    patient: Dict[str, Any] = {"prior_tried": set()}
    for k in _BOOL_KEYS:
        patient[k] = False
    patient["age"] = None
    patient["sex"] = None

    for key, val in (raw or {}).items():
        lk = str(key).strip().lower()
        if lk in _PRIOR_HEADER_TO_ID:
            if _as_bool(val):
                patient["prior_tried"].add(_PRIOR_HEADER_TO_ID[lk])
            continue
        attr = _HEADER_TO_ATTR.get(lk, lk)  # already-canonical keys pass through
        if attr == "age":
            try:
                patient["age"] = int(val) if val is not None and str(val) != "" else None
            except (TypeError, ValueError):
                patient["age"] = None
        elif attr == "sex":
            patient["sex"] = str(val).strip().lower() if val is not None else None
        elif attr == "prior_tried":
            # allow passing an explicit iterable of ids
            if val:
                patient["prior_tried"].update(str(x).strip().lower() for x in val)
        elif attr in _BOOL_KEYS:
            patient[attr] = _as_bool(val)
        # unknown keys are ignored (defensive: never invent an attribute)
    return patient


# ---------------------------------------------------------------------------
# Ruleset model + loader
# ---------------------------------------------------------------------------

@dataclass
class Medication:
    id: str
    display_name: str
    base_rank: int
    quick_summary: str = ""
    long_summary: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    source: str = ""
    verified: bool = False


@dataclass
class SelectionRuleset:
    medications: List[Medication]
    rules: List[Dict[str, Any]]
    provenance: Dict[str, Any] = field(default_factory=dict)

    @property
    def med_by_id(self) -> Dict[str, Medication]:
        return {m.id: m for m in self.medications}

    @property
    def med_ids(self) -> Set[str]:
        return {m.id for m in self.medications}


def _default_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, os.pardir, "data", "medication_selection_rules.json")


def load_selection_ruleset(path: Optional[str] = None) -> SelectionRuleset:
    """Load the DRAFT selection ruleset. Missing/corrupt file -> empty ruleset
    (the caller then simply has nothing to recommend and must defer), never a
    fabricated default."""
    path = path or _default_path()
    if not os.path.isfile(path):
        return SelectionRuleset([], [], {})
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return SelectionRuleset([], [], {})
    meds = [
        Medication(
            id=m["id"], display_name=m.get("display_name", m["id"]),
            base_rank=int(m["base_rank"]),
            quick_summary=m.get("quick_summary", ""),
            long_summary=m.get("long_summary", ""),
            attributes=dict(m.get("attributes", {})),
            source=m.get("source", ""), verified=bool(m.get("verified", False)),
        )
        for m in raw.get("medications", [])
    ]
    meds.sort(key=lambda m: m.base_rank)
    rules = list(raw.get("rules", []))
    return SelectionRuleset(meds, rules, dict(raw.get("_provenance", {})))


# ---------------------------------------------------------------------------
# Predicate evaluation (inspectable, no clinical values here)
# ---------------------------------------------------------------------------

def _matches(pred: Optional[Dict[str, Any]], patient: Dict[str, Any]) -> bool:
    if not pred:
        return False
    if "flag" in pred:
        return bool(patient.get(pred["flag"]))
    if "eq" in pred:
        a = pred["eq"]
        pv = patient.get(a["attr"])
        return pv is not None and str(pv).lower() == str(a["value"]).lower()
    if "gt" in pred:
        a = pred["gt"]
        pv = patient.get(a["attr"])
        return pv is not None and pv > a["value"]
    if "lt" in pred:
        a = pred["lt"]
        pv = patient.get(a["attr"])
        return pv is not None and pv < a["value"]
    if "no_prior_asm" in pred:
        has_prior = bool(patient.get("prior_tried"))
        return (not has_prior) if pred["no_prior_asm"] else has_prior
    if "not" in pred:
        return not _matches(pred["not"], patient)
    if "all" in pred:
        return all(_matches(p, patient) for p in pred["all"])
    if "any" in pred:
        return any(_matches(p, patient) for p in pred["any"])
    return False


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass
class MedSelection:
    """Top-3 medication options as a DRAFT for physician review."""
    options: List[Dict[str, Any]]                       # up to 3, ranked
    ranked_eligible: List[str]                          # all eligible ids, ranked
    eligible: List[str]
    avoided: Dict[str, List[Dict[str, Any]]]            # id -> [{rule, source, reason}]
    favored: Dict[str, Dict[str, Any]]                  # id -> {count, rules:[...]}
    rationale: List[Dict[str, Any]]
    deferred: bool
    defer_reason: Optional[str]
    requires_physician_review: bool = True
    draft: bool = True
    sources: Dict[str, Any] = field(default_factory=dict)
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

def select(patient_raw: Dict[str, Any],
           ruleset: Optional[SelectionRuleset] = None,
           top_n: int = 3) -> MedSelection:
    """Rank the study medications for this patient and return the top `top_n`
    as a DRAFT. `patient_raw` may use canonical keys or the workbook headers."""
    ruleset = ruleset or load_selection_ruleset()
    patient = normalize_patient(patient_raw)

    med_ids = ruleset.med_ids
    avoided: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    favored_rules: Dict[str, List[str]] = defaultdict(list)
    rationale: List[Dict[str, Any]] = []

    for rule in ruleset.rules:
        if not rule.get("active", True):
            continue
        rid = rule.get("id", "?")
        source = rule.get("source") or rule.get("source_cell", "")
        # Special expansion: avoid every medication the patient has already tried.
        if rule.get("avoid_prior_tried"):
            fired_ids = []
            for mid in sorted(patient.get("prior_tried") or []):
                if mid in med_ids:
                    avoided[mid].append({
                        "rule": rid, "source": source,
                        "reason": "already taking or previously tried this medication"})
                    fired_ids.append(mid)
            if fired_ids:
                rationale.append({
                    "rule": rid, "effect": "avoid", "medications": fired_ids,
                    "description": rule.get("source_text", ""),
                    "question": rule.get("question", ""), "source": source,
                    "verified": bool(rule.get("verified", False))})
            continue

        if not _matches(rule.get("when"), patient):
            continue
        avo = [m for m in rule.get("avoid", []) if m in med_ids]
        fav = [m for m in rule.get("favor", []) if m in med_ids]
        for mid in avo:
            avoided[mid].append({"rule": rid, "source": source,
                                 "reason": rule.get("source_text", "")})
        for mid in fav:
            favored_rules[mid].append(rid)
        if avo or fav:
            rationale.append({
                "rule": rid, "effect": ("avoid+favor" if avo and fav
                                        else "avoid" if avo else "favor"),
                "avoid": avo, "favor": fav,
                "description": rule.get("source_text", ""),
                "question": rule.get("question", ""), "source": source,
                "verified": bool(rule.get("verified", False))})

    avoided_set = set(avoided.keys())
    # Rank the survivors: favor-count desc, then base_rank asc (Sheet-1 preference).
    eligible_meds = [m for m in ruleset.medications if m.id not in avoided_set]
    favor_count = {m.id: len(favored_rules.get(m.id, [])) for m in eligible_meds}
    ranked = sorted(eligible_meds, key=lambda m: (-favor_count[m.id], m.base_rank))

    options: List[Dict[str, Any]] = []
    for i, m in enumerate(ranked[:top_n], start=1):
        options.append({
            "rank": i, "id": m.id, "display_name": m.display_name,
            "base_rank": m.base_rank, "favor_count": favor_count[m.id],
            "favor_rules": list(favored_rules.get(m.id, [])),
            "attributes": dict(m.attributes),
            "quick_summary": m.quick_summary,
            "verified": m.verified,
        })

    deferred = len(ranked) < top_n
    defer_reason = None
    if deferred:
        defer_reason = (
            f"Only {len(ranked)} medication(s) remain eligible after applying the "
            f"avoid rules ({len(avoided_set)} of {len(med_ids)} excluded); "
            f"{top_n - len(ranked)} slot(s) left blank for the physician to decide "
            "(defer-on-ambiguity).")

    favored = {mid: {"count": len(rids), "rules": rids}
               for mid, rids in favored_rules.items()}

    sources = {
        "engine": "med_selection (rule-based, no ML/LLM)",
        "engine_version": ENGINE_VERSION,
        "ruleset_source": ruleset.provenance.get(
            "source_workbook", "medication_selection_rules.json"),
        "status": ruleset.provenance.get("status", ""),
        "verified": bool(ruleset.provenance.get("verified", False)),
        "note": ("Medication-selection preference ranking from her workbook. "
                 "The neurologist reviews before anything reaches the family."
                 if ruleset.provenance.get("verified") else
                 "DRAFT medication-selection preference ranking - unverified, "
                 "pending PI sign-off (NEEDS_PI_SIGNOFF #41). Neurologist reviews "
                 "before anything reaches the family."),
        "rules_fired": [r["rule"] for r in rationale],
    }

    return MedSelection(
        options=options,
        ranked_eligible=[m.id for m in ranked],
        eligible=[m.id for m in eligible_meds],
        avoided={k: v for k, v in avoided.items()},
        favored=favored,
        rationale=rationale,
        deferred=deferred,
        defer_reason=defer_reason,
        sources=sources,
    )
