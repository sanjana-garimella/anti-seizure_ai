"""
test_dose_ladder.py
-------------------
Tests for the 2026-07-08 additions:
  * engine_core.dose_ladder  - exact next-dose resolution from Dr. Barbour's
    final_doses_titration_v6 ladder (mg/kg conversion, tier tagging, high-tier
    dagger-lab trigger, band matching, and null/NaN/out-of-bounds robustness).
  * data/breakthrough_decision_tables.json - the cells reconciled with Kristen's
    revised nonurgent/urgent/emergent tables.
  * Kristen validation-review fixes (cases B1/B2/B3 routing, D4 follow-up).

All DRAFT clinical values live in JSON; these tests assert the ENGINE plumbing,
not invented numbers.
"""
import json
import math
import os
import unittest

from engine_core import dose_ladder as dl
from engine_core import titration_config as tc
from engine_core.decision import decide, RecommendationCategory
from engine_core.categorize import PatientStatus
from engine_core.snapshot import SeizureControl, SideEffectSeverity, Adherence

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class TestDoseLadderResolver(unittest.TestCase):
    def setUp(self):
        dl.reset_cache()

    def test_mg_per_kg_next_up_and_maintenance(self):
        # Levetiracetam mg/kg band, 26 kg. Min-maintenance 30 mg/kg = 780 mg/day.
        p = dl.resolve_plan("levetiracetam", 8, 26, 780, n_up=2)
        self.assertIsNotNone(p)
        self.assertEqual(p["dose_basis"], "mg_per_kg_per_day")
        self.assertAlmostEqual(p["min_maintenance_mg_per_day"], 780.0, places=3)
        # next two rungs above 780 are 35 & 40 mg/kg -> 910 & 1040 mg/day, tier "up".
        targets = [(x["target_mg_per_day"], x["tier"]) for x in p["next_up"]]
        self.assertEqual(targets, [(910.0, "up"), (1040.0, "up")])

    def test_high_tier_triggers_dagger_labs(self):
        # Force a dose just below the first "high" rung so the next step is high.
        p = dl.resolve_plan("carbamazepine", 4, 18, 18 * 25, n_up=1)  # 25 mg/kg = eff max
        self.assertIsNotNone(p)
        # next rung above effective max (25 mg/kg) is the high tier (30 mg/kg).
        self.assertTrue(p["reaches_high_tier"])
        self.assertIn("carbamazepine level", p["high_dose_labs"])

    def test_mg_per_day_band_matches_by_weight(self):
        p = dl.resolve_plan("brivaracetam", 12, 60, 100, n_up=1)
        self.assertEqual(p["band_label"], "≥50 kg")
        self.assertEqual(p["dose_basis"], "mg_per_day")

    def test_age_band_matches_by_age(self):
        # Carbamazepine bands are age-based; a 4y/18kg child hits "2 to <6 years".
        b = dl.band_for("carbamazepine", 4, 18)
        self.assertIsNotNone(b)
        self.assertIn("2 to <6", b["band_label"])

    def test_data_quality_flag_surfaces(self):
        p = dl.resolve_plan("zonisamide", 8, 25, 75, n_up=1)        # flagged child band (v9)
        self.assertIsNotNone(p["data_quality_flag"])

    def test_data_quality_flag_is_band_specific(self):
        # A flag on one band must not show on the drug's other bands.
        p = dl.resolve_plan("zonisamide", 17, 60, 300, n_up=1)      # >16 years: not flagged
        self.assertIsNone(p["data_quality_flag"])
        p = dl.resolve_plan("brivaracetam", 10, 30, 100, n_up=1)    # 20 to <50 kg: not flagged
        self.assertIsNone(p["data_quality_flag"])
        p = dl.resolve_plan("brivaracetam", 16, 60, 100, n_up=1)    # >=50 kg: flagged
        self.assertIsNotNone(p["data_quality_flag"])

    def test_addressed_band_is_verified(self):
        p = dl.resolve_plan("levetiracetam", 8, 26, 500, n_up=1)
        self.assertTrue(p["verified"])
        self.assertNotIn("verified:false", p["note"])

    def test_flagged_band_stays_unverified(self):
        p = dl.resolve_plan("zonisamide", 8, 25, 75, n_up=1)
        self.assertFalse(p["verified"])
        self.assertIn("verified:false", p["note"])

    # --- robustness: null / NaN / out-of-bounds must DEGRADE, never crash ---
    def test_none_weight_for_mg_per_kg_defers(self):
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, None, 500, 1))

    def test_zero_and_negative_weight_defer(self):
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, 0, 500, 1))
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, -5, 500, 1))

    def test_nan_and_inf_current_defer(self):
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, 26, float("nan"), 1))
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, 26, float("inf"), 1))

    def test_none_current_defers(self):
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, 26, None, 1))

    def test_unknown_or_empty_drug_defers(self):
        self.assertIsNone(dl.resolve_plan("phenobarbital", 8, 26, 100, 1))
        self.assertIsNone(dl.resolve_plan("", 8, 26, 100, 1))
        self.assertIsNone(dl.resolve_plan(None, 8, 26, 100, 1))

    def test_rounds_offgrid_dose_to_nearest_level(self):
        # entered dose between rungs -> snaps to nearest level for display, flags it,
        # but next-up stays strictly ABOVE the entered dose (never proposes <= current).
        p = dl.resolve_plan("levetiracetam", 8, 30, 510, n_up=1)
        self.assertIsNotNone(p["current_level_index"])
        self.assertLessEqual(p["current_level_index"], p["total_levels"])
        self.assertIsNotNone(p["rounded_from"])            # 510 is off-grid
        self.assertTrue(all(x["target_mg_per_day"] > 510 for x in p["next_up"]))

    def test_ongrid_dose_not_flagged_rounded(self):
        p = dl.resolve_plan("levetiracetam", 8, 30, 900, n_up=1)   # 30 mg/kg exact
        self.assertIsNone(p["rounded_from"])
        self.assertEqual(p["current_tier"], "maintenance")

    def test_dose_above_top_rung_yields_empty_next_up(self):
        p = dl.resolve_plan("levetiracetam", 8, 26, 999999, 1)
        self.assertEqual(p["next_up"], [])
        self.assertTrue(p["at_or_above_top_rung"])

    def test_missing_file_returns_empty_and_defers(self):
        empty = dl.load_ladder(path="/nonexistent/ladder.json")
        self.assertEqual(empty, {})
        self.assertIsNone(dl.resolve_plan("levetiracetam", 8, 26, 500, 1, ladder=empty))

    def test_rungs_are_monotonic_nondecreasing(self):
        for drug in ["levetiracetam", "valproic_acid", "oxcarbazepine", "topiramate"]:
            b = dl.band_for(drug, 30, 60) or dl.band_for(drug, 8, 60) or dl.band_for(drug, 8, 20)
            if not b:
                continue
            rungs = [r["mg_per_day"] for r in dl.build_rungs(b, 60)]
            self.assertEqual(rungs, sorted(rungs), f"{drug} rungs not monotonic")


