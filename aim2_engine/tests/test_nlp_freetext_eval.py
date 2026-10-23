"""
test_nlp_freetext_eval.py
-------------------------
Gates the scored free-text NLP benchmark (validation/nlp_freetext_eval) in the
test suite:

  * RED-FLAG RECALL MUST BE 1.0 - a missed urgent signal is the worst error, so
    this is a hard gate.
  * overall F1 must stay above a regression floor.
  * no false positives regression on the (currently perfect-precision) set.

Two known side-effect misses are expected and documented (comma negation scope
T18; experiencer over-scope 'hitting his sister' T21) - they keep the benchmark
honest, so the F1 floor is set below 1.0 on purpose.
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VAL = os.path.join(_ROOT, "validation")


@unittest.skipUnless(os.path.isfile(os.path.join(_VAL, "nlp_freetext_eval.py")),
                     "nlp_freetext_eval not present")
class TestFreeTextBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for p in (_ROOT, _VAL):
            if p not in sys.path:
                sys.path.insert(0, p)
        import nlp_freetext_eval as E
        cls.report = E.run(write=False)

    def test_red_flag_recall_is_perfect(self):
        rf = self.report["red_flag"]
        self.assertEqual(rf["recall"], 1.0,
                         f"red-flag recall must be 1.0; missed: {rf['miss_detail']}")
        self.assertTrue(rf["pass"])

    def test_overall_f1_above_floor(self):
        self.assertGreaterEqual(self.report["overall"]["f1"], 0.85,
                                f"overall F1 regressed: {self.report['overall']}")

    def test_no_false_positives(self):
        # current pipeline has perfect precision on this set; guard against regressions
        self.assertEqual(self.report["overall"]["fp"], 0,
                         "a false-positive finding was introduced")

    def test_duration_and_frequency_fields_scored(self):
        pf = self.report["per_field"]
        self.assertIn("seizure_duration_bucket", pf)
        self.assertIn("seizure_frequency", pf)


if __name__ == "__main__":
    unittest.main()
