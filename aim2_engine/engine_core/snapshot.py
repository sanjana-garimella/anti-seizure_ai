"""
snapshot.py
-----------
The Aim 2 engine's INPUT contract: a plain-Python "interval snapshot" assembled
from a patient's record plus their latest interval check-in survey (spec §4, §9).

Nothing here is Django-specific. The snapshot can be built from ORM objects,
a CareEvolution export, or a JSON payload - see `snapshot_from_interval()` for
the mapping from the §7 interval-survey answer codes.

IMPORTANT (spec §11): the enums below encode SURVEY-DERIVED STATES (what the
family reported), not clinical values. No dose ranges, rates, thresholds, or
safety copy live in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------------
# Survey-derived state enums  (categories A-E and preferences, per spec §9)
# ---------------------------------------------------------------------------

class SeizureControl(Enum):          # (A)
    SEIZURE_FREE = "seizure_free"
    BREAKTHROUGH = "breakthrough"


class SideEffectSeverity(Enum):      # (B)
    NONE = "none"
    MANAGEABLE_UNWANTED = "manageable_but_unwanted"
    INTOLERABLE = "intolerable"
    # An impact_scale token the taxonomy map does not recognize. Defer-on-
    # ambiguity: this is NOT an actionable severity - the decision layer routes
    # it to INSUFFICIENT_DATA rather than guessing "manageable" (E3).
    UNKNOWN = "unknown"


class Adherence(Enum):               # (C)
    GOOD = "almost_never_misses"
    PARTIAL = "sometimes_misses"
    STOPPED = "stopped"


class DoseGoalStatus(Enum):          # (E) - from Q10a, survey-reported
    STILL_INCREASING = "still_increasing"
    AT_GOAL = "at_goal"
    NOT_SURE = "not_sure"


class FamilyPreference(Enum):        # Q12
    NO_STRONG_PREFERENCE = "no_strong_preference"
    CHANGE_DOSE = "change_dose"
    TRY_DIFFERENT_MED = "try_different_medicine"
    OTHER = "other"


class OverallChange(Enum):           # Q11
    BETTER = "better"
    SAME = "about_the_same"
    WORSE = "worse"
    NOT_SURE = "not_sure"


class SeizureTrend(Enum):            # Q6
    FEWER = "fewer"
    SAME = "about_the_same"
    MORE = "more_frequent"
    NEW_TYPE = "new_type"


class RashSeverity(Enum):            # Q9a - option labels normalized; the
    SMALL = "small_rash"             # SJS trigger mapping is PI-supplied
    LARGE = "large_rash"             # (see NEEDS_PI_SIGNOFF.md). The engine
    BLISTERING = "blistering"        # only reads which option was chosen.


# ---------------------------------------------------------------------------
# Pure helpers (NO clinical thresholds - just survey-option ordering + unit math)
# ---------------------------------------------------------------------------

# Ordinal rank of the seizure-duration buckets (longer = higher). This encodes
# only the ORDER of the survey answer options. The clinical thresholds that act
# on duration (e.g. the >3-minute danger line) live in
# data/titration_thresholds.json and in the decision layer - never here.
#
# CANONICAL buckets (Barbour 2026-06 re-cut): under_1min / 1_3min / over_3min.
#   under_1min = 1   (< 1 minute  - "going up to goal, fine")
#   1_3min     = 2   (1 to 3 minutes)
#   over_3min  = 3   (> 3 minutes - whole-body = danger; see thresholds)
# Legacy buckets are kept mapped to the nearest new rank for back-compat with
# any previously stored data.
DURATION_RANK: Dict[Optional[str], int] = {
    None: 0, "not_sure": 0,
    "under_1min": 1, "under_1_min": 1,
    "1_3min": 2, "1_3_min": 2,
    "over_3min": 3, "over_3_min": 3,
    # 2026-06-25: a finer top bucket for the EMERGENT urgency line (>5 minutes).
    # Rank 4 is additive (above over_3min=3), so the existing >=2 / >=3 thresholds
    # (escalate_faster, whole_body_danger) are unaffected.
    "over_5min": 4, "over_5_min": 4,
    # 2026-06-25 PRECISE per-type urgency: threshold-aligned fine buckets. Ranks
    # keep the minute-based legacy logic sensible (whole-body danger = rank >=3
    # i.e. >=3 min); the exact-seconds classification uses DURATION_SECONDS below.
    "under_20s": 1, "20_30s": 1, "30_90s": 2, "90s_3min": 2, "3_5min": 3,
    # --- legacy keys (pre-2026-06 buckets) -> nearest new rank ---
    "under_31s": 1,
    "31s_2min": 2, "1_5_min": 2,
    "2_5min": 3,
}

# 2026-06-25: minimum seconds each duration bucket represents, for the PRECISE
# per-seizure-type urgency thresholds (20/30/90/180/300 s). Because every
# threshold falls on a bucket boundary, comparing this floor with ">= threshold"
# classifies exactly (if the floor >= threshold, the whole bucket does too).
DURATION_SECONDS: Dict[Optional[str], Optional[int]] = {
    None: None, "not_sure": None,
    "under_20s": 0, "20_30s": 20, "30_90s": 30, "90s_3min": 90,
    "3_5min": 180, "over_5min": 300, "over_5_min": 300,
    # legacy minute buckets -> conservative floor in seconds
    "under_1min": 0, "under_1_min": 0, "1_3min": 60, "1_3_min": 60,
    "over_3min": 180, "over_3_min": 180,
}

# Seizure-frequency RANGE buckets (Barbour 2026-06), with the per-4-week
# definitions she gave, encoded as a representative per-week rate. This is a
# faithful arithmetic encoding of HER bucket boundaries (>28/4wk, >4/4wk,
# >=1/4wk, <1/4wk), not an invented clinical value. The decision layer compares
# these against the clinician-supplied frequency thresholds. Representative rate
# sits just inside each band so the ">" boundaries behave as she described.
FREQUENCY_BUCKET_PER_WEEK: Dict[str, float] = {
    "daily": 7.0,                 # "Daily" = more than 28 per 4 weeks (>7/week)
    "weekly": 2.0,                # "Weekly" = more than 4 per 4 weeks (>1/week)
    "monthly": 0.25,              # "Monthly" = 1 or more per 4 weeks (~0.23/week)
    "less_than_monthly": 0.1,     # "Less than 1 per month"
}


def seizures_per_week(count: Optional[float], unit: Optional[str]) -> Optional[float]:
    """Convert a reported seizure rate to a per-week number (pure arithmetic,
    no clinical value). Returns None when the count or unit is missing/unknown
    so the decision layer DEFERS rather than guessing."""
    if count is None or not isinstance(count, (int, float)):
        return None
    u = (unit or "").strip().lower()
    if u in ("per_day", "day", "daily"):
        return float(count) * 7.0
    if u in ("per_week", "week", "weekly"):
        return float(count)
    if u in ("per_month", "month", "monthly"):
        return float(count) / 4.345
    return None


def frequency_per_week(count: Optional[float], unit: Optional[str],
                       bucket: Optional[str] = None) -> Optional[float]:
    """Resolve a per-week seizure rate from EITHER an explicit count+unit OR a
    frequency RANGE bucket (Barbour 2026-06: daily/weekly/monthly/less_than_monthly).
    Prefers the explicit number when present; falls back to the bucket's
    representative rate. Returns None when neither is usable so the decision
    layer DEFERS rather than guessing."""
    pw = seizures_per_week(count, unit)
    if pw is not None:
        return pw
    if bucket is not None:
        return FREQUENCY_BUCKET_PER_WEEK.get(str(bucket).strip().lower())
    return None


_DOSING_CHOICES = ("uneven", "tid", "wait", "add")


def _dosing_choice(v: Any) -> Optional[str]:
    """Level-3 family choice token, or None (anything unrecognised is ignored)."""
    t = str(v or "").strip().lower()
    return t if t in _DOSING_CHOICES else None


def per_dose_mg(amount: Optional[float], unit: Optional[str],
                concentration: Optional[float]) -> Optional[float]:
    """Convert one reported dose to mg (pure unit arithmetic - no clinical value).

    * mg (or any non-liquid/tablet unit): the amount IS the mg.
    * mL / tablets: needs a strength (concentration mg per mL/tablet); returns
      None when the strength is missing (cannot convert -> the caller defers /
      reports "not computable" rather than inventing a number).

    Single source of truth for the tablet/mL->mg conversion (E9), used by both
    MedicationContext.total_daily_dose and outputs._dose_line so the two can't
    drift apart.
    """
    if amount is None or not isinstance(amount, (int, float)):
        return None
    if (unit or "mg") in ("mL", "ml", "tablets"):
        if not concentration or not isinstance(concentration, (int, float)):
            return None
        return amount * concentration
    return amount   # already in mg


# ---------------------------------------------------------------------------
# Snapshot dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PatientContext:
    """From the patient record / physician intake (spec §5).

    The fields below the original block were added from Dr. Barbour's
    2026-06 review (see PROJECT_LOG.md themes T6, T11). They are
    survey/record-derived FACTS about the patient, not clinical values:
      * concomitant_asms / concomitant_other_meds - other current medicines
        (drives lamotrigine regimen selection + the small-rash polypharmacy
        check; "other meds" already excludes the benign categories Kristen
        named - vitamins, asthma/allergy, GERD, bowel regimen - at capture);
      * prior_asms_tried - medicines already tried (excluded as switch targets);
      * behavioral_comorbidities - e.g. autism/depression/anxiety/aggression
        (a contraindication flag for levetiracetam);
      * southeast_asian_ancestry - HLA-B*1502 SJS risk modifier (lamotrigine/
        carbamazepine small-rash workup). Stored only as a boolean risk flag.
    """
    external_id: str
    age_years: Optional[float] = None
    weight_kg: Optional[float] = None
    sex: Optional[str] = None
    epilepsy_etiology: Optional[str] = None
    seizure_types: List[str] = field(default_factory=list)
    site: Optional[str] = None
    concomitant_asms: List[str] = field(default_factory=list)
    concomitant_other_meds: List[str] = field(default_factory=list)
    prior_asms_tried: List[str] = field(default_factory=list)
    behavioral_comorbidities: List[str] = field(default_factory=list)
    southeast_asian_ancestry: Optional[bool] = None

    def __post_init__(self):
        # Defensive: age/weight feed `:g`-formatted output text throughout
        # outputs.py/dose_ladder.py. A non-numeric value (bad intake data)
        # should degrade to "not recorded" rather than crash note rendering.
        if self.age_years is not None and not isinstance(self.age_years, (int, float)):
            self.age_years = None
        if self.weight_kg is not None and not isinstance(self.weight_kg, (int, float)):
            self.weight_kg = None


@dataclass
class MedicationContext:
    """
    Current ASM the interval survey is about (from PatientMedication + Q1/Q1a).
    `total_daily_dose` is derived from the family's reported dose; it is NOT a
    clinical recommendation. `is_goal_reached` mirrors Q10a / the record - it
    is survey/record-reported, not computed by the engine.
    """
    medication: str                              # generic name (identifier)
    dose_amount: Optional[float] = None          # amount each time (reported)
    dose_unit: Optional[str] = None              # "mg" | "mL" | "tablets"
    formulation: Optional[str] = None            # "tablet" | "liquid" | ...
    frequency_per_day: Optional[int] = None
    concentration: Optional[float] = None        # mg per tablet or mg/mL
    goal_dose: Optional[float] = None            # clinician-set, from intake
    is_goal_reached: Optional[bool] = None
    dose_changed_or_stopped: bool = False
    change_description: Optional[str] = None
    # How long the child has been ON this medication (survey/record bucket):
    # "under_2_weeks" | "2_4_weeks" | "4_12_weeks" | "3_6_months" | "over_6_months".
    # Drives the switch-when-not-helpful timing and the lamotrigine small-rash
    # workup (Kristen review, themes T5/T11). A bucket label, not a threshold.
    time_on_medication: Optional[str] = None

    def __post_init__(self):
        # Defensive: the medication name is used in string ops throughout
        # (.lower/.replace/.title). Coerce a non-string identifier to str so
        # malformed input degrades gracefully instead of crashing the engine.
        if self.medication is not None and not isinstance(self.medication, str):
            self.medication = str(self.medication)

    @property
    def total_daily_dose(self) -> Optional[float]:
        """Reported total daily dose (mg) when computable; else None.

        This only echoes what the family reported (amount x frequency, or
        amount x concentration x frequency for liquids). It introduces no
        clinical value of its own.
        """
        if (self.dose_amount is None or not self.frequency_per_day
                or not isinstance(self.frequency_per_day, (int, float))):
            return None
        # E9: shared tablet/mL->mg conversion (returns None when a liquid/tablet
        # strength is missing, so no dose number is invented).
        per = per_dose_mg(self.dose_amount, self.dose_unit, self.concentration)
        if per is None:
            return None
        return per * self.frequency_per_day


@dataclass
class PhysicianContingency:
    """
    Pre-specified physician options from intake (spec §9). The engine NEVER
    invents these - they come from the intake instrument. Aim 2 depends on
    this data being present (flagged in NEEDS_PI_SIGNOFF.md).
    """
    can_increase_dose: Optional[bool] = None
    can_add_medication: Optional[str] = None
    can_switch_to: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class IntervalSurvey:
    """Structured answers from the §7 interval check-in (survey-derived)."""
    # Q2 / Q2a
    adherence: Adherence = Adherence.GOOD
    missed_dose_reasons: List[str] = field(default_factory=list)
    stopped_reason: Optional[str] = None              # if STOPPED: "side_effects"|"cost"|... (Kristen T10)
    # Q3-Q7
    seizure_control: SeizureControl = SeizureControl.SEIZURE_FREE
    seizure_types: List[str] = field(default_factory=list)
    seizure_count: Optional[int] = None
    seizure_count_unit: Optional[str] = None          # per_day|per_week|per_month
    most_recent_seizure_date: Optional[str] = None    # Q5b (time bucket: under_2_weeks|2_4_weeks|over_4_weeks)
    seizure_trend: Optional[SeizureTrend] = None
    on_medication_2days_prior: Optional[bool] = None  # Q7
    # Per-seizure-type detail (Kristen's #1 ask, themes T1 + 2026-06 update).
    # For EACH reported seizure type we now ask: how long, how often, and are
    # they unresponsive? Each dict:
    #   {"look": [types], "duration": <bucket>, "frequency": <int>,
    #    "frequency_unit": <unit>, "frequency_bucket": <bucket>, "unresponsive": <bool>}
    # duration bucket (2026-06 re-cut): "under_1min" | "1_3min" | "over_3min" | "not_sure".
    # frequency_bucket: "daily" | "weekly" | "monthly" | "less_than_monthly".
    seizure_episodes: List[Dict[str, Any]] = field(default_factory=list)
    seizure_duration: Optional[str] = None            # overall typical length bucket (same vocabulary)
    # Possible triggers before the breakthrough seizures (Barbour 2026-06-25):
    # tokens lack_of_sleep | missed_dose | fever_illness | <free text>, and whether
    # a trigger was present before ALL of the breakthrough seizures (follow-up).
    seizure_triggers: List[str] = field(default_factory=list)
    triggers_all_episodes: Optional[bool] = None
    # Q8-Q10c
    side_effects_present: List[str] = field(default_factory=list)
    side_effects_other: Optional[str] = None          # free-text "Other" side effect (parsed by NLP layer)
    rash_severity: Optional[RashSeverity] = None
    dose_goal_status: DoseGoalStatus = DoseGoalStatus.NOT_SURE   # Q10a
    impact_scale: Optional[str] = None
    # "no_impact" | "small" | "medium_big" | "big_wants_change"  (Q10b/Q10c)
    time_at_current_dose: Optional[str] = None        # Q10c free text
    # Barbour 2026-06 add-vs-switch inputs (survey-reported facts, not clinical values):
    #   dose_limited_by_side_effects: side effects prevent FURTHER dose escalation
    #     (not yet at goal/max, but cannot push higher) -> add-on candidate if helping.
    #   min_goal_unreachable_due_to_se: cannot reach even a minimum effective dose
    #     because of side effects -> switch candidate.
    dose_limited_by_side_effects: Optional[bool] = None
    min_goal_unreachable_due_to_se: Optional[bool] = None
    # Barbour F1/B3 (2026-07-16): family-reported dose the child is BUILDING UP TO on
    # their current titration plan (total mg/day). Used to compute "end +1/+2 increases
    # higher" for urgent/emergent titrating plans (see outputs._attach_ladder). Optional.
    planned_end_total_mg_per_day: Optional[float] = None
    # Dr. Barbour 2026-10-25 (#52): at Level-3 side effects the FAMILY picks how to
    # handle them, from the options the decision-table cell lists (her template H):
    # "uneven" (smaller morning / larger night dose) | "tid" (three times a day) |
    # "wait" (watch and wait) | "add" (lower the dose and add a second medication).
    # A structured survey answer (not free text). None = not answered.
    side_effect_dosing_choice: Optional[str] = None
    # Q11-Q13
    overall_change: Optional[OverallChange] = None    # Q11 - also read as "is the med helping" (T5)
    family_preference: FamilyPreference = FamilyPreference.NO_STRONG_PREFERENCE
    # Barbour 2026-06-25: family's ranked preference among management directions
    # (tokens: increase | add | switch) + how strongly they feel. Advisory only -
    # surfaced to the physician; the engine does not auto-act on it.
    med_pref_rank: List[str] = field(default_factory=list)
    med_pref_strength: Optional[str] = None
    free_text: Optional[str] = None
    recent_viral_illness: Optional[bool] = None       # cold/viral symptoms (lamotrigine rash workup, T11)
    # On another medication independently linked to SJS/TEN at the same time
    # (antibiotic - esp. sulfonamides/penicillins/cephalosporins; NSAID; or
    # allopurinol) - a more specific signal than just "on >1 medication
    # total" for the small-rash workup (T11/R4). Distinct from the generic
    # concomitant-med COUNT criterion in PatientContext, which still applies
    # regardless of which drugs they are.
    on_known_sjs_risk_comed: Optional[bool] = None
    # Safety: any client-side urgent answers (e.g. SJS rash trigger) the survey
    # already raised. The engine re-derives its own flags; this is belt-and-braces.
    urgent_safety_reported: List[str] = field(default_factory=list)


@dataclass
class IntervalSnapshot:
    """Complete input for one engine run."""
    patient: PatientContext
    medication: MedicationContext
    survey: IntervalSurvey
    contingency: PhysicianContingency = field(default_factory=PhysicianContingency)
    snapshot_id: Optional[str] = None
    taken_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Mapping from the §7 interval-survey answer codes -> IntervalSurvey
# (the integration seam with the Aim 1 survey platform - see DATA_CONTRACT.md)
# ---------------------------------------------------------------------------

# Value maps keyed by the structured option `value` strings the survey stores.
_ADHERENCE = {"almost_never": Adherence.GOOD, "sometimes": Adherence.PARTIAL,
              "stopped": Adherence.STOPPED}
_SEIZ = {"no": SeizureControl.SEIZURE_FREE, "yes": SeizureControl.BREAKTHROUGH}
_TREND = {"fewer": SeizureTrend.FEWER, "about_same": SeizureTrend.SAME,
          "more": SeizureTrend.MORE, "new_type": SeizureTrend.NEW_TYPE}
_RASH = {"small": RashSeverity.SMALL, "large": RashSeverity.LARGE,
         "blisters": RashSeverity.BLISTERING}
_GOAL = {"still_increasing": DoseGoalStatus.STILL_INCREASING,
         "at_goal": DoseGoalStatus.AT_GOAL, "not_sure": DoseGoalStatus.NOT_SURE}
_OVERALL = {"better": OverallChange.BETTER, "about_same": OverallChange.SAME,
            "worse": OverallChange.WORSE, "not_sure": OverallChange.NOT_SURE}
_PREF = {"no_preference": FamilyPreference.NO_STRONG_PREFERENCE,
         "change_dose": FamilyPreference.CHANGE_DOSE,
         "different_medicine": FamilyPreference.TRY_DIFFERENT_MED,
         "other": FamilyPreference.OTHER}


def _enum(mapping, value, default=None):
    if value is None:
        return default
    return mapping.get(str(value).strip().lower(), default)


def _to_bool(value):
    """Coerce a yes/no answer to a real bool. Accepts actual bools and the
    common string/int forms a survey platform might send ("true"/"false",
    "yes"/"no", 1/0). Returns None when unknown/absent - so a malformed Q7
    can never be silently read as the wrong boolean (which would skip the
    adherence-first safety rule)."""
    if value is None or isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


def _to_float(value):
    """Coerce a numeric answer (e.g. a family-reported dose in mg/day) to float;
    None when absent/unparseable, so a blank/garbled entry is ignored rather than
    guessed."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def survey_from_answers(answers: Dict[str, Any]) -> IntervalSurvey:
    """
    Build an IntervalSurvey from a dict of structured answer values keyed by
    §7 question code. Expected keys (all optional; missing -> sensible default):

      Q2 adherence, Q2a missed_reasons[], Q3 seizures, Q4 seizure_types[],
      Q5 seizure_count, Q5_unit, Q5b last_date, Q6 trend, Q7 on_med_2d,
      Q9 side_effects[], Q9a rash, Q10a goal_status, Q10b_impact / Q10c_impact,
      Q10c_time, Q11 overall, Q12 preference, Q13 free_text,
      urgent_safety[] (e.g. SJS rash trigger already raised by the survey)

    Impact: Q10b and Q10c share one scale; whichever is present is used.
    """
    impact = answers.get("Q10b_impact") or answers.get("Q10c_impact")
    return IntervalSurvey(
        adherence=_enum(_ADHERENCE, answers.get("Q2"), Adherence.GOOD),
        missed_dose_reasons=list(answers.get("Q2a", []) or []),
        stopped_reason=answers.get("Q2b") or answers.get("stopped_reason"),
        seizure_control=_enum(_SEIZ, answers.get("Q3"), SeizureControl.SEIZURE_FREE),
        seizure_types=list(answers.get("Q4", []) or []),
        seizure_count=answers.get("Q5"),
        seizure_count_unit=answers.get("Q5_unit"),
        most_recent_seizure_date=answers.get("Q5b"),
        seizure_trend=_enum(_TREND, answers.get("Q6")),
        on_medication_2days_prior=_to_bool(answers.get("Q7")),
        seizure_episodes=list(answers.get("seizure_episodes", []) or []),
        seizure_duration=answers.get("seizure_duration"),
        seizure_triggers=list(answers.get("seizure_triggers", []) or []),
        triggers_all_episodes=_to_bool(answers.get("seizure_triggers_all")),
        side_effects_present=list(answers.get("Q9", []) or []),
        side_effects_other=(answers.get("Q9_other") or answers.get("side_effects_other") or None),
        rash_severity=_enum(_RASH, answers.get("Q9a")),
        dose_goal_status=_enum(_GOAL, answers.get("Q10a"), DoseGoalStatus.NOT_SURE),
        impact_scale=impact,
        time_at_current_dose=answers.get("Q10c_time"),
        dose_limited_by_side_effects=_to_bool(answers.get("Q10d_dose_limited_by_se")),
        min_goal_unreachable_due_to_se=_to_bool(answers.get("Q10e_min_goal_unreachable_se")),
        planned_end_total_mg_per_day=_to_float(answers.get("Q10f_planned_end_mg_per_day")),
        side_effect_dosing_choice=_dosing_choice(answers.get("Q10g_dosing_choice")),
        overall_change=_enum(_OVERALL, answers.get("Q11")),
        family_preference=_enum(_PREF, answers.get("Q12"), FamilyPreference.NO_STRONG_PREFERENCE),
        med_pref_rank=list(answers.get("med_pref_rank", []) or []),
        med_pref_strength=answers.get("med_pref_strength"),
        free_text=answers.get("Q13"),
        recent_viral_illness=_to_bool(answers.get("Q14_viral_illness")),
        on_known_sjs_risk_comed=(bool(answers.get("Q_sjs_risk_comeds"))
                                  if answers.get("Q_sjs_risk_comeds") is not None else None),
        urgent_safety_reported=list(answers.get("urgent_safety", []) or []),
    )


def snapshot_from_interval(
    patient: PatientContext,
    medication: MedicationContext,
    contingency: PhysicianContingency,
    answers: Dict[str, Any],
    snapshot_id: Optional[str] = None,
    taken_at: Optional[str] = None,
) -> IntervalSnapshot:
    """Convenience: assemble a full snapshot from patient/med/intake + §7 answers."""
    return IntervalSnapshot(
        patient=patient,
        medication=medication,
        survey=survey_from_answers(answers),
        contingency=contingency,
        snapshot_id=snapshot_id,
        taken_at=taken_at,
    )