class TestReconciledBreakthroughTables(unittest.TestCase):
    """Assert the specific cells reconciled with Kristen's revised xlsx."""
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_DATA, "breakthrough_decision_tables.json")) as fh:
            cls.t = json.load(fh)["tables"]

    def test_non_urgent_holding_L1_L2_now_escalate(self):
        for lvl in (1, 2):
            c = self.t["non_urgent"]["holding"][lvl]
            self.assertEqual(c["category"], "escalate")
            self.assertIn("1 dose increase", c["steps"])

    def test_non_urgent_steady_L1_L2_two_increases(self):
        for lvl in (1, 2):
            self.assertIn("2 dose increases", self.t["non_urgent"]["steady"][lvl]["steps"])

    def test_urgent_holding_at_max_is_add_medication(self):
        for lvl in (0, 1, 2, 3):
            self.assertEqual(self.t["urgent"]["holding"][lvl]["at_max_category"], "add_medication")

    def test_emergent_titrating_at_max_is_add_medication(self):
        for lvl in (0, 1, 2, 3):
            self.assertEqual(self.t["emergent"]["titrating"][lvl]["at_max_category"], "add_medication")

    def test_emergent_L4_is_two_decreases(self):
        for phase in ("titrating", "holding", "steady"):
            steps = self.t["emergent"][phase][4]["steps"].lower()
            self.assertIn("decrease", steps)
            self.assertIn("second dose decrease", steps)

    def test_emergent_steady_low_levels_have_no_labs(self):
        # Kristen's revised emergent steady L0-L2 do not order labs (no dagger).
        for lvl in (0, 1, 2):
            self.assertNotIn("labs", self.t["emergent"]["steady"][lvl])


def _ns_status(level, phase, current_med="valproic_acid"):
    return PatientStatus(
        seizure_control=SeizureControl.SEIZURE_FREE,
        side_effect_severity=SideEffectSeverity.MANAGEABLE_UNWANTED,
        side_effects_present=True, adherence=Adherence.GOOD,
        provoked_by_nonadherence=False, side_effect_level=level, phase=phase,
        current_med=current_med, can_switch_to="lamotrigine",
    )


