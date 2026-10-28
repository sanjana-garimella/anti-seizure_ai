"""
categorize.py
-------------
Turn an IntervalSnapshot into a PatientStatus: the categories A-E plus the
extra survey-derived signals Dr. Barbour's 2026-06 review introduced (seizure
duration/frequency, drug-appropriateness, rash workup, helpfulness/time-on-med).

Everything here is derived from SURVEY-REPORTED STATE + clinician-supplied data
(asm_rules.json, titration_thresholds.json). No clinical thresholds are invented
in this module: numeric thresholds come from the threshold config; the only
in-module judgment calls are the two documented taxonomy/severity maps below,
which are PI sign-off items kept in one place so they can be edited in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .snapshot import (
    IntervalSnapshot, SeizureControl, SideEffectSeverity, Adherence,
    DoseGoalStatus, FamilyPreference, RashSeverity, OverallChange,
    DURATION_RANK, DURATION_SECONDS, FREQUENCY_BUCKET_PER_WEEK,
    seizures_per_week, frequency_per_week,
)
from .asm_rules import ASMRule
from .titration_config import Thresholds, default_thresholds, default_breakthrough_urgency

# Seizure clinical class -> urgency-threshold key (breakthrough_urgency_thresholds.json).
_URGENCY_THKEY = {
    "absence": "absence", "myoclonic": "myoclonic",
    "generalized_tonic_clonic": "generalized_tonic_clonic", "tonic": "generalized_tonic_clonic",
    "focal": "focal", "focal_impaired_awareness": "focal_impaired_awareness",
    "unknown": "other", "other": "other",
}
_TIER_RANK = {"non_urgent": 0, "urgent": 1, "emergent": 2}
_RANK_TIER = {0: "non_urgent", 1: "urgent", 2: "emergent"}


def _episode_freqs(ep):
    """(per_day, per_week, per_4wk) for one episode: prefer the exact 4-week count,
    else the explicit frequency+unit, else the frequency bucket's rate. Honors the
    episode's `frequency`+`frequency_unit` via frequency_per_week() (SAFETY FIX -
    previously only count_4wk/frequency_bucket were read, dropping explicit counts
    so e.g. focal '5 per_week' with no bucket was tiered on duration alone)."""
    n = ep.get("count_4wk")
    if n is not None:
        try:
            n = float(n)
            return n / 28.0, n / 4.0, n
        except (TypeError, ValueError):
            pass
    pw = frequency_per_week(ep.get("frequency"), ep.get("frequency_unit"),
                            ep.get("frequency_bucket"))
    if pw is None:
        return None, None, None
    return pw / 7.0, pw, pw * 4.0


def validate_duration_thresholds_align(urgency=None):
    """E12: the per-episode `dur_hits` classification compares a bucket's FLOOR
    (DURATION_SECONDS) against a configured `*_duration_s` threshold with '>='.
    That is exact ONLY if every configured threshold lands on a known bucket
    floor - otherwise a seizure whose true length crosses the threshold could sit
    in a bucket whose floor is below it and be under-triaged.

    Returns the list of (seizure_type, key, seconds) thresholds that do NOT match
    any DURATION_SECONDS floor. Empty list == all aligned. Advisory (does not
    raise) so a config edit degrades loudly in tests rather than mis-triaging
    silently in production."""
    urgency = urgency if urgency is not None else default_breakthrough_urgency()
    floors = {v for v in DURATION_SECONDS.values() if v is not None}
    misaligned = []
    for stype, th in (urgency.get("by_seizure_type", {}) or {}).items():
        if not isinstance(th, dict):
            continue
        for key, val in th.items():
            if key.endswith("_duration_s") and val is not None and val not in floors:
                misaligned.append((stype, key, val))
    return misaligned


def _tier_for_episode(ep, urgency):
    """Precise non_urgent/urgent/emergent for one episode vs the per-type thresholds.
    Duration OR frequency crossing a tier's cutoff escalates to that tier."""
    by_type = urgency.get("by_seizure_type", {})
    look = (ep.get("look") or ["other"])[0]
    cls = SEIZURE_TOKEN_TO_CLASS.get(look, "other")
    th = by_type.get(_URGENCY_THKEY.get(cls, "other"))
    if not th:
        return 0
    secs = DURATION_SECONDS.get(ep.get("duration"))
    per_day, per_week, per_4wk = _episode_freqs(ep)

    def dur_hits(prefix):
        t = th.get(prefix + "_duration_s")
        return t is not None and secs is not None and secs >= t

    def freq_hits(prefix):
        for unit, val in (("per_day", per_day), ("per_week", per_week), ("per_4wk", per_4wk)):
            t = th.get(f"{prefix}_freq_{unit}")
            if t is not None and val is not None and val >= t:
                return True
        return False

    if dur_hits("emergent") or freq_hits("emergent"):
        return 2
    if dur_hits("urgent") or freq_hits("urgent"):
        return 1
    return 0


