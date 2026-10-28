"""
End-to-end tests for the IN-SURVEY medication-selection flow (switch / add):
  * phase2_applies gating (switch/add with no safety flag);
  * _carried_over reuse of already-asked answers (no re-asking);
  * the selection card renders inside the survey (JS-free);
  * submitting the ONE survey computes + shows the top-3 and persists it;
  * the family ranking handler persists 1st/2nd/3rd/avoid;
  * the doctor review card renders the DRAFT options + family ranking.

Uses a temporary SQLite DB and drives the Handler methods directly (no socket),
mirroring the app's own no-server test approach.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clinic  # noqa: E402
import clinic_store as db  # noqa: E402


def _form(d):
    return {k: (v if isinstance(v, list) else [v]) for k, v in d.items()}


class TestCurrentMedExclusion(unittest.TestCase):
    """The child's CURRENT medicine must be excluded from the 'already tried'
    checklist AND from the suggested switch/add options - whether it is stored
    canonical ('valproic_acid'), as a plain name, or in the 'Name (Brand)'
    display form the classifier does not itself normalize (regression:
    'Lamotrigine (Lamictal)' previously leaked back into the options)."""

    def test_resolver_maps_all_stored_formats(self):
        rs = clinic.SELECTION_RULESET
        for m in rs.medications:
            for label in {m.id, m.display_name, m.display_name.split(" (")[0]}:
                self.assertEqual(clinic._current_med_selection_id(label), m.id,
                                 f"{label!r} should resolve to {m.id}")

    def test_checklist_omits_current_med(self):
        ids = [i for i, _ in clinic._selection_med_checklist(exclude_id="lamotrigine")]
        self.assertNotIn("lamotrigine", ids)
        self.assertEqual(len(ids), len(clinic.SELECTION_RULESET.medications) - 1)

    def test_options_exclude_current_med_including_brand_form(self):
        from engine_core.med_selection import select
        for stored in ("Lamotrigine (Lamictal)", "Valproic acid (Depakote)",
                       "lamotrigine", "valproic_acid"):
            attrs = clinic._selection_attrs_from_form({"medication": stored}, {}, {})
            cur_id = clinic._current_med_selection_id(stored)
            self.assertIn(cur_id, attrs["prior_tried"])
            sel = select(attrs, clinic.SELECTION_RULESET)
            self.assertNotIn(cur_id, sel.ranked_eligible,
                             f"current med {stored!r} leaked into options")


class TestSelectionHelpers(unittest.TestCase):
    def test_phase2_applies_only_safe_switch_add(self):
        self.assertTrue(clinic.phase2_applies({"category": "switch_medication", "safety_concern_flag": False}))
        self.assertTrue(clinic.phase2_applies({"category": "add_medication", "safety_concern_flag": False}))
        self.assertFalse(clinic.phase2_applies({"category": "switch_medication", "safety_concern_flag": True}))
        self.assertFalse(clinic.phase2_applies({"category": "no_change", "safety_concern_flag": False}))

    def test_carried_over_reuses_known_answers(self):
        patient = {"sex": "Female", "age": 12}
        answers = {"Q4": ["brief_staring", "sudden_jerks"], "Q2": "stopped", "Q_ancestry": "yes"}
        co = clinic._carried_over(patient, answers)
        self.assertEqual(co["sex"], "female")
        self.assertEqual(co["age"], 12)
        self.assertTrue(co["staring_seizures"])
        self.assertTrue(co["whole_body_single_jerk"])
        self.assertTrue(co["med_nonadherence"])       # "stopped" -> poor adherence
        self.assertTrue(co["se_asian_han_chinese"])

    def test_selection_questions_moved_off_the_survey(self):
        # Dr. Barbour 2026-10-25: the medicine-options questions are on a second page that
        # appears only when the decision is switch/add - not in the main survey.
        cards = clinic._survey_cards({"medication": "levetiracetam"})
        self.assertNotIn("id=card-medsel", cards)
        self.assertNotIn("name=prefers_no_labs", cards)
        page2 = clinic.medsel_questions_page("tok", 7, {"medication": "levetiracetam"})
        self.assertIn("action='/s/tok/medsel'", page2)
        self.assertIn("name=prefers_no_labs", page2)
        self.assertIn("name=skip", page2)

    def test_sex_asked_up_front_not_in_options_page(self):
        # Sex is a normal survey field ("About your child"), not on the options page.
        self.assertIn("name=sex", clinic._survey_cards({}))
        self.assertNotIn("name=sex", clinic._medsel_detail_fields({}))

    def test_review_card_empty_without_selection(self):
        self.assertEqual(clinic.med_selection_review_html(None), "")

    def test_current_medication_excluded_from_its_own_replacement(self):
        # Bug fix: a switch/add must never recommend the drug the child is on.
        from engine_core.med_selection import select as sel, load_selection_ruleset
        rs = load_selection_ruleset()
        patient = {"medication": "lamotrigine", "age": 6, "sex": "male"}
        attrs = clinic._selection_attrs_from_form(patient, {}, _form({"medsel_interest": "yes"}))
        self.assertIn("lamotrigine", attrs["prior_tried"])          # current med -> tried
        res = sel(attrs, rs)
        self.assertNotIn("lamotrigine", [o["id"] for o in res.options])

    def test_combination_rule_not_fired_when_already_on_a_medicine(self):
        # Bug fix: the treatment-naive brivaracetam rule must not fire for a
        # patient who is already on a medicine (this flow only runs for them).
        from engine_core.med_selection import select as sel, load_selection_ruleset
        rs = load_selection_ruleset()
        patient = {"medication": "oxcarbazepine", "age": 6, "sex": "male"}
        attrs = clinic._selection_attrs_from_form(patient, {}, _form({}))
        res = sel(attrs, rs)
        self.assertNotIn("brivaracetam", res.avoided)               # combo rule did NOT fire
        self.assertNotIn("oxcarbazepine", [o["id"] for o in res.options])  # current med excluded


class TestInSurveyFlowDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_db, self._old_dir = db.DB_PATH, db.DATA_DIR
        db.DATA_DIR = self.tmp
        db.DB_PATH = os.path.join(self.tmp, "clinic.db")
        db.init_db("test-pw")
        # Isolate from the optional NLP/Mongo audit side-channels.
        self._old_nlp, self._old_log = clinic._run_nlp_audit, clinic._log_recommendation_audit
        clinic._run_nlp_audit = lambda *a, **k: None
        clinic._log_recommendation_audit = lambda *a, **k: None
        # Patient the "female older than 10" rule applies to.
        self.pid = db.add_patient({
            "external_id": "EPIL-FLOW", "age": 13, "weight": 40, "sex": "female",
            "epilepsy_type": "focal", "medication": "levetiracetam",
            "dose_amount": 500, "dose_unit": "mg", "frequency": 2,
            "can_increase": True, "switch_to": "lamotrigine"})
        self.inst = db.create_instance(self.pid)
        self.token = self.inst["token"]

    def tearDown(self):
        db.DB_PATH, db.DATA_DIR = self._old_db, self._old_dir
        clinic._run_nlp_audit, clinic._log_recommendation_audit = self._old_nlp, self._old_log

    def _handler(self):
        h = clinic.Handler.__new__(clinic.Handler)
        self.captured = []
        h._send = lambda body, code=200, set_cookie=None, location=None: \
            self.captured.append({"body": body, "code": code, "location": location})
        return h

    def _latest_sub(self):
        con = sqlite3.connect(db.DB_PATH)
        try:
            r = con.execute("SELECT MAX(id) FROM submissions").fetchone()
            return r[0]
        finally:
            con.close()

    # A single survey submission whose answers make the engine recommend a
    # (non-urgent) add/switch: breakthrough seizure + big side-effect impact at goal.
    def _switch_survey_form(self, **extra):
        d = {
            "csrf": clinic.csrf_for(self.token),
            "q1": "same", "adherence": "almost_never",
            "seizures": "yes", "q4": ["focal"], "freq_focal": "weekly",
            "dur_focal": "under_1min", "unresp_focal": "no", "q6": "about_same",
            "se": "yes", "q9": ["dizziness"], "impact": "big",
            "q10a": "at_goal", "q10c_time": "over_2_weeks", "q11": "worse",
            # in-survey selection card:
            "medsel_interest": "yes", "prior_tried": ["levetiracetam"],
            "mood_difficulties": "yes", "prefers_no_labs": "no",
            "behavioral_difficulties": "no", "trouble_sleeping": "no",
            "migraines": "no", "se_asian_han_chinese": "no",
        }
        d.update(extra)
        return _form(d)

    def _page2(self, **extra):
        d = {"csrf": clinic.csrf_for(self.token), "sub": str(self._latest_sub()),
             "prior_tried": ["levetiracetam"], "mood_difficulties": "yes",
             "prefers_no_labs": "no", "behavioral_difficulties": "no",
             "trouble_sleeping": "no", "migraines": "no", "se_asian_han_chinese": "no"}
        d.update(extra)
        return _form(d)

    def test_switch_add_submit_shows_page2_questions_first(self):
        h = self._handler()
        h._do_submit(self.token, self._switch_survey_form())
        body = self.captured[-1]["body"]
        self.assertIn("A few questions about medicine options", body)
        self.assertNotIn("Your medication options", body)
        self.assertIsNone(db.get_med_selection(self._latest_sub()))   # nothing computed yet

    def test_page2_shows_and_persists_options(self):
        h = self._handler()
        h._do_submit(self.token, self._switch_survey_form())
        h._do_medsel(self.token, self._page2())
        body = self.captured[-1]["body"]
        self.assertIn("Your medication options", body)
        ms = db.get_med_selection(self._latest_sub())
        self.assertIsNotNone(ms)
        ids = [o["id"] for o in ms["engine_result"]["options"]]
        self.assertNotIn("levetiracetam", ids)               # tried + mood-avoided
        self.assertNotIn("valproic_acid", ids)               # female > 10
        self.assertEqual(ids[0], "lamotrigine")              # favored, rank 1
        self.assertTrue(ms["engine_result"]["requires_physician_review"])

    def test_page2_skip_records_nothing(self):
        h = self._handler()
        h._do_submit(self.token, self._switch_survey_form())
        h._do_medsel(self.token, self._page2(skip="1"))
        self.assertIn("Thank you", self.captured[-1]["body"])
        self.assertIsNone(db.get_med_selection(self._latest_sub()))

    def test_page2_rejects_another_links_submission(self):
        h = self._handler()
        h._do_submit(self.token, self._switch_survey_form())
        other = db.create_instance(self.pid)["token"]
        h._do_medsel(other, _form({"csrf": clinic.csrf_for(other), "sub": str(self._latest_sub())}))
        self.assertEqual(self.captured[-1]["code"], 404)

    def test_ranking_after_options_persists(self):
        h = self._handler()
        h._do_submit(self.token, self._switch_survey_form())
        h._do_medsel(self.token, self._page2())
        sub = self._latest_sub()
        ms = db.get_med_selection(sub)
        first_id = ms["engine_result"]["options"][0]["id"]
        third_id = ms["engine_result"]["options"][2]["id"]
        h2 = self._handler()
        h2._do_med_rank(self.token, _form({
            "csrf": clinic.csrf_for(self.token), "sub": str(sub),
            f"rank_{first_id}": "first", f"rank_{third_id}": "avoid"}))
        self.assertIn("Thank you", self.captured[-1]["body"])
        ms2 = db.get_med_selection(sub)
        self.assertEqual(ms2["family_ranking"][first_id], "first")
        self.assertEqual(ms2["family_ranking"][third_id], "avoid")
        card = clinic.med_selection_review_html(ms2)          # doctor view
        self.assertIn("1st choice", card)
        self.assertIn("AVOID", card)

    def test_no_options_when_no_change(self):
        # A quiet check-in -> no_change -> no options page, no med_selection row.
        h = self._handler()
        h._do_submit(self.token, _form({
            "csrf": clinic.csrf_for(self.token), "q1": "same",
            "adherence": "almost_never", "seizures": "no",
            "q10a": "at_goal", "q10c_time": "over_2_weeks", "q11": "about_same"}))
        self.assertIn("Thank you", self.captured[-1]["body"])
        self.assertNotIn("Your medication options", self.captured[-1]["body"])
        self.assertIsNone(db.get_med_selection(self._latest_sub()))

    def test_rank_bad_csrf_rejected(self):
        h = self._handler()
        h._do_med_rank(self.token, _form({"csrf": "bogus", "sub": "1"}))
        self.assertEqual(self.captured[-1]["code"], 400)


if __name__ == "__main__":
    unittest.main()
