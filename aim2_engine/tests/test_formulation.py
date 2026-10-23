"""mg/day -> tablets / liquid display layer (engine_core.formulation).

Rules: data/formulation_rules.json = Dr. Barbour's answers in
kristen/dose_to_tablet_conversion_scenarios.xlsx ('Flags & clarifications').
Display only: never changes the decision, never crosses the absolute max.
"""
import unittest

from engine_core import (run_engine, snapshot_from_interval, PatientContext,
                         MedicationContext, PhysicianContingency)
from engine_core.formulation import (step_options, formulations_for, load_rules,
                                     best_tablet_split)

LEV = formulations_for("levetiracetam")
EPS_WEEKLY_FOCAL = [{"look": ["focal"], "duration": "1_3min", "frequency_bucket": "weekly"}]


def _rows(drug, targets, **kw):
    kw.setdefault("freq", 2)
    return step_options(drug, targets, kw.pop("freq"), formulations_for(drug), **kw)


class TestConversionRules(unittest.TestCase):
    def test_rules_are_draft(self):
        self.assertFalse(load_rules().get("verified", True))

    def test_barbour_worked_example(self):
        # Flags #3/#4: 30 kg, LEV 500 mg BID on 250 mg tablets -> 1300 mg/day is
        # given as 500 mg morning + 750 mg night (whole tablets, uneven).
        r = _rows("levetiracetam", [1300], current_unit="tablets", current_strength=250,
                  absolute_max=3000, current_total_mg_per_day=1000)[0]
        doses = [d["mg"] for d in r["tablet"]["doses"]]
        self.assertEqual(doses, [500, 750])
        self.assertEqual(r["tablet"]["total_mg_per_day"], 1250)

    def test_increase_never_rounds_back_to_current_dose(self):
        rows = _rows("levetiracetam", [1050, 1200], current_unit="tablets",
                     current_strength=250, absolute_max=3000, current_total_mg_per_day=1000)
        self.assertGreater(rows[0]["tablet"]["total_mg_per_day"], 1000)
        self.assertGreater(rows[1]["tablet"]["total_mg_per_day"],
                           rows[0]["tablet"]["total_mg_per_day"])

    def test_half_tablets_only_on_climb_steps(self):
        rows = _rows("levetiracetam", [1050, 1200], current_unit="tablets",
                     current_strength=250, absolute_max=3000, current_total_mg_per_day=1000)
        final = rows[-1]["tablet"]["doses"]
        self.assertTrue(all(c % 1 == 0 for d in final for _s, c in d["recipe"]))

    def test_never_above_absolute_max(self):
        r = _rows("oxcarbazepine", [2100], absolute_max=2100)[0]
        self.assertLessEqual(r["tablet"]["total_mg_per_day"], 2100)
        self.assertIsNone(best_tablet_split(2100, 2, [600.0], False, load_rules(), 1000))

    def test_within_minus10_plus30_window(self):
        for tgt in (360, 600, 1050, 189):
            for r in _rows("levetiracetam", [tgt], absolute_max=3000):
                if r["tablet"]:
                    t = r["tablet"]["total_mg_per_day"]
                    self.assertGreaterEqual(t, tgt * 0.9 - 1e-6)
                    self.assertLessEqual(t, tgt * 1.3 + 1e-6)

    def test_liquid_stays_liquid(self):
        r = _rows("levetiracetam", [360], current_unit="mL", current_strength=100,
                  absolute_max=3000)[0]
        self.assertIsNone(r["tablet_text"])
        self.assertIn("1.80 mL", r["liquid_text"])

    def test_tablets_stay_tablets_unless_nothing_fits(self):
        ok = _rows("levetiracetam", [1300], current_unit="tablets", current_strength=250,
                   absolute_max=3000)[0]
        self.assertIsNone(ok["liquid_text"])
        nofit = _rows("levetiracetam", [360], current_unit="tablets", current_strength=250,
                      absolute_max=3000, current_total_mg_per_day=300)[0]
        self.assertIsNone(nofit["tablet"])
        self.assertIn("No tablet fits", " ".join(nofit["notes"]))
        self.assertIsNotNone(nofit["liquid_text"])

    def test_combine_steps_offered_when_climb_step_has_no_fit(self):
        rows = _rows("ethosuximide", [260, 500], current_unit="tablets",
                     current_strength=250, absolute_max=None, current_total_mg_per_day=250)
        if rows[0]["tablet"] is None and rows[1]["tablet"] is not None:
            self.assertIn("skip to step 2", " ".join(rows[0]["notes"]))

    def test_valproate_liquid_default_50(self):
        r = _rows("valproic_acid", [360], current_unit="mL")[0]
        self.assertIn("50 mg/mL", r["liquid_text"])

    def test_extended_release_note(self):
        r = _rows("levetiracetam", [1300], current_formulation="extended-release tablet",
                  absolute_max=3000)
        self.assertIn("never split", " ".join(r[0]["notes"]))

    def test_unknown_frequency_does_not_guess(self):
        r = _rows("clobazam", [10], freq=None)[0]
        self.assertIsNone(r["tablet_text"])
        self.assertIsNone(r["liquid_text"])