class TestKristenValidationFixes(unittest.TestCase):
    def setUp(self):
        tc.reset_caches()

    def test_D4_no_seizures_continue_has_no_followup(self):
        # Case D4: "Only need follow up scheduled if it is listed on the table."
        # The No-Seizures 'continue' cell lists none -> followup_weeks is None.
        for phase in ("titrating", "holding", "steady"):
            for level in (0, 1):
                d = decide(_ns_status(level, phase))
                self.assertEqual(d.category, RecommendationCategory.NO_CHANGE)
                self.assertIsNone(d.followup_weeks,
                                  f"NS continue [{phase}][L{level}] must not schedule follow-up")

    def test_D4_hold_still_lists_followup(self):
        # Contrast: the L3 'hold' cell DOES list a 4-week follow-up (unchanged).
        d = decide(_ns_status(3, "steady"))
        self.assertEqual(d.category, RecommendationCategory.HOLD)
        self.assertEqual(d.followup_weeks, 4)


class TestLadderIntegration(unittest.TestCase):
    """End-to-end: build_titration_schedule attaches the explicit ladder."""
    def test_schedule_attaches_ladder_for_escalate(self):
        # discovery runs with tests/ as the top level, so `tests` is not a package.
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_engine.py")
        spec = importlib.util.spec_from_file_location("aim2_test_engine_helpers", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        make_snapshot = mod.make_snapshot
        from engine_core.categorize import categorize
        from engine_core.decision import decide
        from engine_core.outputs import build_titration_schedule
        from engine_core.asm_rules import load_asm_rules, get_rule

        reg = load_asm_rules()
        snap = make_snapshot(
            {"Q3": "yes", "Q10a": "still_increasing",
             "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                   "frequency": 2, "frequency_unit": "per_week"}]},
            weight=30)
        rule = get_rule(snap.medication.medication, reg)
        d = decide(categorize(snap, rule))
        sched = build_titration_schedule(snap, d, rule)
        self.assertIsNotNone(sched)
        self.assertIn("ladder", sched)
        L = sched["ladder"]
        self.assertTrue(L["next_up"], "ladder should give at least one next rung")
        self.assertTrue(L["verified"])  # levetiracetam band is on her confirmed sheet


class TestConmedRestrictionAndCeiling(unittest.TestCase):
    """2026-07-14 safety fixes: (1) lamotrigine ladder is transcribed *for patients
    NOT on valproate/enzyme inducers - suppress it for those patients (SJS/TEN
    risk); (2) the ladder never proposes a dose above a band's absolute maximum."""
    def setUp(self):
        dl.reset_cache()

    def test_lamotrigine_valproate_suppresses_ladder(self):
        # Same patient, same age/weight/dose: ladder resolves WITHOUT valproate,
        # and is SUPPRESSED (None) WITH valproate.
        base = dl.resolve_plan("lamotrigine", 10, 30, 100, n_up=1)
        self.assertIsNotNone(base)
        supp = dl.resolve_plan("lamotrigine", 10, 30, 100, n_up=1,
                               concomitant_asms=["valproic acid"])
        self.assertIsNone(supp)

    def test_lamotrigine_enzyme_inducer_suppresses_ladder(self):
        supp = dl.resolve_plan("lamotrigine", 10, 30, 100, n_up=1,
                               concomitant_asms=["carbamazepine"])
        self.assertIsNone(supp)

    def test_conmed_suppression_reason_reports_regimen(self):
        r = dl.conmed_suppression_reason("lamotrigine", ["depakote"])
        self.assertIsNotNone(r)
        self.assertEqual(r["regimen"], "valproate")
        self.assertIsNone(dl.conmed_suppression_reason("lamotrigine", []))
        self.assertIsNone(dl.conmed_suppression_reason("lamotrigine", ["clobazam"]))

    def test_non_conmed_drug_unaffected_by_comeds(self):
        # Only lamotrigine carries a conmed restriction; a valproate co-med must
        # NOT suppress another drug's ladder.
        p = dl.resolve_plan("levetiracetam", 8, 26, 780, n_up=1,
                            concomitant_asms=["valproic acid"])
        self.assertIsNotNone(p)

    def test_ladder_never_proposes_above_absolute_max(self):
        # levetiracetam is mg/kg with a 3000 mg/day absolute max; a 60 kg patient's
        # top rungs (55/60 mg/kg = 3300/3600) exceed it and must be withheld.
        p = dl.resolve_plan("levetiracetam", 14, 60, 2500, n_up=6)
        self.assertTrue(p["rungs_above_absolute_max_dropped"])
        self.assertTrue(all(x["target_mg_per_day"] <= p["absolute_max_mg_per_day"] + 1e-6
                            for x in p["next_up"]))

    def test_ceiling_clamp_does_not_affect_in_range_patient(self):
        # A light patient well below the cap keeps the normal rungs (regression).
        p = dl.resolve_plan("levetiracetam", 8, 26, 780, n_up=2)
        self.assertFalse(p["rungs_above_absolute_max_dropped"])
        self.assertEqual([x["target_mg_per_day"] for x in p["next_up"]], [910.0, 1040.0])

    def test_schedule_suppresses_ladder_for_lamotrigine_valproate(self):
        from engine_core import (snapshot_from_interval, PatientContext,
                                 MedicationContext, PhysicianContingency)
        from engine_core.categorize import categorize
        from engine_core.decision import decide
        from engine_core.outputs import build_titration_schedule
        from engine_core.asm_rules import load_asm_rules, get_rule
        reg = load_asm_rules()
        pc = PatientContext(external_id="t", age_years=10, weight_kg=30,
                            concomitant_asms=["valproic acid"])
        mc = MedicationContext(medication="lamotrigine", dose_amount=50,
                               dose_unit="mg", frequency_per_day=2)
        ans = {"Q3": "yes", "Q10a": "still_increasing",
               "seizure_episodes": [{"look": ["focal"], "duration": "over_3min",
                                     "frequency_bucket": "weekly"}]}
        snap = snapshot_from_interval(pc, mc, PhysicianContingency(can_increase_dose=True), ans)
        rule = get_rule(snap.medication.medication, reg)
        d = decide(categorize(snap, rule))
        sched = build_titration_schedule(snap, d, rule)
        self.assertIsNotNone(sched)
        self.assertNotIn("ladder", sched)
        self.assertIn("ladder_suppressed", sched)
        self.assertEqual(sched["ladder_suppressed"]["regimen"], "valproate")


class TestClinicianReportExpectedDose(unittest.TestCase):
    """2026-07-14: the expected dose for the child (age/weight) appears in the
    CLINICIAN summary only - never in the family message."""
    def setUp(self):
        dl.reset_cache()

    def _run(self, med="topiramate", comeds=None, age=7, weight=14.5):
        from engine_core import (snapshot_from_interval, PatientContext,
                                 MedicationContext, PhysicianContingency, run_engine)
        from engine_core.asm_rules import load_asm_rules
        pc = PatientContext(external_id="t", age_years=age, weight_kg=weight,
                            concomitant_asms=list(comeds or []))
        mc = MedicationContext(medication=med, dose_amount=50, dose_unit="mg", frequency_per_day=2)
        ans = {"Q3": "no", "Q10a": "still_increasing"}
        snap = snapshot_from_interval(pc, mc, PhysicianContingency(can_increase_dose=True), ans)
        return run_engine(snap, asm_registry=load_asm_rules())

    def test_expected_dose_in_clinician_summary(self):
        # The reference reads as three bullets in matching units (Kristen 2026-08-04),
        # with the band + DRAFT provenance on the two clinician-supplied ceilings.
        rec = self._run(med="topiramate")
        for expected in ("Current dose:", "Typical range:", "Absolute max:"):
            self.assertIn(expected, rec.clinical_summary)
        # Provenance is stated once. Topiramate's band is on her confirmed sheet,
        # so the footnote names the table and does not call the numbers unverified.
        self.assertIn("Dose range and maximum are from Dr. Barbour's dosing table.",
                      rec.clinical_summary)
        self.assertNotIn("unverified", rec.clinical_summary)
        # Order: current, then typical range, then absolute max.
        self.assertLess(rec.clinical_summary.index("Typical range:"),
                        rec.clinical_summary.index("Absolute max:"))
        self.assertLess(rec.clinical_summary.index("Current dose:"),
                        rec.clinical_summary.index("Typical range:"))

    def test_expected_dose_not_in_family_message(self):
        rec = self._run(med="topiramate")
        msg = rec.draft_patient_message or ""
        self.assertNotIn("Expected dose", msg)
        self.assertNotIn("absolute max", msg)

    def test_lamotrigine_valproate_shows_conmed_note_in_summary(self):
        rec = self._run(med="lamotrigine", comeds=["valproic acid"], age=10, weight=30)
        self.assertIn("standard table does not apply", rec.clinical_summary)


class TestDoseReferenceFormatter(unittest.TestCase):
    """The single, standardized dose-reference formatter shared by the clinician
    summary, inbox card, exact-dose-ladder card, and clinical-reference table.
    Pure string helpers over already-resolved values - no clinical number is
    invented here, and no provenance is added (that stays with each caller)."""

    def setUp(self):
        dl.reset_cache()

    def test_value_mg_per_day_only(self):
        self.assertEqual(dl.dose_value_str(930), "930 mg/day")
        # per_kg requested but no/zero weight -> mg/day only (never a fabricated ratio)
        self.assertEqual(dl.dose_value_str(930, None, True), "930 mg/day")
        self.assertEqual(dl.dose_value_str(930, 0, True), "930 mg/day")

    def test_value_adds_mg_per_kg_when_weight_based(self):
        self.assertEqual(dl.dose_value_str(936, 72, True), "936 mg/day (13 mg/kg/day)")
        self.assertEqual(dl.dose_value_str(930, 72, True), "930 mg/day (12.9 mg/kg/day)")

    def test_value_none_for_missing_or_bad(self):
        for bad in (None, "x", float("nan"), float("inf"), -5):
            self.assertIsNone(dl.dose_value_str(bad))

    def test_line_orders_current_goal_effmax_absmax(self):
        ref = {"dose_basis": "mg_per_day", "min_maintenance_mg_per_day": 900,
               "effective_max_mg_per_day": 1500, "absolute_max_mg_per_day": 3000}
        line = dl.format_dose_reference(ref, current_mg_per_day=600)
        self.assertEqual(
            line,
            "current 600 mg/day · typical range 900–1500 mg/day · "
            "absolute max 3000 mg/day (fixed cap)")

    def test_line_omits_absent_values(self):
        ref = {"dose_basis": "mg_per_day", "min_maintenance_mg_per_day": 900,
               "effective_max_mg_per_day": None, "absolute_max_mg_per_day": 3000}
        line = dl.format_dose_reference(ref)          # no current dose supplied
        self.assertNotIn("current", line)
        self.assertNotIn("effective max", line)
        self.assertIn("typical range 900 mg/day", line)
        self.assertIn("absolute max 3000 mg/day", line)

    def test_line_empty_for_non_dict(self):
        self.assertEqual(dl.format_dose_reference(None), "")
        self.assertEqual(dl.format_dose_reference({}), "")

    def test_line_shows_mg_per_kg_for_weight_based_drug(self):
        # levetiracetam is mg/kg-based; reference_for now carries weight_kg so the
        # formatter renders mg/kg/day with no extra caller arguments.
        ref = dl.reference_for("levetiracetam", 8, 26, [])
        self.assertIsNotNone(ref)
        self.assertEqual(ref.get("dose_basis"), "mg_per_kg_per_day")
        self.assertEqual(ref.get("weight_kg"), 26)
        line = dl.format_dose_reference(ref, current_mg_per_day=500)
        self.assertIn("mg/kg/day", line)
        self.assertIn("current 500 mg/day", line)


    # 2026-09-30: mg/kg/day for every dose whenever weight is known; the flat
    # absolute cap is labelled and never converted to mg/kg.
    def test_mg_per_kg_shown_for_fixed_dose_band_when_weight_known(self):
        ref = {"dose_basis": "mg_per_day", "min_maintenance_mg_per_day": 900,
               "effective_max_mg_per_day": 1320, "absolute_max_mg_per_day": 1500}
        line = dl.format_dose_reference(ref, weight_kg=36, current_mg_per_day=600)
        self.assertIn("current 600 mg/day (16.7 mg/kg/day)", line)
        self.assertIn("900–1320 mg/day (25–36.7 mg/kg/day)", line)

    def test_absolute_cap_not_converted_to_mg_per_kg(self):
        ref = dl.reference_for("levetiracetam", 3, 12, [])
        parts = dict((k, v) for k, _l, v in dl.dose_reference_parts(ref, 12, 500))
        self.assertEqual(parts["absolute_max"], "3000 mg/day (fixed cap)")
        self.assertNotIn("mg/kg", parts["absolute_max"])

    def test_no_weight_no_mg_per_kg(self):
        ref = {"dose_basis": "mg_per_kg_per_day", "min_maintenance_mg_per_day": 900,
               "effective_max_mg_per_day": 1500, "absolute_max_mg_per_day": 3000}
        self.assertNotIn("mg/kg", dl.format_dose_reference(ref, current_mg_per_day=600))


if __name__ == "__main__":
    unittest.main()
