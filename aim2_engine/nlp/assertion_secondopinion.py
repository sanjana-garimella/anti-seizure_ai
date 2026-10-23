"""
nlp/assertion_secondopinion.py
------------------------------
Advisory SECOND OPINION on the assertion status of a free-text finding
(present / absent / possible / hypothetical / conditional / someone-else).

The transparent ConText baseline (context_assertion.assess) stays the primary,
auditable path. Beside it we run an OPTIONAL domain-adapted assertion model and
compare. The two are combined by a deliberately conservative rule:

    baseline and model AGREE   -> return that label
    they DISAGREE              -> DEFER (route the span to a human)

We never let the learned model silently override the rule baseline, and we never
auto-resolve a disagreement — disagreement is exactly where a human should look.
This is the "assertion" analogue of the project's defer-on-ambiguity rule and
is pre-human triage only.

WHY ADD A LEARNED MODEL AT ALL
  Fine-tuned clinical assertion models beat prompted LLMs on the hard classes
  (Absent / Hypothetical) that matter most here — e.g. distinguishing "no rash"
  from "rash", and "my other child had seizures" from the patient's own report.
  Reported: fine-tuned 0.962 vs GPT-4o 0.901 macro-F1 (arXiv 2503.17425).
  CORRECTION FROM VERIFICATION: that paper's system is Spark NLP, not spaCy —
  borrow the *approach* (a fine-tuned assertion classifier as a second reader),
  don't expect a drop-in. Any HF sequence classifier can be plugged in via the
  AssertionModel protocol.

HARD BOUNDARY (clinical-safety.md, NON_NEGOTIABLE_RULES.md, spec §11)
  * ADVISORY only. Output annotates/gates advisory findings for clinician
    review; it is NEVER written into IntervalSurvey or any engine_core input.
  * No ML output enters the decision path. The learned model can only add a
    DEFER (more caution) or confirm the baseline — never fabricate a dose or
    change a recommendation.
  * Optional dependency: if `transformers`/`torch` are absent, the transformer
    adapter degrades to "unavailable" and the layer falls back to baseline-only
    (documented, non-crashing) — same contract as transformer_structurer.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple, runtime_checkable

from . import context_assertion as ctx

log = logging.getLogger(__name__)

# i2b2/ConText-style assertion labels (temporality kept separate as a flag).
PRESENT = "present"
ABSENT = "absent"
POSSIBLE = "possible"
HYPOTHETICAL = "hypothetical"
CONDITIONAL = "conditional"
SOMEONE_ELSE = "associated_with_someone_else"

ASSERTION_LABELS = (PRESENT, ABSENT, POSSIBLE, HYPOTHETICAL, CONDITIONAL, SOMEONE_ELSE)


@dataclass
class AssertionResult:
    label: str
    confidence: float                 # 0..1
    source: str                       # which reader produced it
    historical: bool = False          # temporality flag (orthogonal to label)


@runtime_checkable
class AssertionModel(Protocol):
    """A reader that labels the assertion status of `term` within `text`."""
    def assert_label(self, text: str, span: Tuple[int, int]) -> AssertionResult: ...

    @property
    def available(self) -> bool: ...


# ---------------------------------------------------------------------------
# Baseline adapter: wrap the transparent ConText rules as an AssertionModel.
# ---------------------------------------------------------------------------
class ContextAssertionBaseline:
    """Transparent, dependency-free reader (the current default)."""
    name = "context-rules (baseline)"

    @property
    def available(self) -> bool:
        return True

    @staticmethod
    def _to_label(a: "ctx.Assertion") -> str:
        # Precedence chosen for a safety net: someone-else and absent dominate a
        # bare positive; hedge -> possible.
        if a.experiencer == "other":
            return SOMEONE_ELSE
        if a.negated:
            return ABSENT
        if a.hedged:
            return POSSIBLE
        return PRESENT

    def assert_label(self, text: str, span: Tuple[int, int]) -> AssertionResult:
        a = ctx.assess(text, span)
        return AssertionResult(self._to_label(a), confidence=0.75,
                               source=self.name, historical=a.historical)


# ---------------------------------------------------------------------------
# Optional learned adapter: a fine-tuned HF assertion classifier.
# ---------------------------------------------------------------------------
class TransformerAssertionModel:
    """Domain-adapted assertion classifier (OPTIONAL second reader).

    Degrades gracefully: if transformers/torch or the weights are unavailable,
    `available` is False and the combiner falls back to baseline-only.

    The model is expected to classify a marked span (the finding is wrapped in
    [ENT]...[/ENT] markers, the standard assertion-classifier input) into
    ASSERTION_LABELS. `label_map` maps the model's raw label strings to ours.
    """
    def __init__(self, model_name: str = "spark-nlp-style/clinical-assertion",
                 label_map: Optional[dict] = None):
        self.model_name = model_name
        self.label_map = label_map or {}
        self._pipe = None
        self._ok = False
        try:
            from transformers import pipeline  # type: ignore
            self._pipe = pipeline("text-classification", model=model_name)
            self._ok = True
        except Exception as e:  # ImportError or model download/load failure
            log.warning("TransformerAssertionModel unavailable (%s); "
                        "falling back to baseline-only assertion.", e)

    name = "transformer (second opinion)"

    @property
    def available(self) -> bool:
        return self._ok

    def _mark(self, text: str, span: Tuple[int, int]) -> str:
        s, e = span
        return text[:s] + "[ENT]" + text[s:e] + "[/ENT]" + text[e:]

    def assert_label(self, text: str, span: Tuple[int, int]) -> AssertionResult:
        if not self._ok:
            return AssertionResult(PRESENT, 0.0, self.name)   # never trusted
        out = self._pipe(self._mark(text, span))[0]           # {label, score}
        raw = str(out["label"]).lower()
        label = self.label_map.get(raw, raw if raw in ASSERTION_LABELS else PRESENT)
        return AssertionResult(label, float(out["score"]), self.name)


# ---------------------------------------------------------------------------
# Combiner
# ---------------------------------------------------------------------------
@dataclass
class SecondOpinionResult:
    label: str                        # agreed label, or baseline label if solo
    defer: bool                       # True -> route span to human
    agreement: str                    # "agree" | "disagree" | "baseline_only"
    baseline: AssertionResult
    model: Optional[AssertionResult] = None
    note: str = ("Advisory assertion — clinician review only; "
                 "never used by the decision engine.")


@dataclass
class AssertionSecondOpinion:
    baseline: ContextAssertionBaseline = field(default_factory=ContextAssertionBaseline)
    model: Optional[AssertionModel] = None

    def review(self, text: str, span: Tuple[int, int]) -> SecondOpinionResult:
        b = self.baseline.assert_label(text, span)
        if self.model is None or not self.model.available:
            return SecondOpinionResult(label=b.label, defer=False,
                                       agreement="baseline_only", baseline=b)
        m = self.model.assert_label(text, span)
        if m.label == b.label:
            return SecondOpinionResult(label=b.label, defer=False,
                                       agreement="agree", baseline=b, model=m)
        # DISAGREEMENT -> defer to human (never auto-resolve).
        return SecondOpinionResult(label=b.label, defer=True,
                                   agreement="disagree", baseline=b, model=m)

    def review_term(self, text: str, term: str) -> SecondOpinionResult:
        import re
        mt = re.search(r"\b" + re.escape(term) + r"\b", text, re.I)
        span = (mt.start(), mt.end()) if mt else (0, 0)
        return self.review(text, span)


# ---------------------------------------------------------------------------
# Demo: baseline-only today (no transformers here); a stub model shows how a
# disagreement is turned into a DEFER.
# ---------------------------------------------------------------------------
def _demo() -> None:
    print("Assertion second opinion — baseline + optional learned reader (DRAFT)")
    so = AssertionSecondOpinion()   # baseline-only (transformers absent)
    for text, term in [
        ("no rash or blistering after the dose", "rash"),
        ("my other child had seizures, not this one", "seizures"),
        ("maybe a small rash appeared", "rash"),
        ("she developed a rash on her face", "rash"),
    ]:
        r = so.review_term(text, term)
        print(f"  [{r.agreement:12}] {r.label:28} defer={r.defer}  | {text!r}")

    # Inject a disagreeing stub model to show the DEFER path.
    class _StubModel:
        available = True
        name = "stub"
        def assert_label(self, text, span):
            return AssertionResult(PRESENT, 0.91, "stub")   # always says present
    so2 = AssertionSecondOpinion(model=_StubModel())
    r = so2.review_term("no rash after the dose", "rash")
    print(f"  disagreement demo -> baseline={r.baseline.label} "
          f"model={r.model.label} => defer={r.defer} (route to human)")


if __name__ == "__main__":
    _demo()
