"""
test_clinic.py
--------------
Web-layer unit tests for the clinic app's pure functions: survey parsing,
formulation cross-check, and triage. These do NOT start the HTTP server or touch
the database - they exercise the request-handling helpers directly.

Run:  python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clinic  # noqa: E402
from engine_core import (  # noqa: E402
    run_engine, snapshot_from_interval, PatientContext, MedicationContext,
    PhysicianContingency,
)


def _form(d):
    """Survey forms arrive as {key: [values]} from urllib.parse_qs."""
    return {k: (v if isinstance(v, list) else [v]) for k, v in d.items()}


def _rec(answers, med="levetiracetam", age=8, weight=30, **mc):
    p = PatientContext(external_id="T", age_years=age, weight_kg=weight, seizure_types=["focal"])
    m = MedicationContext(medication=med, dose_amount=500, dose_unit="mg",
                          frequency_per_day=2, **mc)
    return run_engine(snapshot_from_interval(p, m, PhysicianContingency(
        can_increase_dose=True, can_switch_to="lamotrigine"), answers))


class TestParseAnswers(unittest.TestCase):
    def test_per_type_episodes_built(self):
        a = clinic.parse_answers(_form({
            "seizures": "yes", "q4": ["whole_body", "focal"],
            "freq_whole_body": "weekly", "dur_whole_body": "over_3min", "unresp_whole_body": "yes",
            "freq_focal": "daily", "dur_focal": "under_1min", "unresp_focal": "no",
            "q10a": "still_increasing",
        }))
        eps = {tuple(e["look"])[0]: e for e in a["seizure_episodes"]}
        self.assertEqual(eps["whole_body"]["duration"], "over_3min")
        self.assertEqual(eps["whole_body"]["frequency_bucket"], "weekly")
        self.assertIs(eps["whole_body"]["unresponsive"], True)
        self.assertIs(eps["focal"]["unresponsive"], False)

    def test_dose_goal_always_read(self):
        a = clinic.parse_answers(_form({"seizures": "no", "q10a": "at_goal", "q10c_time": "over_2_weeks"}))
        self.assertEqual(a["Q10a"], "at_goal")
        self.assertEqual(a["Q10c_time"], "over_2_weeks")

    def test_free_text_notes_folded_into_q13(self):
        a = clinic.parse_answers(_form({
            "seizures": "yes", "q4": ["not_sure"], "q_seizure_note": "eyes roll up",
            "q13": "thanks", "q10a": "not_sure",
        }))
        self.assertIn("thanks", a["Q13"])
        self.assertIn("eyes roll up", a["Q13"])

    def test_conflict_clarifier_captured(self):
        a = clinic.parse_answers(_form({
            "seizures": "yes", "q4": ["focal"], "q11": "better", "q6": "more",
            "q_conflict": "shorter but more often", "q10a": "still_increasing",
        }))
        self.assertIn("shorter but more often", a["Q13"])

    def test_dosing_choice_is_a_structured_answer(self):
        # Dr. Barbour 2026-10-25 (#52): the family's Level-3 pick is a structured field
        # the decision tables read (not folded into free text).
        a = clinic.parse_answers(_form({
            "seizures": "no", "q10a": "at_goal", "impact": "medium_alot",
            "q_dosing_choice": "uneven",
        }))
        self.assertEqual(a["Q10g_dosing_choice"], "uneven")
        self.assertNotIn("uneven", a.get("Q13") or "")
        bad = clinic.parse_answers(_form({"seizures": "no", "q_dosing_choice": "whatever"}))
        self.assertNotIn("Q10g_dosing_choice", bad)

    def test_preference_questions_removed(self):
        # Kristen 2026-10-15: no increase/add/switch preference questions in the survey;
        # stale fields from an old form are ignored (engine default = no strong preference).
        cards = clinic._survey_cards({"medication": "levetiracetam"})
        for name in ("name=q12", "name=med_pref_rank1", "name=med_pref_strength", "name=q_pref_titrate"):
            self.assertNotIn(name, cards)
        a = clinic.parse_answers(_form({"seizures": "no", "q12": "different_medicine",
                                        "med_pref_rank1": "switch", "q_pref_titrate": "add_medication"}))
        self.assertNotIn("Q12", a)
        self.assertNotIn("med_pref_rank", a)
        self.assertNotIn("add_medication", a.get("Q13") or "")

    def test_dosing_choice_block_uses_template_H_options(self):
        # Uneven / three times a day / watch and wait for any medicine; "lower the dose
        # and add" is marked to show only with breakthrough seizures.
        block = clinic._dosing_choice_block({"medication": "levetiracetam"})
        for v in ("value=wait", "value=uneven", "value=tid", "value=add data-opt=add",
                  "We have a few options to handle side effects"):
            self.assertIn(v, block)
        self.assertNotIn("data-uneven", block)            # no eligible-drug list any more
        self.assertIn("id=fs-dosing", clinic._survey_cards({"medication": "clobazam"}))


class TestFormulationCheck(unittest.TestCase):
    def test_known_strength_passes(self):
        # 500 mg is a real levetiracetam tablet strength -> no warning.
        self.assertIsNone(clinic._formulation_reason(_form({"med": "levetiracetam", "strength": "500"})))

    def test_unknown_strength_flags(self):
        r = clinic._formulation_reason(_form({"med": "levetiracetam", "strength": "333"}))
        self.assertIsNotNone(r)
        self.assertIn("333", r)


class TestTriage(unittest.TestCase):
    def test_safety_is_urgent(self):
        rec = _rec({"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"})   # SJS -> safety
        priority, reasons = clinic.triage(rec, [])
        self.assertEqual(priority, "urgent")
        self.assertTrue(reasons)

    def test_no_change_is_routine(self):
        rec = _rec({"Q3": "no", "Q10a": "at_goal"})                   # seizure-free, at goal
        priority, _ = clinic.triage(rec, [])
        self.assertEqual(priority, "routine")

    def test_switch_is_review(self):
        rec = _rec({"Q3": "no", "Q9": ["dizziness"], "Q10a": "at_goal",
                    "Q10b_impact": "big_wants_change"})               # intolerable -> switch
        priority, reasons = clinic.triage(rec, [])
        self.assertEqual(priority, "review")
        self.assertEqual(rec.category, "switch_medication")

    def test_redflag_text_is_urgent(self):
        rec = _rec({"Q3": "no", "Q10a": "at_goal"})
        priority, _ = clinic.triage(rec, ["breathing"])
        self.assertEqual(priority, "urgent")


if __name__ == "__main__":
    unittest.main()
