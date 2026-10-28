"""
decision.py
-----------
The deterministic, auditable decision logic for the Aim 2 assistant (spec §9),
updated with Dr. Barbour's 2026-06 review (PROJECT_LOG.md themes T1-T18).

NO LLM. NO invented clinical values. The rules reason over survey-derived
categories + clinician-supplied drug facts (asm_rules.json) + clinician-supplied
numeric thresholds (titration_thresholds.json). Every run returns exactly one
category plus a machine-readable `rationale` (which rule fired, which inputs),
a `pace` (faster/standard/slower) where relevant, a recommended `followup`, and
`safety_notes` - all for the physician to audit and approve.

Rules are evaluated in a fixed order in `decide()` (first match wins) and each
firing is recorded by rule id in `rationale`. The clinical VALUES are data the
PI edits without a code change: dose bands (asm_rules.json), thresholds
(titration_thresholds.json), the no-seizures table and the breakthrough tables.
The rule ORDER and the guards around each rule are Python in this file; changing
them is a code change with a test.

Categories (spec §5/§9):
    ESCALATE | SLOWER_UPTITRATION | SWITCH_MEDICATION | ADD_MEDICATION |
    LOWER_DOSE | HOLD | NO_CHANGE | INSUFFICIENT_DATA
A "faster" increase is ESCALATE with pace="faster"; "continue the current plan"
is reported as NO_CHANGE (no new change) - see PROJECT_LOG open question #1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

from .categorize import PatientStatus
from .snapshot import (
    SeizureControl, SideEffectSeverity, Adherence, FamilyPreference,
    DoseGoalStatus,
)
from .titration_config import (Thresholds, default_thresholds,
                               default_no_seizures_table, default_breakthrough_tables)

# Action token (from the no-seizures / breakthrough data tables) -> category.
_ACTION_TO_CATEGORY = {
    "no_change": "NO_CHANGE", "escalate": "ESCALATE", "slower": "SLOWER_UPTITRATION",
    "slower_uptitration": "SLOWER_UPTITRATION", "hold": "HOLD", "lower": "LOWER_DOSE",
    "lower_dose": "LOWER_DOSE", "add_medication": "ADD_MEDICATION",
    "switch_medication": "SWITCH_MEDICATION", "switch": "SWITCH_MEDICATION",
}

# Reasons a STOPPED medication implies a switch (Barbour T10: side effects / cost).
_STOP_SWITCH_REASONS = {"side_effects", "side_effect", "cost", "too_expensive"}

# Barbour 2026-06 family-facing prompts (PI-supplied verbatim; physician-reviewed
# before sending). Kept here as named constants so they can be edited in one place.
PREFERENCE_PROMPT_AT_GOAL_MEDIUM = (
    "The doctor might consider increasing seizure medications to treat your child's "
    "ongoing seizures. From your perspective, do you think your child is tolerating "
    "the medication side effects enough to continue increasing the dose? Or do you "
    "think the doctor should consider adding another medication. Sometimes it can be "
    "helpful to have two medications that work through different mechanisms and have "
    "different side effect profiles, to get the best treatment with the least side effects."
)
ADJUSTMENT_PROMPT_TITRATING_MEDIUM = (
    "Side effects can be the most pronounced immediately after a dose increase and "
    "improve as the body adjusts to the medicine. If your child is showing a pattern "
    "of medium impact side effects early after a dose increase with improvement after "
    "1 week, then we can consider continuing to slowly increase the medication dose. "
    "If your child is showing a pattern of big impact side effects early after a dose "
    "increase and continuing to have medium impact side effects after 1 week, then we "
    "might want to consider adding another medication rather than continuing dose "
    "increases. From your observations, do you think your child is showing an adjustment "
    "to the medication after being on a stable dose for 1 week, and side effects are "
    "manageable after this 1 week adjustment period? Yes - side effects are manageable / "
    "No - side effects are having too much of an impact on daily life and I would prefer "
    "avoiding more dose increases."
)
# Barbour 2026-06-25: seizure-free, in the adjustment-HOLDING period (at goal,
# on this dose < 2 weeks) with medium-impact side effects -> ask whether the side
# effects are easing as the body adjusts (no dose increases are scheduled here).
ADJUSTMENT_PROMPT_HOLDING_MEDIUM = (
    "Side effects are often strongest right after a dose change and can ease as the body "
    "adjusts to the medicine over the first couple of weeks. Thinking about the past week, "
    "do your child's side effects seem to be getting better as they adjust to this dose? "
    "Yes - they seem to be improving / No - they are staying the same or getting worse."
)


def _min_weeks(sw: Dict[str, Any], key: str) -> float:
    """Minimum-trial threshold from titration_thresholds.json. A missing value
    disables the rule; an explicit 0 means the rule applies at any duration."""
    v = sw.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return float("inf")
    return float(v)


class RecommendationCategory(Enum):
    ESCALATE = "escalate"
    SLOWER_UPTITRATION = "slower_uptitration"
    SWITCH_MEDICATION = "switch_medication"
    ADD_MEDICATION = "add_medication"            # Barbour 2026-06: "change = ADD a second med"
    LOWER_DOSE = "lower_dose"                     # Barbour 2026-06-25 no-seizures table (L4)
    HOLD = "hold"                                 # Barbour 2026-06-25 no-seizures table (L3): stay at dose / pause titration
    NO_CHANGE = "no_change"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class Decision:
    category: RecommendationCategory
    rationale: List[Dict[str, Any]] = field(default_factory=list)
    safety_concern_flag: bool = False
    requires_physician_review: bool = True   # engine PROPOSES; physician approves
    switch_target: Optional[str] = None
    adherence_note: Optional[str] = None
    urgent_flags: List[str] = field(default_factory=list)
    # --- added for the Barbour review ---
    pace: Optional[str] = None                       # "faster" | "standard" | "slower"
    followup_weeks: Optional[int] = None             # schedule a digital re-check (T8)
    safety_notes: List[str] = field(default_factory=list)
    switch_exclusions: List[str] = field(default_factory=list)   # do-not-switch-to (T6)
    adherence_support: bool = False                  # add adherence tips to the message (T9)
    reason_summary: Optional[str] = None             # one-line "why" for the physician (T15)
    family_prompt: Optional[str] = None              # PI-supplied preference/adjustment question (Barbour 2026-06)
    lab_orders: List[str] = field(default_factory=list)   # Barbour 2026-06-25 no-seizures table: labs to order (by drug)
    dosing_note: Optional[str] = None                # Barbour 2026-06-25: e.g. "consider uneven dosing" guidance
    guidance_steps: Optional[str] = None             # Barbour 2026-06-25 breakthrough tables: verbatim step list for the clinician
    urgency_tier: Optional[str] = None               # breakthrough urgency tier used (non_urgent|urgent|emergent)
    dosing_options: List[str] = field(default_factory=list)   # Level-3 options the table cell offers (#52)
    dosing_choice: Optional[str] = None              # the family's Level-3 pick, when it is one of dosing_options


# Level-3 options (Dr. Barbour 2026-10-25, #52; wording from her template H).
_DOSING_LABEL = {
    "uneven": "uneven dosing (smaller morning dose, larger night dose; same total, within 30/70-40/60)",
    "tid": "three-times-a-day dosing (same total)",
    "wait": "watch and wait",
    "add": "lower the dose and add a second medication",
}


def _dosing_plan(options, choice):
    """(dosing_note, chosen) for a Level-3 cell. The FAMILY chooses among the options
    the cell lists - any medicine, TID allowed while still increasing. A choice the
    cell does not list is not acted on; it is shown to the clinician instead."""
    opts = [o for o in (options or []) if o in _DOSING_LABEL]
    offered = "; ".join(_DOSING_LABEL[o] for o in opts)
    if choice in opts:
        return (f"Family chose: {_DOSING_LABEL[choice]}. (Options offered: {offered}.)", choice)
    if choice:
        return (f"Family chose '{_DOSING_LABEL.get(choice, choice)}', which this table cell does not "
                f"offer - clinician to discuss. Options here: {offered}.", None)
    return (f"Level-3 options for the family to choose: {offered}.", None)


def _fire(rationale, rule_id, description, status: PatientStatus, **inputs):
    rationale.append({"rule": rule_id, "description": description, "inputs": inputs})


def _safety_notes(status: PatientStatus) -> List[str]:
    """Advisory flags surfaced to the physician (never decide autonomously)."""
    notes: List[str] = []
    if status.dose_exceeds_max and status.current_total_mg_per_day and status.ceiling_mg_per_day:
        notes.append(
            f"Reported dose ({status.current_total_mg_per_day:g} mg/day) is ABOVE the recommended "
            f"maximum for this child ({status.ceiling_mg_per_day:g} mg/day). Verify the entry; the "
            "engine will not increase further.")
    if status.seiztype_contraindication:
        kinds = [c for c in (status.seizure_classes or []) if c not in ("unknown", "other")]
        kind = ", ".join(kinds) if kinds else "the reported seizure type"
        notes.append(f"{status.current_med}: relative contraindication for {kind} "
                     f"(this medicine may worsen {kind} seizures).")
    if status.behavioral_contraindication:
        notes.append(f"{status.current_med}: caution with this patient's behavioral "
                     "comorbidity (e.g. levetiracetam and irritability/aggression).")
    if status.sjs_restart_risk:
        notes.append(f"{status.current_med}: missed/stopped doses raise serious-rash (SJS) "
                     "risk on abrupt restart - consider offering a switch and warn the family.")
    if status.requires_lab_monitoring:
        notes.append(f"{status.current_med}: requires lab monitoring.")
    if str(status.current_med or "").lower() in ("carbamazepine", "oxcarbazepine"):
        notes.append(f"{status.current_med}: consider HLA-B*1502 screening before/around use "
                     "(higher SJS/TEN risk in patients of Southeast/East Asian ancestry).")
    return notes


def decide(status: PatientStatus, cfg: Optional[Thresholds] = None) -> Decision:
    cfg = cfg or default_thresholds()
    rationale: List[Dict[str, Any]] = []
    safety_notes = _safety_notes(status)
    excl = status.switch_exclusions
    still_titrating = (status.dose_goal_status == DoseGoalStatus.STILL_INCREASING) and not status.at_goal
    sw = cfg.section("switch_when_not_helpful")
    fu = cfg.section("followup")
    fu_titrate = fu.get("while_titrating_weeks")

    def D(cat, **kw):
        kw.setdefault("safety_notes", safety_notes)
        kw.setdefault("switch_exclusions", excl)
        return Decision(category=cat, rationale=rationale, **kw)

    # ===== R0: SPREADING / BLISTERING RASH -> stop + switch (urgent) =========
    # Barbour T11/T12 (H1,H2,H5). Family message is withheld (safety_concern).
    if status.sjs_triggered or status.rash_spreading:
        _fire(rationale, "R0", "Spreading/blistering rash reported - serious skin reaction "
              "(SJS/TEN) concern. Stop the medication and switch; urgent physician review.",
              status, rash_spreading=status.rash_spreading, sjs_triggered=status.sjs_triggered,
              drug_sjs_risk=status.drug_sjs_risk)
        return D(RecommendationCategory.SWITCH_MEDICATION, safety_concern_flag=True,
                 switch_target=status.can_switch_to, urgent_flags=(status.urgent_safety or ["sjs_rash"]),
                 reason_summary="Stop + switch: spreading/blistering rash (possible SJS/TEN).")

    # ===== R0b: SEIZURE EMERGENCY (prolonged / repeated) =====================
    # Barbour T13 (H3,H4): urgent flag AND a titration suggestion by helpfulness.
    if status.seizure_emergency:
        helpful = status.overall_helpful
        if helpful == "worse" and (status.weeks_on_med or 0) >= _min_weeks(sw, "worse_min_weeks"):
            _fire(rationale, "R0b-sw", "Prolonged/repeated seizures and the medicine is making "
                  "things worse after the minimum trial - urgent review; consider switching.",
                  status, weeks_on_med=status.weeks_on_med, overall=helpful)
            return D(RecommendationCategory.SWITCH_MEDICATION, safety_concern_flag=True,
                     urgent_flags=status.urgent_safety, switch_target=status.can_switch_to,
                     reason_summary="Urgent: prolonged/repeated seizures; med not helping -> switch.")
        if helpful in ("about_the_same", "same") and (status.weeks_on_med or 0) >= _min_weeks(sw, "unchanged_min_weeks"):
            _fire(rationale, "R0b-sw2", "Prolonged/repeated seizures and the medicine has not "
                  "helped after the minimum trial - urgent review; consider switching.",
                  status, weeks_on_med=status.weeks_on_med, overall=helpful)
            return D(RecommendationCategory.SWITCH_MEDICATION, safety_concern_flag=True,
                     urgent_flags=status.urgent_safety, switch_target=status.can_switch_to,
                     reason_summary="Urgent: prolonged/repeated seizures; med unchanged -> switch.")
        _fire(rationale, "R0b", "Prolonged/repeated seizures reported - urgent physician review. "
              "If the medicine has been helping, increase faster toward the goal dose.",
              status, overall=helpful)
        return D(RecommendationCategory.ESCALATE, safety_concern_flag=True, pace="faster",
                 urgent_flags=status.urgent_safety,
                 reason_summary="Urgent: prolonged/repeated seizures; if med has helped, increase faster.")

    # ===== R0c: other urgent safety (breathing, ER) -> withhold =============
    other_urgent = [u for u in status.urgent_safety if u not in ("sjs_rash",)]
    if other_urgent and not status.seizure_emergency:
        _fire(rationale, "R0c", "Urgent safety signal reported - withhold automated "
              "recommendation; require immediate physician review.", status,
              urgent_safety=status.urgent_safety)
        return D(RecommendationCategory.INSUFFICIENT_DATA, safety_concern_flag=True,
                 urgent_flags=status.urgent_safety,
                 reason_summary="Urgent safety signal - physician review required.")

    # ===== R1: dose was changed / regimen unclear -> defer ===================
    # Barbour I4: "if not taking as prescribed (smaller dose), instruct to take
    # as prescribed; recommend contacting the doctor." Only defers when seizures
    # are still occurring (a changed dose + seizures is the ambiguous case).
    if status.dose_changed and status.seizure_activity:
        _fire(rationale, "R1", "The dose was recently changed and seizures continue - confirm "
              "the current regimen (take as prescribed) and contact the clinic; not enough "
              "information to recommend a change.", status, dose_changed=status.dose_changed)
        return D(RecommendationCategory.INSUFFICIENT_DATA,
                 reason_summary="Dose changed + ongoing seizures -> confirm regimen; contact clinic.")

    # ===== R2: STOPPED due to side effects / cost -> switch ==================
    if status.adherence == Adherence.STOPPED and (status.stopped_due_to or "").lower() in _STOP_SWITCH_REASONS:
        _fire(rationale, "R2", "Family stopped the medication (reason: "
              f"{status.stopped_due_to}) - switch to a better-tolerated/feasible option.",
              status, stopped_due_to=status.stopped_due_to)
        return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                 adherence_support=True,
                 reason_summary=f"Stopped due to {status.stopped_due_to} -> switch.")

    # ===== R3: drug doesn't cover the reported seizure type -> switch ========
    # Barbour T14 (J4): ethosuximide treats absence only.
    if status.indication_mismatch:
        _fire(rationale, "R3", f"{status.current_med} does not cover the reported seizure "
              "type (e.g. ethosuximide treats absence/staring seizures only) - switch.",
              status, seizure_classes=status.seizure_classes)
        return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                 reason_summary="Drug not indicated for the reported seizure type -> switch.")

    # ===== R4: small rash on an SJS-risk drug + workup criterion -> switch ===
    # Barbour T11 (H6).
    if status.sjs_small_rash_switch:
        _fire(rationale, "R4", f"Small rash on {status.current_med} (serious-rash risk) plus a "
              "workup criterion (ancestry / other meds / missed doses / time on med / no viral "
              "illness) - switch.", status)
        return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                 safety_notes=safety_notes + ["Small rash on an SJS-risk drug met a switch "
                 "criterion (theme T11)."],
                 reason_summary="Small rash + SJS-risk workup criterion -> switch.")

    # ===== R4b: cannot reach a minimum effective dose due to side effects -> switch
    # Barbour 2026-06: "unable to reach a minimum level goal dose due to side
    # effects" -> stop & start a new medicine.
    if status.min_goal_unreachable_by_se:
        _fire(rationale, "R4b", "Side effects prevent reaching even a minimum effective "
              "dose - switch to a better-tolerated medicine.", status,
              min_goal_unreachable_by_se=status.min_goal_unreachable_by_se)
        return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                 reason_summary="Can't reach a minimum effective dose due to side effects -> switch.")

    # ===== R5: adherence-driven breakthrough -> address adherence first ======
    if status.adherence_driven_breakthrough:
        _fire(rationale, "R5", "Breakthrough seizures coincide with missed/stopped medication - "
              "address adherence before any dose change.", status,
              adherence=status.adherence.value, provoked=status.provoked_by_nonadherence)
        return D(RecommendationCategory.NO_CHANGE, adherence_support=True,
                 adherence_note=("Breakthrough seizures occurred during missed doses. "
                                 "Support adherence before changing the dose."),
                 reason_summary="Seizures during missed doses -> support adherence, no dose change.")

    adherence_note = None
    adherence_support = False
    if status.adherence in (Adherence.PARTIAL, Adherence.STOPPED):
        adherence_note = "Adherence is suboptimal; weigh this when interpreting efficacy/tolerability."
        adherence_support = True

    family_wants_slower = status.family_preference == FamilyPreference.CHANGE_DOSE
    family_wants_switch = status.family_preference == FamilyPreference.TRY_DIFFERENT_MED

    # ===== R-defer: unrecognized side-effect impact token -> defer ===========
    # E3 defer-on-ambiguity: an impact_scale token the taxonomy map doesn't
    # recognize is NOT a confident severity. Rather than treat it as "manageable"
    # (which would yield a confident NO_CHANGE), flag it for physician review.
    # (Urgent safety / rash / emergency rules above still take precedence.)
    if status.side_effect_severity == SideEffectSeverity.UNKNOWN:
        _fire(rationale, "R-defer-se", "Side-effect impact answer not recognized - "
              "cannot judge tolerability. Physician review required (no confident "
              "recommendation).", status)
        return D(RecommendationCategory.INSUFFICIENT_DATA,
                 reason_summary="Unrecognized side-effect impact answer -> physician review.")

    # ===== BREAKTHROUGH branches ============================================
    if status.seizure_control == SeizureControl.BREAKTHROUGH:
        # ===== BT: Barbour breakthrough decision tables =====================
        # Routes breakthrough by (urgency tier x phase x side-effect level) using
        # Dr. Barbour's breakthrough workbooks. Acute emergencies are handled above by
        # R0b, so the emergent table here applies to non-whole-body prolonged
        # seizures.
        #
        # Two ways in (either is sufficient):
        #  (1) LEVEL route - the 6-level side-effect scale captured a level, 0
        #      through 5 (the table file's own grid). Level 0 and level 1 require
        #      a captured seizure DURATION; without it, level 0 stays on the
        #      frequency-only urgent assumption (R8) and level 1 stays on the
        #      R12 defer (cases B1/B2). Level 2 and above do not need duration.
        #  (2) TIER route - ADDED 2026-07-16 (Kristen v2 review, cases B3/B4/B5).
        #      Reaches the SAME table for legacy-encoded / no-side-effect
        #      breakthrough so her urgency tiers drive the plan even without the
        #      6-level scale. Gated tightly so NO validated case moves (all 63 gold
        #      breakthrough cases are non_urgent and either lack captured duration
        #      or are short/infrequent -> still R8/R10/R12):
        #        * urgent/emergent tier with seizure DURATION captured (B3, B5); or
        #        * non_urgent + difficult-but-tolerable side effects while titrating,
        #          duration captured (B4). Kristen: urgency drives the plan; a
        #          tolerable side effect no longer forces the old R13 "slow down".
        #      NONE/MANAGEABLE severities only (level 0/2 fallback); intolerable
        #      still -> R6, family "change dose" still -> R7, helping-but-dose-
        #      limited still -> R5b.
        bt = default_breakthrough_tables().get("tables", {})
        _bt_dose_limited_addon = (status.med_helping and status.dose_limited_by_se
                                  and status.below_ceiling is not False)
        _bt_level_route = (status.side_effect_level is not None
                           and (status.side_effect_level >= 2 or status.duration_captured))
        _bt_sev_ok = status.side_effect_severity in (SideEffectSeverity.NONE,
                                                     SideEffectSeverity.MANAGEABLE_UNWANTED)
        # urgent/emergent route additionally requires escalate_faster (frequent
        # AND long enough per the thresholds) so a long-but-RARE seizure - whose
        # tier is urgent on DURATION alone - is not escalated (it stays on the
        # legacy continue-plan path; regression test_multitype... locks this in).
        _bt_tier_route = (
            _bt_sev_ok and status.duration_captured
            and status.family_preference != FamilyPreference.CHANGE_DOSE
            and ((status.breakthrough_tier in ("urgent", "emergent") and status.escalate_faster)
                 or (status.breakthrough_tier == "non_urgent" and still_titrating
                     and status.side_effect_severity == SideEffectSeverity.MANAGEABLE_UNWANTED)))
        if (status.breakthrough_tier in bt and not _bt_dose_limited_addon
                and (_bt_level_route or _bt_tier_route)):
            level = status.side_effect_level
            if level is None:   # legacy / no-6-level input -> derive from severity
                level = 0 if status.side_effect_severity == SideEffectSeverity.NONE else 2
            phase = status.phase or ("titrating" if still_titrating else "steady")
            rows = bt[status.breakthrough_tier].get(phase, [])
            cell = rows[level] if 0 <= level < len(rows) else None
            if cell:
                at_max = status.below_ceiling is False
                cat_token = (cell.get("at_max_category") if (at_max and cell.get("at_max_category"))
                             else cell.get("category"))
                # E4: resolve defensively. A clinician-edited grid token that maps
                # to a non-member name must DEFER (INSUFFICIENT_DATA), never raise
                # KeyError. __members__.get(name) returns None for a bad name; fall
                # back to the safe defer member.
                cat_name = _ACTION_TO_CATEGORY.get(cat_token, "INSUFFICIENT_DATA")
                category = (RecommendationCategory.__members__.get(cat_name)
                            or RecommendationCategory.INSUFFICIENT_DATA)
                med = (status.current_med or "").lower()
                ns = default_no_seizures_table()
                labs = ns.get("labs_by_drug", {}).get(med, []) if cell.get("labs") else []
                dose_note, d_opts, d_choice = None, [], None
                if cell.get("dosing"):
                    # Dr. Barbour 2026-10-25 (#52): the family picks from the cell's
                    # options, for any medicine; TID is fine while still increasing.
                    d_opts = list(cell.get("dosing_options") or ["uneven", "tid", "wait"])
                    dose_note, d_choice = _dosing_plan(d_opts, status.side_effect_dosing_choice)
                    if d_choice == "add" and not at_max:
                        # Option 4 from the cell's own list: lower the dose + add a second med.
                        category = RecommendationCategory.ADD_MEDICATION
                        cat_token = "add_medication"
                urgent_flags = list(status.urgent_safety)
                if cell.get("urgent") and "breakthrough_emergent" not in urgent_flags:
                    urgent_flags.append("breakthrough_emergent")
                _fire(rationale, "BT", "Breakthrough; action from Dr. Barbour's 2026-06-25 "
                      "breakthrough table (urgency x phase x side-effect level).",
                      status, tier=status.breakthrough_tier, phase=phase,
                      side_effect_level=level, at_max=at_max, action=cat_token,
                      dosing_choice=d_choice)
                _steps = cell.get("steps")
                if d_choice == "add" and category == RecommendationCategory.ADD_MEDICATION:
                    _steps = ("Family chose option 4 of this cell's Level-3 options: lower dose; add a "
                              f"second medication. (Cell: {cell.get('steps')})")
                return D(category,
                         pace=cell.get("pace"),
                         switch_target=(status.can_switch_to if category == RecommendationCategory.SWITCH_MEDICATION else None),
                         lab_orders=labs, dosing_note=dose_note,
                         dosing_options=d_opts, dosing_choice=d_choice,
                         guidance_steps=_steps, urgency_tier=status.breakthrough_tier,
                         followup_weeks=cell.get("followup_weeks"),
                         safety_concern_flag=bool(cell.get("safety")),
                         urgent_flags=(urgent_flags if (cell.get("urgent") or cell.get("safety")) else []),
                         adherence_note=adherence_note, adherence_support=adherence_support,
                         reason_summary=f"Breakthrough ({status.breakthrough_tier}, {phase}, L{level}): {_steps}")

        # R5b NEW (Barbour 2026-06): the medicine is HELPING but its side effects
        # prevent further dose escalation while seizures continue -> ADD a second
        # medicine (combination), rather than switching off a working drug.
        if status.med_helping and status.dose_limited_by_se and (status.below_ceiling is not False):
            _fire(rationale, "R5b", "Medicine is helping but side effects prevent further "
                  "dose increases while seizures continue - consider ADDING a second "
                  "medicine (combination therapy).", status,
                  med_helping=status.med_helping, dose_limited_by_se=status.dose_limited_by_se)
            return D(RecommendationCategory.ADD_MEDICATION, adherence_note=adherence_note,
                     reason_summary="Helping but dose-limited by side effects + ongoing seizures -> add a second med.")

        # intolerable side effects -> switch
        if status.side_effect_severity == SideEffectSeverity.INTOLERABLE:
            _fire(rationale, "R6", "Breakthrough seizures + intolerable side effects - switch.",
                  status, side_effect_severity=status.side_effect_severity.value)
            return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                     adherence_note=adherence_note, adherence_support=adherence_support,
                     reason_summary="Breakthrough + intolerable side effects -> switch.")

        # family prefers a slower/changed dose -> slower
        if family_wants_slower:
            _fire(rationale, "R7", "Breakthrough but the family prefers a slower/changed dose - "
                  "recommend a slower uptitration.", status,
                  family_preference=status.family_preference.value)
            return D(RecommendationCategory.SLOWER_UPTITRATION, pace="slower",
                     adherence_note=adherence_note, adherence_support=adherence_support,
                     reason_summary="Breakthrough; family prefers slower -> slower uptitration.")

        # no side effects
        if status.side_effect_severity == SideEffectSeverity.NONE:
            if not status.duration_captured:
                # F1 (Kristen v2 review, PI-confirmed 2026-07-16): a breakthrough
                # reported WITH a frequency but WITHOUT a duration is no longer a
                # defer - assume the MIDDLE tier (urgent) and act on the phase-
                # appropriate urgent cell (titrating: quicken to every 3 days, end
                # +1 higher; if at max, no change). Deterministic, so the same-shaped
                # gold cases (A4/A5/A6/A7/A8/C4/J3/J4) move defer -> urgent with it.
                # A bare "yes, breakthrough" with NEITHER frequency nor duration
                # (e.g. gold I5) still defers - escalating on zero information would
                # be unsafe. DRAFT, verified:false.
                bt_u = default_breakthrough_tables().get("tables", {}).get("urgent", {})
                phase = status.phase or ("titrating" if still_titrating else "steady")
                cell = (bt_u.get(phase) or [None])[0]
                if status.seizures_per_week is not None and cell:
                    at_max = status.below_ceiling is False
                    tok = (cell.get("at_max_category") if (at_max and cell.get("at_max_category"))
                           else cell.get("category"))
                    category = (RecommendationCategory.__members__.get(
                                    _ACTION_TO_CATEGORY.get(tok, "INSUFFICIENT_DATA"))
                                or RecommendationCategory.INSUFFICIENT_DATA)
                    _fire(rationale, "R8-assume-urgent", "Breakthrough reported with a frequency "
                          "but no seizure duration - assume URGENT (middle tier) and follow the "
                          "phase-appropriate urgent plan rather than deferring (Barbour F1, "
                          "PI-confirmed 2026-07-16).", status, phase=phase, at_max=at_max, action=tok)
                    return D(category, pace=cell.get("pace"),
                             guidance_steps=cell.get("steps"), urgency_tier="urgent",
                             followup_weeks=cell.get("followup_weeks"),
                             adherence_note=adherence_note,
                             reason_summary=("Breakthrough, frequency known but duration not "
                                             "captured -> assume urgent: " + str(cell.get("steps"))))
                _fire(rationale, "R8", "Breakthrough seizures but their DURATION was not "
                      "captured (and no frequency to act on) - need seizure duration.", status,
                      seizures_per_week=status.seizures_per_week)
                return D(RecommendationCategory.INSUFFICIENT_DATA,
                         reason_summary="Need seizure duration to decide continue vs increase faster.")
            if status.escalate_faster:
                if status.below_ceiling is False:   # already at the clinician maximum
                    if status.overall_helpful == "better":
                        _fire(rationale, "R9-add", "Frequent/long breakthrough seizures at the "
                              "maximum dose, on a medicine that has been helping - ADD "
                              "a second medicine (combination therapy).", status)
                        return D(RecommendationCategory.ADD_MEDICATION,
                                 reason_summary="At max dose, med helping, ongoing seizures -> add a second med.")
                    _fire(rationale, "R9-switch", "Frequent/long breakthrough seizures at the "
                          "maximum dose and the medicine is not clearly helping - switch.", status)
                    return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                             reason_summary="At max dose, not helping -> switch.")
                _fire(rationale, "R9", "Frequent and/or long breakthrough seizures - increase the "
                      "dose FASTER toward the goal (per the duration/frequency thresholds).",
                      status, seizures_per_week=status.seizures_per_week,
                      max_duration_rank=status.max_duration_rank)
                return D(RecommendationCategory.ESCALATE, pace="faster", adherence_note=adherence_note,
                         reason_summary="Frequent/long breakthrough seizures -> increase faster.")
            if status.continue_despite_breakthrough:
                _fire(rationale, "R10", "Breakthrough seizures are short and/or infrequent while "
                      "still titrating - continue the current plan (no change), keep increasing "
                      "toward goal as scheduled.", status, max_duration_rank=status.max_duration_rank)
                return D(RecommendationCategory.NO_CHANGE, pace="standard", followup_weeks=fu_titrate,
                         adherence_note=adherence_note,
                         reason_summary="Short/infrequent breakthrough while titrating -> continue plan.")
            # duration captured, neither faster nor clearly-fine -> continue plan
            _fire(rationale, "R11", "Breakthrough seizures, duration captured but below the "
                  "increase-faster thresholds - continue the current titration plan.", status)
            return D(RecommendationCategory.NO_CHANGE, pace="standard", followup_weeks=fu_titrate,
                     adherence_note=adherence_note,
                     reason_summary="Breakthrough below faster thresholds -> continue plan.")

        # manageable / difficult-but-tolerable ("medium impact") side effects
        if status.side_effect_severity == SideEffectSeverity.MANAGEABLE_UNWANTED:
            # NEW (Barbour 2026-06): AT GOAL + medium impact + ongoing seizures ->
            # ask the family their preference (keep increasing vs add a second med).
            if status.at_goal:
                _fire(rationale, "R12g", "At the goal dose with medium-impact side effects and "
                      "ongoing seizures - ask the family whether to keep increasing the dose or "
                      "add a second medicine (preference question).", status)
                return D(RecommendationCategory.INSUFFICIENT_DATA,
                         family_prompt=PREFERENCE_PROMPT_AT_GOAL_MEDIUM,
                         followup_weeks=fu.get("tolerable_side_effect_reassess_weeks"),
                         adherence_note=adherence_note,
                         reason_summary="At goal + medium impact + ongoing seizures -> ask family: "
                         "tolerate & increase, or add a second medicine.")
            if not status.duration_captured:
                _fire(rationale, "R12", "Breakthrough + manageable side effects, but seizure "
                      "duration was not captured - need duration to weigh increasing vs holding.",
                      status)
                return D(RecommendationCategory.INSUFFICIENT_DATA,
                         reason_summary="Breakthrough + side effects; need seizure duration.")
            # STILL TITRATING + medium impact -> slower uptitration + the 1-week
            # adjustment-period question (Barbour 2026-06).
            _fire(rationale, "R13", "Breakthrough + manageable (medium-impact) side effects while "
                  "still titrating - slow the uptitration and reassess after a 1-week adjustment "
                  "period (side effects are often most pronounced right after an increase).", status)
            return D(RecommendationCategory.SLOWER_UPTITRATION, pace="slower",
                     family_prompt=ADJUSTMENT_PROMPT_TITRATING_MEDIUM,
                     followup_weeks=fu.get("tolerable_side_effect_reassess_weeks"),
                     adherence_note=adherence_note, adherence_support=adherence_support,
                     reason_summary="Titrating + medium-impact side effects -> slower uptitration; "
                     "reassess after 1-week adjustment.")

    # ===== SEIZURE-FREE branches ============================================
    # ===== NS: Barbour 2026-06-25 "No Seizures" 6-level table ===============
    # Fires ONLY when the new 6-level side-effect scale captured a level
    # (side_effect_level 0-5). Legacy 3-level inputs (level is None) - including
    # every gold-validation case - fall straight through to R14-R18 unchanged.
    # DRAFT: the table is verified:false, pending PI sign-off (see
    # NEEDS_PI_SIGNOFF.md). Assumptions are documented there.
    ns_table = default_no_seizures_table()
    if status.side_effect_level is not None and ns_table.get("actions_by_phase_level"):
        level = status.side_effect_level
        # Phase per Barbour's three barometers (categorize.PatientStatus.phase):
        # titrating / holding / steady. Fall back defensively if unset.
        phase = status.phase or ("titrating" if still_titrating else "steady")
        actions = ns_table["actions_by_phase_level"].get(phase, [])
        action = actions[level] if 0 <= level < len(actions) else None
        med = (status.current_med or "").lower()
        labs = ns_table.get("labs_by_drug", {}).get(med, [])
        # Follow-up intervals (UPDATED 2026-06-25 sheet): L3 hold, L4 lower, and
        # L5 switch all follow up in 4 weeks across phases.
        hold_fu = ns_table.get("hold_followup_weeks_by_phase", {}).get(phase)
        lower_fu = ns_table.get("lower_followup_weeks")
        switch_fu = ns_table.get("switch_followup_weeks")
        # L3 dosing (Dr. Barbour 2026-10-25, #52): the FAMILY chooses uneven / TID /
        # wait, for ANY medicine and in every phase (TID may be offered while still
        # increasing). Replaces the 2026-07-16 eligible-drug list + steady-only TID.
        ns_opts = list(ns_table.get("l3_dosing_options") or ["uneven", "tid", "wait"])
        dose_note, ns_choice = _dosing_plan(ns_opts, status.side_effect_dosing_choice)
        # Barbour 2026-06-25: in the adjustment-HOLDING period with medium-impact
        # side effects (L2/L3), ask the family whether side effects are easing as
        # the body adjusts. (None for other phases/levels -> no behavior change.)
        adj_prompt = ADJUSTMENT_PROMPT_HOLDING_MEDIUM if (phase == "holding" and level in (2, 3)) else None
        _fire(rationale, "NS", "Seizure-free; action taken from Dr. Barbour's 2026-06-25 "
              "No-Seizures table by phase x side-effect level.",
              status, side_effect_level=level, phase=phase, action=action)

        if action == "switch":   # L5 "not a fit"
            return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                     adherence_note=adherence_note, followup_weeks=switch_fu,
                     reason_summary="No seizures; side effects make this medicine not a fit (L5) -> switch.")
        if action == "lower":    # L4 "big impact"
            return D(RecommendationCategory.LOWER_DOSE, adherence_note=adherence_note,
                     lab_orders=labs, followup_weeks=lower_fu,
                     reason_summary="No seizures; big-impact side effects (L4) -> lower the dose; order labs.")
        if action == "hold":     # L3 "medium, a lot"
            return D(RecommendationCategory.HOLD, adherence_note=adherence_note,
                     lab_orders=labs, followup_weeks=hold_fu, dosing_note=dose_note,
                     dosing_options=ns_opts, dosing_choice=ns_choice,
                     family_prompt=adj_prompt,
                     reason_summary="No seizures; medium-but-significant side effects (L3) -> hold the "
                     "current dose; follow up; order labs.")
        if action == "slower":   # L2 while titrating
            return D(RecommendationCategory.SLOWER_UPTITRATION, pace="slower",
                     adherence_note=adherence_note,
                     reason_summary="No seizures; medium-tolerable side effects while titrating (L2) -> "
                     "slow the dose increases.")
        if action in ("continue", "no_change"):  # L0/L1 (and L2 when not titrating)
            # Follow-up CHANGED 2026-07-08 (Kristen validation review, case D4):
            # "Only need follow up scheduled if it is listed on the decision_tables
            # file." Her No-Seizures 'continue on schedule' / 'stay at current dose'
            # cells list NO follow-up, so we no longer auto-attach the titrating
            # 4-week recheck here. (hold/lower/switch cells DO list 4 weeks and are
            # unchanged above.) Legacy 3-level path (R18) is untouched.
            return D(RecommendationCategory.NO_CHANGE, pace="standard",
                     followup_weeks=None,
                     adherence_note=adherence_note, family_prompt=adj_prompt,
                     reason_summary="No seizures; side effects low/none (L0-L1) -> continue the current plan.")
        # action unknown / table malformed -> defer rather than guess
        _fire(rationale, "NS-defer", "No-Seizures table returned no action for this "
              "phase/level - defer to physician.", status, level=level, phase=phase)
        return D(RecommendationCategory.INSUFFICIENT_DATA,
                 reason_summary="No-Seizures table had no action for this phase/level -> physician review.")

    # intolerable side effects -> switch, except J2.
    # Dr. Barbour's note on gold case J2: seizure-free, at goal, levetiracetam,
    # intolerable behavior or irritability -> offer switch options and let the
    # physician discuss them. Other intolerable cases (a different medicine, a
    # different side effect, still titrating, or still seizing) stay on R14.
    _LEV_BEHAVIOR = {"behavior", "behavior_change", "irritability", "aggression", "mood"}
    if status.side_effect_severity == SideEffectSeverity.INTOLERABLE:
        reported = set(status.side_effect_tokens or [])
        if (not status.seizure_activity and status.at_goal
                and (status.current_med or "").lower() == "levetiracetam"
                and reported & _LEV_BEHAVIOR):
            _fire(rationale, "R14-offer",
                  "Seizure-free, at goal, levetiracetam, intolerable behavior or irritability. "
                  "Offer switch options for the physician to discuss (Dr. Barbour's note on case J2). "
                  "This is not an automatic switch.",
                  status, side_effects=sorted(reported))
            return D(RecommendationCategory.INSUFFICIENT_DATA,
                     reason_summary="Seizure-free + intolerable levetiracetam behavior at goal "
                     "-> offer switch options (physician).")
        _fire(rationale, "R14", "Seizure-free but intolerable side effects - switch.", status,
              side_effect_severity=status.side_effect_severity.value)
        return D(RecommendationCategory.SWITCH_MEDICATION, switch_target=status.can_switch_to,
                 adherence_note=adherence_note,
                 reason_summary="Seizure-free + intolerable side effects -> switch.")

    # family wants to try a different medicine (no clinical switch trigger) -> offer options
    if family_wants_switch:
        _fire(rationale, "R15", "Family would like to try a different medicine (no clinical "
              "switch trigger) - offer switch options for the physician to discuss.", status,
              family_preference=status.family_preference.value)
        return D(RecommendationCategory.INSUFFICIENT_DATA,
                 reason_summary="Family requests a different medicine -> offer switch options (physician).")

    # family prefers a slower/changed dose while still titrating -> slower
    if family_wants_slower and still_titrating:
        _fire(rationale, "R16", "Seizure-free, still titrating, family prefers a slower/changed "
              "dose - recommend a slower uptitration.", status)
        return D(RecommendationCategory.SLOWER_UPTITRATION, pace="slower",
                 reason_summary="Family prefers slower -> slower uptitration.")

    # manageable side effects -> no change + reassess if improving
    if status.side_effect_severity == SideEffectSeverity.MANAGEABLE_UNWANTED:
        _fire(rationale, "R17", "Seizure-free with manageable/difficult-but-tolerable side "
              "effects - no medication change; reassess whether the side effect eases as the "
              "body adjusts.", status, at_goal=status.at_goal)
        return D(RecommendationCategory.NO_CHANGE,
                 followup_weeks=(fu.get("tolerable_side_effect_reassess_weeks") or fu_titrate),
                 adherence_note=adherence_note,
                 reason_summary="Seizure-free + tolerable side effects -> no change; reassess soon.")

    # seizure-free, no side effects -> no change (continue plan).
    # Barbour review (case D4): "Only need follow up scheduled if it is listed on
    # the decision table. Otherwise leave it out." Her No-Seizures table lists NO
    # follow-up for the Level-0/L1 "Continue on schedule" cells, and the 6-level
    # path above already returns followup_weeks=None for them. Align this legacy
    # (no-level-captured) path so it does not append a 4-week recheck the table
    # does not list.
    _fire(rationale, "R18", "Seizure-free with no side effects - no medication change. "
          "Continue the current plan on the existing schedule.", status,
          dose_goal_status=status.dose_goal_status.value, at_goal=status.at_goal)
    return D(RecommendationCategory.NO_CHANGE,
             followup_weeks=None,
             adherence_note=adherence_note,
             reason_summary="Seizure-free, no side effects -> no change (continue plan).")