def classify_breakthrough_tier(episodes, urgency=None):
    """Highest urgency tier across all reported seizure episodes, using the precise
    per-type thresholds. Returns None when no episode detail is available (the
    caller then falls back to the coarse signal-based mapping)."""
    urgency = urgency if urgency is not None else default_breakthrough_urgency()
    if not episodes or not urgency.get("by_seizure_type"):
        return None
    rank = max((_tier_for_episode(ep, urgency) for ep in episodes), default=0)
    return _RANK_TIER[rank]


# ---------------------------------------------------------------------------
# PI-CONFIGURABLE mapping: family impact answer (Q10b/Q10c) -> severity (B).
# UPDATED per Barbour 2026-06 (theme T7): "difficult but tolerable" (medium_big)
# is MANAGEABLE, not intolerable - only an explicit "intolerable, would like to
# change" drives a switch. This is the boundary that flips slower-vs-switch.
# ---------------------------------------------------------------------------
IMPACT_TO_SEVERITY = {
    # --- legacy 4-option scale (retained for back-compat: in-flight survey data
    #     + the gold validation cases still use these tokens) ---
    "no_impact": SideEffectSeverity.NONE,
    "small": SideEffectSeverity.MANAGEABLE_UNWANTED,
    "medium_big": SideEffectSeverity.MANAGEABLE_UNWANTED,   # "difficult but tolerable" (Barbour)
    "big_wants_change": SideEffectSeverity.INTOLERABLE,
    # --- NEW 6-level scale (Barbour 2026-06-25 "No Seizures" table). The
    #     seizure-free path is driven by side_effect_level (see IMPACT_TO_LEVEL +
    #     the no_seizures_decision_table); the severity below is only used by the
    #     BREAKTHROUGH path. ASSUMPTION (flagged for PI, NEEDS_PI_SIGNOFF.md):
    #     "big" maps to MANAGEABLE (her table lowers the dose rather than
    #     switching), and only "not a fit" (L5) is INTOLERABLE -> switch. ---
    "medium_tolerable": SideEffectSeverity.MANAGEABLE_UNWANTED,   # L2
    "medium_alot": SideEffectSeverity.MANAGEABLE_UNWANTED,        # L3
    "big": SideEffectSeverity.MANAGEABLE_UNWANTED,                # L4
    "not_a_fit": SideEffectSeverity.INTOLERABLE,                  # L5
}

# NEW: the 6-level side-effect scale -> level index 0-5 (Barbour 2026-06-25).
# Only the new tokens (and the two unambiguous legacy tokens) carry a level;
# legacy "medium_big"/"big_wants_change" intentionally have NO level, so they
# fall through to the legacy 3-level seizure-free logic (gold cases unaffected).
IMPACT_TO_LEVEL = {
    "no_impact": 0, "small": 1,
    "medium_tolerable": 2, "medium_alot": 3, "big": 4, "not_a_fit": 5,
}

# Which rash option raises the SJS/TEN URGENT flag (spreading/blistering).
# Barbour 2026-07-01: confirmed large OR blistering both trigger urgent SJS
# review (previously only BLISTERING set this flag directly; it relied on an
# incidental OR with SPREADING_RASH downstream in decision.py, which meant a
# LARGE-only rash could be silently dropped from the urgent_flags list if
# another urgent_safety flag was already present). Now explicit and correct.
# E10: one canonical set for "urgent rash" (large OR blistering both trigger the
# SJS/TEN stop-and-switch path). The two historical names are kept as ALIASES of
# the single constant so they can never drift apart. A small/localized rash
# triggers the lamotrigine small-rash workup instead.
URGENT_RASH = {RashSeverity.BLISTERING, RashSeverity.LARGE}
SJS_TRIGGER_RASH = URGENT_RASH   # alias (raises the SJS/TEN urgent flag)
SPREADING_RASH = URGENT_RASH     # alias (spreading rash -> stop + switch)

