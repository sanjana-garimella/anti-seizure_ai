"""
tests/test_verification_and_advisory.py
----------------------------------------
Tests for the build-time verification tooling and the advisory-layer POCs added
in the 2026-07 modern-methods pass. All of these are OUTSIDE the decision path;
these tests assert that contract as well as behavior.
"""
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RULES = os.path.join(_ROOT, "data", "asm_rules.json")

from verification import verify_asm_rules as V
from nlp.conformal import ConformalRedFlag, FLAG, CLEAR, DEFER
from nlp.assertion_secondopinion import (AssertionSecondOpinion, AssertionResult,
                                         PRESENT, ABSENT, SOMEONE_ELSE)
from nlp import structured_decoding as SD
from nlp.interfaces import FreeTextFinding
from nlp.structurer import LexiconStructurer
from verification import redteam_safety as RT


class TestZ3Harness(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(V.self_test(), 0)

    def test_real_rules_flag_lamotrigine_overlap(self):
        bands = V.load_bands(_RULES)
        findings = V.verify(bands, V.ExactBackend())
        lam_A = [f for f in findings if f.drug == "lamotrigine" and f.prop == "A"]
        # The 3 conmed-dependent bands overlap on age/weight; now handled by
        # conmed-based selection, so reported as WARN (not FAIL) + annotated.
        self.assertTrue(lam_A, "expected lamotrigine overlap findings")
        self.assertTrue(all(f.severity == "WARN" for f in lam_A))
        self.assertTrue(all(f.witness.get("needs_conmed_selection") for f in lam_A))

    def test_clean_single_band_has_no_fail(self):
        good = [V._mk("good", 0, "2-17y", 2, 17, None, None, 20, 40, 60, 80, None, 20)]
        self.assertFalse(any(f.is_fail() for f in V.verify(good, V.ExactBackend())))


class TestConformal(unittest.TestCase):
    def _synth(self, n_pos, n_neg, seed=1):
        import random
        r = random.Random(seed)
        s, y = [], []
        for _ in range(n_pos):
            s.append(min(1.0, max(0.0, r.gauss(0.75, 0.15)))); y.append(1)
        for _ in range(n_neg):
            s.append(min(1.0, max(0.0, r.gauss(0.30, 0.15)))); y.append(0)
        return s, y

    def test_miss_rate_bounded_by_alpha(self):
        cs, cy = self._synth(400, 1600, seed=1)
        ts, ty = self._synth(400, 1600, seed=2)
        for a in (0.10, 0.05):
            crf = ConformalRedFlag(alpha_miss=a, alpha_fp=0.10).calibrate(cs, cy)
            rep = crf.coverage_report(ts, ty)
            self.assertLessEqual(rep["empirical_miss_rate"], a + 0.03,
                                 f"miss rate {rep['empirical_miss_rate']} > alpha {a}")

    def test_decisions_are_in_set(self):
        cs, cy = self._synth(200, 800)
        crf = ConformalRedFlag(0.05, 0.05).calibrate(cs, cy)
        self.assertIn(crf.decide(0.99)["decision"], (FLAG, DEFER))
        self.assertIn(crf.decide(0.01)["decision"], (CLEAR, DEFER))


class TestAssertionSecondOpinion(unittest.TestCase):
    def test_baseline_labels(self):
        so = AssertionSecondOpinion()
        self.assertEqual(so.review_term("no rash after the dose", "rash").label, ABSENT)
        self.assertEqual(so.review_term("she developed a rash", "rash").label, PRESENT)
        self.assertEqual(
            so.review_term("my other child had seizures", "seizures").label,
            SOMEONE_ELSE)

    def test_disagreement_defers(self):
        class Stub:
            available = True
            name = "stub"
            def assert_label(self, text, span):
                return AssertionResult(PRESENT, 0.9, "stub")
        so = AssertionSecondOpinion(model=Stub())
        r = so.review_term("no rash after the dose", "rash")
        self.assertTrue(r.defer)
        self.assertEqual(r.agreement, "disagree")


class TestStructuredDecoding(unittest.TestCase):
    def test_schema_from_lexicon(self):
        sch = SD.FindingSchema.from_lexicon()
        self.assertIn("side_effect", sch.fields)
        self.assertTrue(sch.is_valid("side_effect", "rash"))
        self.assertFalse(sch.is_valid("side_effect", "not_a_value"))
        self.assertTrue(sch.is_valid("drug_mention", "keppra"))

    def test_validation_drops_invalid(self):
        f = [FreeTextFinding("side_effect", "rash", 0.6, "rash"),
             FreeTextFinding("side_effect", "bogus", 0.6, "x"),
             FreeTextFinding("nope", "x", 0.6, "x")]
        rep = SD.validate(f)
        self.assertEqual(len(rep.kept), 1)
        self.assertEqual(rep.n_dropped, 2)

    def test_wrapper_preserves_valid_baseline_output(self):
        wrapped = SD.SchemaConstrainedStructurer(inner=LexiconStructurer())
        out = wrapped.structure("she had a rash and takes keppra")
        self.assertTrue(out)  # baseline output is already schema-valid
        self.assertEqual(wrapped.last_report.n_dropped, 0)


class TestRedTeam(unittest.TestCase):
    def test_no_must_catch_miss(self):
        summary = RT.run(RT.LexiconRedFlagDetector())
        self.assertEqual(summary["must_catch_misses"], 0,
                         "a must-catch danger phrase was missed")

    def test_suite_actually_probes_leaks(self):
        # The suite is only useful if it still contains failing (leak) cases to
        # track; assert it reports the known non-fatal hardening targets.
        summary = RT.run(RT.LexiconRedFlagDetector())
        self.assertGreater(summary["misses"], 0)


if __name__ == "__main__":
    unittest.main()
