"""
engine_core
-----------
Deterministic, rules-based medication assistant for Aim 2 of the Pediatric
Epilepsy AI Medication Assistant study.

NO NLP / NO LLM / NO invented clinical values (see ../NEEDS_PI_SIGNOFF.md and
../COMPLIANCE.md). Decoupled from Django - takes a plain IntervalSnapshot and
returns a plain Recommendation, so it is unit-testable and auditable in
isolation and reusable if the study later moves to CareEvolution.

Typical use:

    from engine_core import run_engine, snapshot_from_interval
    from engine_core.asm_rules import load_asm_rules

    rules = load_asm_rules("data/asm_rules.json")          # clinician-supplied
    snap = snapshot_from_interval(patient, med, contingency, answers)
    rec = run_engine(snap, asm_registry=rules)
"""

from .version import ENGINE_VERSION
from .snapshot import (
    PatientContext, MedicationContext, PhysicianContingency, IntervalSurvey,
    IntervalSnapshot, survey_from_answers, snapshot_from_interval,
    SeizureControl, SideEffectSeverity, Adherence, DoseGoalStatus,
    FamilyPreference, OverallChange, SeizureTrend, RashSeverity,
)
from .asm_rules import ASMRule, DoseBand, load_asm_rules, get_rule, ASM_RULES
from .drug_identity import (
    DrugClassifier, NormalizedDrug, load_synonyms, load_classes,
)
from .categorize import categorize, PatientStatus, IMPACT_TO_SEVERITY, SJS_TRIGGER_RASH
from .decision import decide, Decision, RecommendationCategory
from .api import run_engine, Recommendation

__all__ = [
    "ENGINE_VERSION",
    "PatientContext", "MedicationContext", "PhysicianContingency", "IntervalSurvey",
    "IntervalSnapshot", "survey_from_answers", "snapshot_from_interval",
    "SeizureControl", "SideEffectSeverity", "Adherence", "DoseGoalStatus",
    "FamilyPreference", "OverallChange", "SeizureTrend", "RashSeverity",
    "ASMRule", "DoseBand", "load_asm_rules", "get_rule", "ASM_RULES",
    "DrugClassifier", "NormalizedDrug", "load_synonyms", "load_classes",
    "categorize", "PatientStatus", "IMPACT_TO_SEVERITY", "SJS_TRIGGER_RASH",
    "decide", "Decision", "RecommendationCategory",
    "run_engine", "Recommendation",
]
