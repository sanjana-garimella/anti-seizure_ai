"""
test_family_templates.py
------------------------
Kristen's 2026-07 family-message templates (Family_Message_Templates_for_Review.docx),
wired into engine_core.outputs.build_patient_message.

Locks in:
  * template selection by structured decision fields (category + pace + n_up +
    still-titrating + family_prompt), NOT fragile free-text parsing;
  * doses filled ONLY from Dr. Barbour's clinician ladder (verified via the
    guardrail's allowed-number set), and held off (bracketed / no number) when
    the ladder or a planned-end dose is unavailable (Kristen §3);
  * every engine-produced draft passes nlp.guardrails.validate_message (no
    fabricated dose, no category contradiction, carries the DRAFT notice);
  * the guardrail still REJECTS a genuinely fabricated dose (integrity).
"""

import os
import re
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core import (snapshot_from_interval, PatientContext, MedicationContext,
                         PhysicianContingency, run_engine)
from engine_core.asm_rules import load_asm_rules
from engine_core.decision import Decision, RecommendationCategory as RC
from engine_core.outputs import build_patient_message, family_message_allowed_doses
from nlp.guardrails import validate_message

REG = load_asm_rules()


def _snap(ans, med="topiramate", age=7, wt=14.5, dose=50, freq=2):
    pc = PatientContext(external_id="t", age_years=age, weight_kg=wt)
    mc = MedicationContext(medication=med, dose_amount=dose, dose_unit="mg",
                           frequency_per_day=freq)
    return snapshot_from_interval(pc, mc, PhysicianContingency(can_increase_dose=True), ans)


def _stub_rec(cat, guidance="", message=None):
    """A minimal Recommendation-like object for the text guardrail (it reads
    category, safety_concern_flag, guidance_steps, titration_schedule,
    draft_patient_message)."""
    return types.SimpleNamespace(category=cat.value, safety_concern_flag=False,
                                 guidance_steps=guidance or None, titration_schedule=None,
                                 draft_patient_message=message)


# A titrating topiramate child whose ladder resolves (age 7 / 14.5 kg), current
# 100 mg/day twice daily, planned end 250 mg/day.
BASE = _snap({"Q3": "no", "Q10a": "still_increasing",
              "Q10f_planned_end_mg_per_day": 250})


