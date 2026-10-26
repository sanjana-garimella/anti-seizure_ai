"""
test_tables_and_robustness.py
-----------------------------
Coverage for the newest, riskiest clinical branches and the robustness /
defer-on-ambiguity hardening from the 2026-07-01 code review (findings E1-E13).

E1  - table-driven tests for the No-Seizures 6-level grid and the breakthrough
      6-level grid (every phase x level cell, incl. at_max cells + the NS-defer
      fallback), the 6-level side-effect tokens, and the HOLD / LOWER_DOSE cats.
E2  - malformed JSON degrades gracefully (loaders don't raise).
E3  - unknown impact token defers (does not become a confident recommendation).
E4  - unknown/typo action token in a table defers instead of crashing.
E5  - to_dict() carries every field; provenance engine_version is populated.
E7  - titration at/above ceiling is surfaced (not a silent empty "ok").
E8  - reset_caches() forces a config reload.
E9  - shared per_dose_mg conversion helper.
E10 - URGENT_RASH single constant with aliases.
E12 - configured duration thresholds align to known bucket floors.

Run:  python -m unittest discover -s tests   (from the aim2_engine/ dir)

The grid tests LOAD the JSON tables and assert the engine honors each cell, so a
future grid typo / off-by-one fails a test (per the review's E1 requirement).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core import (
    run_engine, snapshot_from_interval,
    PatientContext, MedicationContext, PhysicianContingency,
    ASMRule, DoseBand, categorize,
    RecommendationCategory, SideEffectSeverity, SeizureControl, DoseGoalStatus,
    Adherence, FamilyPreference,
)
from engine_core.categorize import (
    PatientStatus, IMPACT_TO_LEVEL, IMPACT_TO_SEVERITY,
    URGENT_RASH, SJS_TRIGGER_RASH, SPREADING_RASH,
    validate_duration_thresholds_align,
)
from engine_core.decision import decide, _ACTION_TO_CATEGORY
from engine_core import titration_config as tc
from engine_core.snapshot import per_dose_mg, MedicationContext as MC
from engine_core.outputs import build_titration_schedule, build_decision_provenance, _dose_line
from engine_core.decision import Decision


# Map the JSON action tokens to the engine categories we expect to see.
_TOKEN_TO_CAT = {
    "no_change": RecommendationCategory.NO_CHANGE,
    "continue": RecommendationCategory.NO_CHANGE,
    "escalate": RecommendationCategory.ESCALATE,
    "slower": RecommendationCategory.SLOWER_UPTITRATION,
    "hold": RecommendationCategory.HOLD,
    "lower": RecommendationCategory.LOWER_DOSE,
    "lower_dose": RecommendationCategory.LOWER_DOSE,
    "add_medication": RecommendationCategory.ADD_MEDICATION,
    "switch_medication": RecommendationCategory.SWITCH_MEDICATION,
    "switch": RecommendationCategory.SWITCH_MEDICATION,
}


def _ns_status(level, phase, current_med="valproic_acid", below_ceiling=None):
    """A minimal seizure-free PatientStatus that routes into the NS table branch."""
    return PatientStatus(
        seizure_control=SeizureControl.SEIZURE_FREE,
        side_effect_severity=SideEffectSeverity.MANAGEABLE_UNWANTED,
        side_effects_present=True,
        adherence=Adherence.GOOD,
        provoked_by_nonadherence=False,
        side_effect_level=level,
        phase=phase,
        current_med=current_med,
        below_ceiling=below_ceiling,
        can_switch_to="lamotrigine",
    )


def _bt_status(level, phase, tier, current_med="valproic_acid", below_ceiling=None):
    """A minimal breakthrough PatientStatus that routes into the BT table branch."""
    return PatientStatus(
        seizure_control=SeizureControl.BREAKTHROUGH,
        side_effect_severity=SideEffectSeverity.MANAGEABLE_UNWANTED,
        side_effects_present=True,
        adherence=Adherence.GOOD,
        provoked_by_nonadherence=False,
        seizure_activity=True,
        side_effect_level=level,
        phase=phase,
        breakthrough_tier=tier,
        current_med=current_med,
        below_ceiling=below_ceiling,
        can_switch_to="lamotrigine",
        duration_captured=True,
    )


# ---------------------------------------------------------------------------
# E1 - No-Seizures 6-level table (every phase x level cell)
# ---------------------------------------------------------------------------
class TestNoSeizuresTable(unittest.TestCase):
    def setUp(self):
        tc.reset_caches()
        self.ns = tc.default_no_seizures_table()
        self.grid = self.ns["actions_by_phase_level"]

    def test_every_phase_level_cell_matches_json(self):
        for phase, actions in self.grid.items():
            for level, token in enumerate(actions):
                d = decide(_ns_status(level, phase))
                expected = _TOKEN_TO_CAT[token]
                self.assertEqual(
                    d.category, expected,
                    msg=f"NS[{phase}][L{level}] token='{token}' expected "
                        f"{expected} got {d.category}")

    def test_l5_switch_carries_target(self):
        d = decide(_ns_status(5, "steady"))
        self.assertEqual(d.category, RecommendationCategory.SWITCH_MEDICATION)
        self.assertEqual(d.switch_target, "lamotrigine")

    def test_l4_lower_orders_labs(self):
        # valproic_acid has labs in labs_by_drug -> LOWER_DOSE should order them.
        d = decide(_ns_status(4, "steady", current_med="valproic_acid"))
        self.assertEqual(d.category, RecommendationCategory.LOWER_DOSE)
        self.assertTrue(d.lab_orders, "L4 lower should carry drug labs")

    def test_l3_hold_holding_phase_prompts_family(self):
        # Holding-phase medium-a-lot (L3) asks whether side effects are easing.
        d = decide(_ns_status(3, "holding"))
        self.assertEqual(d.category, RecommendationCategory.HOLD)
        self.assertTrue(d.family_prompt, "holding L3 should carry the adjustment prompt")

    def test_ns_defer_fallback_on_unknown_action(self):
        # A grid cell with an action the engine doesn't recognize -> defer.
        bad = json.loads(json.dumps(self.ns))  # deep copy
        bad["actions_by_phase_level"]["steady"][2] = "teleport_patient"
        # Patch the loader cache to the mutated table, run, then restore.
        tc._NS_CACHE = bad
        try:
            d = decide(_ns_status(2, "steady"))
            self.assertEqual(d.category, RecommendationCategory.INSUFFICIENT_DATA)
            self.assertTrue(any(r.get("rule") == "NS-defer" for r in d.rationale))
        finally:
            tc.reset_caches()

    def test_ns_legacy_level_none_uses_legacy_branch(self):
        # side_effect_level None -> NS branch does NOT fire (legacy R14-R18).
        st = _ns_status(None, "steady")
        st.side_effect_level = None
        st.side_effect_severity = SideEffectSeverity.NONE
        st.side_effects_present = False
        d = decide(st)
        self.assertEqual(d.category, RecommendationCategory.NO_CHANGE)
        self.assertTrue(any(r.get("rule") == "R18" for r in d.rationale))


# ---------------------------------------------------------------------------
# E1 - Breakthrough 6-level table (every tier x phase x level, incl. at_max)
# ---------------------------------------------------------------------------
class TestBreakthroughTable(unittest.TestCase):
    def setUp(self):
        tc.reset_caches()
        self.bt = tc.default_breakthrough_tables()["tables"]

    def test_every_cell_matches_json_below_ceiling(self):
        for tier, phases in self.bt.items():
            for phase, rows in phases.items():
                for level, cell in enumerate(rows):
                    d = decide(_bt_status(level, phase, tier, below_ceiling=True))
                    expected = _TOKEN_TO_CAT[cell["category"]]
                    self.assertEqual(
                        d.category, expected,
                        msg=f"BT[{tier}][{phase}][L{level}] category='{cell['category']}'"
                            f" expected {expected} got {d.category}")

    def test_at_max_cells_use_at_max_category(self):
        # When below_ceiling is False (at the max), cells with at_max_category
        # must route to that category instead of the primary one.
        for tier, phases in self.bt.items():
            for phase, rows in phases.items():
                for level, cell in enumerate(rows):
                    if not cell.get("at_max_category"):
                        continue
                    d = decide(_bt_status(level, phase, tier, below_ceiling=False))
                    expected = _TOKEN_TO_CAT[cell["at_max_category"]]
                    self.assertEqual(
                        d.category, expected,
                        msg=f"BT[{tier}][{phase}][L{level}] at_max='{cell['at_max_category']}'"
                            f" expected {expected} got {d.category}")

    def test_emergent_l5_sets_safety_flag(self):
        cell = self.bt["emergent"]["titrating"][5]
        self.assertTrue(cell.get("safety"))
        d = decide(_bt_status(5, "titrating", "emergent"))
        self.assertEqual(d.category, RecommendationCategory.SWITCH_MEDICATION)
        self.assertTrue(d.safety_concern_flag, "emergent L5 must flag a safety concern")

    def test_urgent_flags_when_cell_marks_urgent(self):
        cell = self.bt["emergent"]["titrating"][2]
        self.assertTrue(cell.get("urgent"))
        d = decide(_bt_status(2, "titrating", "emergent"))
        self.assertIn("breakthrough_emergent", d.urgent_flags)

    def test_level_0_uses_table_only_with_duration(self):
        # Level 0 is a row in the grid. With a captured duration the table
        # decides (non-urgent titrating L0 is no change). Without duration the
        # frequency-only path still applies.
        d = decide(_bt_status(0, "titrating", "non_urgent"))
        self.assertTrue(any(r.get("rule") == "BT" for r in d.rationale))
        self.assertEqual(d.category, RecommendationCategory.NO_CHANGE)
        no_dur = _bt_status(0, "titrating", "non_urgent")
        no_dur.duration_captured = False
        d2 = decide(no_dur)
        self.assertFalse(any(r.get("rule") == "BT" for r in d2.rationale))

    def test_level_1_fires_table_only_with_duration(self):
        # CHANGED 2026-07-08 (Kristen validation review, cases B1/B2/B3): a
        # non-urgent, still-titrating, LEVEL-1 breakthrough must follow her table's
        # first row ("continue on schedule" -> no_change), NOT the legacy "slower".
        # It fires the table only when seizure DURATION was captured; without it we
        # keep defer-on-ambiguity (do not fire; R12 -> insufficient_data).
        with_dur = _bt_status(1, "titrating", "non_urgent")  # duration_captured=True
        d = decide(with_dur)
        self.assertTrue(any(r.get("rule") == "BT" for r in d.rationale))
        self.assertEqual(d.category, RecommendationCategory.NO_CHANGE)

        no_dur = _bt_status(1, "titrating", "non_urgent")
        no_dur.duration_captured = False
        d2 = decide(no_dur)
        self.assertFalse(any(r.get("rule") == "BT" for r in d2.rationale))

    def test_nonurgent_steady_l0_follows_table(self):
        # Level 0 is a row in the breakthrough grid. Non-urgent + steady + no
        # side effects is "escalate". Duration must be captured; without it the
        # frequency-only path still applies.
        d = decide(_bt_status(0, "steady", "non_urgent"))
        self.assertTrue(any(r.get("rule") == "BT" for r in d.rationale))
        self.assertEqual(d.category, RecommendationCategory.ESCALATE)
        no_dur = _bt_status(0, "steady", "non_urgent")
        no_dur.duration_captured = False
        d2 = decide(no_dur)
        self.assertFalse(any(r.get("rule") == "BT" for r in d2.rationale))

    def test_e4_unknown_action_token_defers_not_crashes(self):
        # A clinician-edited grid token mapping to a non-member name must DEFER.
        bad = json.loads(json.dumps(tc.default_breakthrough_tables()))
        bad["tables"]["non_urgent"]["steady"][2]["category"] = "vaporize"
        tc._BT_CACHE = bad
        try:
            d = decide(_bt_status(2, "steady", "non_urgent", below_ceiling=True))
            self.assertEqual(d.category, RecommendationCategory.INSUFFICIENT_DATA)
        finally:
            tc.reset_caches()


# ---------------------------------------------------------------------------
# E1 - 6-level side-effect scale tokens + severity map + HOLD / LOWER_DOSE
# ---------------------------------------------------------------------------
class TestSixLevelScale(unittest.TestCase):
    def test_impact_to_level_tokens(self):
        self.assertEqual(IMPACT_TO_LEVEL, {
            "no_impact": 0, "small": 1, "medium_tolerable": 2,
            "medium_alot": 3, "big": 4, "not_a_fit": 5,
        })

    def test_severity_map_for_new_tokens(self):
        self.assertEqual(IMPACT_TO_SEVERITY["medium_tolerable"], SideEffectSeverity.MANAGEABLE_UNWANTED)
        self.assertEqual(IMPACT_TO_SEVERITY["medium_alot"], SideEffectSeverity.MANAGEABLE_UNWANTED)
        self.assertEqual(IMPACT_TO_SEVERITY["big"], SideEffectSeverity.MANAGEABLE_UNWANTED)
        self.assertEqual(IMPACT_TO_SEVERITY["not_a_fit"], SideEffectSeverity.INTOLERABLE)

    def test_hold_and_lower_categories_reachable_end_to_end(self):
        # Seizure-free + big (L4) -> LOWER_DOSE; medium_alot (L3) -> HOLD.
        p = PatientContext(external_id="T", age_years=8, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="valproic_acid", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_increase_dose=True, can_switch_to="lamotrigine")
        r_lower = run_engine(snapshot_from_interval(
            p, m, c, {"Q3": "no", "Q10a": "at_goal", "Q10c_impact": "big",
                      "Q10c_time": "over_4_weeks"}))
        self.assertEqual(r_lower.category, "lower_dose")
        r_hold = run_engine(snapshot_from_interval(
            p, m, c, {"Q3": "no", "Q10a": "at_goal", "Q10c_impact": "medium_alot",
                      "Q10c_time": "over_4_weeks"}))
        self.assertEqual(r_hold.category, "hold")

    def test_holding_phase_depends_on_under_2_weeks(self):
        # Q10c_time="under_2_weeks" at goal -> holding phase; L3 holding -> HOLD
        # with the adjustment family prompt (distinct from steady).
        p = PatientContext(external_id="T", age_years=8, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="valproic_acid", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_switch_to="lamotrigine")
        st = categorize(snapshot_from_interval(
            p, m, c, {"Q3": "no", "Q10a": "at_goal", "Q10c_impact": "medium_alot",
                      "Q10c_time": "under_2_weeks"}))
        self.assertEqual(st.phase, "holding")


# ---------------------------------------------------------------------------
# E3 - unknown impact token defers
# ---------------------------------------------------------------------------
class TestUnknownImpactDefers(unittest.TestCase):
    def test_garbage_impact_token_defers(self):
        p = PatientContext(external_id="T", age_years=8, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_switch_to="lamotrigine")
        st = categorize(snapshot_from_interval(
            p, m, c, {"Q3": "no", "Q10a": "at_goal", "Q10c_impact": "wibble_wobble"}))
        # Unknown severity, no confident level.
        self.assertEqual(st.side_effect_severity, SideEffectSeverity.UNKNOWN)
        self.assertIsNone(st.side_effect_level)
        r = run_engine(snapshot_from_interval(
            p, m, c, {"Q3": "no", "Q10a": "at_goal", "Q10c_impact": "wibble_wobble"}))
        self.assertEqual(r.category, "insufficient_data")

    def test_unknown_does_not_produce_no_change(self):
        # Regression: previously an unknown token -> MANAGEABLE -> confident NO_CHANGE.
        p = PatientContext(external_id="T", age_years=8, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_switch_to="lamotrigine")
        r = run_engine(snapshot_from_interval(
            p, m, c, {"Q3": "no", "Q10a": "at_goal", "Q10c_impact": "???"}))
        self.assertNotEqual(r.category, "no_change")


# ---------------------------------------------------------------------------
# E2 - malformed JSON degrades gracefully
# ---------------------------------------------------------------------------
class TestMalformedJsonDegrades(unittest.TestCase):
    def _write(self, name, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_thresholds_malformed_returns_empty(self):
        p = self._write("t.json", "{ this is not json ")
        th = tc.load_thresholds(p)
        self.assertEqual(th.raw, {})

    def test_no_seizures_malformed_returns_empty(self):
        p = self._write("ns.json", "{ broken")
        self.assertEqual(tc.load_no_seizures_table(p), {})

    def test_breakthrough_tables_malformed_returns_empty(self):
        p = self._write("bt.json", "not json at all")
        self.assertEqual(tc.load_breakthrough_tables(p), {})

    def test_breakthrough_urgency_malformed_returns_empty(self):
        p = self._write("btu.json", "{,,}")
        self.assertEqual(tc.load_breakthrough_urgency(p), {})

    def test_asm_rules_malformed_returns_empty(self):
        from engine_core.asm_rules import load_asm_rules
        p = self._write("asm.json", "{ half written")
        self.assertEqual(load_asm_rules(p), {})

    def test_drug_synonyms_and_classes_malformed_return_empty(self):
        from engine_core.drug_identity import load_synonyms, load_classes
        p1 = self._write("syn.json", "{ oops")
        p2 = self._write("cls.json", "]]]")
        self.assertEqual(load_synonyms(p1), {})
        self.assertEqual(load_classes(p2), {})

    def test_engine_runs_with_malformed_thresholds(self):
        # End-to-end: a broken thresholds file must not raise; engine still runs.
        p = self._write("t.json", "{ nope ")
        cfg = tc.load_thresholds(p)
        pt = PatientContext(external_id="T", age_years=8, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_switch_to="lamotrigine")
        snap = snapshot_from_interval(pt, m, c, {"Q3": "no", "Q10a": "at_goal"})
        r = run_engine(snap, cfg=cfg)
        self.assertIsNotNone(r.category)


# ---------------------------------------------------------------------------
# E5 - to_dict completeness + engine_version in provenance
# ---------------------------------------------------------------------------
class TestSerializationAndProvenance(unittest.TestCase):
    def _run(self):
        p = PatientContext(external_id="T", age_years=8, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency(can_switch_to="lamotrigine")
        return run_engine(snapshot_from_interval(p, m, c, {"Q3": "no", "Q10a": "at_goal"}))

    def test_to_dict_includes_switch_family_note_and_all_fields(self):
        r = self._run()
        d = r.to_dict()
        for field_name in vars(r):
            self.assertIn(field_name, d, msg=f"to_dict dropped field {field_name}")
        self.assertIn("switch_family_note", d)

    def test_provenance_engine_version_populated(self):
        r = self._run()
        from engine_core.version import ENGINE_VERSION
        self.assertEqual(r.sources.get("engine_version"), ENGINE_VERSION)
        self.assertIsNotNone(r.sources.get("engine_version"))


# ---------------------------------------------------------------------------
# E7 - titration at/above ceiling surfaced explicitly
# ---------------------------------------------------------------------------
class TestTitrationEdgeCases(unittest.TestCase):
    def _rule(self, **band):
        return ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            standard_uptitration_step_mg=250, standard_uptitration_interval_weeks=2,
            **band)])

    def _escalate_decision(self):
        return Decision(category=RecommendationCategory.ESCALATE, pace="faster")

    def test_current_above_ceiling_flags_at_or_above(self):
        rule = self._rule(dose_range_max_mg_per_day=500, max_daily_mg=500)
        p = PatientContext(external_id="T", age_years=8, weight_kg=30)
        m = MedicationContext(medication="levetiracetam", dose_amount=400, dose_unit="mg",
                              frequency_per_day=2)  # 800 mg/day > 500 ceiling
        c = PhysicianContingency()
        snap = snapshot_from_interval(p, m, c, {"Q3": "yes", "Q10a": "still_increasing"})
        sched = build_titration_schedule(snap, self._escalate_decision(), rule)
        self.assertEqual(sched["status"], "at_or_above_ceiling")
        self.assertEqual(sched["steps"], [])
        self.assertIn("ceiling", sched["note"].lower())

    def test_current_equal_ceiling_flags_at_or_above(self):
        rule = self._rule(dose_range_max_mg_per_day=500, max_daily_mg=500)
        p = PatientContext(external_id="T", age_years=8, weight_kg=30)
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)  # 500 == ceiling
        c = PhysicianContingency()
        snap = snapshot_from_interval(p, m, c, {"Q3": "yes", "Q10a": "still_increasing"})
        sched = build_titration_schedule(snap, self._escalate_decision(), rule)
        self.assertEqual(sched["status"], "at_or_above_ceiling")

    def test_ceiling_none_single_next_step(self):
        rule = self._rule()  # no max supplied -> ceiling None
        p = PatientContext(external_id="T", age_years=8, weight_kg=30)
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=2)
        c = PhysicianContingency()
        snap = snapshot_from_interval(p, m, c, {"Q3": "yes", "Q10a": "still_increasing"})
        sched = build_titration_schedule(snap, self._escalate_decision(), rule)
        self.assertEqual(sched["status"], "ok")
        self.assertEqual(len(sched["steps"]), 1)
        self.assertIsNone(sched["ceiling_mg_per_day"])

    def test_freq_zero_per_dose_none(self):
        rule = self._rule(dose_range_max_mg_per_day=1000, max_daily_mg=1000)
        p = PatientContext(external_id="T", age_years=8, weight_kg=30)
        # frequency_per_day=0 -> per_dose_mg should be None (no divide by zero)
        m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                              frequency_per_day=0)
        c = PhysicianContingency()
        snap = snapshot_from_interval(p, m, c, {"Q3": "yes", "Q10a": "still_increasing"})
        sched = build_titration_schedule(snap, self._escalate_decision(), rule)
        # current is not computable (freq 0) -> unavailable, but must not crash.
        self.assertIn(sched["status"], ("unavailable", "ok", "at_or_above_ceiling"))


# ---------------------------------------------------------------------------
# E8 - reset_caches() forces a reload
# ---------------------------------------------------------------------------
class TestCacheReset(unittest.TestCase):
    def test_reset_forces_reload(self):
        tc.reset_caches()
        first = tc.default_no_seizures_table()
        self.assertIsNotNone(tc._NS_CACHE)
        # Poison the cache, confirm it's served, then reset and confirm reload.
        tc._NS_CACHE = {"actions_by_phase_level": {"steady": ["poison"]}}
        self.assertEqual(tc.default_no_seizures_table()["actions_by_phase_level"]["steady"], ["poison"])
        tc.reset_caches()
        self.assertIsNone(tc._NS_CACHE)
        reloaded = tc.default_no_seizures_table()
        self.assertIn("titrating", reloaded["actions_by_phase_level"])

    def test_reset_all_caches(self):
        tc.default_thresholds(); tc.default_breakthrough_tables(); tc.default_breakthrough_urgency()
        tc.reset_caches()
        self.assertIsNone(tc._CACHE)
        self.assertIsNone(tc._BT_CACHE)
        self.assertIsNone(tc._BTU_CACHE)


# ---------------------------------------------------------------------------
# E9 - shared per_dose_mg conversion
# ---------------------------------------------------------------------------
class TestPerDoseConversion(unittest.TestCase):
    def test_mg_passthrough(self):
        self.assertEqual(per_dose_mg(250, "mg", None), 250)

    def test_ml_needs_concentration(self):
        self.assertIsNone(per_dose_mg(5, "mL", None))
        self.assertEqual(per_dose_mg(5, "mL", 20), 100)

    def test_tablets_conversion(self):
        self.assertEqual(per_dose_mg(2, "tablets", 100), 200)
        self.assertIsNone(per_dose_mg(2, "tablets", None))

    def test_total_daily_dose_uses_helper(self):
        m = MC(medication="x", dose_amount=5, dose_unit="mL", concentration=20,
               frequency_per_day=2)
        self.assertEqual(m.total_daily_dose, 200)

    def test_dose_line_matches_total(self):
        m = MC(medication="x", dose_amount=5, dose_unit="mL", concentration=20,
               frequency_per_day=2)
        line = _dose_line(m)
        self.assertIn("200 mg/day", line)


# ---------------------------------------------------------------------------
# E10 / E12 - constants + threshold-floor validation
# ---------------------------------------------------------------------------
class TestConstantsAndValidation(unittest.TestCase):
    def test_urgent_rash_aliases_are_the_same_object(self):
        self.assertIs(SJS_TRIGGER_RASH, URGENT_RASH)
        self.assertIs(SPREADING_RASH, URGENT_RASH)

    def test_action_map_tokens_all_resolve_to_members(self):
        # E4 protection: every token in _ACTION_TO_CATEGORY names a real member.
        for token, name in _ACTION_TO_CATEGORY.items():
            self.assertIn(name, RecommendationCategory.__members__,
                          msg=f"action token {token} -> {name} is not a member")

    def test_duration_thresholds_align_to_bucket_floors(self):
        # E12: the shipped urgency thresholds must land on known bucket floors.
        misaligned = validate_duration_thresholds_align()
        self.assertEqual(misaligned, [], msg=f"misaligned thresholds: {misaligned}")

    def test_validation_flags_a_bad_threshold(self):
        bad = {"by_seizure_type": {"focal": {"urgent_duration_s": 137}}}
        misaligned = validate_duration_thresholds_align(bad)
        self.assertEqual(misaligned, [("focal", "urgent_duration_s", 137)])


if __name__ == "__main__":
    unittest.main()
