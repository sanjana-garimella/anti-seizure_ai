"""
context_assertion.py
--------------------
ConText-style assertion for free-text findings (pure stdlib, no medspaCy/NegEx
dependency). For a candidate finding span it determines four assertions used by
the clinician-review layer to gate/annotate advisory findings:

  * negated     - a negation cue scopes the finding ("no rash", "denies jerks")
  * historical  - past/resolved rather than current ("used to", "last year")
  * experiencer - "other" when the finding is about someone else ("my other
                  child had seizures"), else "patient"
  * hedged      - uncertainty cue present ("maybe", "I think", "seemed like")

Scope resets at sentence punctuation AND contrastive conjunctions (but/however/
though/although), mirroring the existing negation heuristic. This is HIGH-RECALL
by design for the escalate path; findings are advisory only and never enter the
decision engine (clinical-safety.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Cue lexicons (extend via data files later; kept inline + small for clarity).
_NEGATION = [
    "no", "not", "never", "without", "denies", "denied", "deny", "negative for",
    "no sign of", "no signs of", "ruled out", "resolved", "free of", "hasn't had",
    "hasnt had", "haven't had", "havent had", "didn't have", "didnt have",
]
_HISTORICAL = [
    "used to", "history of", "hx of", "previously", "in the past", "last year",
    "last month", "months ago", "years ago", "a while back", "before", "resolved",
    "no longer", "used", "formerly",
]
_EXPERIENCER_OTHER = [
    "other child", "sibling", "brother", "sister", "father", "mother", "parent",
    "cousin", "grandmother", "grandfather", "family history", "runs in the family",
    "her friend", "his friend", "another kid",
]
_HEDGE = [
    "maybe", "may be", "might", "possibly", "i think", "we think", "not sure",
    "unsure", "seemed", "seems", "seem like", "seemed like", "kind of", "sort of",
    "perhaps", "guess", "hard to say", "could be", "i guess", "probably",
]
# scope-reset boundaries
_RESET = re.compile(r"[.!?;]|\b(but|however|though|although|except|whereas)\b", re.I)


@dataclass
class Assertion:
    negated: bool = False
    historical: bool = False
    experiencer: str = "patient"      # "patient" | "other"
    hedged: bool = False

    @property
    def is_current_patient_positive(self) -> bool:
        """True only when the finding is asserted, current, about the patient."""
        return not (self.negated or self.historical or self.experiencer == "other")


def _left_window(text: str, start: int) -> str:
    """Text from the last scope-reset boundary up to the finding span."""
    pre = text[:start]
    last = 0
    for m in _RESET.finditer(pre):
        last = m.end()
    return pre[last:].lower()


def _sentence(text: str, start: int, end: int) -> str:
    left = 0
    for m in _RESET.finditer(text[:start]):
        left = m.end()
    right = len(text)
    m = _RESET.search(text, end)
    if m:
        right = m.start()
    return text[left:right].lower()


def _any(cues, hay: str) -> bool:
    return any(re.search(r"\b" + re.escape(c) + r"\b", hay) for c in cues)


def assess(text: str, span: "tuple[int, int]") -> Assertion:
    """Assertion for a finding located at char span (start, end) in `text`."""
    start, end = span
    left = _left_window(text, start)
    sent = _sentence(text, start, end)
    a = Assertion()
    a.negated = _any(_NEGATION, left)
    a.historical = _any(_HISTORICAL, sent)
    a.experiencer = "other" if _any(_EXPERIENCER_OTHER, sent) else "patient"
    a.hedged = _any(_HEDGE, sent)
    return a


def assess_term(text: str, term: str) -> Assertion:
    """Convenience: locate `term` in `text` and assess it (first occurrence)."""
    m = re.search(r"\b" + re.escape(term) + r"\b", text, re.I)
    if not m:
        return Assertion()
    return assess(text, (m.start(), m.end()))


# --------------------------- abstention (reject option) ---------------------
def should_abstain(confidence: float, assertion: Assertion,
                   tau: float = 0.5, escalate_only: bool = False) -> bool:
    """Selective-prediction gate for a structured (point-A) finding.

    Abstain (route to human, emit NO structured value) when the model is unsure
    or the context undermines a confident positive:
      * confidence < tau, OR
      * the finding is negated / historical / about someone else, OR
      * hedged AND borderline confidence.

    escalate_only=True (red-flag path) NEVER abstains on negation/hedge - those
    are high-recall by design (a missed urgent signal is the worst error); it
    only abstains below a very low confidence floor.
    """
    if escalate_only:
        return confidence < 0.15
    if confidence < tau:
        return True
    if not assertion.is_current_patient_positive:
        return True
    if assertion.hedged and confidence < (tau + 0.2):
        return True
    return False
