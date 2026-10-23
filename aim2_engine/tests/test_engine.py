"""
test_engine.py
--------------
Offline unit tests for the Aim 2 rules engine (stdlib unittest; no Django).

Run:  python -m unittest discover -s tests        (from the aim2_engine/ dir)
  or: python tests/test_engine.py
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core import (
    run_engine, snapshot_from_interval, survey_from_answers,
    PatientContext, MedicationContext, PhysicianContingency,
    ASMRule, DoseBand, ASM_RULES, categorize,
    RecommendationCategory, Adherence, SideEffectSeverity, DoseGoalStatus,
    FamilyPreference, RashSeverity,
)


def make_snapshot(answers, med=None, cont=None, age=8, weight=30):
    p = PatientContext(external_id="T", age_years=age, weight_kg=weight, seizure_types=["focal"])
    m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                          frequency_per_day=2, **(med or {}))
    c = PhysicianContingency(**(cont or {"can_increase_dose": True, "can_switch_to": "lamotrigine"}))
    return snapshot_from_interval(p, m, c, answers)


def cat(answers, **kw):
    return run_engine(make_snapshot(answers, **kw)).category


# Per-type seizure episodes that DO/DON'T cross Kristen's "increase faster"
# thresholds (2026-06 buckets: frequent breakthrough lasting 1-3 min+ -> faster;
# <1 min -> continue). Non-whole-body so the whole-body >3-min DANGER rule
# (tested separately) is not triggered here.
FAST_EPISODES = [{"look": ["focal"], "duration": "1_3min", "frequency": 2,
                  "frequency_unit": "per_week"}]          # rank 2, 2/wk -> faster
SHORT_EPISODES = [{"look": ["focal"], "duration": "under_1min", "frequency": 5,
                   "frequency_unit": "per_week"}]         # rank 1 -> continue plan


class TestDecisionRules(unittest.TestCase):
    # Barbour 2026-06: a breakthrough seizure whose DURATION wasn't captured ->
    # defer (need more info), since duration decides continue-vs-increase-faster.
    def test_breakthrough_no_duration_needs_info(self):
        self.assertEqual(cat({"Q3": "yes", "Q10a": "still_increasing"}), "insufficient_data")

    # Frequent and/or long breakthrough seizures -> increase FASTER (R9).
    def test_breakthrough_frequent_long_escalates_faster(self):
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": FAST_EPISODES}))
        self.assertEqual(r.category, "escalate")
        self.assertEqual(r.pace, "faster")

    # Short/infrequent breakthrough while titrating -> continue plan (no change).
    def test_breakthrough_short_continue_plan(self):
        self.assertEqual(cat({"Q3": "yes", "Q10a": "still_increasing",
                              "seizure_episodes": SHORT_EPISODES}), "no_change")

    # REGRESSION (cross-type pairing bug): a FREQUENT-but-SHORT type (5/wk, <31s)
    # plus a RARE-but-LONG type (1/mo, 2-5 min). Neither type alone crosses an
    # increase-faster threshold, so the engine must NOT escalate faster - it must
    # continue the plan. (Before the fix it paired total freq with max duration.)
    def test_multitype_no_single_type_crosses_does_not_escalate_faster(self):
        episodes = [
            {"look": ["sudden_jerks"], "duration": "under_1min", "frequency": 5, "frequency_unit": "per_week"},
            {"look": ["focal"], "duration": "over_3min", "frequency": 1, "frequency_unit": "per_month"},
        ]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": episodes}))
        self.assertNotEqual(r.pace, "faster")
        self.assertEqual(r.category, "no_change")

    # ...but if ONE type alone crosses (>1/wk lasting 1-3 min), it DOES escalate faster.
    def test_multitype_one_type_crosses_escalates_faster(self):
        episodes = [
            {"look": ["sudden_jerks"], "duration": "under_1min", "frequency": 5, "frequency_unit": "per_week"},
            {"look": ["focal"], "duration": "1_3min", "frequency": 2, "frequency_unit": "per_week"},
        ]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": episodes}))
        self.assertEqual(r.category, "escalate")
        self.assertEqual(r.pace, "faster")

    # Seizure-free + manageable SE while titrating -> no change (supersedes the
    # old "slower uptitration" proposal example 2).
    def test_free_manageable_titrating_no_change(self):
        self.assertEqual(cat({"Q3": "no", "Q9": ["sleepiness"], "Q10a": "still_increasing",
                              "Q10b_impact": "small"}), "no_change")

    def test_ex3_free_intolerable_switch(self):
        r = run_engine(make_snapshot({"Q3": "no", "Q9": ["dizziness"], "Q10a": "at_goal",
                                      "Q10c_impact": "big_wants_change"}))
        self.assertEqual(r.category, "switch_medication")
        self.assertEqual(r.switch_target, "lamotrigine")  # uses contingency target if supplied

    def test_breakthrough_level1_titrating_urgent_escalates(self):
        # FREQ FIX 2026-07-23: SHORT_EPISODES is focal 5/wk = URGENT per Kristen's
        # threshold sheet (focal >=1/wk) and her B2 case. With a LEVEL-1 ("small")
        # side effect the urgent 6-level route fires -> quicken q3d/end 1 higher
        # (escalate), not the non-urgent "continue". (The dropped-frequency bug had
        # mis-tiered this non-urgent -> no_change.)
        self.assertEqual(cat({"Q3": "yes", "Q9": ["sleepiness"], "Q10a": "still_increasing",
                              "Q10b_impact": "small", "seizure_episodes": SHORT_EPISODES}),
                         "escalate")

    def test_breakthrough_intolerable_switch(self):
        self.assertEqual(cat({"Q3": "yes", "Q9": ["behavior"], "Q10b_impact": "big_wants_change"}),
                         "switch_medication")

    def test_at_ceiling_frequent_not_helping_switch(self):
        # Frequent/long breakthrough AT the clinician ceiling + not clearly helping -> switch.
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=500, max_daily_mg=500,
            standard_uptitration_step_mg=250, standard_uptitration_interval_weeks=2)])
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "at_goal", "Q11": "about_same",
                                      "seizure_episodes": FAST_EPISODES}), asm_rule=rule)
        # FREQ FIX 2026-07-23: focal 2/wk is urgent; at the ceiling this routes via
        # Kristen's urgent breakthrough table, whose at-max action is "add a second
        # medication" (was legacy R9-switch only because it was mis-tiered non-urgent).
        self.assertEqual(r.category, "add_medication")

    def test_at_ceiling_frequent_helping_add_medication(self):
        # Same but the med IS helping -> ADD a second medicine (Barbour 2026-06).
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=500, max_daily_mg=500,
            standard_uptitration_step_mg=250, standard_uptitration_interval_weeks=2)])
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "at_goal", "Q11": "better",
                                      "seizure_episodes": FAST_EPISODES}), asm_rule=rule)
        self.assertEqual(r.category, "add_medication")

    def test_family_pref_slower_modifier(self):
        self.assertEqual(cat({"Q3": "yes", "Q10a": "still_increasing", "Q12": "change_dose"}),
                         "slower_uptitration")

    def test_free_no_se_titrating_no_change(self):
        # Barbour: seizure-free + no SE + still titrating -> no change (continue plan).
        # Barbour review (case D4): follow-up is scheduled ONLY when the decision
        # table lists one; the No-Seizures "Continue on schedule" cell (L0) lists
        # none, so no 4-week recheck is appended (matches the 6-level path).
        r = run_engine(make_snapshot({"Q3": "no", "Q10a": "still_increasing"}))
        self.assertEqual(r.category, "no_change")
        self.assertIsNone(r.followup_weeks)

    def test_free_no_se_titrating_blocked_no_change(self):
        self.assertEqual(cat({"Q3": "no", "Q10a": "still_increasing"},
                             cont={"can_increase_dose": False}), "no_change")

    def test_free_at_goal_no_change(self):
        self.assertEqual(cat({"Q3": "no", "Q10a": "at_goal"}), "no_change")

    def test_free_titrating_but_dose_maxed_no_change(self):
        # Regression: seizure-free + no SE + still titrating, but already at the
        # clinician ceiling (dose_maximized True) and contingency unset (None)
        # must NOT escalate. (R8 must check dose_maximized, not only can_increase.)
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=500, max_daily_mg=500,
            standard_uptitration_step_mg=250, standard_uptitration_interval_weeks=2)])
        sn = make_snapshot({"Q3": "no", "Q10a": "still_increasing"},
                           cont={})  # can_increase_dose defaults to None
        r = run_engine(sn, asm_rule=rule)   # reported 250x2=500 == ceiling -> maxed
        self.assertEqual(r.category, "no_change")

    def test_free_goal_unknown_no_change(self):
        # Barbour I1-I3: seizure-free + no SE + dose-goal unknown -> no change.
        self.assertEqual(cat({"Q3": "no", "Q10a": "not_sure"}), "no_change")

    def test_dose_changed_with_seizures_defers(self):
        # Barbour I4: dose recently changed + ongoing seizures -> need more info.
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "not_sure"},
                                     med={"dose_changed_or_stopped": True}))
        self.assertEqual(r.category, "insufficient_data")

    def test_stopped_due_to_cost_switches(self):
        # Barbour C3: stopped (cost) -> switch.
        self.assertEqual(cat({"Q2": "stopped", "Q2b": "cost", "Q3": "yes",
                              "Q10a": "still_increasing"}), "switch_medication")

    def test_ethosuximide_wrong_seizure_type_switches(self):
        # Barbour J4: ethosuximide treats absence only; focal -> switch.
        from engine_core.asm_rules import load_asm_rules
        reg = load_asm_rules(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "asm_rules.json"))
        p = PatientContext(external_id="J4", age_years=7, weight_kg=25, seizure_types=["focal"])
        m = MedicationContext(medication="ethosuximide", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        sn = snapshot_from_interval(p, m, PhysicianContingency(),
                                    {"Q3": "yes", "Q4": ["focal"], "Q10a": "still_increasing"})
        self.assertEqual(run_engine(sn, asm_registry=reg).category, "switch_medication")

    def test_adherence_driven_no_change(self):
        self.assertEqual(cat({"Q2": "sometimes", "Q3": "yes", "Q7": False,
                              "Q10a": "still_increasing"}), "no_change")

    def test_q7_string_false_triggers_adherence_first(self):
        # Regression: Q7 as the string "false" (off meds) must coerce to bool
        # and route to adherence-first NO_CHANGE, not ESCALATE.
        self.assertEqual(cat({"Q2": "almost_never", "Q3": "yes", "Q7": "false",
                              "Q10a": "still_increasing"}), "no_change")
        # And the boolean form agrees.
        self.assertEqual(cat({"Q2": "almost_never", "Q3": "yes", "Q7": False,
                              "Q10a": "still_increasing"}), "no_change")

    def test_every_input_returns_category_and_rationale(self):
        r = run_engine(make_snapshot({"Q3": "no"}))
        self.assertIn(r.category, [c.value for c in RecommendationCategory])
        self.assertTrue(r.rationale and "rule" in r.rationale[-1])


class TestSafety(unittest.TestCase):
    def test_sjs_rash_switches_and_flags(self):
        # Barbour H1/H2/H5: spreading/blistering rash -> STOP + switch, urgent.
        r = run_engine(make_snapshot({"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"}))
        self.assertEqual(r.category, "switch_medication")
        self.assertTrue(r.safety_concern_flag)
        self.assertIn("sjs_rash", r.urgent_flags)

    def test_sjs_emits_approved_stop_draft(self):
        # Kristen 2026-07: a rash safety case now emits the engine's approved STOP
        # draft (was withheld/None). Still a physician-approved draft, never auto-sent;
        # it must tell the family to STOP and to contact the clinic, with no ER/911 copy.
        r = run_engine(make_snapshot({"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"}))
        self.assertIsNotNone(r.draft_patient_message)
        self.assertIn("stop giving", r.draft_patient_message.lower())
        self.assertIn("contact the clinic", r.draft_patient_message.lower())

    def test_urgent_safety_reported_passthrough(self):
        r = run_engine(make_snapshot({"Q3": "no", "urgent_safety": ["er_visit"]}))
        self.assertTrue(r.safety_concern_flag)
        self.assertIn("er_visit", r.urgent_flags)

    def test_large_rash_alone_switches_and_flags(self):
        # Barbour 2026-07-01: confirmed LARGE (non-blistering) rash alone also
        # triggers the urgent SJS switch pathway, same as blistering.
        r = run_engine(make_snapshot({"Q3": "no", "Q9": ["rash"], "Q9a": "large"}))
        self.assertEqual(r.category, "switch_medication")
        self.assertTrue(r.safety_concern_flag)
        self.assertIn("sjs_rash", r.urgent_flags)

    def test_large_rash_plus_other_urgent_flag_keeps_sjs_rash(self):
        # Regression (H5): a LARGE rash reported alongside an unrelated urgent
        # flag (e.g. an ER visit) must still surface "sjs_rash" in urgent_flags.
        # Before the 2026-07-01 fix, `categorize.SJS_TRIGGER_RASH` only matched
        # BLISTERING, so `sjs_triggered` was False for a LARGE-only rash; the
        # switch/safety_concern behavior still fired (via `rash_spreading`,
        # which already included LARGE), but decision.py's
        # `status.urgent_safety or ["sjs_rash"]` fallback never ran because
        # `status.urgent_safety` already held the other flag - silently
        # dropping "sjs_rash" from the list a clinician sees.
        r = run_engine(make_snapshot({"Q3": "no", "Q9": ["rash"], "Q9a": "large",
                                       "urgent_safety": ["er_visit"]}))
        self.assertEqual(r.category, "switch_medication")
        self.assertTrue(r.safety_concern_flag)
        self.assertIn("sjs_rash", r.urgent_flags)
        self.assertIn("er_visit", r.urgent_flags)


class TestCategorization(unittest.TestCase):
    def test_impact_mapping(self):
        # Barbour T7: "difficult but tolerable" (medium_big) is MANAGEABLE, not intolerable.
        s = categorize(make_snapshot({"Q3": "no", "Q9": ["x"], "Q10b_impact": "medium_big"}))
        self.assertEqual(s.side_effect_severity, SideEffectSeverity.MANAGEABLE_UNWANTED)
        s = categorize(make_snapshot({"Q3": "no", "Q9": ["x"], "Q10b_impact": "big_wants_change"}))
        self.assertEqual(s.side_effect_severity, SideEffectSeverity.INTOLERABLE)
        s = categorize(make_snapshot({"Q3": "no", "Q9": ["x"], "Q10b_impact": "small"}))
        self.assertEqual(s.side_effect_severity, SideEffectSeverity.MANAGEABLE_UNWANTED)
        s = categorize(make_snapshot({"Q3": "no"}))
        self.assertEqual(s.side_effect_severity, SideEffectSeverity.NONE)

    def test_dose_maximized_unknown_without_asm_data(self):
        s = categorize(make_snapshot({"Q3": "yes"}))
        self.assertIsNone(s.dose_maximized)

    def test_dose_maximized_with_asm_data(self):
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=500, max_daily_mg=500,
            standard_uptitration_step_mg=250, standard_uptitration_interval_weeks=2)])
        # reported 250 x2 = 500/day -> at ceiling -> maximized True
        s = categorize(make_snapshot({"Q3": "yes"}), rule)
        self.assertTrue(s.dose_maximized)

    def test_wants_med_change_flag(self):
        s = categorize(make_snapshot({"Q3": "no", "Q12": "different_medicine"}))
        self.assertTrue(s.wants_med_change)


class TestOutputsHardRules(unittest.TestCase):
    def test_no_titration_numbers_without_asm_data(self):
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": FAST_EPISODES}))
        self.assertEqual(r.category, "escalate")
        self.assertEqual(r.titration_schedule["status"], "unavailable")

    def test_titration_numbers_only_from_supplied_data(self):
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=1500, max_daily_mg=1500,
            standard_uptitration_step_mg=500, standard_uptitration_interval_weeks=2)])
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": FAST_EPISODES}), asm_rule=rule)
        self.assertEqual(r.titration_schedule["status"], "ok")
        targets = [s["target_total_mg_per_day"] for s in r.titration_schedule["steps"]]
        self.assertEqual(targets, [1000, 1500])  # 500 -> +500 -> +500 capped at 1500

    def test_clinical_summary_is_human_readable(self):
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": FAST_EPISODES}))
        # Plain-English note: friendly section + reason, draft notice, and NO
        # developer rule codes / proposal references in the human text.
        # (Kristen v2 review, D1) the verbose "Why this is suggested" block is
        # dropped ONLY on no-change recommendations; this case ESCALATES, so the
        # block is present. (The concise no-change path is covered below.)
        self.assertIn("Why this is suggested", r.clinical_summary)
        self.assertIn("Reason:", r.clinical_summary)
        self.assertIn("DRAFT", r.clinical_summary)
        self.assertNotIn("[R9]", r.clinical_summary)
        self.assertNotIn("proposal example", r.clinical_summary)
        # ...but the machine-readable rule trace is still in the rationale (audit).
        # FREQ FIX 2026-07-23: FAST_EPISODES (focal 2/wk) is now correctly urgent, so
        # the escalate-faster plan comes from Kristen's urgent breakthrough table
        # (rule "BT") rather than the legacy R9 path.
        self.assertTrue(any(x["rule"] == "BT" for x in r.rationale))

    def test_no_change_summary_is_concise(self):
        # (Kristen v2 review, D1) a no-change recommendation drops the verbose
        # "Why this is suggested" block and the do-not-switch list; the one-line
        # "Reason:" carries the why. Safety notes are always kept (guardrail).
        r = run_engine(make_snapshot({"Q3": "no", "Q10a": "still_increasing"}))
        self.assertEqual(r.category, "no_change")
        self.assertNotIn("Why this is suggested", r.clinical_summary)
        self.assertNotIn("Do NOT switch", r.clinical_summary)
        self.assertIn("Reason:", r.clinical_summary)

    # --- note layout: fixed, numbered sections in a fixed order ---------------
    def _section_headers(self, summary):
        """[(number, TITLE)] for the note's section headers, in render order."""
        return [(int(mo.group(1)), mo.group(2))
                for mo in (re.match(r"^(\d+)\. ([A-Z][A-Z &]+)$", ln)
                           for ln in summary.split("\n")) if mo]

    def test_summary_sections_are_numbered_in_fixed_order(self):
        # Every case, whatever the category, lays the note out in the SAME order:
        # a reviewer always finds the same kind of fact in the same place. Sections
        # with nothing to say are omitted and the numbering closes up (no gaps).
        canonical = ["MEDICATION AND DOSE", "SEIZURES", "SIDE EFFECTS",
                     "FAMILY REPORT", "RECOMMENDATION", "SAFETY", "CHECKS"]
        for answers in ({"Q3": "no", "Q10a": "at_goal"},
                        {"Q3": "yes", "Q10a": "still_increasing"},
                        {"Q3": "yes", "Q10a": "still_increasing",
                         "seizure_episodes": FAST_EPISODES},
                        {"Q3": "no", "Q10a": "at_goal", "Q9": ["rash"],
                         "Q9a": "blisters", "urgent_safety": ["rash_blistering"]}):
            summary = run_engine(make_snapshot(answers)).clinical_summary
            headers = self._section_headers(summary)
            titles = [t for _, t in headers]
            self.assertEqual([n for n, _ in headers], list(range(1, len(headers) + 1)),
                             f"section numbering has a gap: {headers}")
            self.assertEqual(titles, [t for t in canonical if t in titles],
                             f"sections out of canonical order: {titles}")
            # The four always-present sections, and the notice last.
            self.assertEqual(titles[:5], canonical[:5])
            self.assertEqual(summary.strip().split("\n")[-1],
                             "No recommendation is final until the reviewing physician "
                             "approves it.")

    def test_all_three_urgency_tiers_reach_the_note(self):
        # The tier used to print only on the breakthrough-table path, so a
        # non-urgent breakthrough showed none and so did an EMERGENT one (whole-body
        # >3 min routes through the danger rule) - the case where the reviewer most
        # needs it. The note now falls back to the classifier's tier. Hyphenated:
        # "NON_URGENT" reads as a code token and broke the review-page badge.
        cases = {
            "NON-URGENT": [{"look": ["focal"], "duration": "under_1min",
                            "frequency": 1, "frequency_unit": "per_month"}],
            "URGENT": [{"look": ["focal"], "duration": "1_3min",
                        "frequency": 2, "frequency_unit": "per_week"}],
            "EMERGENT": [{"look": ["whole_body"], "duration": "over_3min",
                          "frequency": 1, "frequency_unit": "per_week"}],
        }
        for expected, episodes in cases.items():
            summary = run_engine(make_snapshot(
                {"Q3": "yes", "Q10a": "still_increasing",
                 "seizure_episodes": episodes})).clinical_summary
            self.assertIn(f"Urgency tier: {expected}", summary,
                          f"{expected} tier missing from the note")
            self.assertNotIn("_URGENT", summary)      # no raw token form

    def test_side_effect_level_is_stated_explicitly(self):
        # A 5400-case sweep found Kristen's L0-L5 level reached the note only
        # incidentally, embedded in the one-line Reason. It indexes her decision
        # tables, so the reviewer should see the same number the rule used.
        r = run_engine(make_snapshot({"Q3": "no", "Q10a": "at_goal",
                                      "Q9": ["sleepiness"], "Q10b_impact": "big"}))
        self.assertRegex(r.clinical_summary, r"Side-effect level: L[0-5]")

    def test_safety_flag_renders_inside_the_safety_section(self):
        # Safety content lives in the SAFETY section, not scattered through the note.
        r = run_engine(make_snapshot({"Q3": "no", "Q10a": "at_goal", "Q9": ["rash"],
                                      "Q9a": "blisters",
                                      "urgent_safety": ["rash_blistering"]}))
        self.assertTrue(r.safety_concern_flag)
        body = r.clinical_summary.split("SAFETY\n", 1)
        self.assertEqual(len(body), 2, "no SAFETY section rendered")
        self.assertIn("urgent safety concern was flagged", body[1])

    def test_default_registry_empty(self):
        self.assertEqual(ASM_RULES, {})

    def test_determinism(self):
        a = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing"}))
        b = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing"}))
        self.assertEqual(a.to_dict(), b.to_dict())


