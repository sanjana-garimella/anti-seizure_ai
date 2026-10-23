"""
nlp/interfaces.py
-----------------
Boundaries for the OPTIONAL NLP layer (insertion points A, B, D from the
architecture map). This layer sits OUTSIDE the deterministic decision path:

  * A  FreeTextStructurer  - free text -> SUGGESTED structured fields (for
                             clinician review; never feeds the decision engine)
  * B  RedFlagDetector     - free text -> urgent human flags (escalate-only)
  * D  MessageGenerator    - render the engine's already-decided outputs as text

These are Protocols so a fine-tuned BioBERT / Bio_ClinicalBERT classifier (A/B)
or a generative clinical model (D) can be dropped in later WITHOUT touching the
engine. The shipped implementations are deterministic, dependency-free
baselines (lexicon + regex) so the system works offline and is auditable today.

HARD RULES (spec §11): nothing here makes or alters the medication
recommendation, no clinical values are invented, and generated text is
guard-railed + physician-reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable, Dict, Any


@dataclass
class FreeTextFinding:
    """A SUGGESTED structured value extracted from free text (point A).

    A finding is advisory: it is surfaced to the clinician review layer and
    attached to the summary, but it is NEVER fed into categorization/decision.
    """
    field: str                       # e.g. "missed_dose_reason", "side_effect"
    suggested_value: str
    confidence: float                # 0..1 (baseline uses coarse buckets)
    evidence: str                    # the matched span / phrase
    source_question: Optional[str] = None
    note: str = "SUGGESTION for clinician review - not used by the decision engine."


@dataclass
class RedFlag:
    """An urgent signal found in free text (point B). Escalate-only."""
    term: str
    category: str                    # clinician-defined bucket
    severity: str                    # "urgent" | "review"
    evidence: str
    source_question: Optional[str] = None


@runtime_checkable
class FreeTextStructurer(Protocol):
    def structure(self, text: str, source_question: Optional[str] = None
                  ) -> List[FreeTextFinding]: ...


@runtime_checkable
class RedFlagDetector(Protocol):
    def scan(self, text: str, source_question: Optional[str] = None
             ) -> List[RedFlag]: ...


@runtime_checkable
class MessageGenerator(Protocol):
    # Returns the family-facing message text, or None when it must be withheld
    # (e.g. a safety concern). `recommendation` and `snapshot` are the engine's
    # objects; generators may ONLY restate facts already present in them.
    def generate_patient_message(self, recommendation: Any, snapshot: Any,
                                 physician_name: str = "[physician name]"
                                 ) -> Optional[str]: ...
