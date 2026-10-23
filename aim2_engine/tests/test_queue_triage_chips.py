"""
test_queue_triage_chips.py
--------------------------
The review queue classifies each check-in as emergent / urgent / non-urgent, plus
safety and any other signal that needs a second look. Everything is read from
STORED engine output (`recommendations.urgency_tier`, `.safety_flag`,
`.dose_exceeds_max`, `.recognized`, `.urgent_flags_json`) - the queue never
re-decides anything and never parses the note prose.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ASM_DB", os.path.join(tempfile.mkdtemp(prefix="queuechips_"), "clinic.db"))

import clinic  # noqa: E402
import clinic_store as db  # noqa: E402


def labels(item):
    import re
    return re.findall(r"<span class='pill \w+'>([^<]+)</span>", clinic._queue_chips(item))


def classes(item):
    import re
    return re.findall(r"<span class='pill (\w+)'>", clinic._queue_chips(item))


class TestQueueChips(unittest.TestCase):
    def test_three_tiers_get_a_three_step_scale(self):
        for tier, label, cls in (("emergent", "Emergent", "urgent"),
                                 ("urgent", "Urgent", "review"),
                                 ("non_urgent", "Non-urgent", "routine")):
            item = {"urgency_tier": tier}
            self.assertIn(label, labels(item))
            self.assertIn(cls, classes(item))

    def test_no_breakthrough_says_so_rather_than_showing_nothing(self):
        self.assertEqual(labels({"urgency_tier": None}), ["No seizures"])

    def test_safety_comes_first_and_suppresses_the_no_seizures_chip(self):
        item = {"safety_flag": 1, "urgency_tier": None}
        self.assertEqual(labels(item)[0], "Safety")
        self.assertNotIn("No seizures", labels(item))

    def test_safety_and_tier_can_both_show(self):
        self.assertEqual(labels({"safety_flag": 1, "urgency_tier": "emergent"}),
                         ["Safety", "Emergent"])

    def test_other_signals_are_chipped(self):
        item = {"urgency_tier": "urgent", "dose_exceeds_max": 1, "recognized": 0}
        got = labels(item)
        self.assertIn("Dose above max", got)
        self.assertIn("Med not recognized", got)

    def test_a_finding_is_not_chipped_twice(self):
        # The engine's urgent_flags also arrive through triage()'s reason list.
        item = {"safety_flag": 1,
                "urgent_flags": ["rash_spreading_blistering"],
                "flags": ["Urgent safety signal. Physician review required.",
                          "Possible serious skin reaction (SJS/TEN), urgent"]}
        got = labels(item)
        self.assertEqual(len(got), len(set(got)), f"duplicate chip in {got}")

    def test_redundant_and_empty_triage_reasons_are_dropped(self):
        # "No changes suggested" adds nothing next to the Routine header pill, and
        # reasons already covered by a chip must not repeat.
        item = {"urgency_tier": None, "dose_exceeds_max": 1, "recognized": 0,
                "flags": ["No changes suggested; nothing unusual detected",
                          "Reported dose is ABOVE the recommended maximum. Verify the entry.",
                          "Medication name not recognized",
                          "Suggested next step: Increase the dose"]}
        got = labels(item)
        self.assertEqual(got, ["No seizures", "Dose above max", "Med not recognized"])

    def test_actionable_triage_reasons_survive(self):
        item = {"urgency_tier": None, "flags": ["Family would like a medication change"]}
        self.assertIn("Family would like a medication change", labels(item))

    def test_labels_are_escaped(self):
        item = {"urgency_tier": None, "flags": ["<script>alert(1)</script>"]}
        html_out = clinic._queue_chips(item)
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)


class TestDosePosition(unittest.TestCase):
    """The queue card states where the reported dose sits rather than making the
    reviewer compare three numbers. Band values are clinician-supplied; nothing is
    implied when they are missing."""

    BAND = {"min_maintenance_mg_per_day": 900, "effective_max_mg_per_day": 1800,
            "absolute_max_mg_per_day": 3000}

    def test_positions(self):
        for dose, expected in ((500, "below the typical range"),
                               (1200, "within the typical range"),
                               (1800, "at or above the usual effective max"),
                               (2500, "at or above the usual effective max"),
                               (3200, "above the absolute maximum")):
            got = clinic._dose_position(self.BAND, dose)
            self.assertIsNotNone(got, f"no position for {dose}")
            self.assertEqual(got[0], expected, f"dose {dose}")

    def test_severity_class_escalates_with_the_position(self):
        self.assertEqual(clinic._dose_position(self.BAND, 3200)[1], "urgent")
        self.assertEqual(clinic._dose_position(self.BAND, 1200)[1], "routine")

    def test_nothing_is_implied_without_a_band_or_a_dose(self):
        self.assertIsNone(clinic._dose_position(self.BAND, None))
        self.assertIsNone(clinic._dose_position(None, 1000))
        self.assertIsNone(clinic._dose_position({}, 1000))


class TestQueueTriageColumns(unittest.TestCase):
    """The tier and dose-above-max are stored as COLUMNS so the queue can classify
    without parsing the note, and older DBs get them by migration."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="queuecols_")
        db.DB_PATH = os.path.join(self._tmp, "clinic.db")
        db.DATA_DIR = self._tmp
        db.init_db(default_doctor_pw="x")

    def test_columns_exist_after_init(self):
        import sqlite3
        c = sqlite3.connect(db.DB_PATH)
        cols = [r[1] for r in c.execute("pragma table_info(recommendations)")]
        self.assertIn("urgency_tier", cols)
        self.assertIn("dose_exceeds_max", cols)

    def test_add_recommendation_persists_the_triage_signals(self):
        pid = db.add_patient({"external_id": "q1", "age": 8, "weight": 26,
                              "epilepsy_type": "unspecified", "medication": "levetiracetam",
                              "dose_amount": 250, "dose_unit": "mg", "frequency": 2,
                              "concentration": None, "can_increase": True, "switch_to": None})
        sub = db.add_submission(1, pid, {"Q3": "yes"}, "review", ["x"])
        db.add_recommendation(sub, {"category": "escalate", "urgency_tier": "emergent",
                                    "dose_exceeds_max": True, "clinical_summary": "s",
                                    "medication_recognized": True})
        row = db.get_submission_bundle(sub)["recommendation"]
        self.assertEqual(row["urgency_tier"], "emergent")
        self.assertEqual(row["dose_exceeds_max"], 1)

    def test_reason_summary_is_persisted_and_reaches_the_queue(self):
        # The card's one-line "why" - what the check-in needs - was not stored at
        # all, so the queue could only show the category label.
        pid = db.add_patient({"external_id": "q2", "age": 9, "weight": 30,
                              "epilepsy_type": "unspecified", "medication": "levetiracetam",
                              "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
                              "concentration": None, "can_increase": True, "switch_to": None})
        inst = db.create_instance(pid)
        sub = db.add_submission(inst["id"], pid, {"Q3": "yes"}, "review", [])
        db.add_recommendation(sub, {"category": "insufficient_data", "clinical_summary": "s",
                                    "medication_recognized": True,
                                    "reason_summary": "Need seizure duration to decide."})
        db.mark_submitted(inst["id"])
        row = [r for r in db.review_queue() if r["sub_id"] == sub][0]
        self.assertEqual(row["reason_summary"], "Need seizure duration to decide.")

    def test_queue_sorts_emergent_above_non_urgent_within_a_priority(self):
        ranks = [db._TIER_RANK.get(t, 3) for t in ("emergent", "urgent", "non_urgent", None)]
        self.assertEqual(ranks, sorted(ranks))


if __name__ == "__main__":
    unittest.main()
