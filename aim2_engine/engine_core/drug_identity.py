"""
drug_identity.py
----------------
Deterministic drug-name normalizer + same-family checker (per the approved
plan). NO ML, NO network: it uses clinician-supplied lookup tables
(data/drug_synonyms.json, data/drug_classes.json) and Python's stdlib only.

Two jobs:
  * normalize a drug mention -> canonical generic (brand/typo tolerant)
  * decide whether two drugs are in the "same family" by a chosen definition
    (mechanism of action [default], ATC class, or both)

BOUNDARY: this never makes or changes the medication recommendation. The engine
uses it only to ANNOTATE a switch suggestion (e.g. "the proposed switch target
is the same mechanism family as the current drug - confirm intent"). It is
fully optional; the engine runs without it. A fine-tuned NER model or RxNav can
replace the table-based normalizer later without changing this interface.
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DEFAULT_SYN = os.path.join(_DATA, "drug_synonyms.template.json")
_DEFAULT_CLS = os.path.join(_DATA, "drug_classes.template.json")


def _resolve(filled: str, template: str) -> str:
    """Prefer a clinician-filled file (e.g. drug_synonyms.json) over the template."""
    return filled if os.path.isfile(filled) else template


def study_synonym_map(path: Optional[str] = None) -> Dict[str, str]:
    """{name_lower: generic} for all in-study names (generics + brands +
    abbreviations), preferring a clinician-filled drug_synonyms.json over the
    template. Used to recognize a medication entry (brand or generic)."""
    p = path or _resolve(os.path.join(_DATA, "drug_synonyms.json"), _DEFAULT_SYN)
    return load_synonyms(p)


def known_study_medications(path: Optional[str] = None) -> set:
    """The set of in-study GENERIC names (the canonical 11). Empty set if no
    synonyms file is present (then recognition is not asserted)."""
    return set(study_synonym_map(path).values())


@dataclass
class NormalizedDrug:
    raw: str
    canonical: Optional[str]                 # generic name, or None if unresolved
    matched_via: Optional[str] = None        # "exact" | "synonym" | "fuzzy" | None
    confidence: float = 0.0
    candidates: List[str] = field(default_factory=list)


def load_synonyms(path: Optional[str] = None) -> Dict[str, str]:
    """Return {name_lower: generic} for generics + their synonyms."""
    path = path or _DEFAULT_SYN
    table: Dict[str, str] = {}
    if not os.path.isfile(path):
        return table
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # Malformed synonyms file -> same empty table as missing-file: drug
        # recognition simply isn't asserted (never raises).
        return table
    if not isinstance(raw, dict):
        return table
    for generic, syns in raw.get("synonyms", {}).items():
        g = generic.strip().lower()
        table[g] = g
        for s in syns or []:
            table[s.strip().lower()] = g
    return table


def load_classes(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    path = path or _DEFAULT_CLS
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k.strip().lower(): v for k, v in raw.get("classes", {}).items()}


def _atc_level4(code: str) -> str:
    """First 5 chars of an ATC code = anatomical/therapeutic/pharmacological/
    chemical subgroup, e.g. 'N03AF01' -> 'N03AF'."""
    return (code or "").strip().upper()[:5]


class DrugClassifier:
    """Table-backed normalizer + same-family checker."""

    def __init__(self, synonyms_path: Optional[str] = None,
                 classes_path: Optional[str] = None,
                 default_by: str = "mechanism",
                 fuzzy: bool = True, fuzzy_cutoff: float = 0.85):
        self.synonyms = load_synonyms(synonyms_path)
        self.classes = load_classes(classes_path)
        self.default_by = default_by
        self.fuzzy = fuzzy
        self.fuzzy_cutoff = fuzzy_cutoff

    # ---- normalization ----
    def normalize(self, text: Optional[str]) -> NormalizedDrug:
        if not text or not str(text).strip():
            return NormalizedDrug(raw=text or "", canonical=None)
        key = str(text).strip().lower()
        if key in self.synonyms:
            generic = self.synonyms[key]
            via = "exact" if generic == key else "synonym"
            return NormalizedDrug(text, generic, via, 1.0)
        if self.fuzzy and self.synonyms:
            matches = difflib.get_close_matches(key, list(self.synonyms.keys()),
                                                n=3, cutoff=self.fuzzy_cutoff)
            if matches:
                best = matches[0]
                conf = difflib.SequenceMatcher(None, key, best).ratio()
                cands = sorted({self.synonyms[m] for m in matches})
                return NormalizedDrug(text, self.synonyms[best], "fuzzy", round(conf, 3), cands)
        return NormalizedDrug(text, None, None, 0.0, [])

    def classes_of(self, name: Optional[str]) -> Dict[str, Any]:
        nd = self.normalize(name)
        if not nd.canonical:
            return {}
        return self.classes.get(nd.canonical, {})

    # ---- same family ----
    def same_family(self, a: Optional[str], b: Optional[str],
                    by: Optional[str] = None) -> Dict[str, Any]:
        by = by or self.default_by
        na, nb = self.normalize(a), self.normalize(b)
        ga, gb = na.canonical, nb.canonical
        out: Dict[str, Any] = {"a": ga, "b": gb, "basis": by, "same": None, "shared": []}
        if not ga or not gb:
            out["reason"] = "could not resolve one or both drug names"
            return out
        ca, cb = self.classes.get(ga, {}), self.classes.get(gb, {})
        if not ca or not cb:
            out["reason"] = "class data missing for one or both drugs"
            return out
        if ga == gb:
            out["same"] = True
            out["shared"] = ["identical_drug"]
            return out

        mech_match = bool(ca.get("mechanism") and ca.get("mechanism") == cb.get("mechanism"))
        atc_a = {_atc_level4(c) for c in ca.get("atc", []) if c}
        atc_b = {_atc_level4(c) for c in cb.get("atc", []) if c}
        atc_shared = sorted(atc_a & atc_b)
        atc_match = bool(atc_shared)

        if by == "mechanism":
            out["same"] = mech_match
            out["shared"] = [ca["mechanism"]] if mech_match else []
        elif by == "atc":
            out["same"] = atc_match
            out["shared"] = atc_shared
        elif by == "both":
            out["same"] = mech_match or atc_match
            out["shared"] = ([ca["mechanism"]] if mech_match else []) + atc_shared
        else:
            out["reason"] = f"unknown family definition '{by}'"
        return out
