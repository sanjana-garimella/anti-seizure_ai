"""
nlp/generate.py
---------------
Point D - render the engine's already-decided outputs as family-facing text.

Two implementations of the MessageGenerator protocol:

  * TemplateMessageGenerator - DEFAULT. Returns the deterministic message the
    engine already built (engine_core.outputs). No model, always safe.

  * GuardedGenerator - wraps an INJECTED generative model callable (e.g. a
    clinical LLM / BioGPT served on your infra). It withholds on a safety
    concern, builds a fact-constrained prompt, runs guardrails on the output,
    and FALLS BACK to the template if the model is absent or fails a guardrail.

NOTE: encoder models such as BioBERT/Bio_ClinicalBERT CANNOT generate text -
do not pass one here. `model_callable` must be a *generative* model wrapper
`Callable[[str], str]`. Generation never touches the decision (spec §11.3); it
only restates structured facts and is always physician-reviewed.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Callable, Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .guardrails import validate_message, GuardrailResult


class TemplateMessageGenerator:
    """Deterministic default - returns the engine's own drafted message."""

    def generate_patient_message(self, recommendation: Any, snapshot: Any,
                                 physician_name: str = "[physician name]"
                                 ) -> Optional[str]:
        # The engine already computed a safe, template-based draft (None when
        # withheld for a safety concern).
        return getattr(recommendation, "draft_patient_message", None)


def build_constrained_prompt(recommendation: Any, snapshot: Any,
                             physician_name: str = "[physician name]") -> str:
    """A fact-only prompt for a generative model. Facts come solely from the
    engine's structured output - the model may rephrase, never add."""
    med = getattr(snapshot, "medication", None)
    med_name = getattr(med, "medication", "the medicine") if med else "the medicine"
    facts = [
        f"recommendation_category: {getattr(recommendation, 'category', '')}",
        f"medication: {med_name}",
        f"switch_target: {getattr(recommendation, 'switch_target', None)}",
        f"titration_schedule: {getattr(recommendation, 'titration_schedule', None)}",
        f"physician_name: {physician_name}",
    ]
    return (
        "Write a short, plain-language message to a parent, 6th-grade reading "
        "level. Use ONLY these facts; do NOT add any clinical detail, dose "
        "number, or instruction not present here. Do NOT tell them to change "
        "the medicine themselves. End with a note that it is a draft for "
        "physician review.\n\nFACTS:\n" + "\n".join(facts)
    )


class GuardedGenerator:
    """
    Wrap a generative model with guardrails + template fallback.

    model_callable: Callable[[str], str] or None. If None (no model configured,
    e.g. offline), every call returns the deterministic template.
    """

    def __init__(self, model_callable: Optional[Callable[[str], str]] = None,
                 fallback: Optional[Any] = None):
        self.model_callable = model_callable
        self.fallback = fallback or TemplateMessageGenerator()
        self.last_validation: Optional[GuardrailResult] = None
        self.last_source: Optional[str] = None      # "model" | "template" | "withheld"

    def generate_patient_message(self, recommendation: Any, snapshot: Any,
                                 physician_name: str = "[physician name]"
                                 ) -> Optional[str]:
        # 1) Safety: NEVER invoke the model; return what the engine returned, which is
        #    None (no family message) on every safety flag - Dr. Barbour 2026-10-25, #51.
        #    The model must never author safety copy.
        if getattr(recommendation, "safety_concern_flag", False):
            self.last_source = "safety_template"
            self.last_validation = None
            return self.fallback.generate_patient_message(recommendation, snapshot, physician_name)

        # 2) No model configured -> deterministic template.
        if self.model_callable is None:
            self.last_source = "template"
            return self.fallback.generate_patient_message(recommendation, snapshot, physician_name)

        # 3) Generate, then guardrail; fall back on any failure.
        prompt = build_constrained_prompt(recommendation, snapshot, physician_name)
        try:
            text = self.model_callable(prompt)
        except Exception:
            self.last_source = "template"
            self.last_validation = GuardrailResult(ok=False, problems=["model call raised"])
            return self.fallback.generate_patient_message(recommendation, snapshot, physician_name)

        result = validate_message(text, recommendation, snapshot)
        self.last_validation = result
        if result.ok:
            self.last_source = "model"
            return text
        self.last_source = "template"
        return self.fallback.generate_patient_message(recommendation, snapshot, physician_name)
