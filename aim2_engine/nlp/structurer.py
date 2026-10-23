"""
nlp/structurer.py
-----------------
Point A - free text -> SUGGESTED structured fields.

A deterministic, dependency-free baseline (lexicon + regex) implementing the
FreeTextStructurer protocol. It reads free-text answers (Q13 "anything else",
Q1a "describe what changed", "Other" fields) and proposes structured values a
clinician can confirm.

CRITICAL BOUNDARY: findings are ADVISORY. They are surfaced to the clinician
review layer / attached to the summary. They are NEVER written into the
IntervalSurvey that the decision engine consumes - the decision path stays
purely from the family's explicit structured answers. (Swap in a fine-tuned
BioBERT/Bio_ClinicalBERT NER+classifier later via the same protocol.)
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple, Dict

from .interfaces import FreeTextFinding
from .negation import is_negated

# Generic + common brand names for the study ASMs. A deliberately separate,
# lightweight copy local to nlp/ rather than importing
# engine_core.drug_identity.known_study_medications() — nlp/ stays fully
# decoupled from engine_core/ (one-way boundary, see clinical-safety.md),
# even though this is just name-matching, not a clinical value. If the
# clinician-maintained drug list (data/drug_synonyms.json) changes, update
# here too.
_DRUG_NAMES = [
    "levetiracetam", "keppra",
    "lamotrigine", "lamictal",
    "oxcarbazepine", "trileptal",
    "valproate", "valproic acid", "depakote",
    "clobazam", "onfi",
    "topiramate", "topamax",
    "zonisamide", "zonegran",
    "brivaracetam", "briviact",
    "ethosuximide", "zarontin",
    "lacosamide", "vimpat",
    "carbamazepine", "tegretol",
]
# Common seizure/medication-monitoring tests/labs mentioned in free text.
_TEST_PHRASES = [
    "eeg", "mri", "ct scan", "blood work", "blood draw", "lab work", "labs",
    "liver function", "blood test", "imaging study", "x-ray",
]

# field -> list of (compiled pattern, suggested_value). suggested_value=None
# is a sentinel meaning "use the matched text itself" (for drug_mention /
# test_mention, where the specific name/test matters more than a fixed code).
_LEXICON: Dict[str, List[Tuple[str, Optional[str]]]] = {
    "missed_dose_reason": [
        (r"cost|insuran|afford|copay", "cost_or_insurance"),
        (r"forg|remember|remind", "hard_to_remember"),
        (r"ran out|run out|refill|pharmacy|out of", "ran_out"),
        (r"refus|won'?t take|spit|fight", "child_refuses"),
        (r"side effect|made (him|her|them) ", "side_effects"),
    ],
    "dose_change": [
        (r"stopped|quit|discontinu", "stopped"),
        (r"increas|went up|upped|raised|higher", "increased"),
        (r"decreas|lower|reduc|cut", "decreased"),
        (r"switch|changed to|new med", "changed"),
    ],
    "side_effect": [
        # sleep(?!ing (normally|well|...)) - bare "sleep" is a substring of
        # "sleepy"/"sleeping too much" (real symptom) but also of "sleeping
        # normally" (a REASSURANCE, the opposite meaning). Found via
        # validation/nlp_confusion_matrix.py: "eating and sleeping normally"
        # in a plain-negative test sentence was wrongly flagged as sleepiness.
        (r"tired|drowsy|fatigue|lethargic|sleep(?!ing\s+(?:normally|well|fine|great|ok|okay))",
         "sleepiness"),
        (r"rash|hives|skin", "rash"),
        (r"dizz|unsteady|balance|wobbl", "dizziness"),
        (r"irritab|mood|angry|anger|cranky|emotional", "irritability"),
        # Same fix as above, for nausea: bare "eating" matched "eating ...
        # normally" (reassurance). Kept "appetite" bare (no observed false
        # positive for it yet) and added explicit loss-of-appetite phrasing
        # instead of the bare word.
        (r"nause|vomit|appetite|throw\w*\s*up|off.{0,15}food|not\s+eating|"
         r"trouble\s+eating|won'?t\s+eat", "nausea"),
        (r"behav|aggress|hyper|acting out", "behavior"),
    ],
    "seizure_mention": [
        (r"seizure|convuls|staring|jerk|twitch|shaking|spell", "seizure_reported"),
    ],
    "drug_mention": [(rf"\b{re.escape(n)}\b", None) for n in _DRUG_NAMES],
    "test_mention": [(rf"\b{re.escape(p)}\b", None) for p in _TEST_PHRASES],
}

_COMPILED = {
    field: [(re.compile(pat, re.I), val) for pat, val in rules]
    for field, rules in _LEXICON.items()
}


class LexiconStructurer:
    """Deterministic baseline FreeTextStructurer (implements the protocol)."""

    def structure(self, text: str, source_question: Optional[str] = None
                  ) -> List[FreeTextFinding]:
        if not text:
            return []
        findings: List[FreeTextFinding] = []
        seen = set()
        for field, rules in _COMPILED.items():
            for pattern, value in rules:
                m = pattern.search(text)
                if not m:
                    continue
                # Negation only makes sense for the things that can be
                # AFFIRMED OR DENIED (a symptom, a seizure). A drug or test
                # NAME is a fact regardless of whether an effect tied to it
                # is being denied - "no adverse effects with lacosamide"
                # should still report the lacosamide mention. Found via
                # validation/nlp_pubmed_eval.py: this was wrongly suppressing
                # real drug mentions before the field check was added.
                if field in ("side_effect", "seizure_mention") and is_negated(text, m.start()):
                    continue
                actual_value = value if value is not None else m.group(0)
                key = (field, actual_value)
                if key not in seen:
                    seen.add(key)
                    findings.append(FreeTextFinding(
                        field=field,
                        suggested_value=actual_value,
                        confidence=0.6,                 # coarse baseline confidence
                        evidence=m.group(0),
                        source_question=source_question,
                    ))
        return findings

    def structure_many(self, answers: Dict[str, str]) -> List[FreeTextFinding]:
        out: List[FreeTextFinding] = []
        for q, txt in (answers or {}).items():
            if isinstance(txt, str):
                out.extend(self.structure(txt, source_question=q))
        return out
