"""
test_submission_med_context.py
------------------------------
`submissions.med_context_json` stores the RESOLVED medication context the engine
ran on, so a stored submission can be re-run losslessly.

Why it exists: the family confirms their real dose/strength in Q1a and those
fields arrive on the raw POST form. Only `answers_json` used to be persisted, so a
later re-run fell back to the doctor's intake record - which silently changed a
clinical input. On 2026-08-04 one clobazam submission could not be regenerated for
exactly this reason: its `5 mL of 2.5 mg/mL = 25 mg/day` degraded to "mg total not
computable" because the intake record carries no concentration.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSubmissionMedContext(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="medctx_")
        os.environ["ASM_DB"] = os.path.join(self._tmp, "clinic.db")
        for mod in ("clinic_store", "clinic"):
            sys.modules.pop(mod, None)
        import clinic_store as db
        import clinic
        self.db, self.clinic = db, clinic
        db.DB_PATH = os.environ["ASM_DB"]
        db.DATA_DIR = self._tmp
        db.init_db(default_doctor_pw="x")
        # Intake record with NO concentration - the gap that caused the data loss.
        self.pid = db.add_patient({
            "external_id": "t-medctx", "age": 7, "weight": 20,
            "epilepsy_type": "unspecified", "medication": "clobazam",
            "dose_amount": 5, "dose_unit": "mL", "frequency": 2,
            "concentration": None, "can_increase": True, "switch_to": None})
        self.patient = db.get_patient(self.pid)
        self.answers = {"Q3": "no", "Q10a": "at_goal"}

    def tearDown(self):
        os.environ.pop("ASM_DB", None)
        for mod in ("clinic_store", "clinic"):
            sys.modules.pop(mod, None)

    # --- the context itself ------------------------------------------------
    def test_med_context_of_captures_the_family_confirmed_fields(self):
        snap = self.clinic.build_snapshot(self.patient, self.answers)
        snap.medication.concentration = 2.5          # what the family entered in Q1a
        ctx = self.clinic.med_context_of(snap)
        self.assertEqual(set(ctx), set(self.clinic.MED_CONTEXT_FIELDS))
        self.assertEqual(ctx["concentration"], 2.5)
        self.assertEqual(ctx["medication"], "clobazam")

    def test_round_trip_preserves_a_dose_the_intake_record_cannot_supply(self):
        # As submitted: 5 mL of a 2.5 mg/mL liquid, twice a day = 25 mg/day.
        submitted = self.clinic.build_snapshot(self.patient, self.answers)
        submitted.medication.concentration = 2.5
        self.assertEqual(submitted.medication.total_daily_dose, 25)
        ctx = self.clinic.med_context_of(submitted)

        sub_id = self.db.add_submission(1, self.pid, self.answers, "routine", [],
                                        med_context=ctx)
        stored = self.db.get_submission_bundle(sub_id)["submission"]
        self.assertIsNotNone(stored.get("med_context"),
                             "med_context must come back out of the store")

        # Re-run from storage: the dose survives.
        rebuilt = self.clinic.build_snapshot(self.patient, self.answers,
                                             med_context=stored["med_context"])
        self.assertEqual(rebuilt.medication.total_daily_dose, 25)
        self.assertEqual(rebuilt.medication.concentration, 2.5)

    def test_without_med_context_the_dose_is_lost(self):
        # Guards the regression the column exists to prevent: no context -> the
        # intake record has no strength -> mg/day is no longer computable.
        rebuilt = self.clinic.build_snapshot(self.patient, self.answers, med_context=None)
        self.assertIsNone(rebuilt.medication.total_daily_dose)

    def test_med_context_is_optional_and_partial_values_do_not_clobber_intake(self):
        # A legacy row (no context) still builds, and a partial context only
        # overrides the fields it actually carries.
        sub_id = self.db.add_submission(1, self.pid, self.answers, "routine", [])
        self.assertIsNone(
            self.db.get_submission_bundle(sub_id)["submission"].get("med_context"))
        snap = self.clinic.build_snapshot(self.patient, self.answers,
                                          med_context={"concentration": 5.0})
        self.assertEqual(snap.medication.concentration, 5.0)
        self.assertEqual(snap.medication.medication, "clobazam")   # from intake
        self.assertEqual(snap.medication.frequency_per_day, 2)     # from intake


if __name__ == "__main__":
    unittest.main()