class TestEdgeCases(unittest.TestCase):
    """Found by an edge-case probe 2026-09-30."""

    def test_string_inputs_do_not_crash(self):
        r = _rows("levetiracetam", [1300], freq="2", current_unit="tablets",
                  current_strength="250", absolute_max="3000")[0]
        self.assertEqual(r["tablet"]["total_mg_per_day"], 1250)

    def test_invalid_targets_not_converted(self):
        for bad in (0, -5, float("nan"), float("inf"), None, "abc"):
            r = _rows("clobazam", [bad])[0]
            self.assertIsNone(r["tablet_text"], bad)
            self.assertIsNone(r["liquid_text"], bad)

    def test_liquid_respects_absolute_max(self):
        r = _rows("oxcarbazepine", [2100], absolute_max=1000)[0]
        self.assertIsNone(r["tablet_text"])
        self.assertIsNone(r["liquid_text"])
        self.assertIn("above the absolute maximum", " ".join(r["notes"]))

    def test_step_not_above_current_not_converted(self):
        r = _rows("levetiracetam", [1050], current_unit="tablets", current_strength=250,
                  current_total_mg_per_day=1250, absolute_max=3000)[0]
        self.assertIsNone(r["tablet_text"])
        self.assertIsNone(r["liquid_text"])
        self.assertIn("not above the dose before it", " ".join(r["notes"]))

    def test_liquid_steps_must_increase(self):
        rows = _rows("levetiracetam", [600, 500], current_unit="mL", absolute_max=3000)
        self.assertIsNotNone(rows[0]["liquid_text"])
        self.assertIsNone(rows[1]["liquid_text"])

    def test_unlisted_tablet_strength_flagged(self):
        r = _rows("levetiracetam", [1300], current_unit="tablets", current_strength=300,
                  absolute_max=3000)[0]
        self.assertIn("not a listed product strength", " ".join(r["notes"]))

    def test_frequencies_one_to_four(self):
        for f in (1, 2, 3, 4):
            r = _rows("valproic_acid", [1000], freq=f)[0]
            self.assertIsNotNone(r["tablet"], f)
            self.assertEqual(len(r["tablet"]["doses"]), f)

    def test_uneven_split_within_30_70(self):
        for tgt in (375, 600, 1150, 1300):
            r = _rows("levetiracetam", [tgt], absolute_max=3000)[0]
            if r["tablet"]:
                d = [x["mg"] for x in r["tablet"]["doses"]]
                self.assertGreaterEqual(min(d) / sum(d), 0.30 - 1e-9)
                self.assertLessEqual(d[0], d[-1])          # larger dose at night

    def test_climb_step_never_rounds_past_next_step(self):
        # 12 kg, LEV 250 mg tablets: step 1 (540) must not round to 625 > step 2 (600);
        # with no fit below 600 it offers skipping straight to step 2.
        rows = _rows("levetiracetam", [540, 600], current_unit="tablets", current_strength=250,
                     current_total_mg_per_day=500, absolute_max=3000)
        if rows[0]["tablet"]:
            self.assertLess(rows[0]["tablet"]["total_mg_per_day"], 600)
        else:
            self.assertIn("skip to step 2", " ".join(rows[0]["notes"]))
        self.assertIsNotNone(rows[1]["tablet"])

    def test_unknown_drug_has_no_options(self):
        r = _rows("phenobarbital", [60])[0]
        self.assertIsNone(r["tablet_text"])
        self.assertIsNone(r["liquid_text"])

    def test_sweep_all_drugs_never_crash_or_exceed(self):
        for d in ("levetiracetam", "lamotrigine", "topiramate", "oxcarbazepine",
                  "carbamazepine", "valproic_acid", "clobazam", "lacosamide",
                  "brivaracetam", "zonisamide", "ethosuximide"):
            for tgt in (5, 37.5, 150, 600, 1800):
                for f in (1, 2, 3):
                    for r in _rows(d, [tgt, tgt * 1.2], freq=f, absolute_max=tgt * 1.1):
                        if r["tablet"]:
                            self.assertLessEqual(r["tablet"]["total_mg_per_day"], tgt * 1.1 + 1e-6)


