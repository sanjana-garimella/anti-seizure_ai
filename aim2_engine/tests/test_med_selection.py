"""
Tests for engine_core.med_selection (rule-based medication SELECTION for the
switch / add flow). Stdlib-only, like the rest of the suite.

Coverage:
  * data-file integrity (9 ranked meds, valid rule references, DRAFT provenance);
  * one unit test per Sheet-2 rule, asserting the avoid/favor from Dr. Barbour's
    wording;
  * hand-computed ranking + tie-breaking;
  * defer-on-ambiguity (< 3 eligible -> blank slots, flagged);
  * the safety invariant (no recommended med is in the avoided set);
  * DRAFT / physician-review posture;
  * the normalizer (Yes/No, headers vs canonical keys, prior-tried mapping);
  * a 50-case regression lock against the workbook fixture.
"""

import json
import os
import unittest

from engine_core.med_selection import (
    load_selection_ruleset, select, normalize_patient, MedSelection,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "med_selection_reference_cases.json")


def names(sel: MedSelection):
    return [o["display_name"] for o in sel.options]


def ids(sel: MedSelection):
    return [o["id"] for o in sel.options]


class TestDataIntegrity(unittest.TestCase):
    def setUp(self):
        self.rs = load_selection_ruleset()

    def test_nine_ranked_medications(self):
        self.assertEqual(len(self.rs.medications), 9)
        ranks = sorted(m.base_rank for m in self.rs.medications)
        self.assertEqual(ranks, list(range(1, 10)))  # unique 1..9

    def test_top_ranked_is_lamotrigine(self):
        top = min(self.rs.medications, key=lambda m: m.base_rank)
        self.assertEqual(top.id, "lamotrigine")

    def test_rules_reference_known_medications(self):
        valid = self.rs.med_ids
        for r in self.rs.rules:
            for mid in r.get("avoid", []) + r.get("favor", []):
                self.assertIn(mid, valid, f"rule {r.get('id')} references unknown med {mid}")

    def test_workbook_values_are_verified(self):
        for m in self.rs.medications:
            self.assertTrue(m.verified)
        for r in self.rs.rules:
            self.assertTrue(r.get("verified", False))

    def test_kidney_stones_rule_active(self):
        # Rules!B18; the clinic survey now asks about kidney stones (2026-09-30).
        ks = [r for r in self.rs.rules if r["id"] == "SEL-KIDNEY-STONES"]
        self.assertEqual(len(ks), 1)
        self.assertTrue(ks[0].get("active", True))


class TestNormalizer(unittest.TestCase):
    def test_yes_no_and_missing(self):
        p = normalize_patient({"Mood difficulties": "Yes", "Migraines": "No"})
        self.assertTrue(p["mood_difficulties"])
        self.assertFalse(p["migraines"])
        self.assertFalse(p["kidney_disease"])  # unanswered -> False, never fires a rule

    def test_headers_and_canonical_equivalent(self):
        a = normalize_patient({"Sex": "Female", "Age": 12, "No labs": "Yes"})
        b = normalize_patient({"sex": "female", "age": 12, "prefers_no_labs": True})
        self.assertEqual(a["sex"], b["sex"])
        self.assertEqual(a["age"], b["age"])
        self.assertEqual(a["prefers_no_labs"], b["prefers_no_labs"])

    def test_prior_tried_mapping(self):
        p = normalize_patient({"Previously tried Keppra": "Yes",
                               "Previously tried depakote": "Yes"})
        self.assertEqual(p["prior_tried"], {"levetiracetam", "valproic_acid"})


class TestRankingBasics(unittest.TestCase):
    def setUp(self):
        self.rs = load_selection_ruleset()

    def test_baseline_naive_child(self):
        # All-negative young child, no prior med, no mood/behavior:
        # combination rule avoids brivaracetam; nothing favored; rank by base order.
        sel = select({"Age": 3, "Sex": "Male"}, self.rs)
        self.assertEqual(list(sel.avoided.keys()), ["brivaracetam"])
        self.assertEqual(ids(sel), ["lamotrigine", "lacosamide", "oxcarbazepine"])
        self.assertFalse(sel.deferred)

    def test_favor_count_beats_base_rank(self):
        # Behavioral difficulties favors oxcarbazepine, lamotrigine, clobazam and
        # avoids levetiracetam/zonisamide/topiramate. Lamotrigine (rank1) and
        # oxcarbazepine (rank4) each get a favor; both outrank unfavored meds.
        sel = select({"Age": 6, "Sex": "Male", "Behavioral difficulties": "Yes"}, self.rs)
        self.assertEqual(ids(sel)[0], "lamotrigine")           # rank1 + favored
        self.assertIn("oxcarbazepine", ids(sel))               # favored, jumps ahead
        self.assertNotIn("levetiracetam", sel.eligible)        # avoided