class TestTemplateSelection(unittest.TestCase):

    def _msg(self, cat, guidance="", pace=None, labs=None, dosing=None, prompt=None,
             sn=BASE, followup=4):
        d = Decision(category=cat, guidance_steps=guidance or None, pace=pace,
                     followup_weeks=followup, lab_orders=labs or [],
                     dosing_note=dosing, family_prompt=prompt)
        return build_patient_message(sn, d, physician_name="Dr. Barbour")

    def test_A1_continue_titration(self):
        # Kristen kb-edits (2026-07-28): A1 = "continue the current plan and keep
        # increasing [med] on the schedule we already set"; the goal-dose number was
        # dropped, and A1 still never gives a new target dose.
        m = self._msg(RC.NO_CHANGE, "Continue titration on schedule; follow up in 4 weeks")
        self.assertIn("keep increasing", m)
        self.assertIn("continue the current plan", m)
        self.assertNotIn("increase to", m)               # A1 does not give a new target
        self.assertNotIn("goal dose of", m)              # goal-dose number dropped in kb-edits

    def test_A1_carries_no_dose_number(self):
        # A1 now carries no dose number at all (kb-edits), so nothing can be fabricated.
        sn = _snap({"Q3": "no", "Q10a": "still_increasing"})   # no planned end
        m = self._msg(RC.NO_CHANGE, sn=sn)
        self.assertIn("keep increasing", m)
        self.assertNotIn("mg/day", m)                    # no dose number in an A1 message
        self.assertNotIn("goal dose of", m)

    def test_A2_stay_at_current_dose(self):
        # Kristen kb-edits (2026-07-28): A2 = "You can continue the same [med] dose with
        # no change right now." (the current-dose number was dropped).
        m = self._msg(RC.NO_CHANGE, sn=_snap({"Q3": "no", "Q10a": "at_goal"}))
        self.assertIn("continue the same topiramate dose", m)
        self.assertIn("no change right now", m)

    def test_B1_one_step_fills_decided_dose(self):
        # Dr. Barbour 2026-10-25: the decided dose (mg, with the mL or tablet count),
        # here ladder 125 mg/day as liquid for a 7-year-old with no reported form.
        m = self._msg(RC.ESCALATE, "Make 1 dose increase; follow up 4 weeks")
        self.assertIn("increase topiramate: give 62.5 mg (2.5 mL) twice a day", m)

    def test_B2_two_steps_names_both_rungs_and_interval(self):
        m = self._msg(RC.ESCALATE, "Make 2 dose increases; follow up 4 weeks")
        self.assertIn("in two steps", m)
        self.assertIn("give 62.5 mg (2.5 mL) twice a day for 1 week", m)   # v9 interval "Weekly"
        self.assertIn("give 75 mg (3 mL) twice a day and stay at this dose", m)
        self.assertNotIn("go higher than", m)            # #67: no "don't go higher than" line

    def test_B3_faster_every_3_days(self):
        m = self._msg(RC.ESCALATE, "Quicken titration to every 3 days, end 2 dose increases higher",
                      pace="faster")
        self.assertIn("more quickly than usual", m)
        self.assertIn("every 3 days instead of every 7", m)      # Kristen's kb-edit wording
        self.assertIn("twice a day for 3 days, then give", m)

    def test_C_slow_the_cadence(self):
        # Kristen kb-edits: C = slow the pace ("every 2 weeks instead of weekly"),
        # still climbing - NOT a hold.
        m = self._msg(RC.SLOWER_UPTITRATION)
        self.assertIn("every 2 weeks instead of weekly", m)
        self.assertNotIn("rather than increasing", m)

    def test_HOLD_watch_and_wait(self):
        # HOLD (no "weeks 1-2" guidance) = stay at the current dose to let the body
        # settle (side-effect-aware; not A2's "doing well enough").
        m = self._msg(RC.HOLD)
        self.assertIn("keep the current dose of 100 mg/day", m)
        self.assertIn("more time to settle", m)

    def test_B5_hold_two_weeks_then_increase(self):
        m = self._msg(RC.HOLD, "Switch dosing (uneven/TID/wait); weeks 1-2 stay, after week 2 "
                               "make 1 dose increase; order labs", labs=["CBC", "CMP"])
        self.assertIn("for the next 2 weeks, then increase", m)
        self.assertIn("ordered some labs", m)            # labs add-on (kb-edits wording)

    def test_L3_options_listed_when_family_has_not_chosen(self):
        # Dr. Barbour 2026-10-25 (#52): the family chooses; when they have not yet, the
        # draft lists the options the table cell offers (her template H wording).
        d = Decision(category=RC.HOLD, followup_weeks=4, dosing_note="Level-3 options",
                     dosing_options=["uneven", "tid", "wait"])
        m = build_patient_message(BASE, d, physician_name="Dr. Barbour")
        self.assertIn("We have a few options to handle the side effects", m)
        self.assertIn("smaller dose in the morning and a larger dose at night", m)
        self.assertIn("three times a day", m)
        self.assertIn("watching and waiting", m)
        self.assertNotIn("adding a second medication", m)   # not offered by this cell

    def test_L3_family_choice_drives_the_hold_message(self):
        tabs = _snap({"Q3": "no", "Q10a": "at_goal"})
        tabs.medication.dose_unit, tabs.medication.concentration = "tablets", 25
        tabs.medication.dose_amount = 2                     # 2 x 25 mg twice a day = 100 mg/day
        for choice, words in (("uneven", "in the morning and"), ("tid", "three times a day")):
            d = Decision(category=RC.HOLD, followup_weeks=4, dosing_options=["uneven", "tid", "wait"],
                         dosing_choice=choice, dosing_note="Family chose")
            m = build_patient_message(tabs, d, physician_name="Dr. Barbour")
            self.assertIn("as you chose", m)
            self.assertIn(words, m)
            self.assertNotIn("We have a few options", m)

    def test_lower_and_add_wording(self):
        # Level 4 + ongoing seizures (her table): lower the dose AND add a second medicine.
        m = self._msg(RC.ADD_MEDICATION, "Lower dose; add a second medication; follow up 4 weeks")
        self.assertIn("lower the topiramate dose to [lower dose", m)
        self.assertIn("add a second medicine", m)

    def test_E1_lower_dose_brackets_the_target(self):
        m = self._msg(RC.LOWER_DOSE, "Lower dose; follow up 4 weeks", labs=["CBC"])
        self.assertIn("lower the topiramate dose to [lower dose", m)   # engine does not compute a lower target

    def test_F_switch(self):
        m = self._msg(RC.SWITCH_MEDICATION)
        self.assertIn("trying a different seizure medicine", m)
        self.assertIn("keep giving topiramate as prescribed", m)

    def test_G_add(self):
        m = self._msg(RC.ADD_MEDICATION)
        self.assertIn("add a second medicine", m)

    def test_H_question_embeds_prompt(self):
        m = self._msg(RC.INSUFFICIENT_DATA, prompt="Are side effects easing after one week? Yes / No")
        self.assertIn("I'd like to ask you one question", m)
        self.assertIn("Are side effects easing after one week?", m)

    def test_every_template_is_a_draft(self):
        for cat, guid in [(RC.NO_CHANGE, ""), (RC.ESCALATE, "Make 1 dose increase"),
                          (RC.ESCALATE, "Quicken titration to every 3 days, end 1 higher"),
                          (RC.SLOWER_UPTITRATION, ""), (RC.LOWER_DOSE, ""),
                          (RC.SWITCH_MEDICATION, ""), (RC.ADD_MEDICATION, ""),
                          (RC.INSUFFICIENT_DATA, "")]:
            m = self._msg(cat, guid, pace=("faster" if "Quicken" in guid else None))
            self.assertIn("DRAFT", m)
            self.assertIn("physician review", m.lower())


