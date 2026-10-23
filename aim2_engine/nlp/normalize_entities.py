"""
normalize_entities.py
---------------------
Map free-text mentions to a controlled vocabulary so synonyms collapse to one
canonical concept (better dedup + consistency for the review layer). Pure stdlib
(dictionary + difflib fuzzy fallback for typos/near-misses). Advisory only.

  * SIDE_EFFECT_VOCAB   - caregiver synonyms -> canonical concept
                          (sleepy/drowsy/tired -> somnolence).
  * SEIZURE_VOCAB       - descriptions -> ILAE-style class token used elsewhere
                          in the engine (whole body shaking -> generalized_tonic_clonic).
  * normalize(mention, kind)  -> (canonical, method, score)
        method in {"exact","synonym","fuzzy","unknown"}.

The canonical seizure tokens intentionally match SEIZURE_TOKEN_TO_CLASS in
engine_core.categorize so a normalized value is comparable, but nothing here is
written into the engine (clinical-safety.md).
"""
from __future__ import annotations

import difflib
import re
from typing import Dict, List, Tuple

# canonical concept -> list of surface synonyms
SIDE_EFFECT_SYNONYMS: Dict[str, List[str]] = {
    "somnolence": ["sleepy", "drowsy", "tired", "fatigue", "fatigued", "sedated",
                   "sleepiness", "lethargic", "sluggish", "worn out"],
    "irritability": ["irritable", "cranky", "moody", "mood swings", "agitated",
                     "short tempered", "grumpy"],
    "aggression": ["aggressive", "angry", "hitting", "violent", "outbursts", "rage"],
    "dizziness": ["dizzy", "lightheaded", "light headed", "off balance", "vertigo",
                  "wobbly", "unsteady"],
    "rash": ["rash", "hives", "spots", "red skin", "skin rash", "welts"],
    "nausea": ["nausea", "nauseous", "queasy", "sick to stomach", "throwing up",
               "vomiting", "vomit"],
    "headache": ["headache", "head ache", "head hurts", "migraine"],
    "appetite_loss": ["not eating", "no appetite", "loss of appetite", "poor appetite"],
    "cognitive": ["foggy", "brain fog", "word finding", "trouble concentrating",
                  "forgetful", "slowed thinking"],
}

# canonical ILAE-ish class -> surface descriptions
SEIZURE_SYNONYMS: Dict[str, List[str]] = {
    "absence": ["staring", "staring spells", "blank stare", "spacing out", "absence",
                "brief staring"],
    "myoclonic": ["jerks", "jerking", "twitches", "myoclonic", "sudden jerks"],
    "generalized_tonic_clonic": ["whole body shaking", "whole body stiffening",
                                 "grand mal", "convulsion", "tonic clonic", "stiffen and shake",
                                 "whole body"],
    "focal": ["one part", "one side", "arm shaking", "leg twitching", "focal",
              "one limb"],
    "focal_impaired_awareness": ["acting off", "not responding", "unresponsive staring",
                                 "confused episode", "focal impaired"],
}

_VOCABS = {"side_effect": SIDE_EFFECT_SYNONYMS, "seizure": SEIZURE_SYNONYMS}


def _flat(vocab: Dict[str, List[str]]) -> Dict[str, str]:
    """surface -> canonical (includes the canonical token itself)."""
    out = {}
    for canon, syns in vocab.items():
        out[canon.replace("_", " ")] = canon
        out[canon] = canon
        for s in syns:
            out[s.lower()] = canon
    return out


_FLAT = {k: _flat(v) for k, v in _VOCABS.items()}


def normalize(mention: str, kind: str = "side_effect",
              fuzzy_cutoff: float = 0.86) -> Tuple[str, str, float]:
    """Return (canonical, method, score). Tries exact surface, then substring
    synonym, then difflib fuzzy; 'unknown' if nothing clears the cutoff."""
    if kind not in _FLAT:
        return (mention, "unknown", 0.0)
    m = re.sub(r"\s+", " ", (mention or "").strip().lower())
    if not m:
        return ("", "unknown", 0.0)
    flat = _FLAT[kind]
    # exact
    if m in flat:
        method = "exact" if m in _VOCABS[kind] else "synonym"
        return (flat[m], method, 1.0)
    # substring synonym (mention contains a known surface, or vice-versa)
    for surface, canon in flat.items():
        if surface in m or m in surface:
            return (canon, "synonym", 0.95)
    # fuzzy
    best = difflib.get_close_matches(m, list(flat.keys()), n=1, cutoff=fuzzy_cutoff)
    if best:
        score = difflib.SequenceMatcher(None, m, best[0]).ratio()
        return (flat[best[0]], "fuzzy", round(score, 3))
    return (mention, "unknown", 0.0)


def normalize_all(mentions: List[str], kind: str = "side_effect") -> Dict[str, str]:
    """Map many mentions to canonical concepts (drops unknowns)."""
    out = {}
    for men in mentions:
        canon, method, _ = normalize(men, kind)
        if method != "unknown":
            out[men] = canon
    return out