class TestMapper(unittest.TestCase):
    def test_survey_from_answers(self):
        s = survey_from_answers({"Q2": "sometimes", "Q3": "yes", "Q4": ["whole_body"],
                                 "Q5": 3, "Q5_unit": "per_week", "Q6": "more", "Q7": False,
                                 "Q9": ["rash"], "Q9a": "blisters", "Q10a": "at_goal",
                                 "Q12": "different_medicine", "Q13": "thanks"})
        self.assertEqual(s.adherence, Adherence.PARTIAL)
        self.assertEqual(s.dose_goal_status, DoseGoalStatus.AT_GOAL)
        self.assertEqual(s.rash_severity, RashSeverity.BLISTERING)
        self.assertEqual(s.family_preference, FamilyPreference.TRY_DIFFERENT_MED)
        self.assertEqual(s.seizure_count, 3)
        self.assertFalse(s.on_medication_2days_prior)
        self.assertEqual(s.free_text, "thanks")

    def test_q7_bool_coercion(self):
        from engine_core.snapshot import _to_bool
        self.assertIs(_to_bool(False), False)
        self.assertIs(_to_bool("false"), False)
        self.assertIs(_to_bool("no"), False)
        self.assertIs(_to_bool("true"), True)
        self.assertIs(_to_bool("yes"), True)
        self.assertIsNone(_to_bool("maybe"))
        self.assertIsNone(_to_bool(None))
        self.assertIs(survey_from_answers({"Q7": "false"}).on_medication_2days_prior, False)