# Factual seizure-token -> clinical class map (ILAE-style; PI to confirm).
# Used only to compare a reported seizure type against a drug's indication
# (ethosuximide = absence only) or contraindication (lamotrigine vs myoclonic).
SEIZURE_TOKEN_TO_CLASS = {
    "brief_staring": "absence", "staring": "absence", "absence": "absence",
    "sudden_jerks": "myoclonic", "jerks": "myoclonic", "myoclonic": "myoclonic",
    "stiffening": "tonic", "tonic": "tonic",
    "whole_body": "generalized_tonic_clonic", "whole_body_shaking": "generalized_tonic_clonic",
    "generalized": "generalized_tonic_clonic",
    "focal": "focal", "focal_movements": "focal",
    # Barbour 2026-06: "acting off, not responding without shaking/stiffening" =
    # focal impaired awareness; mapped to the focal class so indication/contra
    # checks (which key on "focal") stay correct.
    "acting_off": "focal",
    "not_sure": "unknown", "other": "other",
}

# Time-on-medication bucket -> representative weeks (ordinal; PI to confirm).
TIME_ON_MED_WEEKS = {
    "under_2_weeks": 1.0, "2_4_weeks": 3.0, "4_12_weeks": 8.0,
    "3_6_months": 18.0, "over_6_months": 30.0,
}

# Seizure-emergency tokens the Q0 gate may report (prolonged / cluster).
_SEIZURE_EMERGENCY_TOKENS = {
    "seizure_over_5_min", "prolonged_seizure", "over_5_min", "longer_than_5_min",
    "repeated_seizures", "cluster", "without_recovery",
}


@dataclass
class PatientStatus:
    # (A) seizure control
    seizure_control: SeizureControl
    # (B) side-effect severity
    side_effect_severity: SideEffectSeverity
    side_effects_present: bool
    # (C) adherence
    adherence: Adherence
    # (D) provoking factors
    provoked_by_nonadherence: bool
    other_provoking_factors: List[str] = field(default_factory=list)
    # (E) dose / goal
    dose_goal_status: DoseGoalStatus = DoseGoalStatus.NOT_SURE
    at_goal: bool = False
    dose_maximized: Optional[bool] = None
    # preferences + contingency (chart contingency now OPTIONAL - Kristen T2)
    family_preference: FamilyPreference = FamilyPreference.NO_STRONG_PREFERENCE
    can_increase_dose: Optional[bool] = None
    can_switch_to: Optional[str] = None
    can_add_medication: Optional[str] = None
    # derived safety / triage signals
    sjs_triggered: bool = False
    urgent_safety: List[str] = field(default_factory=list)
    seizure_activity: bool = False
    wants_med_change: bool = False
    adherence_driven_breakthrough: bool = False

    # --- NEW (Barbour 2026-06) -------------------------------------------
    current_med: Optional[str] = None
    seizure_classes: List[str] = field(default_factory=list)
    # T1/T4 seizure duration + frequency
    seizures_per_week: Optional[float] = None
    max_duration_rank: int = 0
    duration_captured: bool = False
    escalate_faster: bool = False
    continue_despite_breakthrough: bool = False
    # T5 helpfulness + time on med
    overall_helpful: Optional[str] = None              # better|same|worse|None
    weeks_on_med: Optional[float] = None
    # T10 stop reason
    stopped_due_to: Optional[str] = None
    # T6 drug-appropriateness
    indication_mismatch: bool = False
    seiztype_contraindication: bool = False            # e.g. lamotrigine + myoclonic
    behavioral_contraindication: bool = False          # e.g. levetiracetam + behavioral comorbidity
    no_switch_targets: List[str] = field(default_factory=list)
    switch_exclusions: List[str] = field(default_factory=list)
    requires_lab_monitoring: bool = False
    # T11/T12 rash / SJS
    rash_spreading: bool = False
    rash_small: bool = False
    sjs_small_rash_switch: bool = False
    sjs_restart_risk: bool = False
    drug_sjs_risk: bool = False
    # T13 seizure emergency
    seizure_emergency: bool = False
    dose_changed: bool = False                         # Q1: dose recently changed (I4)
    # T3/T18 ceiling
    ceiling_mg_per_day: Optional[float] = None
    current_total_mg_per_day: Optional[float] = None
    below_ceiling: Optional[bool] = None
    dose_exceeds_max: bool = False        # reported dose STRICTLY above the ceiling
    # --- NEW (Barbour 2026-06 update) ------------------------------------
    whole_body_danger: bool = False                    # whole-body seizure > 3 min
    any_unresponsive: bool = False                     # any episode reported unresponsive
    med_helping: bool = False                          # overall impression = better (improvement)
    dose_limited_by_se: bool = False                   # SE prevent further escalation (add-on candidate)
    min_goal_unreachable_by_se: bool = False           # can't reach min effective dose (switch candidate)
    wants_switch: bool = False                          # family explicitly wants a different medicine
    # --- NEW (Barbour 2026-06-25 no-seizures table) ----------------------
    # 0-5 side-effect level from the 6-option scale (None for the legacy scale).
    # Drives the seizure-free table branch; legacy inputs (None) use R14-R18.
    side_effect_level: Optional[int] = None
    # Phase per Barbour's three barometers: "titrating" (still increasing) |
    # "holding" (at goal, on this dose <2 wks - adjustment period) | "steady"
    # (at goal, established). None when not derivable. Selects the no-seizures
    # action row + the L3 follow-up interval (Holding = 2 wks, else 4).
    phase: Optional[str] = None
    side_effect_dosing_choice: Optional[str] = None   # family's Level-3 pick (#52): uneven|tid|wait|add
    # How the child already takes the medicine (Q1a): "tid" when 3+ doses a day,
    # "uneven" when the family reported different amounts at different doses. A
    # Level-3 pick that is already in place is not acted on (decision._dosing_plan).
    current_dosing: List[str] = field(default_factory=list)
    # Barbour 2026-06-25: was a possible trigger (sleep/missed dose/fever/...)
    # present before ALL breakthrough seizures? (survey follow-up; advisory.)
    triggers_all_episodes: Optional[bool] = None
    # Barbour 2026-06-25: breakthrough urgency tier (non_urgent | urgent |
    # emergent | None). Coarse mapping; selects the breakthrough decision table.
    breakthrough_tier: Optional[str] = None
    # Q9 tokens, lowercased. Used by the J2 path (levetiracetam behavior at goal).
    side_effect_tokens: List[str] = field(default_factory=list)



