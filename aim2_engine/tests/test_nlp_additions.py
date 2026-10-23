"""
test_nlp_additions.py
---------------------
Tests for the 2026-07-08 advisory-NLP additions (all pure stdlib):
  duration/frequency normalizer, ConText assertion + abstention, calibration,
  interpretable NB baseline, weak supervision, entity normalization, typo
  corrector, and the span-level eval metrics.

These exercise the ADVISORY layer only; none of it touches engine_core.
"""
import unittest

from nlp.normalize_duration_frequency import normalize as dfn
from nlp.context_assertion import assess_term, should_abstain, Assertion
from nlp.calibration import (IsotonicCalibrator, PlattCalibrator,
                             expected_calibration_error, pick_threshold)
from nlp.baseline_nb import TextClassifier
from nlp.weak_supervision import LabelModel, keyword_lf, make_training_set
from nlp.normalize_entities import normalize as enorm
from nlp.typo import correct_text
from validation.nlp_eval_metrics import span_prf, bootstrap_ci, red_flag_recall


class TestDurationFrequency(unittest.TestCase):
    def _d(self, text):
        return {f.suggested_value for f in dfn(text) if f.field == "seizure_duration_bucket"}

    def _f(self, text):
        return {f.suggested_value for f in dfn(text) if f.field == "seizure_frequency"}

    def test_minutes_range_upper_bound(self):
        self.assertIn("over_3min", self._d("about 2 to 3 minutes"))

    def test_seconds(self):
        self.assertIn("under_1min", self._d("lasts 45 seconds"))

    def test_vague_couple_minutes(self):
        self.assertIn("1_3min", self._d("a couple minutes"))

    def test_over_five(self):
        self.assertIn("over_5min", self._d("more than 5 minutes"))

    def test_frequency_count_unit(self):
        self.assertIn("3 per_day", self._f("3 times a day"))
        self.assertIn("1 per_week", self._f("once a week"))

    def test_frequency_word_count(self):
        self.assertIn("2 per_day", self._f("twice a day"))

    def test_no_false_finding(self):
        self.assertEqual(dfn("no idea how long or how often"), [])


class TestContextAssertion(unittest.TestCase):
    def test_negation(self):
        self.assertTrue(assess_term("no rash on arms", "rash").negated)

    def test_historical(self):
        self.assertTrue(assess_term("she used to have staring spells", "staring").historical)

    def test_experiencer_other(self):
        self.assertEqual(assess_term("my other child had seizures", "seizures").experiencer, "other")

    def test_hedged(self):
        self.assertTrue(assess_term("maybe some dizziness", "dizziness").hedged)

    def test_current_patient_positive(self):
        self.assertTrue(assess_term("he has jerks every morning", "jerks").is_current_patient_positive)

    def test_abstain_on_negation(self):
        self.assertTrue(should_abstain(0.9, assess_term("no rash", "rash")))

    def test_abstain_low_conf(self):
        self.assertTrue(should_abstain(0.2, Assertion()))

    def test_red_flag_never_abstains_on_negation(self):
        a = assess_term("denies trouble breathing", "breathing")
        self.assertFalse(should_abstain(0.9, a, escalate_only=True))


class TestCalibration(unittest.TestCase):
    def setUp(self):
        import random
        random.seed(1)
        self.s, self.y = [], []
        for _ in range(300):
            x = random.random(); self.s.append(x)
            self.y.append(1 if random.random() < x * x else 0)

    def test_isotonic_reduces_ece(self):
        before = expected_calibration_error(self.s, self.y)
        iso = IsotonicCalibrator().fit(self.s, self.y)
        after = expected_calibration_error([iso.predict(x) for x in self.s], self.y)
        self.assertLessEqual(after, before)

    def test_platt_monotone(self):
        p = PlattCalibrator().fit(self.s, self.y)
        self.assertLess(p.predict(0.1), p.predict(0.9))

    def test_thresholds_asymmetric(self):
        hi_rec = pick_threshold(self.s, self.y, target_recall=0.99)
        hi_prec = pick_threshold(self.s, self.y, target_precision=0.9)
        self.assertLessEqual(hi_rec["tau"], hi_prec["tau"])