class TestEachSheet2Rule(unittest.TestCase):
    """One assertion per rule, straight from Dr. Barbour's wording."""

    def setUp(self):
        self.rs = load_selection_ruleset()

    def _sel(self, **attrs):
        base = {"Age": 6, "Sex": "Male"}
        base.update(attrs)
        return select(base, self.rs)

    def test_female_over_10(self):
        s = self._sel(Sex="Female", Age=13)
        self.assertIn("valproic_acid", s.avoided)
        self.assertIn("topiramate", s.avoided)
        self.assertIn("lamotrigine", s.favored)

    def test_female_10_or_under_not_triggered(self):
        s = self._sel(Sex="Female", Age=10)
        self.assertNotIn("valproic_acid", s.avoided)  # strictly older than 10

    def test_single_jerk(self):
        s = self._sel(**{"Presence of whole body single jerk seizures": "Yes"})
        for m in ("lamotrigine", "clobazam", "brivaracetam"):
            self.assertIn(m, s.favored)
        self.assertIn("oxcarbazepine", s.avoided)
        self.assertIn("lacosamide", s.avoided)

    def test_staring(self):
        s = self._sel(**{"Presence of staring seizures": "Yes"})
        self.assertIn("lamotrigine", s.favored)
        self.assertIn("lacosamide", s.avoided)

    def test_nonadherence(self):
        s = self._sel(**{"Presence of medication non-adherence": "Yes"})
        self.assertIn("lamotrigine", s.avoided)
        self.assertIn("oxcarbazepine", s.avoided)

    def test_prior_tried_is_avoided(self):
        s = self._sel(**{"Previously tried lamotrigine": "Yes"})
        self.assertIn("lamotrigine", s.avoided)
        self.assertNotIn("lamotrigine", s.eligible)

    def test_ancestry(self):
        s = self._sel(**{"Southeastern Asian or Han Chinese": "Yes"})
        self.assertIn("lamotrigine", s.avoided)
        self.assertIn("oxcarbazepine", s.avoided)

    def test_no_labs(self):
        s = self._sel(**{"No labs": "Yes"})
        for m in ("oxcarbazepine", "zonisamide", "valproic_acid", "topiramate"):
            self.assertIn(m, s.avoided)

    def test_mood(self):
        s = self._sel(**{"Mood difficulties": "Yes"})
        self.assertIn("lamotrigine", s.favored)
        self.assertIn("oxcarbazepine", s.favored)
        for m in ("levetiracetam", "zonisamide", "topiramate"):
            self.assertIn(m, s.avoided)

    def test_behavior(self):
        s = self._sel(**{"Behavioral difficulties": "Yes"})
        for m in ("oxcarbazepine", "lamotrigine", "clobazam"):
            self.assertIn(m, s.favored)
        for m in ("levetiracetam", "zonisamide", "topiramate"):
            self.assertIn(m, s.avoided)

    def test_combination_rule_avoids_brivaracetam(self):
        # naive + no mood + no behavior -> avoid brivaracetam
        s = self._sel()
        self.assertIn("brivaracetam", s.avoided)

    def test_combination_rule_not_triggered_if_mood(self):
        s = self._sel(**{"Mood difficulties": "Yes"})
        self.assertNotIn("brivaracetam", s.avoided)

    def test_combination_rule_not_triggered_if_prior(self):
        s = self._sel(**{"Previously tried topiramate": "Yes"})
        self.assertNotIn("brivaracetam", s.avoided)

    def test_sleep(self):
        s = self._sel(**{"Trouble sleeping": "Yes"})
        self.assertIn("clobazam", s.favored)
        for m in ("levetiracetam", "zonisamide", "topiramate"):
            self.assertIn(m, s.avoided)

    def test_migraines(self):
        s = self._sel(**{"Migraines": "Yes"})
        for m in ("topiramate", "valproic_acid", "zonisamide"):
            self.assertIn(m, s.favored)

    def test_kidney_disease(self):
        s = self._sel(**{"Kidney disease": "Yes"})
        self.assertIn("lamotrigine", s.favored)
        for m in ("topiramate", "zonisamide", "levetiracetam", "brivaracetam"):
            self.assertIn(m, s.avoided)

    def test_kidney_stones(self):
        s = self._sel(**{"Kidney stones": "Yes"})
        for m in ("topiramate", "zonisamide"):
            self.assertIn(m, s.avoided)

    def test_liver_diabetes_obesity_bleeding_avoid_valproate(self):
        for col in ("Liver disease", "Diabetes", "Obesity", "Bleeding disorder"):
            s = self._sel(**{col: "Yes"})
            self.assertIn("valproic_acid", s.avoided, col)

    def test_heart(self):
        s = self._sel(**{"Heart issue": "Yes"})
        self.assertIn("lacosamide", s.avoided)

    def test_sulfa(self):
        s = self._sel(**{"Sulfa drug allergy": "Yes"})
        self.assertIn("zonisamide", s.avoided)

    def test_prior_chemo(self):
        s = self._sel(**{"Prior chemotherapy": "Yes"})
        self.assertIn("oxcarbazepine", s.avoided)


