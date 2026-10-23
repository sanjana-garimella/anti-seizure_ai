"""
nlp/redflags.py
---------------
Point B - free-text red-flag safety-net.

A high-recall, deterministic detector that scans free-text answers for urgent
clinician-authored phrases and raises human flags. It is ESCALATE-ONLY: a hit
can only raise an urgent flag for human review; it can never change or lower a
recommendation.

The term list is clinician-supplied (data/redflag_terms.template.json, derived
from the form's own safety language; PI confirms/expands - NEEDS_PI_SIGNOFF.md).
Matching (2026-07-01 rewrite, finding D1) is deterministic but higher-recall
than the original exact-phrase-only baseline, while staying escalate-only:

  * "phrase" terms (default / legacy): word-boundary exact-phrase match, with a
    light suffix tolerance (peel -> peeling/peeled, blister -> blistering, etc.)
    so morphological variants are caught. Existing entries keep working exactly.
  * "token" terms: a standalone word-boundary token (e.g. SJS/TEN) that fires
    on its own.
  * "skin_term" / "skin_modifier" terms: TERM+MODIFIER logic — a skin token
    (rash|blister|peel|lesion|sore|ulcer|hives|welts|spots) fires sjs_skin ONLY
    when an escalation modifier (spreading|large|all over|face|mouth|eye|lip|
    mucos|target|...) co-occurs anywhere in the same text. A bare "small rash"
    with no modifier does NOT fire via this rule (though a standalone SJS/TEN
    token or an exact phrase still can).
  * Seizure duration: generalized in code (regex) to fire prolonged_seizure for
    ANY duration >= the configured threshold (default 5 min, read from the JSON
    "_config.prolonged_seizure_min_minutes"), expressed in numerals or words
    ("6 minutes", "ten minutes", "more than 8 min", "lasted 7 minutes"). The
    threshold is a configurable clinical value in the JSON, not a magic number
    buried in logic.

A fine-tuned classifier (e.g. Bio_ClinicalBERT) can still replace this via the
RedFlagDetector protocol.

DECISION-PATH NOTE: by default these flags do NOT feed the engine - they go to
the clinician review layer. `urgent_categories()` is provided so an integrator
can OPT IN (a PI/IRB decision) to also escalate the engine to withhold + urgent
review. Default is off, keeping the decision path purely from structured input.
This module deliberately does NOT import engine_core (one-way advisory boundary,
see NON_NEGOTIABLE_RULES.md §2).
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Dict, Any

from .interfaces import RedFlag

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_TERMS = os.path.join(_HERE, "data", "redflag_terms.template.json")
_FILLED_TERMS = os.path.join(_HERE, "data", "redflag_terms.json")

# Default prolonged-seizure threshold (minutes) if the JSON omits _config.
# The authoritative value lives in the JSON so it is not hardcoded here; this
# is only the fallback for a legacy/hand-written term file.
_DEFAULT_SEIZURE_MIN_MINUTES = 5

# Escalation modifiers for the skin term+modifier rule. A skin_term only fires
# sjs_skin when one of these co-occurs (deliberately broad — escalate-only, we
# would rather over-flag than miss an SJS/TEN presentation).
_SKIN_MODIFIERS = [
    "spreading", "spread", "large", "big", "wide", "widespread", "worse",
    "bigger", "all over", "whole body", "face", "facial", "mouth", "eye",
    "lip", "mucos", "target", "fast", "everywhere",
]

# Number words -> value, for word-expressed seizure durations.
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30,
}


def _default_terms_path() -> str:
    # Prefer a clinician-filled file over the starter template (resolved at call
    # time so edits made while the app is running take effect on reload).
    return _FILLED_TERMS if os.path.isfile(_FILLED_TERMS) else _TEMPLATE_TERMS


def _load_raw(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or _default_terms_path()
    if not os.path.isfile(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def load_terms(path: Optional[str] = None) -> List[Dict[str, Any]]:
    raw = _load_raw(path)
    return [t for t in raw.get("terms", []) if t.get("phrase")]


def _seizure_threshold_minutes(path: Optional[str] = None) -> int:
    raw = _load_raw(path)
    cfg = raw.get("_config", {}) or {}
    try:
        return int(cfg.get("prolonged_seizure_min_minutes",
                           _DEFAULT_SEIZURE_MIN_MINUTES))
    except (TypeError, ValueError):
        return _DEFAULT_SEIZURE_MIN_MINUTES


def _phrase_pattern(phrase: str) -> re.Pattern:
    """Word-boundary pattern for an exact phrase, with light suffix tolerance
    so morphological variants match (peel -> peeling/peeled/peels; blister ->
    blistering/blistered). Only applied to single alphabetic tokens; multiword
    phrases match as-is (still word-boundary-anchored)."""
    p = phrase.lower()
    if re.fullmatch(r"[a-z]+", p):
        # single word: allow common inflectional suffixes
        return re.compile(r"(?<!\w)" + re.escape(p) + r"(?:s|es|ed|ing)?(?!\w)")
    return re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)")


class LexiconRedFlagDetector:
    """Deterministic baseline RedFlagDetector (implements the protocol).

    Higher-recall than exact-phrase matching (finding D1) but still fully
    deterministic and escalate-only.
    """

    def __init__(self, terms: Optional[List[Dict[str, Any]]] = None,
                 terms_path: Optional[str] = None):
        self.terms = terms if terms is not None else load_terms(terms_path)
        self._seizure_min = _seizure_threshold_minutes(terms_path)

        # Bucket terms by type. Default (missing type) == "phrase" == legacy.
        self._phrase_terms = []          # (compiled_pattern, term)  matched on lowercased text
        self._token_terms = []           # (compiled_pattern, term)  matched on lowercased text
        self._token_terms_cased = []     # (compiled_pattern, term)  matched on ORIGINAL text
        self._skin_terms = []            # (compiled_pattern, term)  matched on lowercased text
        for t in self.terms:
            ttype = (t.get("type") or "phrase").lower()
            if ttype == "token":
                if t.get("case_sensitive"):
                    # e.g. acronyms SJS / TEN — match on the original-case text
                    # so the English word "ten" does not over-fire the TEN token.
                    pat = re.compile(r"(?<!\w)" + re.escape(t["phrase"]) + r"(?!\w)")
                    self._token_terms_cased.append((pat, t))
                else:
                    pat = re.compile(r"(?<!\w)" + re.escape(t["phrase"].lower()) + r"(?!\w)")
                    self._token_terms.append((pat, t))
            elif ttype == "skin_term":
                self._skin_terms.append((_phrase_pattern(t["phrase"]), t))
            else:  # "phrase" (default) — unchanged behavior for legacy entries
                self._phrase_terms.append((_phrase_pattern(t["phrase"]), t))

        # Modifier detector for the skin term+modifier rule.
        self._skin_modifier_re = re.compile(
            "|".join(re.escape(m) for m in _SKIN_MODIFIERS))

        # Generalized seizure-duration regex: numeral or number-word followed by
        # a minute unit. Threshold comparison is done in code against
        # self._seizure_min. Matches "6 minutes", "ten minutes", "7 min",
        # "more than 8 minutes", "lasted 5 min".
        num_word_alt = "|".join(_NUM_WORDS)
        self._duration_re = re.compile(
            r"(?<!\w)(\d{1,3}|" + num_word_alt + r")\s*(?:-|\s)?\s*"
            r"(?:minute|minutes|min|mins)(?!\w)")

    # -- helpers ------------------------------------------------------------

    def _duration_minutes(self, low: str) -> List[str]:
        """Return the matched duration spans (as evidence) that are >= the
        configured prolonged-seizure threshold."""
        hits = []
        for m in self._duration_re.finditer(low):
            raw = m.group(1)
            val = _NUM_WORDS.get(raw)
            if val is None:
                try:
                    val = int(raw)
                except ValueError:
                    continue
            if val >= self._seizure_min:
                hits.append(m.group(0).strip())
        return hits

    # -- protocol -----------------------------------------------------------

    def scan(self, text: str, source_question: Optional[str] = None) -> List[RedFlag]:
        if not text:
            return []
        low = text.lower()
        flags: List[RedFlag] = []
        seen = set()

        def add(term_phrase: str, category: str, severity: str, evidence: str):
            key = (evidence, category)
            if key in seen:
                return
            seen.add(key)
            flags.append(RedFlag(
                term=term_phrase,
                category=category,
                severity=severity,
                evidence=evidence,
                source_question=source_question,
            ))

        # 1) exact-phrase terms (legacy behavior + suffix tolerance)
        for pattern, t in self._phrase_terms:
            m = pattern.search(low)
            if m:
                add(t["phrase"], t.get("category", "unspecified"),
                    t.get("severity", "review"), t["phrase"])

        # 2a) standalone tokens matched case-insensitively (cyanotic ...)
        for pattern, t in self._token_terms:
            m = pattern.search(low)
            if m:
                add(t["phrase"], t.get("category", "unspecified"),
                    t.get("severity", "review"), t["phrase"])

        # 2b) standalone tokens matched CASE-SENSITIVELY on original text
        # (acronyms SJS / TEN — avoids the number word "ten" over-firing).
        for pattern, t in self._token_terms_cased:
            m = pattern.search(text)
            if m:
                add(t["phrase"], t.get("category", "unspecified"),
                    t.get("severity", "review"), t["phrase"])

        # 3) skin term + modifier co-occurrence
        if self._skin_terms and self._skin_modifier_re.search(low):
            for pattern, t in self._skin_terms:
                m = pattern.search(low)
                if m:
                    add(t["phrase"], t.get("category", "sjs_skin"),
                        t.get("severity", "urgent"),
                        t["phrase"] + "+modifier")

        # 4) generalized seizure duration >= threshold
        for span in self._duration_minutes(low):
            add("duration>=%dmin" % self._seizure_min,
                "prolonged_seizure", "urgent", span)

        return flags

    def scan_many(self, answers: Dict[str, str]) -> List[RedFlag]:
        """Scan a dict of {question_code: free_text} -> all flags."""
        out: List[RedFlag] = []
        for q, txt in (answers or {}).items():
            if isinstance(txt, str):
                out.extend(self.scan(txt, source_question=q))
        return out


# ---------------------------------------------------------------------------
# OPT-IN escalation helper (IRB-gated). See DECISION-PATH NOTE in the module
# docstring. IRB PRECONDITION: this helper may only be wired to raise an urgent
# HUMAN flag; it must NEVER change or lower an engine recommendation, and it is
# NOT imported/used by the default clinic.py decision path. Enabling any engine
# escalation from red-flag hits is a PI/IRB decision, not a code default, and is
# additionally unsafe until D1 recall is clinician-validated. Kept escalate-only.
# ---------------------------------------------------------------------------
def urgent_categories(flags: List[RedFlag]) -> List[str]:
    """Distinct urgent categories - what an integrator MAY (opt-in, IRB-gated)
    escalate. This is a pure read-only helper over already-produced flags; it
    performs no engine action and cannot alter a recommendation."""
    return sorted({f.category for f in flags if f.severity == "urgent"})