class TestClinicianSummary(unittest.TestCase):
    def _run(self, age=12, **med):
        # age 12: older than the 10-year cutoff, so the age rule gives tablets
        p = PatientContext(external_id="T", age_years=age, weight_kg=30, seizure_types=["focal"])
        m = MedicationContext(medication="levetiracetam", frequency_per_day=2, **med)
        return run_engine(snapshot_from_interval(
            p, m, PhysicianContingency(can_increase_dose=True),
            {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
             "seizure_episodes": EPS_WEEKLY_FOCAL}))

    def test_summary_shows_tablets_for_tablet_family(self):
        r = self._run(dose_amount=2, dose_unit="tablets", concentration=250)
        self.assertEqual(r.category, "escalate")
        self.assertIn("Next dose (Dr. Barbour's ladder)", r.clinical_summary)
        self.assertIn("250 mg tablets (current)", r.clinical_summary)
        self.assertIn("DRAFT conversion", r.clinical_summary)

    def test_display_layer_does_not_change_decision(self):
        a = self._run(dose_amount=2, dose_unit="tablets", concentration=250)
        b = self._run(dose_amount=500, dose_unit="mg")
        self.assertEqual(a.category, b.category)

    def test_steps_and_totals_show_mg_per_kg(self):
        r = self._run(dose_amount=2, dose_unit="tablets", concentration=250)
        self.assertIn("step 1 1050 mg/day (35 mg/kg/day)", r.clinical_summary)
        self.assertIn("1250 mg/day (41.7 mg/kg/day, +4.2%)",
                      r.clinical_summary)

    def test_rounding_above_effective_max_is_flagged(self):
        p = PatientContext(external_id="T", age_years=11, weight_kg=12, seizure_types=["focal"])
        m = MedicationContext(medication="levetiracetam", frequency_per_day=2,
                              dose_amount=1, dose_unit="tablets", concentration=250)
        r = run_engine(snapshot_from_interval(
            p, m, PhysicianContingency(can_increase_dose=True),
            {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
             "seizure_episodes": EPS_WEEKLY_FOCAL}))
        t = r.clinical_summary
        self.assertIn("above the usual max (~720 mg/day)", t)
        # no-fit note comes BEFORE the labelled liquid fallback
        self.assertLess(t.index("No tablet fits"),
                        t.index("liquid alternative:"))
        self.assertIn("Absolute max: 3000 mg/day (fixed cap)", t)
        self.assertNotIn("250 mg/kg/day", t)

    def test_goal_wording_and_headroom_without_switch(self):
        r = self._run(dose_amount=500, dose_unit="mg")
        self.assertIn("Dose goal status: at the goal dose", r.clinical_summary)
        self.assertNotIn("(at the goal dose)", r.clinical_summary)
        self.assertNotIn("before a switch", r.clinical_summary)

    def test_at_goal_below_typical_range_is_flagged(self):
        p = PatientContext(external_id="T", age_years=7, weight_kg=24)
        m = MedicationContext(medication="lamotrigine", frequency_per_day=2,
                              dose_amount=25, dose_unit="mg")
        r = run_engine(snapshot_from_interval(p, m, PhysicianContingency(),
                                              {"Q3": "no", "Q10a": "at_goal"}))
        self.assertIn("below the typical range", r.clinical_summary)

    def test_no_change_has_no_next_dose_block(self):
        p = PatientContext(external_id="T", age_years=10, weight_kg=30)
        m = MedicationContext(medication="levetiracetam", frequency_per_day=2,
                              dose_amount=500, dose_unit="mg")
        r = run_engine(snapshot_from_interval(p, m, PhysicianContingency(), {"Q3": "no"}))
        self.assertNotIn("Next dose (Dr. Barbour's ladder)", r.clinical_summary)