class TestMedicationRecognition(unittest.TestCase):
    def _snap(self, med_name, answers):
        p = PatientContext(external_id="T", age_years=8, weight_kg=30)
        m = MedicationContext(medication=med_name, dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_increase_dose=True)
        return snapshot_from_interval(p, m, c, answers)

    def test_unknown_medication_flagged_with_name(self):
        r = run_engine(self._snap("aspirin", {"Q3": "no", "Q10a": "at_goal"}))
        self.assertFalse(r.medication_recognized)
        self.assertIn("aspirin", r.medication_flag)
        # The flag reaches the physician note in its own CHECKS section, ABOVE the
        # closing review notice (it used to be concatenated on after it).
        self.assertIn("CHECKS", r.clinical_summary)
        self.assertIn("Unrecognized medication 'aspirin'", r.clinical_summary)
        self.assertLess(r.clinical_summary.index("Unrecognized medication"),
                        r.clinical_summary.index("No recommendation is final"))

    def test_known_generic_not_flagged(self):
        r = run_engine(self._snap("levetiracetam", {"Q3": "no", "Q10a": "at_goal"}))
        self.assertTrue(r.medication_recognized)
        self.assertIsNone(r.medication_flag)

    def test_brand_recognized_without_classifier(self):
        r = run_engine(self._snap("Keppra", {"Q3": "no", "Q10a": "at_goal"}))
        self.assertTrue(r.medication_recognized)

    def test_unknown_medication_still_returns_category(self):
        # Flagging the name must not crash or block the (med-agnostic) category.
        r = run_engine(self._snap("madeupazine", {"Q3": "yes", "Q10a": "still_increasing"}))
        self.assertIn(r.category, [c.value for c in RecommendationCategory])
        self.assertFalse(r.medication_recognized)


