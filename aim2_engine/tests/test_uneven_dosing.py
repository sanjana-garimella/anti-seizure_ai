"""
test_uneven_dosing.py
---------------------
Uneven and three-times-a-day doses the family ALREADY gives (2026-10-28).

Q1a used to ask only "amount each time x times per day", so a child on 2 tablets in
the morning and 3 at night could not be reported. The survey now asks "Is it the same
amount each time?" and, if not, one amount per dose. These tests cover:
  * the reported total (sum of the doses) and the "not computable" defer on a gap;
  * the summary line naming the split;
  * a Level-3 pick that is already in place (TID on TID, uneven on uneven) is NOT acted
    on - it goes to the clinician, and the family's option list leaves it out;
  * an increase keeps the family's uneven split when one fits;
  * the survey form parsing (clinic._split_from_form).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core import run_engine                                    # noqa: E402
from engine_core.snapshot import (PatientContext, MedicationContext,   # noqa: E402
                                  PhysicianContingency, snapshot_from_interval)
from engine_core.asm_rules import load_asm_rules                       # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L3 = {"Q3": "no", "Q9": ["sleepiness"], "Q10a": "at_goal", "Q10c_time": "over_2_weeks",
      "Q10b_impact": "medium_alot"}
FAST_UP = {"Q2": "almost_never", "Q3": "yes", "Q10a": "still_increasing",
           "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                 "frequency_bucket": "weekly", "count_4wk": 20}]}


def _reg():
    return load_asm_rules(os.path.join(_HERE, "data", "asm_rules.json"))


def _med(amounts=None, amt=None, freq=2):
    return MedicationContext(medication="levetiracetam", dose_amount=amt, dose_unit="tablets",
                             concentration=250, frequency_per_day=freq, dose_amounts=amounts)


def _run(med, answers):
    snap = snapshot_from_interval(PatientContext(external_id="T", age_years=10, weight_kg=30),
                                  med, PhysicianContingency(can_increase_dose=True), answers)
    return snap, run_engine(snap, asm_registry=_reg())


class TestReportedSplit(unittest.TestCase):
    def test_total_is_the_sum_of_the_doses(self):
        m = _med([2, 3], amt=2)
        self.assertTrue(m.is_uneven)
        self.assertEqual(m.total_daily_dose, 1250)
        self.assertEqual(m.per_dose_mg_list(), [500, 750])

    def test_equal_amounts_are_not_uneven(self):
        m = _med([2, 2], amt=2)
        self.assertFalse(m.is_uneven)
        self.assertEqual(m.total_daily_dose, 1000)

    def test_gap_or_wrong_count_is_not_computable(self):
        self.assertIsNone(_med([2, None], amt=2).total_daily_dose)
        self.assertIsNone(_med([1, 1, 2], amt=1, freq=2).total_daily_dose)

    def test_summary_names_the_split(self):
        _s, r = _run(_med([2, 3], amt=2), L3)
        line = next(l for l in r.clinical_summary.splitlines() if "Current dose" in l)
        self.assertIn("1250 mg/day", line)
        self.assertIn("500 mg morning, 750 mg night", line)


class TestLevel3ChoiceAlreadyInPlace(unittest.TestCase):
    def test_tid_on_tid_goes_to_clinician(self):
        _s, r = _run(_med(amt=1, freq=3), dict(L3, Q10g_dosing_choice="tid"))
        self.assertEqual(r.category, "hold")
        d = r.to_dict()
        self.assertIsNone(d.get("dosing_choice"))
        self.assertIn("already takes it three or more times a day", r.clinical_summary)
        msg = r.draft_patient_message
        self.assertNotIn("three times a day instead of", msg)
        self.assertIn("smaller dose in the morning", msg)        # uneven still offered

    def test_uneven_on_uneven_goes_to_clinician(self):
        _s, r = _run(_med([2, 3], amt=2), dict(L3, Q10g_dosing_choice="uneven"))
        self.assertIsNone(r.to_dict().get("dosing_choice"))
        self.assertIn("already takes different amounts", r.clinical_summary)
        self.assertNotIn("smaller dose in the morning and a larger dose at night, if",
                         r.draft_patient_message)

    def test_tid_from_uneven_bid_resplits_same_total(self):
        _s, r = _run(_med([2, 3], amt=2), dict(L3, Q10g_dosing_choice="tid"))
        self.assertEqual(r.to_dict().get("dosing_choice"), "tid")
        msg = r.draft_patient_message
        self.assertIn("three times a day instead of twice a day", msg)
        self.assertIn("the same total each day", msg)

    def test_tid_from_once_a_day_wording(self):
        _s, r = _run(_med(amt=4, freq=1), dict(L3, Q10g_dosing_choice="tid"))
        self.assertNotIn("instead of twice a day", r.draft_patient_message)


class TestIncreaseKeepsSplit(unittest.TestCase):
    def test_uneven_family_gets_uneven_steps(self):
        _s, r = _run(_med([2, 3], amt=2), FAST_UP)
        self.assertIn("in the morning and", r.draft_patient_message)
        self.assertIn("at night", r.draft_patient_message)

    def test_even_family_unchanged(self):
        _s, r = _run(_med(amt=2), FAST_UP)
        self.assertEqual(r.category, "escalate")


class TestSurveyForm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clinic
        cls.split = staticmethod(clinic._split_from_form)

    def test_same_each_time(self):
        self.assertIsNone(self.split({"same_each": ["yes"], "amt_1": ["2"]}, 2))

    def test_uneven_amounts_in_day_order(self):
        f = {"same_each": ["no"], "amt_1": ["1"], "amt_2": ["1"], "amt_3": ["2"], "amt_4": ["9"]}
        self.assertEqual(self.split(f, 3), [1.0, 1.0, 2.0])     # amt_4 ignored at 3/day

    def test_blank_slot_kept_as_none(self):
        self.assertEqual(self.split({"same_each": ["no"], "amt_1": ["2"], "amt_2": [""]}, 2),
                         [2.0, None])

    def test_once_a_day_has_no_split(self):
        self.assertIsNone(self.split({"same_each": ["no"], "amt_1": ["2"]}, 1))


if __name__ == "__main__":
    unittest.main()
