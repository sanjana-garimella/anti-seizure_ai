"""
nlp/guardrails.py
-----------------
Safety guardrails for generated text (point D). Any generative model output
MUST pass these checks before it can be shown; otherwise the caller falls back
to the deterministic template.

The guardrails enforce that generated language only RESTATES the engine's
already-decided, structured facts (spec §11): it must be withheld on a safety
concern, must not invent dose numbers, must not contradict the recommendation
category, and must carry the draft/review notice.

Pure stdlib, fully testable offline - this is the part that makes plugging in
an LLM/BioGPT safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Any, Set

# Phrases that would contradict a given category if they appear in family text.
# Every RecommendationCategory has an entry. The phrases are checked against the
# engine's own drafts in tests/test_family_templates.py, so a phrase the template
# legitimately uses (HOLD B5 says "then increase"; template G says "rather than
# stopping it") must not be listed for that category.
_ADD_PHRASES = [
    "add a second medicine", "add a second medication",
    "add another medicine", "add another medication",
    "start a second medicine", "start a second medication",
]
_STOP_PHRASES = [
    "stop the medicine", "stop taking it", "stop taking the medicine",
    "discontinue it", "discontinue the medicine",
]
_SWITCH_PHRASES = [
    "switch", "different medicine", "different medication",
    "change to another medicine", "change to another medication",
]
_INCREASE_PHRASES = ["increase the dose", "raise the dose", "go up on the dose"]
_LOWER_PHRASES = ["lower the dose", "reduce the dose", "decrease the dose"]
_KEEP_PHRASES = ["keep the dose the same", "stay at the same dose"]
_CONTRADICTIONS = {
    "no_change": _INCREASE_PHRASES + _LOWER_PHRASES + _STOP_PHRASES
                 + _SWITCH_PHRASES + _ADD_PHRASES,
    "escalate": _LOWER_PHRASES + _STOP_PHRASES + _SWITCH_PHRASES + _ADD_PHRASES,
    "slower_uptitration": _LOWER_PHRASES + _STOP_PHRASES
                          + _SWITCH_PHRASES + _ADD_PHRASES,
    "hold": _INCREASE_PHRASES + _LOWER_PHRASES + _STOP_PHRASES
            + _SWITCH_PHRASES + _ADD_PHRASES,
    "lower_dose": _INCREASE_PHRASES + _KEEP_PHRASES + _STOP_PHRASES
                  + _SWITCH_PHRASES + _ADD_PHRASES,
    "add_medication": _INCREASE_PHRASES + _STOP_PHRASES + _SWITCH_PHRASES,
    "switch_medication": _INCREASE_PHRASES + _LOWER_PHRASES
                         + _KEEP_PHRASES + _ADD_PHRASES,
    "insufficient_data": _INCREASE_PHRASES + _LOWER_PHRASES
                         + _STOP_PHRASES + _SWITCH_PHRASES + _ADD_PHRASES,
}

# Dose number followed by a unit, abbreviated or spelled out ("900 milligrams").
_DOSE_NUMBER_RE = re.compile(
    r"(?<![\d.,])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(?:mg|ml|milligrams?|millilit(?:er|re)s?)\b")

_DISCLAIMER_MARKERS = ("draft", "physician review")


@dataclass
class GuardrailResult:
    ok: bool
    problems: List[str] = field(default_factory=list)


def _allowed_numbers(recommendation: Any, snapshot: Any) -> Set[float]:
    """Numbers the text is allowed to mention (mg/mL), from structured facts only."""
    allowed: Set[float] = set()
    sched = getattr(recommendation, "titration_schedule", None)
    if isinstance(sched, dict):
        for key in ("current_total_mg_per_day", "ceiling_mg_per_day", "interval_weeks_between_steps"):
            v = sched.get(key)
            if isinstance(v, (int, float)):
                allowed.add(float(v))
        for step in sched.get("steps", []) or []:
            for key in ("target_total_mg_per_day", "per_dose_mg", "after_weeks_at_previous"):
                v = step.get(key)
                if isinstance(v, (int, float)):
                    allowed.add(float(v))
    med = getattr(snapshot, "medication", None)
    if med is not None:
        for v in (getattr(med, "dose_amount", None), getattr(med, "concentration", None),
                  getattr(med, "goal_dose", None), med.total_daily_dose):
            if isinstance(v, (int, float)):
                allowed.add(float(v))
    # Clinician DOSE-LADDER values the family DRAFT may name (Kristen 2026-07:
    # "the engine fills the doses from your ladder"). These are engine structured
    # facts (Barbour final_doses ladder + the family-reported current/planned-end
    # dose), not model output - so the guardrail recognizes them without widening
    # what a *model* may say (any number NOT in this set is still rejected). The set
    # is produced by the SAME helper the message builder uses, so the two can't drift.
    try:
        from engine_core.outputs import family_message_allowed_doses
        allowed |= family_message_allowed_doses(snapshot, recommendation)
    except Exception:
        pass   # ladder unavailable -> keep the (stricter) schedule-only allowed set
    return allowed


def validate_message(text, recommendation, snapshot) -> GuardrailResult:
    problems: List[str] = []

    safety = bool(getattr(recommendation, "safety_concern_flag", False))
    if safety:
        # SAFETY (Dr. Barbour 2026-10-25, #51): the engine withholds the family message
        # on any safety flag (draft_patient_message is None), so no generated text
        # passes here. The equality check is kept so that, if an approved safety
        # template is ever reinstated, a model still cannot inject its own safety copy.
        approved = (getattr(recommendation, "draft_patient_message", None) or "").strip()
        if text and text.strip() != approved:
            problems.append("Safety concern: only the engine's approved safety draft may "
                            "be sent (arbitrary text rejected).")
        return GuardrailResult(ok=not problems, problems=problems)

    if not text or not text.strip():
        problems.append("Empty generated text.")
        return GuardrailResult(ok=False, problems=problems)

    low = text.lower()

    # 1) No invented dose numbers.
    allowed = _allowed_numbers(recommendation, snapshot)
    for num in _DOSE_NUMBER_RE.findall(low):
        parsed = float(num.replace(",", ""))
        if not any(abs(parsed - a) < 1e-6 for a in allowed):
            problems.append(f"Mentions dose number '{num}' not present in the "
                            f"engine's structured output (possible fabrication).")

    # 2) No category contradiction.
    cat = getattr(recommendation, "category", "")
    for bad in _CONTRADICTIONS.get(cat, []):
        if bad in low:
            problems.append(f"Text contradicts category '{cat}' (contains '{bad}').")

    # 3) Must carry a draft / review notice.
    if not any(m in low for m in _DISCLAIMER_MARKERS):
        problems.append("Missing draft/physician-review notice.")

    return GuardrailResult(ok=not problems, problems=problems)
