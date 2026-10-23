"""
humanize.py
-----------
Plain-English labels for the human-facing outputs. The engine keeps its
machine-readable values (category codes, rule IDs, snake_case tokens) for the
audit trail and storage; this module is used only to DISPLAY them to people.

One place to edit wording, so the clinical note, the UI, and any future view
all read the same.
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional

CATEGORY_HUMAN = {
    "escalate": "Increase the dose",
    "slower_uptitration": "Increase the dose more slowly",
    "switch_medication": "Consider switching to a different medicine",
    "add_medication": "Consider adding a second medicine (combination therapy)",
    "lower_dose": "Lower the dose",
    "hold": "Hold at the current dose (pause increases)",
    "no_change": "Keep the current dose (no change)",
    "insufficient_data": "Needs clinician review - not enough information to suggest a change",
}

SEIZURE_TYPE_HUMAN = {
    "brief_staring": "brief staring spells",
    "sudden_jerks": "quick jerks",
    "stiffening": "stiffening of the body",
    "whole_body": "whole-body shaking or stiffening",
    "whole_body_shaking": "whole-body shaking or stiffening",
    "focal": "one part of the body shaking or twitching",
    "focal_movements": "one part of the body shaking or twitching",
    "acting_off": "acting off or not responding appropriately, without shaking or stiffening",
    "not_sure": "not sure",
    "other": "other (described by family)",
}

# Side-effect tokens -> friendly labels (display only; severity comes from the
# impact scale, not the token). 2026-06: added "acting confused/off".
SIDE_EFFECT_HUMAN = {
    "acting_confused": "acting confused or 'off'",
    "acting_confused_or_off": "acting confused or 'off'",
    "sleepy": "sleepiness",
    "dizzy": "dizziness",
    "mood": "mood changes",
    "behavior": "behavior changes",
    "irritable": "irritability",
    "word_finding": "word-finding trouble",
    "nausea": "nausea",
    "rash": "rash",
    "other": "other (described by family)",
}

IMPACT_HUMAN = {
    # legacy 4-option scale
    "no_impact": "no impact on daily life",
    "small": "a small impact",
    "medium_big": "a lot of impact (still tolerable)",
    "big_wants_change": "a big impact - family would like a change",
    # new 6-level scale (Barbour 2026-06-25)
    "medium_tolerable": "a medium impact (noticeable but manageable)",
    "medium_alot": "a medium impact affecting daily life a lot",
    "big": "a big impact",
    "not_a_fit": "this medicine does not seem to be a fit",
}

ADHERENCE_HUMAN = {
    "almost_never_misses": "almost never misses a dose",
    "sometimes_misses": "sometimes misses doses",
    "stopped": "has stopped taking it",
}

PREFERENCE_HUMAN = {
    "no_strong_preference": "no strong preference (defers to the team)",
    "change_dose": "would prefer a slower or changed dose",
    "try_different_medicine": "would like to try a different medicine",
    "other": "other",
}

OVERALL_HUMAN = {
    "better": "better", "about_the_same": "about the same",
    "worse": "worse", "not_sure": "not sure",
}

RASH_HUMAN = {
    "small_rash": "small / localized rash",
    "large_rash": "large rash",
    "blistering": "spreading or blistering rash",
}

URGENT_FLAG_HUMAN = {
    "sjs_rash": "Possible serious skin reaction (SJS/TEN), urgent",
    "seizure_over_5_min": "Seizure lasting longer than 5 minutes, urgent",
    "whole_body_over_3min": "Whole-body (generalized) seizure lasting longer than 3 minutes, urgent",
    "repeated_seizures": "Repeated seizures without recovery, urgent",
    "breathing_or_unresponsive": "Breathing trouble or not waking, urgent",
    "er_or_hospital": "Recent ER or hospital visit",
    "er_visit": "Recent ER or hospital visit",
}


def humanize(token: Any, mapping: Optional[Dict[str, str]] = None) -> str:
    """Friendly label for a token: use the map if given, else de-snake-case it."""
    if token is None:
        return ""
    s = str(token)
    if mapping and s in mapping:
        return mapping[s]
    return s.replace("_", " ").strip()


def humanize_list(tokens, mapping: Optional[Dict[str, str]] = None) -> str:
    return ", ".join(humanize(t, mapping) for t in (tokens or []))


def category_label(category: str) -> str:
    return CATEGORY_HUMAN.get(category, humanize(category))


_PROPOSAL_RE = re.compile(r"\s*\((?:proposal example|see)[^)]*\)", re.I)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:])")


def human_reasons(rationale: List[Dict[str, Any]]) -> List[str]:
    """Plain-English reason sentences from the rationale, with the developer
    bits removed (no [R#] codes, no '(proposal example N)' references) wherever
    they appear, and spacing tidied."""
    out = []
    for r in rationale or []:
        desc = _PROPOSAL_RE.sub("", (r.get("description") or ""))
        desc = _SPACE_BEFORE_PUNCT.sub(r"\1", desc).strip()
        if desc:
            out.append(desc)
    return out
