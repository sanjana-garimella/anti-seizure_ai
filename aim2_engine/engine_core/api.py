"""
api.py
------
Public entry point for the Aim 2 rules engine. Pure function: an
IntervalSnapshot in, a Recommendation out. No Django, no NLP/LLM, no I/O.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from .version import ENGINE_VERSION
from .snapshot import IntervalSnapshot
from .asm_rules import ASMRule, get_rule
from .categorize import categorize, PatientStatus
from .decision import decide, Decision, RecommendationCategory
from .outputs import (build_clinical_summary, build_titration_schedule,
                      build_patient_message, build_decision_provenance,
                      append_summary_check)
from .drug_identity import study_synonym_map
from .titration_config import Thresholds, default_thresholds


@dataclass
class Recommendation:
    """The engine's output object (spec §5 Recommendation)."""
    category: str                                  # RecommendationCategory value
    titration_schedule: Optional[Dict[str, Any]]
    clinical_summary: str
    draft_patient_message: Optional[str]
    safety_concern_flag: bool
    rationale: List[Dict[str, Any]]
    requires_physician_review: bool = True
    switch_target: Optional[str] = None
    switch_family_note: Optional[str] = None     # annotation only (drug_identity)
    medication_recognized: bool = True
    medication_flag: Optional[str] = None         # set when the med name is unknown
    urgent_flags: List[str] = field(default_factory=list)
    engine_version: str = ENGINE_VERSION
    # --- Barbour review additions ---
    pace: Optional[str] = None                    # faster | standard | slower (T4/T16)
    followup_weeks: Optional[int] = None          # recommended digital re-check (T8)
    safety_notes: List[str] = field(default_factory=list)   # advisory flags (T6/T11)
    switch_exclusions: List[str] = field(default_factory=list)  # do-not-switch-to (T6)
    adherence_support: bool = False               # add adherence tips to message (T9)
    reason_summary: Optional[str] = None          # one-line "why" for the physician (T15)
    family_prompt: Optional[str] = None           # PI-supplied preference/adjustment question (Barbour 2026-06)
    lab_orders: List[str] = field(default_factory=list)  # Barbour 2026-06-25 no-seizures table: labs to order
    dosing_note: Optional[str] = None             # Barbour 2026-06-25: e.g. "consider uneven dosing"
    guidance_steps: Optional[str] = None          # Barbour 2026-06-25 breakthrough tables: verbatim clinician steps
    # Urgency tier of THIS breakthrough (non_urgent|urgent|emergent), not "the tier
    # the firing rule tagged": taken from the decision when the path sets one, else
    # from the classifier. Which rule fired is in `rationale`.
    urgency_tier: Optional[str] = None
    sources: Optional[Dict[str, Any]] = None      # provenance: rules fired + every source the decision used

    # Convenience triage signals mirrored from categorization (the Django
    # adapter turns these into Flag rows).
    seizure_activity: bool = False
    wants_med_change: bool = False
    dose_exceeds_max: bool = False        # reported dose above the recommended max

    def to_dict(self) -> Dict[str, Any]:
        # E5: serialize EVERY dataclass field (previously hand-listed, which had
        # dropped fields in the past). dataclasses.asdict() stays in sync with the
        # dataclass definition automatically, so no audit field is silently lost.
        return dataclasses.asdict(self)