def _current_dosing(m) -> List[str]:
    """Dosing patterns already in place, from the family's Q1a answer (facts only)."""
    out: List[str] = []
    f = getattr(m, "frequency_per_day", None)
    if isinstance(f, (int, float)) and not isinstance(f, bool) and f >= 3:
        out.append("tid")
    if getattr(m, "is_uneven", False):
        out.append("uneven")
    return out

def _severity(snapshot: IntervalSnapshot) -> tuple[SideEffectSeverity, bool]:
    s = snapshot.survey
    present = bool(s.side_effects_present)
    if not present and not s.impact_scale:
        return SideEffectSeverity.NONE, False
    if s.impact_scale:
        # Defer-on-ambiguity (E3): an UNRECOGNIZED impact token must NOT collapse
        # to MANAGEABLE_UNWANTED (a real, actionable severity that would drive a
        # confident NO_CHANGE with no flag). Map it to UNKNOWN so the decision
        # layer routes to INSUFFICIENT_DATA - consistent with side_effect_level,
        # which maps unknown tokens to None.
        sev = IMPACT_TO_SEVERITY.get(s.impact_scale, SideEffectSeverity.UNKNOWN)
        return sev, (sev != SideEffectSeverity.NONE or present)
    return SideEffectSeverity.MANAGEABLE_UNWANTED, True


def _ceiling_and_current(snapshot, asm_rule):
    """(ceiling_mg_per_day, current_total, below_ceiling) from clinician data only."""
    current = snapshot.medication.total_daily_dose
    if asm_rule is None:
        return None, current, None
    band = asm_rule.band_for(snapshot.patient.age_years, snapshot.patient.weight_kg,
                             snapshot.patient.concomitant_asms)
    if band is None:
        return None, current, None
    ceiling = band.ceiling_mg_per_day(snapshot.patient.weight_kg)
    below = (current < ceiling) if (ceiling is not None and current is not None) else None
    return ceiling, current, below


