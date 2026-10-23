"""
test_nlp.py
-----------
Offline tests for the OPTIONAL NLP layer (points A, B, D). Proves the modules
work AND that they never touch the deterministic decision path.

Run:  python -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core import (
    run_engine, snapshot_from_interval, PatientContext, MedicationContext,
    PhysicianContingency, ASMRule, DoseBand,
)
from nlp import (
    LexiconStructurer, LexiconRedFlagDetector, urgent_categories,
    TemplateMessageGenerator, GuardedGenerator, validate_message,
)


def snap(answers, med=None, cont=None):
    p = PatientContext(external_id="T", age_years=8, weight_kg=30)
    m = MedicationContext(medication="levetiracetam", dose_amount=250, dose_unit="mg",
                          frequency_per_day=2, **(med or {}))
    c = PhysicianContingency(**(cont or {"can_increase_dose": True, "can_switch_to": "lamotrigine"}))
    return snapshot_from_interval(p, m, c, answers)


# Episodes that cross the "increase faster" threshold (so a breakthrough case
# yields ESCALATE post-Barbour-review, instead of deferring for duration).
_FAST_EP = [{"look": ["focal"], "duration": "2_5min", "frequency": 2, "frequency_unit": "per_week"}]


class TestRedFlags(unittest.TestCase):
    def setUp(self):
        self.det = LexiconRedFlagDetector()

    def test_detects_sjs_and_emergency(self):
        flags = self.det.scan("the rash is spreading and she has a blister")
        cats = {f.category for f in flags}
        self.assertIn("sjs_skin", cats)

    def test_detects_large_rash(self):
        # 2026-07-01: the structured Q9a field already treats a LARGE
        # (non-blistering) rash as an urgent SJS trigger (categorize.py);
        # the free-text safety net was missing the matching phrase.
        flags = self.det.scan("noticed a large rash on his arms and legs today")
        self.assertTrue(any(f.category == "sjs_skin" for f in flags))

    def test_detects_widespread_rash(self):
        # Barbour T12 explicitly named "widespread rash" (alongside spreading/
        # blistering/mouth sores) as an SJS/TEN descriptor for CBZ/OXC; it was
        # never added to the term list until now.
        flags = self.det.scan("a widespread rash showed up over the weekend")
        self.assertTrue(any(f.category == "sjs_skin" for f in flags))

    def test_detects_unresponsive(self):
        flags = self.det.scan("he would not wake up afterward")
        self.assertTrue(any(f.category == "breathing_or_unresponsive" for f in flags))

    def test_benign_text_no_flags(self):
        self.assertEqual(self.det.scan("everything is going well, thanks!"), [])

    def test_scan_many_and_urgent_categories(self):
        flags = self.det.scan_many({"Q13": "went to the emergency room", "Q1a": "no change"})
        self.assertIn("emergency_care", urgent_categories(flags))

    def test_terms_loaded(self):
        self.assertGreater(len(self.det.terms), 0)


class TestStructurer(unittest.TestCase):
    def setUp(self):
        self.s = LexiconStructurer()

    def test_suggests_missed_reason_and_side_effect(self):
        f = self.s.structure("we couldn't afford it and he was very sleepy", "Q13")
        fields = {(x.field, x.suggested_value) for x in f}
        self.assertIn(("missed_dose_reason", "cost_or_insurance"), fields)
        self.assertIn(("side_effect", "sleepiness"), fields)

    def test_findings_are_advisory(self):
        f = self.s.structure("she stopped taking it", "Q1a")
        self.assertTrue(all(0.0 <= x.confidence <= 1.0 for x in f))
        self.assertTrue(all("not used by the decision engine" in x.note for x in f))

    def test_no_findings_on_empty(self):
        self.assertEqual(self.s.structure(""), [])


class TestDecisionPathUntouchedByFreeText(unittest.TestCase):
    """The whole point: NLP inputs must not change the engine's decision."""

    def test_free_text_does_not_change_decision(self):
        base = {"Q3": "yes", "Q10a": "still_increasing"}
        without = run_engine(snap(base))
        with_text = run_engine(snap({**base, "Q13": "the rash is spreading, blister!"}))
        self.assertEqual(without.category, with_text.category)
        self.assertEqual([r["rule"] for r in without.rationale],
                         [r["rule"] for r in with_text.rationale])

    def test_redflag_runs_in_parallel(self):
        # The red flag is found by the detector even though it didn't alter
        # the engine's category (escalate). Duration episodes -> escalate-faster.
        rec = run_engine(snap({"Q3": "yes", "Q10a": "still_increasing", "Q13": "blister",
                               "seizure_episodes": _FAST_EP}))
        self.assertEqual(rec.category, "escalate")
        flags = LexiconRedFlagDetector().scan("blister")
        self.assertTrue(flags)