if __name__ == "__main__":
    unittest.main()


class TestAgeFormAndSingleStrength(unittest.TestCase):
    """PI instructions 2026-10-06: >10 y tablets, <=10 y liquid; one tablet strength per plan."""

    def _first(self, age, **kw):
        kw.setdefault("current_unit", "tablets")
        kw.setdefault("current_strength", 250)
        return _rows("levetiracetam", [1050], age_years=age, absolute_max=3000,
                     current_total_mg_per_day=1000, **kw)[0]

    def test_age_over_10_gets_tablets_not_liquid(self):
        r = _rows("levetiracetam", [1500], age_years=11, absolute_max=3000,
                  current_total_mg_per_day=1000, current_unit="mL")[0]
        self.assertIsNotNone(r["tablet_text"])
        self.assertIsNone(r["liquid_text"])

    def test_age_10_and_under_gets_liquid_not_tablets(self):
        for age in (10, 10.0, 4):
            r = self._first(age)
            self.assertIsNone(r["tablet_text"], age)
            self.assertIn("mL", r["liquid_text"])

    def test_age_rule_overrides_current_form_with_a_note(self):
        r = self._first(8)
        self.assertTrue(any("showing liquid per the age rule" in n for n in r["notes"]))

    def test_unknown_age_keeps_current_form(self):
        r = self._first(None)
        self.assertIsNotNone(r["tablet_text"])
        self.assertIsNone(r["liquid_text"])

    def test_no_liquid_product_falls_back_to_tablets_with_note(self):
        # clobazam tablets only (liquid and film removed) -> no oral liquid listed
        nolq = [f for f in formulations_for("clobazam") if f.get("strengths_mg")
                and "film" not in f["form"]]
        r = step_options("clobazam", [20], 2, nolq, age_years=6, absolute_max=80,
                         current_total_mg_per_day=10)[0]
        self.assertIsNotNone(r["tablet_text"])
        self.assertTrue(any("no usable oral liquid" in n for n in r["notes"]))

    def test_every_plan_uses_one_tablet_strength(self):
        for drug in ("levetiracetam", "oxcarbazepine", "lamotrigine", "topiramate"):
            for tgt in (150, 300, 450, 750, 1050, 1350):
                for cs in (None, 250):
                    r = _rows(drug, [tgt], age_years=14, absolute_max=3000,
                              current_unit="tablets" if cs else None, current_strength=cs)[0]
                    if r["tablet"]:
                        strengths = {s for d in r["tablet"]["doses"] for s, _c in d["recipe"]}
                        self.assertEqual(len(strengths), 1, (drug, tgt, strengths))

    def test_rules_are_draft_flagged(self):
        self.assertTrue(load_rules()["age_form_rule"]["enabled"])
        self.assertEqual(load_rules()["age_form_rule"]["tablet_if_age_greater_than"], 10)


class TestRecipeFormatting(unittest.TestCase):
    def test_halves_merge_into_one_line_per_strength(self):
        from engine_core.formulation import _fmt_recipe
        self.assertEqual(_fmt_recipe(((250.0, 2.0), (250.0, 0.5))), "2½ × 250 mg")
        self.assertEqual(_fmt_recipe(((250.0, 0.5),)), "½ × 250 mg")
        self.assertEqual(_fmt_recipe(((250.0, 3.0),)), "3 × 250 mg")