def _seizure_freq_duration(s):
    """Per-week frequency and duration from per-type episodes (preferred) or the
    overall fields. Returns (per_week_total, max_rank, captured, pairs,
    any_unresponsive) where `pairs` is a list of (per_week, duration_rank) for
    EACH reported seizure type. The increase-faster rules are evaluated per type
    against `pairs` (Barbour's thresholds are per seizure type); `per_week_total`/
    `max_rank` are kept for the clinician summary and the continue-the-plan check.
    Frequency is bucket-aware (daily/weekly/monthly/less_than_monthly) per the
    2026-06 update."""
    per_week_total = 0.0
    max_rank = 0
    captured = False
    saw_any_freq = False
    any_unresponsive = False
    pairs = []
    if s.seizure_episodes:
        for ep in s.seizure_episodes:
            rank = DURATION_RANK.get(ep.get("duration"), 0)
            max_rank = max(max_rank, rank)
            if rank > 0:
                captured = True
            if ep.get("unresponsive") is True:
                any_unresponsive = True
            pw = frequency_per_week(ep.get("frequency"), ep.get("frequency_unit"),
                                    ep.get("frequency_bucket"))
            if pw is not None:
                per_week_total += pw
                saw_any_freq = True
                pairs.append((pw, rank))
        return (per_week_total if saw_any_freq else None), max_rank, captured, pairs, any_unresponsive
    # fall back to overall fields (single, unpaired estimate)
    rank = DURATION_RANK.get(s.seizure_duration, 0)
    captured = s.seizure_duration not in (None, "not_sure")
    # E6: the overall path resolves frequency from COUNT + UNIT only. IntervalSurvey
    # has no `seizure_count_bucket` field (the per-EPISODE path above uses a
    # `frequency_bucket`), so a bucket is not available here - passing None is
    # correct. (Previously getattr(s, "seizure_count_bucket", None) read a field
    # that never exists; removed as dead. To support an overall frequency bucket,
    # add `seizure_count_bucket` to IntervalSurvey + survey_from_answers and pass
    # it as the 3rd arg below.)
    pw = frequency_per_week(s.seizure_count, s.seizure_count_unit, None)
    return pw, rank, captured, ([(pw, rank)] if pw is not None else []), False


def _whole_body_danger(s, cfg: Thresholds) -> bool:
    """Barbour 2026-06: a whole-body (generalized tonic-clonic) seizure lasting
    over the danger duration (> 3 min) is a DANGER -> urgent. Class + threshold
    are clinician-supplied; if the config is absent the rule does not fire."""
    wb = cfg.section("whole_body_danger")
    target_class = wb.get("seizure_class")
    min_rank = wb.get("min_duration_rank")
    if not target_class or min_rank is None:
        return False
    for ep in (s.seizure_episodes or []):
        rank = DURATION_RANK.get(ep.get("duration"), 0)
        classes = {SEIZURE_TOKEN_TO_CLASS.get(t, t) for t in (ep.get("look") or [])}
        if target_class in classes and rank >= min_rank:
            return True
    return False


def _escalate_faster(per_week, rank, cfg: Thresholds) -> bool:
    """Does a SINGLE seizure type (its own frequency + its own duration) cross an
    increase-faster threshold?"""
    if per_week is None or not rank:
        return False
    ef = cfg.section("escalate_faster")
    a_gt, a_dr = ef.get("rule_a_freq_per_week_gt"), ef.get("rule_a_min_duration_rank")
    b_gt, b_dr = ef.get("rule_b_freq_per_week_gt"), ef.get("rule_b_min_duration_rank")
    if a_gt is not None and a_dr is not None and per_week > a_gt and rank >= a_dr:
        return True
    if b_gt is not None and b_dr is not None and per_week > b_gt and rank >= b_dr:
        return True
    return False


def _escalate_faster_any(pairs, cfg: Thresholds) -> bool:
    """True if ANY one seizure type crosses an increase-faster threshold. Never
    pairs one type's frequency with a different type's duration (that cross-type
    pairing was a bug: a frequent SHORT type plus a rare LONG type could wrongly
    trigger a faster increase)."""
    return any(_escalate_faster(pw, rank, cfg) for pw, rank in pairs)


