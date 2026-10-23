"""Add/switch may only come from Dr. Barbour's sources (2026-09-30).

ADD_MEDICATION / SWITCH_MEDICATION must trace to a rule that applies one of her
decision-table cells (BT, R8-assume-urgent, NS, R6/R14 = L5 'not a fit'), one of
her gold-labelled switch rules (R0, R2, R3, R4), or her written notes:
  R5b, R4b  - kristen/modif.rtf "Change = adding" / "Change = stopping ... new one"
  R9-add/R9-switch - Aim2_Validation_Cases.docx.pdf, case C6 notes (at max: add if
             helpful, switch if not)
  R0b-sw/R0b-sw2   - Aim2_Validation_Cases.docx.pdf, case H3 notes (not helpful
             >=4 wk unchanged / >=2 wk worse -> consider switching)
Any new add/switch rule must be added here with its source.
"""
import json
import os
import unittest

from engine_core import run_engine
from validation.harness import _snapshot_from_case

TABLE_RULES = {"BT", "R8-assume-urgent", "NS", "R6", "R14"}
PI_LABELLED_EXCEPTIONS = {"R0", "R2", "R3", "R4"}
PI_NOTES_RULES = {"R5b", "R4b", "R9-add", "R9-switch", "R0b-sw", "R0b-sw2"}
ALLOWED = TABLE_RULES | PI_LABELLED_EXCEPTIONS | PI_NOTES_RULES

_HERE = os.path.dirname(os.path.abspath(__file__))
_VAL = os.path.join(_HERE, os.pardir, "validation")


def _cases(name):
    with open(os.path.join(_VAL, name), encoding="utf-8") as fh:
        d = json.load(fh)
    return d["cases"] if isinstance(d, dict) else d


class TestTableOnlyAddSwitch(unittest.TestCase):
    def test_add_switch_only_from_allowed_rules(self):
        for name in ("gold_cases.json", "simulated_cases.json"):
            for case in _cases(name):
                rec = run_engine(_snapshot_from_case(case))
                if rec.category in ("add_medication", "switch_medication"):
                    rule = rec.rationale[-1]["rule"]
                    with self.subTest(case=case.get("label"), rule=rule):
                        self.assertIn(rule, ALLOWED)


if __name__ == "__main__":
    unittest.main()