class TestGenerators(unittest.TestCase):
    def _escalate_rec(self):
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=1500, max_daily_mg=1500,
            standard_uptitration_step_mg=500, standard_uptitration_interval_weeks=2)])
        sn = snap({"Q3": "yes", "Q10a": "still_increasing", "seizure_episodes": _FAST_EP})
        return run_engine(sn, asm_rule=rule), sn

    def test_template_default_returns_engine_draft(self):
        rec, sn = self._escalate_rec()
        out = TemplateMessageGenerator().generate_patient_message(rec, sn)
        self.assertEqual(out, rec.draft_patient_message)
        self.assertIsNotNone(out)

    def test_template_safety_returns_approved_stop_draft(self):
        # Kristen 2026-07: safety cases now emit the engine's approved DRAFT (was None).
        # A rash -> "stop" draft; still a physician-approved draft, never auto-sent.
        sn = snap({"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"})
        rec = run_engine(sn)
        out = TemplateMessageGenerator().generate_patient_message(rec, sn)
        self.assertIsNotNone(out)
        self.assertIn("stop giving", out.lower())

    def test_guarded_no_model_uses_template(self):
        rec, sn = self._escalate_rec()
        g = GuardedGenerator(model_callable=None)
        out = g.generate_patient_message(rec, sn)
        self.assertEqual(out, rec.draft_patient_message)
        self.assertEqual(g.last_source, "template")

    def test_guarded_good_model_used(self):
        rec, sn = self._escalate_rec()
        good = lambda prompt: ("Your care team may slowly increase the medicine to "
                               "1000 mg. This is a draft for physician review.")
        g = GuardedGenerator(model_callable=good)
        out = g.generate_patient_message(rec, sn)
        self.assertEqual(g.last_source, "model")
        self.assertIn("1000 mg", out)

    def test_guarded_bad_model_falls_back(self):
        rec, sn = self._escalate_rec()
        bad = lambda prompt: "Switch to a different medicine; take 9999 mg now."
        g = GuardedGenerator(model_callable=bad)
        out = g.generate_patient_message(rec, sn)
        self.assertEqual(g.last_source, "template")           # guardrail rejected
        self.assertEqual(out, rec.draft_patient_message)

    def test_guarded_safety_never_calls_model(self):
        calls = []
        sn = snap({"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"})
        rec = run_engine(sn)
        g = GuardedGenerator(model_callable=lambda p: calls.append(1) or "x")
        out = g.generate_patient_message(rec, sn)
        self.assertIsNotNone(out)                             # emits the approved draft
        self.assertEqual(g.last_source, "safety_template")
        self.assertEqual(calls, [])                           # model STILL never invoked


class TestGuardrails(unittest.TestCase):
    def _escalate_rec(self):
        rule = ASMRule(medication="levetiracetam", dose_bands=[DoseBand(
            label="b", min_age_years=2, max_age_years=18,
            dose_range_max_mg_per_day=1500, max_daily_mg=1500,
            standard_uptitration_step_mg=500, standard_uptitration_interval_weeks=2)])
        sn = snap({"Q3": "yes", "Q10a": "still_increasing", "Q4": ["focal"],
                   "seizure_episodes": _FAST_EP})
        return run_engine(sn, asm_rule=rule), sn

    def test_rejects_invented_number(self):
        rec, sn = self._escalate_rec()
        r = validate_message("Increase to 9999 mg. Draft for physician review.", rec, sn)
        self.assertFalse(r.ok)

    def test_rejects_missing_disclaimer(self):
        rec, sn = self._escalate_rec()
        # No draft / physician-review / care-team marker anywhere -> rejected.
        r = validate_message("Please increase the medicine to 1000 mg now.", rec, sn)
        self.assertFalse(r.ok)

    def test_accepts_valid(self):
        rec, sn = self._escalate_rec()
        r = validate_message("Your care team may increase to 1000 mg. Draft for review.", rec, sn)
        self.assertTrue(r.ok, r.problems)

    def test_safety_only_approved_draft_passes(self):
        # Kristen 2026-07: safety guardrail now allows None OR the engine's approved
        # draft - but still REJECTS arbitrary text (a model can't inject safety copy).
        sn = snap({"Q3": "no", "Q9": ["rash"], "Q9a": "blisters"})
        rec = run_engine(sn)
        self.assertFalse(validate_message("anything", rec, sn).ok)                 # arbitrary rejected
        self.assertTrue(validate_message(None, rec, sn).ok)                        # withhold still valid
        self.assertTrue(validate_message(rec.draft_patient_message, rec, sn).ok)   # approved draft passes

    def test_category_contradiction(self):
        sn = snap({"Q3": "no", "Q10a": "at_goal"})       # no_change
        rec = run_engine(sn)
        r = validate_message("We will increase the dose. Draft for review.", rec, sn)
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