def _continue_ok(rank, still_titrating, cfg: Thresholds) -> bool:
    if not rank:
        return False
    co = cfg.section("continue_ok")
    all_short = co.get("all_short_max_duration_rank")
    if all_short is not None and rank <= all_short:
        return True
    titr_lt = co.get("still_titrating_max_duration_rank_lt")
    if still_titrating and titr_lt is not None and rank < titr_lt:
        return True
    return False


def _small_rash_workup_switch(snapshot, asm_rule, weeks_on_med, cfg: Thresholds) -> bool:
    """Theme T11: a small/non-spreading rash on an SJS-risk drug -> switch if any
    workup criterion is met. All criteria are clinician-supplied (config)."""
    if asm_rule is None or not asm_rule.sjs_risk:
        return False
    p, s = snapshot.patient, snapshot.survey
    w = cfg.section("lamotrigine_small_rash_workup")
    if w.get("switch_if_southeast_asian_ancestry") and p.southeast_asian_ancestry:
        return True
    gt = w.get("switch_if_other_significant_meds_gt")
    if gt is not None:
        total_meds = 1 + len(p.concomitant_asms) + len(p.concomitant_other_meds)
        if total_meds > gt:
            return True
    if w.get("switch_if_missed_doses_2plus_days") and s.adherence in (Adherence.PARTIAL, Adherence.STOPPED):
        return True
    lt = w.get("switch_if_months_on_med_lt")
    if lt is not None and weeks_on_med is not None and (weeks_on_med / 4.345) < lt:
        return True
    if w.get("switch_if_over_6_months_and_no_viral_illness") and weeks_on_med is not None:
        if (weeks_on_med / 4.345) > 6 and s.recent_viral_illness is False:
            return True
    # On another medication independently linked to SJS/TEN (antibiotic -
    # esp. sulfonamides/penicillins/cephalosporins - NSAID, or allopurinol) -
    # a more specific signal than the generic "on >1 medication" count
    # above, per published pediatric SJS/TEN risk-factor guidance.
    if w.get("switch_if_on_known_sjs_risk_comed") and s.on_known_sjs_risk_comed:
        return True
    return False


