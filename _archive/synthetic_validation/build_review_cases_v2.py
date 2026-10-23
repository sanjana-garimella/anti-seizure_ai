"""
build_review_cases_v2.py  (2026-07-14)
--------------------------------------
Generate ~50 SYNTHETIC interval-survey cases for Dr. Barbour (Kristen) to review,
weighted toward (1) dose-titration goal / no-goal adjustments and (2) critical
side effects.  100% synthetic - no PHI.

NON-NEGOTIABLE: the recommendation, AI summary, draft family message, and every
safety flag are produced by the RULE-BASED engine (engine_core) via the exact
production path (clinic.build_snapshot -> run_engine -> redflag scan -> triage).
This file fabricates NO clinical decision. Every output is a DRAFT for physician
review (verified:false, pending PI sign-off).

Outputs (in-folder only, per confidentiality rule #1):
  synthetic_validation/review_cases_v2.json   - full engine output + provenance
  synthetic_validation/Kristen_Review_Cases_v2.xlsx  - flat review sheet (built
                                                 by a separate step from the JSON)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "aim2_engine")
sys.path.insert(0, ENGINE)

import clinic                       # noqa: E402
from engine_core import run_engine  # noqa: E402


# --- reusable answer fragments (survey option vocab; see snapshot.py) ----------
def epi(look, duration, freq_bucket, **extra):
    d = {"look": list(look), "duration": duration, "frequency_bucket": freq_bucket}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# CASES.  Each: (id, group, scenario, patient, answers).
#   patient keys: external_id, age, weight(kg), epilepsy_type, medication,
#                 dose_amount, dose_unit, [concentration], frequency,
#                 can_increase, switch_to
#   answers: §7 survey codes (Q2 adherence, Q3 breakthrough, Q9 side effects,
#            Q9a rash, Q10a goal status, Q10b/c impact, Q11 overall, Q12 pref,
#            Q13 free text, seizure_episodes[...], etc.)
# Weights are realistic-for-age; doses chosen per scenario (over-max cases are
# deliberately above ceiling). Scenario = the INPUT intent; the engine decides
# the actual category.
# ---------------------------------------------------------------------------
CASES = [
    # ============ A. TITRATION - seizure-free, goal vs still increasing ========
    ("A1", "titration", "Seizure-free, at goal, doing well (tablet dosing)",
     dict(external_id="A1", age=10, weight=32, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=1, dose_unit="tablets",
          concentration=500, frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),

    ("A2", "titration", "Seizure-free, still increasing toward goal",
     dict(external_id="A2", age=6, weight=20, epilepsy_type="focal",
          medication="oxcarbazepine", dose_amount=150, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing", "Q11": "better"}),

    ("A3", "titration", "Seizure-free at goal, small manageable sleepiness",
     dict(external_id="A3", age=14, weight=50, epilepsy_type="generalized",
          medication="lamotrigine", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["sleepiness"], "Q10c_impact": "small",
      "Q10c_time": "over_4_weeks", "Q10a": "at_goal", "Q11": "better"}),

    ("A4", "titration", "Seizure-free, still increasing, valproate",
     dict(external_id="A4", age=8, weight=26, epilepsy_type="generalized",
          medication="valproic_acid", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing"}),

    ("A5", "titration", "Absence epilepsy, ethosuximide, seizure-free at goal",
     dict(external_id="A5", age=7, weight=23, epilepsy_type="absence",
          medication="ethosuximide", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),

    ("A6", "titration", "Seizure-free, still increasing, brivaracetam",
     dict(external_id="A6", age=9, weight=29, epilepsy_type="focal",
          medication="brivaracetam", dose_amount=25, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "still_increasing"}),

    ("A7", "titration", "Seizure-free at goal, liquid dosing (mL->mg)",
     dict(external_id="A7", age=5, weight=18, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=4, dose_unit="mL",
          concentration=100, frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),

    # ============ B. TITRATION - breakthrough: continue / escalate / slow ======
    ("B1", "titration", "Breakthrough short & infrequent while titrating",
     dict(external_id="B1", age=8, weight=26, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "under_1min", "monthly")]}),

    ("B2", "titration", "Breakthrough weekly 1-3 min while titrating -> raise faster",
     dict(external_id="B2", age=7, weight=23, epilepsy_type="focal",
          medication="oxcarbazepine", dose_amount=150, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    ("B3", "titration", "Frequent daily breakthrough, still increasing -> raise faster",
     dict(external_id="B3", age=8, weight=26, epilepsy_type="generalized",
          medication="valproic_acid", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["generalized"], "1_3min", "daily", count_4wk=24)]}),

    ("B4", "titration", "Breakthrough + medium-impact behavior SE -> slow the increase",
     dict(external_id="B4", age=12, weight=40, epilepsy_type="focal",
          medication="topiramate", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["behavior"], "Q10b_impact": "medium_big",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "monthly")]}),

    ("B5", "titration", "Breakthrough + medium dizziness while titrating -> slow down",
     dict(external_id="B5", age=10, weight=32, epilepsy_type="focal",
          medication="lamotrigine", dose_amount=50, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["dizziness"], "Q10b_impact": "medium_big",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    ("B6", "titration", "Breakthrough weekly, still increasing, carbamazepine -> raise",
     dict(external_id="B6", age=13, weight=46, epilepsy_type="focal",
          medication="carbamazepine", dose_amount=200, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    ("B7", "titration", "Breakthrough sudden jerks, infrequent, still increasing",
     dict(external_id="B7", age=6, weight=20, epilepsy_type="generalized",
          medication="clobazam", dose_amount=5, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["sudden_jerks"], "under_1min", "monthly")]}),

    ("B8", "titration", "Breakthrough short, still increasing, lacosamide",
     dict(external_id="B8", age=15, weight=56, epilepsy_type="focal",
          medication="lacosamide", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "under_1min", "monthly")]}),

    ("B9", "titration", "Breakthrough weekly, still increasing, brivaracetam",
     dict(external_id="B9", age=13, weight=46, epilepsy_type="focal",
          medication="brivaracetam", dose_amount=50, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    # ============ C. AT MAX / cannot increase -> add-on ========================
    ("C1", "titration", "At max dose, medicine helping, still seizing -> add 2nd med",
     dict(external_id="C1", age=8, weight=26, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=780, dose_unit="mg",
          frequency=2, can_increase=False, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q11": "better", "Q10a": "at_goal",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly", count_4wk=16)]}),

    ("C2", "titration", "At goal/max, oxcarbazepine helping, breakthrough monthly -> add-on",
     dict(external_id="C2", age=12, weight=40, epilepsy_type="focal",
          medication="oxcarbazepine", dose_amount=600, dose_unit="mg",
          frequency=2, can_increase=False, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q11": "better", "Q10a": "at_goal",
      "seizure_episodes": [epi(["focal"], "1_3min", "monthly")]}),

    ("C3", "titration", "Max dose, side effects block further increase, helping -> add-on",
     dict(external_id="C3", age=11, weight=36, epilepsy_type="focal",
          medication="topiramate", dose_amount=150, dose_unit="mg",
          frequency=2, can_increase=False, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q11": "better", "Q10a": "at_goal",
      "Q9": ["sleepiness"], "Q10b_impact": "medium_big",
      "Q10d_dose_limited_by_se": "yes",
      "seizure_episodes": [epi(["focal"], "1_3min", "monthly")]}),

    ("C4", "titration", "At max, lacosamide, still seizing weekly -> add-on",
     dict(external_id="C4", age=16, weight=60, epilepsy_type="focal",
          medication="lacosamide", dose_amount=200, dose_unit="mg",
          frequency=2, can_increase=False, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q11": "better", "Q10a": "at_goal",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    # ============ D. ADHERENCE-driven breakthrough =============================
    ("D1", "adherence", "Breakthrough coincides with missed doses -> address adherence",
     dict(external_id="D1", age=11, weight=36, epilepsy_type="focal",
          medication="oxcarbazepine", dose_amount=300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "sometimes", "Q2a": ["forget"], "Q3": "yes", "Q7": "no",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    ("D2", "adherence", "Partial adherence, infrequent breakthrough",
     dict(external_id="D2", age=9, weight=29, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "sometimes", "Q2a": ["busy", "forget"], "Q3": "yes", "Q7": "no",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "under_1min", "monthly")]}),

    ("D3", "adherence", "Stopped the medicine (forgot/ran out), now seizure-free",
     dict(external_id="D3", age=12, weight=40, epilepsy_type="generalized",
          medication="lamotrigine", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "stopped", "stopped_reason": "ran_out", "Q3": "no", "Q10a": "at_goal"}),

    # ============ E. DOSE OVER MAXIMUM -> safety ceiling flag ==================
    ("E1", "safety_ceiling", "Reported dose ABOVE recommended max for weight",
     dict(external_id="E1", age=8, weight=20, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=1300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q11": "better"}),

    ("E2", "safety_ceiling", "Topiramate reported well above ceiling",
     dict(external_id="E2", age=6, weight=20, epilepsy_type="generalized",
          medication="topiramate", dose_amount=300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal"}),

    ("E3", "safety_ceiling", "Valproate reported above ceiling for weight",
     dict(external_id="E3", age=10, weight=32, epilepsy_type="generalized",
          medication="valproic_acid", dose_amount=1500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal"}),

    # ============ F. INSUFFICIENT DATA -> defer to physician ==================
    ("F1", "defer", "Breakthrough reported but no seizure duration captured",
     dict(external_id="F1", age=10, weight=32, epilepsy_type="focal",
          medication="topiramate", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q5": 3, "Q5_unit": "per_week",
      "Q10a": "still_increasing"}),

    ("F2", "defer", "Goal status not sure, vague breakthrough",
     dict(external_id="F2", age=9, weight=29, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "not_sure",
      "seizure_episodes": [epi(["focal"], "not_sure", "monthly")]}),

    ("F3", "defer", "Side-effect impact answer not interpretable -> defer",
     dict(external_id="F3", age=8, weight=26, epilepsy_type="focal",
          medication="oxcarbazepine", dose_amount=300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["sleepiness"],
      "Q10c_impact": "not_sure_weird_token", "Q10a": "at_goal"}),

    # ============ G. CRITICAL SIDE EFFECT - SJS/serious rash (URGENT) ==========
    ("G1", "critical_se", "Spreading/blistering rash on lamotrigine (liquid) -> URGENT",
     dict(external_id="G1", age=7, weight=24, epilepsy_type="generalized",
          medication="lamotrigine", dose_amount=5, dose_unit="mL",
          concentration=5, frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "blisters",
      "Q10a": "at_goal", "Q13": "the rash looks like it is spreading and blistering"}),

    ("G2", "critical_se", "Large rash on carbamazepine -> URGENT SJS review",
     dict(external_id="G2", age=13, weight=46, epilepsy_type="focal",
          medication="carbamazepine", dose_amount=200, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="oxcarbazepine"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "large", "Q10a": "at_goal"}),

    ("G3", "critical_se", "Blistering rash on oxcarbazepine -> URGENT",
     dict(external_id="G3", age=9, weight=29, epilepsy_type="focal",
          medication="oxcarbazepine", dose_amount=300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "blisters", "Q10a": "still_increasing"}),

    ("G4", "critical_se", "Free-text 'mouth sores and peeling skin' (no checkbox) -> URGENT",
     dict(external_id="G4", age=11, weight=36, epilepsy_type="generalized",
          medication="lamotrigine", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal",
      "Q13": "she has mouth sores and the skin is peeling on her arms"}),

    ("G5", "critical_se", "Large rash on zonisamide (sulfonamide) -> URGENT",
     dict(external_id="G5", age=12, weight=40, epilepsy_type="focal",
          medication="zonisamide", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "large", "Q10a": "at_goal"}),

    # ============ H. SMALL RASH workup (switch) vs benign (contrast) ===========
    ("H1", "critical_se", "Small rash on lamotrigine + SE/E-Asian ancestry -> workup/switch",
     dict(external_id="H1", age=8, weight=26, epilepsy_type="generalized",
          medication="lamotrigine", dose_amount=25, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "small",
      "Q_ancestry": "yes", "Q10a": "still_increasing"}),

    ("H2", "critical_se", "Small rash on lamotrigine + recent viral illness + antibiotic",
     dict(external_id="H2", age=10, weight=32, epilepsy_type="generalized",
          medication="lamotrigine", dose_amount=50, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "small",
      "Q14_viral_illness": "yes", "Q_sjs_risk_comeds": ["antibiotic"],
      "Q10a": "still_increasing"}),

    ("H3", "critical_se", "Small rash, no risk factors (contrast: monitor, not urgent)",
     dict(external_id="H3", age=9, weight=29, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["rash"], "Q9a": "small",
      "Q_ancestry": "no", "Q10a": "at_goal"}),

    # ============ I. INTOLERABLE / IMPACTFUL SIDE EFFECTS ======================
    ("I1", "critical_se", "Intolerable behavior SE on carbamazepine, family wants change -> switch",
     dict(external_id="I1", age=13, weight=50, epilepsy_type="focal",
          medication="carbamazepine", dose_amount=300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="oxcarbazepine"),
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["behavior"], "Q10b_impact": "big_wants_change",
      "Q12": "different_medicine", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    ("I2", "critical_se", "Levetiracetam irritability, seizure-free, family wants change -> switch",
     dict(external_id="I2", age=9, weight=29, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=False, switch_to="brivaracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["irritability", "behavior"],
      "Q10b_impact": "big_wants_change", "Q12": "different_medicine", "Q10a": "at_goal"}),

    ("I3", "critical_se", "Topiramate cognitive 'confused/off', big impact -> switch",
     dict(external_id="I3", age=12, weight=40, epilepsy_type="generalized",
          medication="topiramate", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="valproic_acid"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["acting_confused"],
      "Q10b_impact": "big_wants_change", "Q12": "different_medicine", "Q10a": "still_increasing"}),

    ("I4", "critical_se", "Seizure-free at goal + medium-significant SE -> HOLD dose + labs (L3)",
     dict(external_id="I4", age=10, weight=32, epilepsy_type="focal",
          medication="zonisamide", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["sleepiness"], "Q10c_impact": "medium_alot",
      "Q10c_time": "over_4_weeks", "Q10a": "at_goal"}),

    ("I5", "critical_se", "Seizure-free, SE make med 'not a fit', wants change -> switch (L5)",
     dict(external_id="I5", age=14, weight=55, epilepsy_type="focal",
          medication="valproic_acid", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=False, switch_to="lamotrigine"),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["dizziness"], "Q10c_impact": "not_a_fit",
      "Q12": "different_medicine", "Q10a": "at_goal"}),

    ("I6", "critical_se", "SE prevents further increase but med helping -> add-on candidate",
     dict(external_id="I6", age=8, weight=26, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q11": "better", "Q9": ["irritability"],
      "Q10b_impact": "medium_big", "Q10d_dose_limited_by_se": "yes",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "monthly")]}),

    ("I7", "critical_se", "Cannot reach minimum effective dose due to SE -> switch candidate",
     dict(external_id="I7", age=11, weight=36, epilepsy_type="generalized",
          medication="topiramate", dose_amount=50, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["acting_confused"],
      "Q10b_impact": "big_wants_change", "Q10e_min_goal_unreachable_se": "yes",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["generalized"], "1_3min", "weekly")]}),

    ("I8", "critical_se", "Clobazam sedation medium impact while titrating -> slow down",
     dict(external_id="I8", age=6, weight=20, epilepsy_type="generalized",
          medication="clobazam", dose_amount=5, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q9": ["sleepiness"], "Q10b_impact": "medium_big",
      "Q10a": "still_increasing",
      "seizure_episodes": [epi(["generalized"], "under_1min", "monthly")]}),

    ("I9", "critical_se", "Seizure-free at goal + big-impact SE -> LOWER the dose + labs (L4)",
     dict(external_id="I9", age=13, weight=46, epilepsy_type="generalized",
          medication="valproic_acid", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "no", "Q9": ["dizziness"], "Q10c_impact": "big",
      "Q10a": "at_goal"}),

    # ============ J. CRITICAL free-text seizure emergencies ====================
    ("J1", "critical_se", "Free text: seizure >5 min, called 911 -> URGENT",
     dict(external_id="J1", age=6, weight=20, epilepsy_type="generalized",
          medication="levetiracetam", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["generalized"], "over_3min", "monthly")],
      "Q13": "the seizure lasted more than 5 minutes and we called 911"}),

    ("J2", "critical_se", "Free text: back-to-back seizures without recovering -> URGENT",
     dict(external_id="J2", age=9, weight=29, epilepsy_type="focal",
          medication="valproic_acid", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")],
      "Q13": "he had back to back seizures without recovering in between"}),

    ("J3", "critical_se", "Free text: turned blue and unresponsive -> URGENT",
     dict(external_id="J3", age=10, weight=32, epilepsy_type="generalized",
          medication="oxcarbazepine", dose_amount=300, dose_unit="mg",
          frequency=2, can_increase=True, switch_to=None),
     {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
      "seizure_episodes": [epi(["generalized"], "1_3min", "monthly")],
      "Q13": "during the seizure he turned blue and was unresponsive for a bit"}),

    # ============ K. Indication mismatch / other switch =======================
    ("K1", "switch", "Ethosuximide (absence-only) reported with focal seizures -> switch",
     dict(external_id="K1", age=9, weight=28, epilepsy_type="focal",
          medication="ethosuximide", dose_amount=250, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "almost_never", "Q3": "yes", "Q4": ["focal"], "Q10a": "still_increasing",
      "seizure_episodes": [epi(["focal"], "1_3min", "weekly")]}),

    ("K2", "switch", "Stopped due to side effects -> switch to better-tolerated option",
     dict(external_id="K2", age=10, weight=30, epilepsy_type="focal",
          medication="zonisamide", dose_amount=100, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="levetiracetam"),
     {"Q2": "stopped", "stopped_reason": "side_effects", "Q3": "no", "Q10a": "at_goal"}),

    ("K3", "titration", "Seizure-free but family would like to try a different medicine",
     dict(external_id="K3", age=9, weight=31, epilepsy_type="focal",
          medication="levetiracetam", dose_amount=500, dose_unit="mg",
          frequency=2, can_increase=True, switch_to="brivaracetam"),
     {"Q2": "almost_never", "Q3": "no", "Q10a": "at_goal", "Q12": "different_medicine"}),
]


def run_all():
    asm = clinic.current_asm()
    rf = clinic.current_redflag()
    out = []
    counts = {}
    for cid, group, scenario, patient, answers in CASES:
        rec = run_engine(clinic.build_snapshot(patient, answers),
                         asm_registry=asm, drug_classifier=clinic.CLASSIFIER,
                         physician_name="your care team")
        rf_cats = sorted({fl.category for fl in rf.scan(answers["Q13"])}) \
            if rf and answers.get("Q13") else []
        priority, reasons = clinic.triage(rec, rf_cats)
        d = rec.to_dict()
        counts[d["category"]] = counts.get(d["category"], 0) + 1
        out.append({
            "case_id": cid,
            "group": group,
            "scenario": scenario,
            "priority": priority,
            "triage_reasons": reasons,
            "redflag_categories": rf_cats,
            "patient": patient,
            "answers": answers,
            "engine_output": d,
        })
    return out, counts


def main():
    cases, counts = run_all()
    payload = {
        "_README": ("Synthetic interval-survey cases for Dr. Barbour's review of the Aim 2 "
                    "rule-based engine. 100% synthetic - no PHI. Recommendation, AI summary, "
                    "draft family message, and safety flags are produced by engine_core "
                    "(rule-based) via the production path (clinic.build_snapshot -> run_engine "
                    "-> redflag scan -> triage); this file fabricates no clinical decision. "
                    "Every output is a DRAFT for physician review (verified:false, pending PI "
                    "sign-off, NEEDS_PI_SIGNOFF.md)."),
        "generated": "2026-07-14",
        "focus": "dose-titration goal/no-goal adjustments + critical side effects",
        "engine_version": cases[0]["engine_output"]["engine_version"] if cases else None,
        "n_cases": len(cases),
        "category_counts": counts,
        "priority_counts": _by(cases, "priority"),
        "cases": cases,
    }
    path = os.path.join(HERE, "review_cases_v2.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(cases)} cases -> {path}")
    print("category_counts:", json.dumps(counts, indent=2))
    print("priority_counts:", json.dumps(payload["priority_counts"], indent=2))


def _by(cases, key):
    c = {}
    for x in cases:
        c[x[key]] = c.get(x[key], 0) + 1
    return c


if __name__ == "__main__":
    main()
