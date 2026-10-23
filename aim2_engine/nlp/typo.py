"""
typo.py
-------
Conservative spelling correction over the clinical lexicon (pure stdlib,
SymSpell-style idea via difflib) so caregiver typos don't cause a lexicon/NER
miss. Runs BEFORE matching.

Design for safety (advisory layer, but still cautious):
  * Only corrects a token that is NOT already a known term, is long enough
    (>= min_len), and has a single clearly-closest known term above `cutoff`.
  * Never "corrects" toward a term if two known terms tie (ambiguous -> leave
    as-is; defer).
  * Preserves original case/positions; returns both corrected text and an audit
    list of (original, corrected, score) so every change is inspectable.

The default vocabulary is built from the entity-normalization synonyms + a small
clinical seed. Extendable. Nothing here touches the decision engine.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .normalize_entities import SIDE_EFFECT_SYNONYMS, SEIZURE_SYNONYMS

_WORD = re.compile(r"[A-Za-z]+")

_SEED = [
    "seizure", "seizures", "medication", "medicine", "dose", "twice", "daily",
    "morning", "evening", "rash", "blisters", "breathing", "hospital",
    "emergency", "minutes", "seconds", "irritable", "aggressive", "drowsy",
    "dizzy", "nausea", "headache", "appetite", "levetiracetam", "lamotrigine",
    "valproic", "topiramate", "oxcarbazepine", "carbamazepine", "clobazam",
    "lacosamide", "brivaracetam", "zonisamide", "ethosuximide",
]


def _build_vocab() -> set:
    v = set(_SEED)
    for syns in list(SIDE_EFFECT_SYNONYMS.values()) + list(SEIZURE_SYNONYMS.values()):
        for s in syns:
            v.update(_WORD.findall(s.lower()))
    for canon in list(SIDE_EFFECT_SYNONYMS) + list(SEIZURE_SYNONYMS):
        v.update(_WORD.findall(canon.replace("_", " ")))
    return {w for w in v if len(w) >= 3}


_VOCAB = _build_vocab()


@dataclass
class Correction:
    original: str
    corrected: str
    score: float


def correct_token(tok: str, vocab: set = None, cutoff: float = 0.84,
                  min_len: int = 4) -> Tuple[str, float]:
    """Best unambiguous correction for one token, or (tok, 1.0) if unchanged."""
    vocab = vocab or _VOCAB
    low = tok.lower()
    if len(low) < min_len or low in vocab:
        return tok, 1.0
    matches = difflib.get_close_matches(low, vocab, n=2, cutoff=cutoff)
    if not matches:
        return tok, 1.0
    r0 = difflib.SequenceMatcher(None, low, matches[0]).ratio()
    if len(matches) > 1:
        r1 = difflib.SequenceMatcher(None, low, matches[1]).ratio()
        if abs(r0 - r1) < 0.05:      # ambiguous -> do not guess
            return tok, 1.0
    fixed = matches[0]
    # preserve simple capitalization of the original
    if tok[:1].isupper():
        fixed = fixed.capitalize()
    return fixed, round(r0, 3)


def correct_text(text: str, vocab: set = None, cutoff: float = 0.84,
                 min_len: int = 4) -> Tuple[str, List[Correction]]:
    """Return (corrected_text, audit). Only alphabetic tokens are considered;
    punctuation/spacing preserved."""
    audit: List[Correction] = []

    def _sub(m):
        tok = m.group(0)
        fixed, score = correct_token(tok, vocab, cutoff, min_len)
        if fixed.lower() != tok.lower():
            audit.append(Correction(tok, fixed, score))
            return fixed
        return tok

    return _WORD.sub(_sub, text or ""), audit
