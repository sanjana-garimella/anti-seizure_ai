"""
test_initiation.py
------------------
Dr. Barbour's v9 starting schedules (data/initiation_schedules.json, from the
'<Drug> Initiation' tabs) and the v9 ladder rebuild. Display only: cells are her
text verbatim, the lookup defers whenever the band or regimen is unclear, and the
data files can't drift from her workbook.
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "data"))

from engine_core import initiation as ini                                   # noqa: E402
from engine_core import (run_engine, snapshot_from_interval, PatientContext,  # noqa: E402
                         MedicationContext, PhysicianContingency)
from engine_core.asm_rules import load_asm_rules                            # noqa: E402

_WB = os.path.join(os.path.dirname(_ROOT), "kristen", "final_doses_titration_v9.xlsx")


class TestScheduleLookup(unittest.TestCase):
    def test_data_is_draft(self):
        self.assertTrue(ini.default().get("verified", False))
        self.assertFalse(ini.default().get("switch_taper", {}).get("verified", True))

    def test_cells_are_verbatim_and_tablets_preferred_over_10(self):
        s = ini.schedule_for("levetiracetam", 12, 35)
        self.assertTrue(s["available"])
        self.assertEqual(s["forms"][0]["kind"], "solid")                       # >10 y -> tablets first
        self.assertEqual(s["forms"][0]["band_label"], "35–40 kg")
        self.assertEqual(s["forms"][0]["cells"][0], "½ × 500 mg once daily")

    def test_liquid_preferred_10_and_under(self):
        s = ini.schedule_for("levetiracetam", 10, 35)
        self.assertEqual(s["forms"][0]["kind"], "liquid")

    def test_dash_band_says_use_liquid(self):
        s = ini.schedule_for("levetiracetam", 12, 18)                            # 17.5–20 kg: no tablet plan
        lines = " ".join(ini.schedule_lines(s))
        self.assertIn("no tablet schedule fits this band - use the liquid", lines)

    def test_carbamazepine_never_started_as_new(self):
        # Dr. Barbour 2026-10-25: carbamazepine is never offered as a new medication.
        s = ini.schedule_for("carbamazepine", 9, 30)
        self.assertFalse(s["available"])
        self.assertIn("never offered as a new medication", s["reason"])

    def test_no_tab_defers(self):
        s = ini.schedule_for("ethosuximide_xr", 9, 30)
        self.assertFalse(s["available"])
        self.assertIn("No starting schedule", s["reason"])

    def test_switch_taper_off_after_week_4(self):
        # #69: old medicine x1, x0.75, x0.50, x0.25 by week, off after week 4; the new
        # medicine starts in week 1.
        line = " ".join(ini.taper_lines("levetiracetam", 1000, 2, "tablets", 250))
        for frag in ("week 1 (current dose): 500 mg (2 × 250 mg) twice a day",
                     "week 2 (x0.75): 375 mg (1½ × 250 mg)", "week 3 (x0.5): 250 mg",
                     "week 4 (x0.25): 125 mg (½ × 250 mg)", "off after week 4",
                     "new medicine in week 1"):
            self.assertIn(frag, line)

    def test_switch_taper_needs_dose_and_frequency(self):
        self.assertIn("clinician to set the taper",
                      " ".join(ini.taper_lines("levetiracetam", None, 2)))

    def test_form_follows_family_not_age(self):
        s = ini.schedule_for("levetiracetam", 8, 35, current_unit="tablets")
        self.assertEqual(s["preferred_kind"], "solid")

    def test_lamotrigine_with_valproate_defers(self):
        s = ini.schedule_for("lamotrigine", 9, 30, ["valproic_acid"])
        self.assertFalse(s["available"])
        self.assertIn("valproate", s["reason"])

    def test_two_matching_bands_defer(self):
        # 13 y / 40 kg matches both a lamotrigine weight band and the 12–16 y band.
        s = ini.schedule_for("lamotrigine", 13, 40)
        self.assertFalse(s["available"])
        self.assertIn("clinician to choose", s["reason"])

    def test_gap_between_bands_defers(self):
        self.assertFalse(ini.schedule_for("topiramate", 3, 11.5)["available"])   # ≤11 kg | 12–22 kg

    def test_unknown_size_defers(self):
        self.assertFalse(ini.schedule_for("levetiracetam", None, None)["available"])


class TestScheduleShownToClinician(unittest.TestCase):
    def test_switch_summary_lists_starting_schedule(self):
        reg = load_asm_rules(os.path.join(_ROOT, "data", "asm_rules.json"))
        sn = snapshot_from_interval(
            PatientContext(external_id="t", age_years=12, weight_kg=35),
            MedicationContext(medication="oxcarbazepine", dose_amount=300, dose_unit="mg",
                              frequency_per_day=2),
            PhysicianContingency(can_increase_dose=True, can_switch_to="levetiracetam"),
            {"Q3": "no", "Q10a": "at_goal", "Q9": ["sleepiness"], "Q10b_impact": "not_a_fit"})
        rec = run_engine(sn, asm_registry=reg)
        self.assertEqual(rec.category, "switch_medication")
        self.assertIn("Starting schedule for levetiracetam", rec.clinical_summary)
        self.assertIn("½ × 500 mg once daily", rec.clinical_summary)

    def test_review_card_shows_each_options_schedule(self):
        import clinic
        ms = {"engine_result": {"options": [{"id": "levetiracetam", "rank": 1,
                                              "display_name": "Levetiracetam (Keppra)"}]},
              "family_ranking": {}}
        h = clinic.med_selection_review_html(ms, (12, 35, []))
        self.assertIn("Starting schedule", h)
        self.assertIn("½ × 500 mg once daily", h)
        self.assertNotIn("Starting schedule", clinic.med_selection_review_html(ms))


@unittest.skipUnless(os.path.isfile(_WB), "Dr. Barbour's v9 workbook not present")
class TestDataMatchesWorkbook(unittest.TestCase):
    """Drift guard: the JSON files must equal a fresh build from her workbook."""

    def test_initiation_json_matches_workbook(self):
        import _build_initiation_schedules as b
        self.assertEqual(b.build(_WB), ini.load()["by_drug"])

    def test_ladder_json_matches_workbook(self):
        import json
        import _build_dose_ladder as b
        with open(os.path.join(_ROOT, "data", "dose_titration_ladder.json")) as fh:
            cur = json.load(fh)["bands_by_drug"]
        self.assertEqual(b.build(_WB), cur)


if __name__ == "__main__":
    unittest.main()