class TestDeferAndSafety(unittest.TestCase):
    def setUp(self):
        self.rs = load_selection_ruleset()

    def test_defer_when_fewer_than_three(self):
        # Case-16 shape: female>10 + staring + SE-Asian + kidney disease -> only
        # clobazam survives; 2 slots must be left blank and flagged.
        sel = select({"Age": 12, "Sex": "Female",
                      "Presence of staring seizures": "Yes",
                      "Southeastern Asian or Han Chinese": "Yes",
                      "Migraines": "Yes", "Kidney disease": "Yes"}, self.rs)
        self.assertEqual(ids(sel), ["clobazam"])
        self.assertTrue(sel.deferred)
        self.assertIsNotNone(sel.defer_reason)

    def test_no_recommendation_is_in_avoided_set(self):
        # Safety invariant across a stress patient with many avoids.
        sel = select({"Age": 6, "Sex": "Male",
                      "Presence of whole body single jerk seizures": "Yes",
                      "Presence of medication non-adherence": "Yes",
                      "No labs": "Yes", "Mood difficulties": "Yes",
                      "Behavioral difficulties": "Yes"}, self.rs)
        for mid in ids(sel):
            self.assertNotIn(mid, sel.avoided)


class TestDraftPosture(unittest.TestCase):
    def test_requires_physician_review_and_draft(self):
        sel = select({"Age": 4, "Sex": "Female"})
        self.assertTrue(sel.requires_physician_review)
        self.assertTrue(sel.draft)
        self.assertTrue(sel.sources.get("verified", False))
        self.assertIn("Switching to a different medication.xlsx", sel.sources.get("status", ""))
        for opt in sel.options:
            self.assertTrue(opt["verified"])

    def test_rationale_traces_to_rules(self):
        sel = select({"Age": 13, "Sex": "Female", "Mood difficulties": "Yes"})
        fired = {r["rule"] for r in sel.rationale}
        self.assertIn("SEL-AGE-SEX-FEMALE-GT10", fired)
        self.assertIn("SEL-MOOD", fired)
        self.assertEqual(fired, set(sel.sources["rules_fired"]))


class TestRegressionFixture(unittest.TestCase):
    """Lock behavior against the 50 simulated cases in the workbook."""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.doc = json.load(fh)
        cls.rs = load_selection_ruleset()

    def test_all_fifty_cases_reproduce(self):
        mismatches = []
        for case in self.doc["cases"]:
            sel = select(case["attributes"], self.rs)
            got = names(sel) + [None] * (3 - len(sel.options))
            got = [g or None for g in got]
            expected = [e or None for e in case["expected"]]
            if got != expected:
                mismatches.append((case["case_id"], expected, got))
        self.assertEqual(mismatches, [], f"regression drift: {mismatches}")

    def test_no_recommendation_in_avoided_across_fixture(self):
        for case in self.doc["cases"]:
            sel = select(case["attributes"], self.rs)
            for mid in ids(sel):
                self.assertNotIn(mid, sel.avoided,
                                 f"case {case['case_id']} recommends an avoided med")


if __name__ == "__main__":
    unittest.main()