def categorize(snapshot: IntervalSnapshot, asm_rule: Optional[ASMRule] = None,
               cfg: Optional[Thresholds] = None) -> PatientStatus:
    s = snapshot.survey
    p = snapshot.patient
    cfg = cfg or default_thresholds()

    severity, present = _severity(snapshot)

    provoked = ((s.on_medication_2days_prior is False)
                or ("missed_medication" in (s.missed_dose_reasons or []))
                or ("missed_dose" in (s.seizure_triggers or [])))   # Barbour 2026-06-25 triggers
    # Adherence-driven ONLY when the breakthrough is attributed to a missed dose
    # (Barbour: a sleep/illness trigger is NOT an adherence resolution - see C4 vs C1).
    adherence_driven = (s.seizure_control == SeizureControl.BREAKTHROUGH and provoked)

    at_goal = (s.dose_goal_status == DoseGoalStatus.AT_GOAL) or bool(snapshot.medication.is_goal_reached)

    sjs = (s.rash_severity in SJS_TRIGGER_RASH) if s.rash_severity else False
    urgent = list(s.urgent_safety_reported)
    if sjs and "sjs_rash" not in urgent:
        urgent.append("sjs_rash")
    seizure_emergency = any(t in _SEIZURE_EMERGENCY_TOKENS for t in (s.urgent_safety_reported or []))

    wants_change = (
        s.family_preference in (FamilyPreference.CHANGE_DOSE, FamilyPreference.TRY_DIFFERENT_MED)
        or s.impact_scale == "big_wants_change"
    )

    # --- seizure frequency / duration ---
    per_week, max_rank, captured, freq_pairs, any_unresponsive = _seizure_freq_duration(s)
    still_titrating = (s.dose_goal_status == DoseGoalStatus.STILL_INCREASING) and not at_goal

    # Phase per Barbour's three barometers (2026-06-25). The survey already
    # carries the signal: still increasing -> titrating; at goal + "on this dose
    # < 2 weeks" (Q10c_time) -> adjustment-holding; at goal + >= 2 weeks -> steady.
    # Unknown duration at goal defaults to steady (preserves the prior collapsed
    # 4-week behavior; flagged in NEEDS_PI_SIGNOFF.md item 4).
    if still_titrating:
        phase = "titrating"
    elif at_goal:
        if s.time_at_current_dose == "under_2_weeks":
            phase = "holding"
        else:
            phase = "steady"
    else:
        phase = None
    escalate_faster = _escalate_faster_any(freq_pairs, cfg)
    continue_ok = _continue_ok(max_rank, still_titrating, cfg)

    # --- whole-body danger (Barbour 2026-06: whole-body > 3 min) ---
    whole_body_danger = _whole_body_danger(s, cfg)
    if whole_body_danger:
        seizure_emergency = True
        if "whole_body_over_3min" not in urgent:
            urgent.append("whole_body_over_3min")

    # --- breakthrough urgency tier (Barbour 2026-06-25: non_urgent/urgent/emergent) ---
    # PRECISE per-seizure-type classification (exact duration-second + frequency
    # thresholds) when per-type episode detail is captured; otherwise a COARSE
    # fallback from the signals the engine already derives. whole_body_danger
    # (whole-body seizure > 3 min) floors the tier at emergent.
    # NOTE (2026-07-14): the emergent coarse-floor keys on whole_body_danger only.
    # A former `max_rank >= 4` disjunct was DEAD - DURATION_RANK maxes at 3
    # (over_3min), so it never fired. It was deliberately NOT changed to `>= 3`:
    # coarse "over_3min" cannot tell a 3-5 min seizure (URGENT for focal types)
    # from a >5 min one (emergent), so flooring all of them to emergent would
    # over-triage. Precise emergent detection stays with classify_breakthrough_tier
    # (exact DURATION_SECONDS) plus whole_body_danger.
    if s.seizure_control == SeizureControl.BREAKTHROUGH:
        precise = classify_breakthrough_tier(s.seizure_episodes)
        if precise is not None:
            breakthrough_tier = precise
            if whole_body_danger and _TIER_RANK[breakthrough_tier] < 2:
                breakthrough_tier = "emergent"   # safety floor: whole-body danger
        elif whole_body_danger:
            breakthrough_tier = "emergent"
        elif escalate_faster:
            breakthrough_tier = "urgent"
        else:
            breakthrough_tier = "non_urgent"
    else:
        breakthrough_tier = None

    # --- helpfulness + time on med ---
    helpful = s.overall_change.value if isinstance(s.overall_change, OverallChange) else None
    med_helping = (helpful == "better")
    weeks_on_med = TIME_ON_MED_WEEKS.get(snapshot.medication.time_on_medication)

    # --- ceiling ---
    ceiling, current_total, below = _ceiling_and_current(snapshot, asm_rule)
    dose_maximized = (below is False) if below is not None else None
    # STRICTLY above the ceiling = the reported dose exceeds the recommended max
    # (distinct from "at max"). Surfaced as a safety note + queue flag.
    dose_exceeds_max = (ceiling is not None and current_total is not None
                        and current_total > ceiling + 1e-6)

    # --- drug-appropriateness (needs asm_rule facts + patient context) ---
    # Use the seizure types reported this interval (Q4); fall back to the
    # intake seizure types when the survey didn't list any.
    # Coerce to non-empty strings so malformed tokens (None, ints, blanks) can't
    # crash the set/sort below — bad input should degrade gracefully, not raise.
    # The interval's types = Q4 PLUS the per-episode "look" tokens (seizure_episodes),
    # so an input that only describes episodes (hand-built or API callers) still
    # gets the indication check - e.g. ethosuximide with focal seizures (2026-10-28).
    _interval = list(s.seizure_types or [])
    for _ep in (s.seizure_episodes or []):
        if isinstance(_ep, dict):
            _look = _ep.get("look")
            _interval += list(_look) if isinstance(_look, (list, tuple)) else ([_look] if _look else [])
    reported_types = [str(t) for t in (_interval or list(p.seizure_types or [])) if t]
    classes = sorted({SEIZURE_TOKEN_TO_CLASS.get(t, t) for t in reported_types})
    indication_mismatch = False
    seiztype_contra = False
    behavioral_contra = False
    no_switch = []
    drug_sjs = False
    requires_labs = False
    if asm_rule is not None:
        drug_sjs = asm_rule.sjs_risk
        requires_labs = asm_rule.requires_lab_monitoring
        no_switch = list(asm_rule.no_switch_targets)
        if asm_rule.treats_only and classes:
            real = [c for c in classes if c not in ("unknown", "other")]
            if real and any(c not in asm_rule.treats_only for c in real):
                indication_mismatch = True
        if asm_rule.avoid_if_seizure_type and any(c in asm_rule.avoid_if_seizure_type for c in classes):
            seiztype_contra = True
        if asm_rule.avoid_if_comorbidity and any(
                cm in asm_rule.avoid_if_comorbidity for cm in (p.behavioral_comorbidities or [])):
            behavioral_contra = True

    # switch exclusions: prior meds + the current drug's do-not-switch targets
    exclusions = sorted(set([m.strip().lower() for m in (p.prior_asms_tried or [])]) | set(no_switch))

    # --- rash / SJS ---
    rash_spreading = (s.rash_severity in SPREADING_RASH) if s.rash_severity else False
    rash_small = (s.rash_severity == RashSeverity.SMALL) if s.rash_severity else False
    sjs_small_switch = _small_rash_workup_switch(snapshot, asm_rule, weeks_on_med, cfg) if rash_small else False
    sjs_restart = bool(drug_sjs and asm_rule and asm_rule.conmed_dependent_titration
                       and s.adherence in (Adherence.PARTIAL, Adherence.STOPPED))

    return PatientStatus(
        seizure_control=s.seizure_control,
        side_effect_severity=severity,
        side_effect_level=IMPACT_TO_LEVEL.get(s.impact_scale),
        phase=phase,
        side_effect_dosing_choice=getattr(s, "side_effect_dosing_choice", None),
        current_dosing=_current_dosing(snapshot.medication),
        side_effects_present=present,
        adherence=s.adherence,
        provoked_by_nonadherence=provoked,
        other_provoking_factors=list(s.seizure_triggers or []),
        triggers_all_episodes=s.triggers_all_episodes,
        breakthrough_tier=breakthrough_tier,
        dose_goal_status=s.dose_goal_status,
        at_goal=at_goal,
        dose_maximized=dose_maximized,
        family_preference=s.family_preference,
        can_increase_dose=snapshot.contingency.can_increase_dose,
        can_switch_to=snapshot.contingency.can_switch_to,
        can_add_medication=snapshot.contingency.can_add_medication,
        sjs_triggered=sjs,
        urgent_safety=urgent,
        seizure_activity=(s.seizure_control == SeizureControl.BREAKTHROUGH),
        wants_med_change=wants_change,
        adherence_driven_breakthrough=adherence_driven,
        current_med=(str(snapshot.medication.medication)
                     if snapshot.medication.medication is not None else None),
        seizure_classes=classes,
        seizures_per_week=per_week,
        max_duration_rank=max_rank,
        duration_captured=captured,
        escalate_faster=escalate_faster,
        continue_despite_breakthrough=continue_ok,
        overall_helpful=helpful,
        weeks_on_med=weeks_on_med,
        stopped_due_to=s.stopped_reason,
        indication_mismatch=indication_mismatch,
        seiztype_contraindication=seiztype_contra,
        behavioral_contraindication=behavioral_contra,
        no_switch_targets=no_switch,
        switch_exclusions=exclusions,
        requires_lab_monitoring=requires_labs,
        rash_spreading=rash_spreading,
        rash_small=rash_small,
        sjs_small_rash_switch=sjs_small_switch,
        sjs_restart_risk=sjs_restart,
        drug_sjs_risk=drug_sjs,
        seizure_emergency=seizure_emergency,
        dose_changed=bool(snapshot.medication.dose_changed_or_stopped),
        ceiling_mg_per_day=ceiling,
        current_total_mg_per_day=current_total,
        below_ceiling=below,
        dose_exceeds_max=dose_exceeds_max,
        whole_body_danger=whole_body_danger,
        any_unresponsive=any_unresponsive,
        med_helping=med_helping,
        dose_limited_by_se=bool(s.dose_limited_by_side_effects),
        min_goal_unreachable_by_se=bool(s.min_goal_unreachable_due_to_se),
        wants_switch=(s.family_preference == FamilyPreference.TRY_DIFFERENT_MED),
        side_effect_tokens=[str(x).strip().lower()
                            for x in (s.side_effects_present or [])
                            if str(x).strip()],
    )