class TestBarbour202606(unittest.TestCase):
    """Barbour 2026-06 update: whole-body >3min danger, <1min fine, add-vs-switch,
    the new ADD_MEDICATION category, the preference/adjustment prompts, the new
    'acting confused' side effect, the 'Other' free-text box, and the unresponsive
    capture."""

    # --- whole-body > 3 min = DANGER (urgent) ---
    def test_whole_body_over_3min_is_danger(self):
        eps = [{"look": ["whole_body"], "duration": "over_3min", "frequency": 1,
                "frequency_unit": "per_week"}]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "Q11": "better", "seizure_episodes": eps}))
        self.assertTrue(r.safety_concern_flag)
        self.assertIn("whole_body_over_3min", r.urgent_flags)

    def test_whole_body_under_1min_titrating_is_fine(self):
        eps = [{"look": ["whole_body"], "duration": "under_1min", "frequency": 5,
                "frequency_unit": "per_week"}]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": eps}))
        self.assertEqual(r.category, "no_change")
        self.assertFalse(r.safety_concern_flag)

    # --- frequency RANGE buckets work without an explicit count ---
    def test_frequency_bucket_daily_long_escalates_faster(self):
        eps = [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "daily"}]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": eps}))
        self.assertEqual(r.category, "escalate")
        self.assertEqual(r.pace, "faster")

    # --- ADD vs SWITCH ---
    def test_add_when_helping_but_dose_limited_by_se(self):
        # Helping, side effects block further increases, still seizing -> ADD.
        r = run_engine(make_snapshot(
            {"Q3": "yes", "Q10a": "still_increasing", "Q11": "better",
             "Q9": ["sleepiness"], "Q10b_impact": "small",
             "Q10d_dose_limited_by_se": "yes",
             "seizure_episodes": SHORT_EPISODES}))
        self.assertEqual(r.category, "add_medication")

    def test_switch_when_min_goal_unreachable_due_to_se(self):
        r = run_engine(make_snapshot(
            {"Q3": "no", "Q10a": "still_increasing",
             "Q10e_min_goal_unreachable_se": "yes"}))
        self.assertEqual(r.category, "switch_medication")

    # --- preference / adjustment prompts ---
    def test_at_goal_medium_impact_breakthrough_asks_preference(self):
        r = run_engine(make_snapshot(
            {"Q3": "yes", "Q10a": "at_goal", "Q9": ["sleepiness"],
             "Q10b_impact": "medium_big", "seizure_episodes": SHORT_EPISODES}))
        self.assertEqual(r.category, "insufficient_data")
        self.assertIsNotNone(r.family_prompt)
        self.assertIn("adding another medication", r.family_prompt)

    # SUPERSEDED by Barbour v2 review (2026-07-16, case B4). Previously a
    # breakthrough while titrating with MEDIUM-but-tolerable (Level 2) side
    # effects auto-slowed the uptitration (old R13). Her reconciled tier table
    # says a NON-URGENT breakthrough continues titration ON SCHEDULE - the
    # tolerable side effect no longer forces a slow-down; seizure urgency drives
    # the plan. (The old 1-week adjustment family prompt is not in her table.)
    def test_titrating_nonurgent_medium_impact_continues_on_schedule(self):
        r = run_engine(make_snapshot(
            {"Q3": "yes", "Q10a": "still_increasing", "Q9": ["sleepiness"],
             "Q10b_impact": "medium_big", "seizure_episodes": SHORT_EPISODES}))
        # FREQ FIX 2026-07-23: focal 5/wk is URGENT (threshold sheet: focal >=1/wk),
        # so this no longer hits the non-urgent "continue" cell; a frequent focal
        # breakthrough + tolerable SE while titrating falls to legacy R13 -> slower.
        # FLAG for PI: escalate (6-level urgent table) vs slower (R13) for urgent +
        # tolerable-SE is unreconciled; also no-SE->continue vs small-SE->escalate.
        self.assertEqual(r.category, "slower_uptitration")
        # 2026-08-05: `urgency_tier` now means "the urgency tier of THIS
        # breakthrough", not "the tier the firing rule happened to tag". R13 is a
        # legacy path that tags none, so the field falls back to the classifier -
        # which rates this focal 5/wk breakthrough urgent, exactly as the comment
        # above says. The previous assertIsNone documented a per-path gap that hid
        # the tier from the note and the review queue (emergent cases showed none).
        # The rule trace in `rationale` is what identifies which path fired.
        self.assertEqual(r.urgency_tier, "urgent")
        self.assertTrue(any(x["rule"] == "R13" for x in r.rationale))

    # Barbour v2 review (2026-07-16, cases B3/B4/B5): breakthrough URGENCY tier
    # drives the plan via her reconciled tier table even for legacy-encoded /
    # no-side-effect inputs (previously these fell back to R9/R13 and missed the
    # tier protocol). Uses frequency_bucket episodes so the tier is confident.
    def test_v2_tier_route_legacy_and_no_side_effect_breakthrough(self):
        # B5-like: URGENT (weekly focal, 1-3 min) + legacy "medium" token while
        # titrating -> quicken (escalate faster), NOT slow down.
        r = run_engine(make_snapshot(
            {"Q3": "yes", "Q10a": "still_increasing", "Q9": ["dizziness"],
             "Q10b_impact": "medium_big",
             "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                   "frequency_bucket": "weekly"}]}))
        self.assertEqual(r.category, "escalate")
        self.assertEqual(r.urgency_tier, "urgent")
        self.assertEqual(r.pace, "faster")
        # B3-like: EMERGENT (daily generalized, 1-3 min) + NO side effects while
        # titrating -> emergent protocol (escalate faster) with structured steps.
        r2 = run_engine(make_snapshot(
            {"Q3": "yes", "Q10a": "still_increasing",
             "seizure_episodes": [{"look": ["generalized"], "duration": "1_3min",
                                   "frequency_bucket": "daily"}]}))
        self.assertEqual(r2.category, "escalate")
        self.assertEqual(r2.urgency_tier, "emergent")
        self.assertIn("2 dose increase", r2.guidance_steps)

    def test_b3_planned_end_dose_compute(self):
        # B3 (Kristen, PI-confirmed 2026-07-16): capture the family-reported planned
        # end dose and, for an urgent "end +N higher" titrating plan, compute the
        # target N rungs up the clinician ladder FROM that end dose (not today's dose).
        a = {"Q3": "yes", "Q10a": "still_increasing",
             "Q10f_planned_end_mg_per_day": 1500,
             "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                   "frequency_bucket": "weekly"}]}
        snap = make_snapshot(a)
        self.assertEqual(snap.survey.planned_end_total_mg_per_day, 1500.0)
        r = run_engine(snap)
        self.assertEqual(r.urgency_tier, "urgent")
        self.assertIn("Planned end dose (family-reported): 1500", r.clinical_summary)
        self.assertIn("end +1 higher", r.clinical_summary)

    def test_b3_no_end_dose_no_line(self):
        # Without the planned end dose the summary shows no B3 target line (falls
        # back to the guidance text; no regression for existing cases).
        a = {"Q3": "yes", "Q10a": "still_increasing",
             "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                   "frequency_bucket": "weekly"}]}
        self.assertNotIn("Planned end dose", run_engine(make_snapshot(a)).clinical_summary)

    # --- new side effect option + Other free text + unresponsive capture ---
    def test_acting_confused_side_effect_and_other_text(self):
        r = run_engine(make_snapshot(
            {"Q3": "no", "Q10a": "at_goal", "Q9": ["acting_confused"],
             "Q9_other": "grinds teeth at night", "Q10b_impact": "small"}))
        self.assertIn("acting confused", r.clinical_summary)
        self.assertIn("grinds teeth", r.clinical_summary)

    def test_unresponsive_episode_captured(self):
        snap = make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                              "seizure_episodes": [{"look": ["whole_body"],
                              "duration": "under_1min", "frequency": 1,
                              "frequency_unit": "per_week", "unresponsive": True}]})
        st = categorize(snap)
        self.assertTrue(st.any_unresponsive)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSeizureFrequencyLine(unittest.TestCase):
    """Kristen v2 review (case B2): per-type frequency replaces 'number not given'."""

    def test_no_number_not_given_when_per_type_frequency_known(self):
        eps = [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly"}]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": eps}))
        self.assertNotIn("number not given", r.clinical_summary)
        self.assertIn("occurring about weekly", r.clinical_summary)

    def test_number_not_given_kept_when_nothing_captured(self):
        eps = [{"look": ["focal"], "duration": "1_3min"}]
        r = run_engine(make_snapshot({"Q3": "yes", "Q10a": "still_increasing",
                                      "seizure_episodes": eps}))
        self.assertIn("number not given", r.clinical_summary)
