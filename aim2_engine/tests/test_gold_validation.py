"""
test_gold_validation.py
-----------------------
Guards the gold-standard validation set (Dr. Barbour's reviewed A1-J10 cases,
validation/gold_cases.json). Asserts the harness still meets the proposal's
Cohen's kappa target and that the only engine/gold divergence is the one we
have flagged for the PI (J2). Regenerates the cases from the builder so the
test fails loudly if the encoded set drifts from the builder.

Run:  python -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from engine_core import run_engine
from engine_core.asm_rules import load_asm_rules
from validation.harness import run_validation, _snapshot_from_case

_VAL = os.path.join(_ROOT, "validation")
_GOLD = os.path.join(_VAL, "gold_cases.json")
_ASM = os.path.join(_ROOT, "data", "asm_rules.json")


class TestGoldValidation(unittest.TestCase):
    def setUp(self):
        # Rebuild BOTH reproducible artifacts so the test can't pass/fail against
        # a stale or clobbered file: the gold cases AND the clinician ASM rules
        # (asm_rules.json drives indication/SJS logic and was once silently
        # overwritten by an older builder, regressing J4/H6 - see PROJECT_LOG).
        for builder in (os.path.join(_VAL, "_build_gold_cases.py"),
                        os.path.join(_ROOT, "data", "_build_asm_rules.py")):
            subprocess.run([sys.executable, builder], check=True, capture_output=True)
        with open(_GOLD) as fh:
            self.cases = json.load(fh)["cases"]
        self.reg = load_asm_rules(_ASM)
        # Guard: the Barbour drug facts the gold cases depend on must be present.
        assert self.reg["ethosuximide"].treats_only == ["absence"], \
            "asm_rules.json is missing Barbour review fields - rebuild data/_build_asm_rules.py"

    def test_all_63_cases_present(self):
        self.assertEqual(len(self.cases), 63)

    def test_kappa_meets_target(self):
        rep = run_validation(self.cases, asm_registry=self.reg, verbose=False)
        self.assertGreaterEqual(rep["cohens_kappa"], 0.8, rep["rejections"])

    def test_only_flagged_divergence(self):
        rep = run_validation(self.cases, asm_registry=self.reg, verbose=False)
        diverging = {r["label"].split()[0] for r in rep["rejections"]}
        # J2 is the documented PI question (LEV behavioral SE: switch vs offer options).
        self.assertEqual(diverging, {"J2"}, rep["rejections"])

    def test_safety_cases_flag_concern(self):
        # The blistering/spreading-rash + seizure-emergency cases must raise a
        # safety concern (H1,H2,H3,H4,H5).
        rep = run_validation(self.cases, asm_registry=self.reg, verbose=False)
        self.assertGreaterEqual(rep["safety_concern_count"], 5)

    def test_h5_large_rash_surfaces_sjs_rash_flag(self):
        # Regression: H5 is a LARGE (non-blistering) rash reported alongside an
        # unrelated urgent flag ("er_visit"). test_safety_cases_flag_concern
        # only checks the safety_concern COUNT, which stayed correct even when
        # `categorize.SJS_TRIGGER_RASH` was blistering-only (the switch fired
        # via `rash_spreading`) - it would not have caught "sjs_rash" silently
        # missing from urgent_flags. Check the actual flag content here.
        h5 = next(c for c in self.cases if c.get("label", "").startswith("H5"))
        snap = _snapshot_from_case(h5)
        rec = run_engine(snap, asm_registry=self.reg)
        self.assertEqual(rec.category, "switch_medication")
        self.assertIn("sjs_rash", rec.urgent_flags)
        self.assertIn("er_visit", rec.urgent_flags)


if __name__ == "__main__":
    unittest.main()
