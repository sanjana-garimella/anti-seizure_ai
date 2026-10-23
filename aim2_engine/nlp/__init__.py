"""
nlp
---
OPTIONAL NLP layer for the Aim 2 assistant - insertion points A, B, D from the
architecture map. It sits OUTSIDE the deterministic decision path:

  A  LexiconStructurer      free text -> SUGGESTED fields (clinician review)
  B  LexiconRedFlagDetector free text -> urgent human flags (escalate-only)
  D  TemplateMessageGenerator / GuardedGenerator  render decided outputs as text

Shipped implementations are deterministic, dependency-free baselines so the
system runs offline and stays auditable. The Protocols let a fine-tuned
BioBERT/Bio_ClinicalBERT (A/B) or a generative clinical model (D) drop in later
WITHOUT changing the engine. Nothing here makes or alters the recommendation,
invents clinical values, or sends unreviewed text (spec §11).
"""

from .interfaces import (
    FreeTextFinding, RedFlag,
    FreeTextStructurer, RedFlagDetector, MessageGenerator,
)
from .structurer import LexiconStructurer
from .redflags import LexiconRedFlagDetector, load_terms, urgent_categories
from .guardrails import validate_message, GuardrailResult
from .generate import (
    TemplateMessageGenerator, GuardedGenerator, build_constrained_prompt,
)

__all__ = [
    "FreeTextFinding", "RedFlag",
    "FreeTextStructurer", "RedFlagDetector", "MessageGenerator",
    "LexiconStructurer",
    "LexiconRedFlagDetector", "load_terms", "urgent_categories",
    "validate_message", "GuardrailResult",
    "TemplateMessageGenerator", "GuardedGenerator", "build_constrained_prompt",
]
