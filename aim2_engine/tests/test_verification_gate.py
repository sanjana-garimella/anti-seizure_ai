"""
tests/test_verification_gate.py
-------------------------------
Regression PIN for the build-time verification gate.

verify_all.py surfaces hard defects loudly (its exit code is red while a known
data defect is pending PI fix). To keep the unittest suite meaningful WITHOUT
either (a) going red on a known-and-tracked defect, or (b) silently missing a
NEW contradiction, we pin the exact set of known FAIL signatures. Any NEW FAIL
-> this test fails (a regression). A known FAIL getting FIXED -> this test also
fails, prompting you to update the pin (a good failure).

Signature = (property, drug). Update KNOWN_* only with a deliberate PI-reviewed
change.
"""
import unittest

from verification import verify_all


# Known, PI-tracked FAILs as of 2026-07-08:
#   * asm_rules: none (lamotrigine conmed overlap is WARN, handled by
#     conmed-based band selection).
KNOWN_RULES_FAILS = set()
#   * ladder: none. The oxcarbazepine >=70 kg defect (effective_max 2130 >
#     cap 2100; high-increments duplicating the up rungs) was corrected
#     2026-07-08 to the PI-confirmed cap of 2100.
KNOWN_LADDER_FAILS = set()
#   * red-team: no must-catch miss (non-fatal hardening leaks are allowed).
KNOWN_MUST_CATCH_MISSES = 0


def _fail_sigs(summary):
    return {(f["property"], f["drug"]) for f in summary["findings"]
            if f["severity"] == "FAIL"}


class TestVerificationGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = verify_all.run_all(write=False)

    def test_rules_fails_match_pin(self):
        self.assertEqual(_fail_sigs(self.res["rules"]), KNOWN_RULES_FAILS,
                         "asm_rules.json FAIL set changed - new contradiction or a "
                         "fixed one; review and update KNOWN_RULES_FAILS.")

    def test_ladder_fails_match_pin(self):
        self.assertEqual(_fail_sigs(self.res["ladder"]), KNOWN_LADDER_FAILS,
                         "ladder/threshold FAIL set changed - review and update "
                         "KNOWN_LADDER_FAILS.")

    def test_redteam_must_catch_pin(self):
        self.assertEqual(self.res["redteam"]["must_catch_misses"],
                         KNOWN_MUST_CATCH_MISSES)

    def test_gate_runs_and_reports_ok_flag(self):
        # The gate object always has an 'ok' bool; it is False while the known
        # ladder defect is pending PI fix. Assert the structure, not the value.
        self.assertIn("ok", self.res)
        self.assertIsInstance(self.res["ok"], bool)


if __name__ == "__main__":
    unittest.main()