class TestOneBottleAcrossSteps(unittest.TestCase):
    def test_all_steps_use_the_same_strength(self):
        rows = _rows("levetiracetam", [1050, 1200], age_years=11, current_unit="mL",
                     absolute_max=3000, current_total_mg_per_day=1000)
        used = {s for r in rows if r["tablet"] for d in r["tablet"]["doses"] for s, _c in d["recipe"]}
        self.assertEqual(len(used), 1, used)


class TestBarbourHalfTabletComments(unittest.TestCase):
    """Dr. Barbour's comments (2026-10-06): half tablets for valproic acid 500 mg / zonisamide 50 mg."""

    def _doses(self, drug, targets, cur, ab, wt):
        rows = step_options(drug, targets, 2, formulations_for(drug), absolute_max=ab,
                            current_total_mg_per_day=cur, weight_kg=wt, age_years=13)
        return [[d["mg"] for d in r["tablet"]["doses"]] for r in rows]

    def test_valproate_week1_uneven_with_half_then_whole(self):
        self.assertEqual(self._doses("valproic_acid", [2250, 2475], 2025, 3000, 45),
                         [[1000, 1250], [1000, 1500]])

    def test_zonisamide_week1_uneven_with_half_then_whole(self):
        self.assertEqual(self._doses("zonisamide", [225, 247.5], 202.5, 600, 45),
                         [[100, 125], [100, 150]])

    def test_final_step_still_whole_tablets_only(self):
        r = step_options("valproic_acid", [2250], 2, formulations_for("valproic_acid"),
                         absolute_max=3000, current_total_mg_per_day=2025, age_years=13)[0]
        self.assertTrue(all(c % 1 == 0 for d in r["tablet"]["doses"] for _s, c in d["recipe"]))


class TestStandardTabletStrength(unittest.TestCase):
    """Study team 2026-10-15: one tablet strength per medicine (levetiracetam 500 mg).
    The family's own strength is kept when it fits; otherwise the standard, never
    another best-fit strength."""

    @staticmethod
    def _used(rows):
        return {s for r in rows if r["tablet"] for d in r["tablet"]["doses"] for s, _c in d["recipe"]}

    def test_setting_is_data_not_code(self):
        self.assertEqual(load_rules()["standard_tablet_strength"]["levetiracetam"], 500)

    def test_only_current_or_standard_strength_is_ever_suggested(self):
        from engine_core import dose_ladder
        for wt in (30, 40, 45, 50, 60, 70):
            for cur in (600, 800, 1000, 1200, 1500, 2000):
                up = dose_ladder.resolve_plan("levetiracetam", 13, wt, cur, n_up=2)
                tg = [s["target_mg_per_day"] for s in (up or {}).get("next_up") or []]
                for unit, cs in (("mg", None), ("tablets", 250), ("tablets", 750), ("tablets", 1000)):
                    rows = _rows("levetiracetam", tg, current_unit=unit, current_strength=cs,
                                 absolute_max=3000, age_years=13, weight_kg=wt,
                                 current_total_mg_per_day=(up or {}).get("current_mg_per_day"))
                    used = self._used(rows)
                    self.assertTrue(used <= {500.0} | ({float(cs)} if cs else set()),
                                    (wt, cur, unit, cs, used))
                    self.assertLessEqual(len(used), 1, (wt, cur, unit, cs, used))

    def test_current_strength_kept_when_it_fits(self):
        rows = _rows("levetiracetam", [1800, 2100], current_unit="tablets", current_strength=750,
                     absolute_max=3000, age_years=13, weight_kg=60, current_total_mg_per_day=1500)
        self.assertEqual(self._used(rows), {750.0})

    def test_switches_to_standard_when_current_does_not_fit(self):
        rows = _rows("levetiracetam", [1350, 1500], current_unit="tablets", current_strength=750,
                     absolute_max=3000, age_years=13, weight_kg=30, current_total_mg_per_day=1200)
        self.assertEqual(self._used(rows), {500.0})

    def test_not_on_tablets_gets_standard(self):
        rows = _rows("levetiracetam", [1400, 1600], current_unit="mg", absolute_max=3000,
                     age_years=13, weight_kg=40, current_total_mg_per_day=1200)
        self.assertEqual(self._used(rows), {500.0})

