"""
test_drug_identity.py
---------------------
Offline tests for the drug-name normalizer + same-family checker, and for the
engine's switch annotation (which must NOT change the category).

Run:  python -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core import (
    DrugClassifier, run_engine, snapshot_from_interval,
    PatientContext, MedicationContext, PhysicianContingency,
)


def snap(answers, med_name="carbamazepine", cont=None):
    p = PatientContext(external_id="T", age_years=12, weight_kg=45)
    m = MedicationContext(medication=med_name, dose_amount=200, dose_unit="mg",
                          frequency_per_day=2)
    c = PhysicianContingency(**(cont or {"can_increase_dose": True}))
    return snapshot_from_interval(p, m, c, answers)


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.dc = DrugClassifier()

    def test_exact_generic(self):
        self.assertEqual(self.dc.normalize("levetiracetam").canonical, "levetiracetam")

    def test_brand_to_generic(self):
        nd = self.dc.normalize("Keppra")
        self.assertEqual(nd.canonical, "levetiracetam")
        self.assertEqual(nd.matched_via, "synonym")

    def test_typo_fuzzy(self):
        nd = self.dc.normalize("lamotragine")   # misspelled lamotrigine
        self.assertEqual(nd.canonical, "lamotrigine")
        self.assertEqual(nd.matched_via, "fuzzy")
        self.assertLess(nd.confidence, 1.0)

    def test_unknown_returns_none(self):
        nd = self.dc.normalize("aspirin")
        self.assertIsNone(nd.canonical)

    def test_empty_returns_none(self):
        self.assertIsNone(self.dc.normalize("").canonical)


class TestSameFamily(unittest.TestCase):
    def setUp(self):
        self.dc = DrugClassifier(default_by="mechanism")

    def test_same_mechanism(self):
        # levetiracetam & brivaracetam are both SV2A ligands
        r = self.dc.same_family("levetiracetam", "Briviact", by="mechanism")
        self.assertTrue(r["same"])

    def test_different_mechanism(self):
        r = self.dc.same_family("levetiracetam", "carbamazepine", by="mechanism")
        self.assertFalse(r["same"])

    def test_same_atc(self):
        # carbamazepine & oxcarbazepine share ATC N03AF
        r = self.dc.same_family("carbamazepine", "oxcarbazepine", by="atc")
        self.assertTrue(r["same"])
        self.assertIn("N03AF", r["shared"])

    def test_both_definition(self):
        r = self.dc.same_family("levetiracetam", "brivaracetam", by="both")
        self.assertTrue(r["same"])

    def test_identical_drug(self):
        r = self.dc.same_family("Keppra", "levetiracetam")
        self.assertTrue(r["same"])

    def test_unresolved_returns_none(self):
        r = self.dc.same_family("levetiracetam", "aspirin")
        self.assertIsNone(r["same"])


class TestEngineAnnotation(unittest.TestCase):
    """The annotation must not alter the recommendation category."""

    def _switch_case(self, switch_to):
        # seizure-free + intolerable SE -> switch_medication
        answers = {"Q3": "no", "Q9": ["dizziness"], "Q10a": "at_goal",
                   "Q10c_impact": "big_wants_change"}
        return snap(answers, med_name="carbamazepine",
                    cont={"can_increase_dose": False, "can_switch_to": switch_to})

    def test_same_family_annotation_added(self):
        dc = DrugClassifier(default_by="atc")
        sn = self._switch_case("oxcarbazepine")   # same ATC family as carbamazepine
        rec = run_engine(sn, drug_classifier=dc)
        self.assertEqual(rec.category, "switch_medication")
        self.assertIsNotNone(rec.switch_family_note)
        self.assertIn("SAME", rec.switch_family_note)

    def test_category_unchanged_with_and_without_classifier(self):
        sn = self._switch_case("oxcarbazepine")
        without = run_engine(sn)
        with_dc = run_engine(sn, drug_classifier=DrugClassifier())
        self.assertEqual(without.category, with_dc.category)
        self.assertIsNone(without.switch_family_note)

    def test_different_family_annotation(self):
        dc = DrugClassifier(default_by="mechanism")
        sn = self._switch_case("levetiracetam")    # different mechanism from carbamazepine
        rec = run_engine(sn, drug_classifier=dc)
        self.assertIn("DIFFERENT", rec.switch_family_note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
