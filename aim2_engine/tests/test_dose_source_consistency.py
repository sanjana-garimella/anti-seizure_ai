"""
test_dose_source_consistency.py
-------------------------------
Guard against SILENT DRIFT between the two files that carry dose numbers:

  * data/dose_titration_ladder.json  - Dr. Barbour's final_doses_titration_v6.xlsx
    (the SOURCE OF TRUTH), used by the Dosing & pacing reference, the clinician
    report's expected-dose line, and the review-queue expected dose.
  * data/asm_rules.json              - drug rules used by the decision engine +
    the titration SCHEDULE ceiling/step and the over-max flag.

They agree today, but they are separate files: an edit to one that isn't mirrored
in the other would let the schedule/ceiling drift from the source of truth. These
tests FAIL if that happens, so the two files stay a single logical source.
(Fix #4 from the 2026-07-14 review: "guarantee they can't drift.")
"""
import json
import os
import unittest

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _load(name):
    with open(os.path.join(_DATA, name)) as fh:
        return json.load(fh)


class TestDoseSourceConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asm = _load("asm_rules.json")
        cls.ladder = (_load("dose_titration_ladder.json").get("bands_by_drug") or {})

    def _asm_ceilings(self, drug):
        """Every absolute ceiling asm_rules encodes for a drug (across bands)."""
        out = set()
        for b in (self.asm.get(drug, {}) or {}).get("dose_bands", []):
            for k in ("absolute_max_mg_per_day", "max_daily_mg", "dose_range_max_mg_per_day"):
                v = b.get(k)
                if v is not None:
                    out.add(round(float(v), 3))
        return out

    def test_every_ladder_ceiling_exists_in_asm_rules(self):
        """SAFETY-CRITICAL: each ladder band's absolute max must also be an
        asm_rules ceiling for that drug (the engine's over-max flag + schedule
        use asm_rules; the reference/report use the ladder - they must match)."""
        problems = []
        for drug, bands in self.ladder.items():
            asm_ceils = self._asm_ceilings(drug)
            for b in bands:
                m = b.get("max_absolute_mg_per_day")
                if m is None:
                    continue
                if round(float(m), 3) not in asm_ceils:
                    problems.append(f"{drug}: ladder band '{b.get('band_label')}' max "
                                    f"{m} not in asm_rules ceilings {sorted(asm_ceils)}")
        self.assertEqual(problems, [], "asm_rules / ladder ceiling drift:\n" + "\n".join(problems))

    def test_first_band_increment_matches(self):
        """Pacing consistency: the per-step increment for a drug's first band must
        agree between asm_rules (step) and the ladder (dose_increase_amount)."""
        problems = []
        for drug, bands in self.ladder.items():
            if not bands:
                continue
            lad_step = (bands[0].get("dose_increase_amount") or {}).get("value")
            ab = ((self.asm.get(drug, {}) or {}).get("dose_bands") or [{}])[0]
            asm_step = (ab.get("standard_uptitration_step_mg")
                        or ab.get("uptitration_step_mg_per_kg_per_day"))
            if lad_step is None or asm_step is None:
                continue
            if abs(float(lad_step) - float(asm_step)) > 1e-6:
                problems.append(f"{drug}: asm_rules step {asm_step} != ladder increment {lad_step}")
        self.assertEqual(problems, [], "asm_rules / ladder increment drift:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