def run_engine(
    snapshot: IntervalSnapshot,
    asm_rule: Optional[ASMRule] = None,
    asm_registry: Optional[Dict[str, ASMRule]] = None,
    physician_name: str = "[physician name]",
    drug_classifier: Optional[Any] = None,
    cfg: Optional[Thresholds] = None,
) -> Recommendation:
    """
    Run the deterministic engine over one interval snapshot.

    `asm_rule` (or a lookup in `asm_registry` by medication name) supplies the
    clinician dosing values. If neither is provided, the engine still returns a
    category + rationale, but no titration numbers are fabricated.

    `drug_classifier` (optional engine_core.drug_identity.DrugClassifier) is used
    ONLY to ANNOTATE a switch recommendation with whether the proposed switch
    target is the same drug family as the current medication. It never changes
    the category - the decision stays purely rules-based.
    """
    if asm_rule is None:
        asm_rule = get_rule(snapshot.medication.medication, asm_registry)
    cfg = cfg or default_thresholds()

    status: PatientStatus = categorize(snapshot, asm_rule, cfg)
    decision: Decision = decide(status, cfg)

    schedule = build_titration_schedule(snapshot, decision, asm_rule, cfg)
    summary = build_clinical_summary(snapshot, status, decision)
    message = build_patient_message(snapshot, decision, physician_name=physician_name)
    sources = build_decision_provenance(snapshot, decision, asm_rule, cfg,
                                        engine_version=ENGINE_VERSION)

    # Unknown-medication check - flag (echoing the name) when the medication is
    # not one of the in-study ASMs. Recognition is asserted only when a reference
    # is available (the study med list, a classifier, or supplied ASM rules), so
    # we never raise a false "unknown" when no reference exists.
    med_name = snapshot.medication.medication
    medication_recognized = True
    medication_flag = None
    synmap = study_synonym_map()                 # {generic/brand/abbrev -> generic}
    known_generics = set(synmap.values())
    raw = (med_name or "").strip().lower()
    if synmap or asm_rule or asm_registry:
        canonical = drug_classifier.normalize(med_name).canonical if drug_classifier else None
        in_registry = bool(asm_rule) or bool(asm_registry and raw in asm_registry)
        recognized = (raw in synmap) or (canonical in known_generics) or in_registry
        if not recognized:
            medication_recognized = False
            medication_flag = (
                f"Unrecognized medication '{med_name}' - not one of the "
                f"{len(known_generics)} study ASMs. Verify the medication entry before acting; "
                "no medication-specific dosing or family checks are available for it.")
            summary = append_summary_check(summary, "Medication", medication_flag)
            decision.rationale.append({
                "rule": "MED", "description": "Unrecognized medication name flagged.",
                "inputs": {"medication": med_name}})

    # Optional same-family ANNOTATION on switch recommendations (not a decision).
    switch_family_note = None
    if (drug_classifier is not None
            and decision.category == RecommendationCategory.SWITCH_MEDICATION
            and decision.switch_target):
        current = snapshot.medication.medication
        res = drug_classifier.same_family(current, decision.switch_target)
        by = res.get("basis")
        if res.get("same") is True:
            switch_family_note = (
                f"Proposed switch target '{decision.switch_target}' is the SAME "
                f"{by} family as current '{current}' (shared: {', '.join(res.get('shared') or [])}). "
                "Confirm intent - a different family is often preferred.")
        elif res.get("same") is False:
            switch_family_note = (
                f"Proposed switch target '{decision.switch_target}' is a DIFFERENT "
                f"{by} family from current '{current}'.")
        else:
            switch_family_note = ("Drug-family check unavailable "
                                  f"({res.get('reason', 'no data')}) - confirm manually.")
        summary = append_summary_check(summary, "Drug family", switch_family_note)

    return Recommendation(
        category=decision.category.value,
        titration_schedule=schedule,
        clinical_summary=summary,
        draft_patient_message=message,
        safety_concern_flag=decision.safety_concern_flag,
        rationale=decision.rationale,
        requires_physician_review=decision.requires_physician_review,
        switch_target=decision.switch_target,
        switch_family_note=switch_family_note,
        medication_recognized=medication_recognized,
        medication_flag=medication_flag,
        urgent_flags=decision.urgent_flags,
        pace=decision.pace,
        followup_weeks=decision.followup_weeks,
        lab_orders=decision.lab_orders,
        dosing_note=decision.dosing_note,
        guidance_steps=decision.guidance_steps,
        # The tier the recommendation was taken from, falling back to the
        # CLASSIFIER's tier. decision.urgency_tier is only set on the
        # breakthrough-table path, so without this fallback an EMERGENT case
        # (whole-body >3 min, which routes through the danger rule) reported no
        # tier at all - the review queue could never classify one, and the badge,
        # filter and count for the most severe tier were dead. Set here so every
        # consumer (queue column, note, audit) reads the same value.
        urgency_tier=(decision.urgency_tier or status.breakthrough_tier),
        safety_notes=decision.safety_notes,
        switch_exclusions=decision.switch_exclusions,
        adherence_support=decision.adherence_support,
        reason_summary=decision.reason_summary,
        family_prompt=decision.family_prompt,
        sources=sources,
        seizure_activity=status.seizure_activity,
        wants_med_change=status.wants_med_change,
        dose_exceeds_max=status.dose_exceeds_max,
    )