class TestGuardrailOnEngineDrafts(unittest.TestCase):
    """Every engine-produced family DRAFT must pass its own text guardrail -
    including the ones that now carry clinician-ladder dose numbers."""

    def _check(self, cat, guidance="", pace=None):
        d = Decision(category=cat, guidance_steps=guidance or None, pace=pace, followup_weeks=4)
        msg = build_patient_message(BASE, d, physician_name="Dr. Barbour")
        rec = _stub_rec(cat, guidance, message=msg)
        r = validate_message(msg, rec, BASE)
        self.assertTrue(r.ok, f"{cat} guardrail rejected engine draft: {r.problems}\n{msg}")

    def test_dose_bearing_drafts_pass(self):
        self._check(RC.ESCALATE, "Make 1 dose increase")
        self._check(RC.ESCALATE, "Make 2 dose increases")
        self._check(RC.ESCALATE, "Quicken titration to every 3 days, end 2 dose increases higher", "faster")
        self._check(RC.NO_CHANGE, "Continue titration on schedule")   # A1 (carries no dose number)
        self._check(RC.NO_CHANGE, "")                                 # A2, current dose

    def test_realistic_pipeline_draft_passes(self):
        # Full engine over a real urgent breakthrough -> faster escalation w/ ladder dose.
        ans = {"Q3": "yes", "Q4": ["focal"], "Q5": 3, "Q5_unit": "per_week", "Q6": "more",
               "Q10a": "still_increasing",
               "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                     "frequency_bucket": "weekly", "unresponsive": False}]}
        sn = _snap(ans, med="levetiracetam", age=8, wt=20, dose=250, freq=2)
        rec = run_engine(sn, asm_registry=REG, physician_name="Dr. Barbour")
        self.assertEqual(rec.category, "escalate")
        r = validate_message(rec.draft_patient_message, rec, sn)
        self.assertTrue(r.ok, r.problems)

    def test_non_round_ladder_dose_is_rounded_and_passes_guardrail(self):
        # Ethosuximide lands on non-round ladder rungs (v9: e.g. 621.411 at 23.3 kg).
        # Dr. Barbour 2026-10-25: the family sees only the decided, measurable dose, so
        # no 6-digit ladder value is printed; the draft still passes its guardrail.
        ans = {"Q3": "yes", "Q4": ["focal"], "Q5": 2, "Q5_unit": "per_week",
               "Q10a": "at_goal", "Q10b": "over_2_weeks",
               "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                     "frequency_bucket": "weekly", "unresponsive": False}]}
        sn = _snap(ans, med="ethosuximide", age=8, wt=23.3, dose=250, freq=2)
        rec = run_engine(sn, asm_registry=REG, physician_name="Dr. Barbour")
        self.assertEqual(rec.category, "escalate")
        msg = rec.draft_patient_message
        self.assertFalse(re.findall(r"\d+\.\d{2,} ?(?:mg|mL)", msg), msg)   # nothing like 621.41 mg
        r = validate_message(msg, rec, sn)
        self.assertTrue(r.ok, r.problems)
        # Integrity: a nearby number that is NOT the printed decided value is still caught.
        mg = re.search(r"give (\d+(?:\.\d+)?) mg", msg).group(1)
        bad = msg.replace(f"give {mg} mg", f"give {float(mg) + 0.01:g} mg", 1)
        self.assertFalse(validate_message(bad, rec, sn).ok)

    def test_guardrail_still_rejects_a_fabricated_dose(self):
        # Integrity: a number NOT traceable to the ladder / reported dose is still caught.
        d = Decision(category=RC.ESCALATE, guidance_steps="Make 1 dose increase")
        rec = _stub_rec(RC.ESCALATE, "Make 1 dose increase")
        r = validate_message("Please increase to 99999 mg. Draft for physician review.", rec, BASE)
        self.assertFalse(r.ok)

    def test_allowed_doses_are_ladder_values(self):
        d = Decision(category=RC.ESCALATE, guidance_steps="Make 2 dose increases")
        allowed = family_message_allowed_doses(BASE, d)
        self.assertIn(100.0, allowed)     # reported current total
        self.assertIn(125.0, allowed)     # ladder rung 1
        self.assertIn(150.0, allowed)     # ladder rung 2
        self.assertIn(62.5, allowed)      # per-dose of rung 1 (125/2)


if __name__ == "__main__":
    unittest.main()


class TestFamilyTabletLine(unittest.TestCase):
    """Family draft names tablets/mL from the SAME rows as the clinician summary."""

    def _msg(self, age, unit, conc, dose=1, wt=30):
        from engine_core.outputs import _formulation_rows
        pc = PatientContext(external_id="t", age_years=age, weight_kg=wt)
        mc = MedicationContext(medication="levetiracetam", dose_amount=dose, dose_unit=unit,
                               concentration=conc, frequency_per_day=2, formulation="tablet (IR)")
        sn = snapshot_from_interval(pc, mc, PhysicianContingency(can_increase_dose=True),
                                    {"Q3": "no", "Q10a": "still_increasing"})
        d = Decision(RC.ESCALATE, guidance_steps="Increase by 1 step")
        msg = build_patient_message(sn, d)
        rec = types.SimpleNamespace(category=d.category.value, safety_concern_flag=False,
                                    guidance_steps=d.guidance_steps, titration_schedule=None,
                                    draft_patient_message=msg)
        return msg, validate_message(msg, rec, sn), _formulation_rows(sn, d)[1]

    def test_tablets_for_older_child_match_summary_rows_and_pass_guardrail(self):
        # Every step converts -> the step sentence itself names the tablets (same rows
        # as the clinician summary) and the separate 'How much to give' line is dropped.
        msg, gr, rows = self._msg(12, "tablets", 250, dose=2)
        self.assertIn("give " + rows[0]["family_tablet_text"], msg)
        self.assertIn("500 mg (2 tablets) in the morning and 750 mg (3 tablets) at night", msg)
        self.assertNotIn("How much to give", msg)
        self.assertNotIn("go higher than", msg)
        self.assertTrue(gr.ok, gr.problems)

    def test_strength_change_is_stated_when_the_dose_needs_another_tablet(self):
        # 750 mg tablets don't fit the next dose -> the best-fitting strength (#62/#66),
        # and the family is told it is a different tablet.
        msg, gr, rows = self._msg(12, "tablets", 750, dose=1)
        st = rows[0]["strength_mg"]
        self.assertNotEqual(st, 750)
        self.assertIn(f"uses {st:g} mg tablets, a different tablet from the 750 mg tablets "
                      "your child takes now", msg)
        self.assertTrue(gr.ok, gr.problems)

    def test_no_upper_limit_line_and_interval_wording(self):
        # #67 (Dr. Barbour 2026-10-25): no "don't go higher than" line.
        from engine_core.outputs import _formulation_rows
        pc = PatientContext(external_id="t", age_years=12, weight_kg=30)
        mc = MedicationContext(medication="levetiracetam", dose_amount=2, dose_unit="tablets",
                               concentration=250, frequency_per_day=2, formulation="tablet (IR)")
        sn = snapshot_from_interval(pc, mc, PhysicianContingency(can_increase_dose=True),
                                    {"Q3": "no", "Q10a": "still_increasing"})
        d = Decision(RC.ESCALATE, guidance_steps="Make 2 dose increases")
        msg = build_patient_message(sn, d)
        rows = _formulation_rows(sn, d)[1]
        self.assertTrue(rows[-1]["tablet"])
        self.assertNotIn("go higher than", msg)
        self.assertIn("for 1 week", msg)
        self.assertNotIn("Weekly", msg)

    def test_liquid_for_young_child(self):
        msg, gr, _rows = self._msg(8, "mL", 100, dose=5)
        self.assertRegex(msg, r"give \d+(\.\d)? mg \(\d+(\.\d)? mL\) twice a day")
        self.assertTrue(gr.ok, gr.problems)

    def test_no_line_without_frequency(self):
        pc = PatientContext(external_id="t", age_years=12, weight_kg=30)
        mc = MedicationContext(medication="levetiracetam", dose_amount=1, dose_unit="tablets",
                               concentration=500, frequency_per_day=None)
        sn = snapshot_from_interval(pc, mc, PhysicianContingency(can_increase_dose=True),
                                    {"Q3": "no", "Q10a": "still_increasing"})
        msg = build_patient_message(sn, Decision(RC.ESCALATE, guidance_steps="Increase by 1 step"))
        self.assertNotIn("How much to give", msg)


class TestMixedLiquidTabletCap(unittest.TestCase):
    """Mixed plan (Hazel: step 1 liquid, step 2 capsules): Dr. Barbour 2026-10-25 - the
    step sentence names the capsules (decided dose), not the ladder dose."""

    def test_step_sentence_names_each_steps_decided_dose(self):
        from engine_core.outputs import _formulation_rows, _family_up_plan
        ans = {"Q3": "yes", "Q10a": "at_goal", "Q10c_time": "over_2_weeks", "Q5": 2,
               "Q5_unit": "per_week",
               "seizure_episodes": [{"look": ["focal"], "duration": "1_3min",
                                     "frequency_bucket": "weekly", "unresponsive": False}]}
        sn = _snap(ans, med="ethosuximide", age=12, wt=52.2, dose=500, freq=2)
        rec = run_engine(sn, asm_registry=REG, physician_name="Dr. Barbour")
        self.assertEqual(rec.category, "escalate")
        d = Decision(RC.ESCALATE, guidance_steps=rec.guidance_steps, pace=None)
        rows = _formulation_rows(sn, d, _family_up_plan(sn, d))[1]
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0].get("family_tablet_text"))        # step 1: liquid
        self.assertTrue(rows[1].get("family_tablet_text"))         # step 2: tablets
        tot = rows[1]["tablet"]["total_mg_per_day"]
        ladder = rows[1]["target_mg_per_day"]
        self.assertNotAlmostEqual(tot, ladder, places=1)           # the case that diverged
        msg = rec.draft_patient_message
        self.assertIn("then give " + rows[1]["family_tablet_text"], msg.replace("Then, give", "then give"))
        self.assertIn(rows[0]["family_liquid_text"], msg)
        self.assertIn("capsules", msg)                              # ethosuximide 250 mg = capsule
        self.assertNotIn(f"{ladder:g}", msg)                        # never the exact ladder dose
        self.assertNotIn("go higher than", msg)
        self.assertTrue(validate_message(msg, rec, sn).ok)