class TestBaselineNB(unittest.TestCase):
    def setUp(self):
        self.clf = TextClassifier().fit(
            ["sleepy and drowsy", "tired and sleepy", "irritable and angry",
             "aggressive and moody", "dizzy and lightheaded", "off balance dizzy"],
            ["somnolence", "somnolence", "behavior", "behavior", "dizziness", "dizziness"])

    def test_predicts_reasonably(self):
        self.assertEqual(self.clf.predict("very drowsy child"), "somnolence")
        self.assertEqual(self.clf.predict("angry outbursts"), "behavior")

    def test_top_features_interpretable(self):
        self.assertIn("dizzy", self.clf.top_features("dizziness", 5))

    def test_proba_sums_to_one(self):
        p = self.clf.predict_proba("drowsy")
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)


class TestWeakSupervision(unittest.TestCase):
    def setUp(self):
        self.lm = LabelModel([
            keyword_lf("sleepy", "somnolence", ["sleepy", "drowsy", "tired"]),
            keyword_lf("behav", "behavior", ["irritable", "angry", "aggressive"]),
            keyword_lf("rash", "rash", ["rash", "blister", "hives"]),
        ])

    def test_majority_vote(self):
        label, conf = self.lm.predict("kid is drowsy")
        self.assertEqual(label, "somnolence")
        self.assertGreater(conf, 0)

    def test_abstain_when_no_lf_fires(self):
        self.assertIsNone(self.lm.predict("no clear symptom")[0])

    def test_weighted_vote_resolves_conflict(self):
        self.lm.estimate_accuracies(["drowsy", "angry", "rash"],
                                    ["somnolence", "behavior", "rash"])
        rows = make_training_set(self.lm, ["so drowsy today", "itchy rash"])
        self.assertEqual(len(rows), 2)


class TestEntityNormalization(unittest.TestCase):
    def test_synonym(self):
        self.assertEqual(enorm("drowsy", "side_effect")[0], "somnolence")

    def test_fuzzy_typo(self):
        canon, method, _ = enorm("drowsey", "side_effect")
        self.assertEqual(canon, "somnolence")
        self.assertEqual(method, "fuzzy")

    def test_seizure_class(self):
        self.assertEqual(enorm("whole body shaking", "seizure")[0], "generalized_tonic_clonic")

    def test_unknown(self):
        self.assertEqual(enorm("banana", "side_effect")[1], "unknown")


class TestTypo(unittest.TestCase):
    def test_corrects_clinical_typos(self):
        fixed, audit = correct_text("drowsey and dizzly siezures")
        self.assertIn("drowsy", fixed)
        self.assertIn("seizures", fixed)
        self.assertTrue(audit)

    def test_leaves_normal_text(self):
        fixed, audit = correct_text("the cat sat on the mat")
        self.assertEqual(audit, [])


class TestEvalMetrics(unittest.TestCase):
    def test_span_prf(self):
        g = [{("a", "1"), ("b", "2")}]
        p = [{("a", "1")}]
        r = span_prf(g, p)
        self.assertEqual(r["recall"], 0.5)
        self.assertEqual(r["precision"], 1.0)

    def test_bootstrap_ci_bounds(self):
        g = [{("a", "1")}, {("b", "2")}, {("c", "3")}]
        ci = bootstrap_ci(g, g, "f1", n_boot=200)
        self.assertEqual(ci["point"], 1.0)
        self.assertLessEqual(ci["lo"], ci["hi"])

    def test_red_flag_recall_detects_miss(self):
        g = [{("red_flag", "breathing")}]
        p = [set()]
        r = red_flag_recall(g, p)
        self.assertEqual(r["recall"], 0.0)
        self.assertFalse(r["pass"])


if __name__ == "__main__":
    unittest.main()
