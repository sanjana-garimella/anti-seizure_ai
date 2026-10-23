"""
test_review_note_html.py
------------------------
The /review page renders the stored clinical note as laid-out HTML instead of a
monospace block. The renderer re-lays-out the SAME stored text and must never
add, drop or alter a clinical value - these tests pin that, plus escaping of
patient free text and the fallback for notes that don't match the grammar.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ASM_DB", os.path.join(tempfile.mkdtemp(prefix="notehtml_"), "clinic.db"))

import clinic  # noqa: E402

NOTE = "\n".join([
    "BETWEEN-VISIT SUMMARY",
    "Patient Ivy (demo) · 8 yr · 26 kg · Medication: Valproic Acid",
    "",
    "1. MEDICATION AND DOSE",
    "  - Current dose: 500 mg/day (19.2 mg/kg/day)",
    "  - Typical range: 780–1248 mg/day (30–48 mg/kg/day)",
    "      Headroom: still below the usual effective max (~1248 mg/day).",
    "",
    "2. SEIZURES",
    "  - Breakthrough seizures (family-reported). Urgency tier: URGENT",
    "  - By type:",
    "      1) focal motor seizures, lasting about 1 to 3 minutes",
    "",
    "5. RECOMMENDATION",
    "  - Suggested step: Increase the dose",
    "  - Why this is suggested:",
    "      - Breakthrough; action from the breakthrough table. DRAFT.",
    "",
    "6. SAFETY",
    "  ! An urgent safety concern was flagged.",
    "  - Possible serious skin reaction (SJS/TEN), urgent",
    "",
    "Dose range and maximum are DRAFT, unverified values.",
    "",
    "No recommendation is final until the reviewing physician approves it.",
])


class TestClinicalNoteHtml(unittest.TestCase):
    def setUp(self):
        self.h = clinic._clinical_note_html(NOTE)

    def test_sections_render_in_the_stored_order(self):
        titles = ["MEDICATION AND DOSE", "SEIZURES", "RECOMMENDATION", "SAFETY"]
        seen = [t for t in titles if f"<h3>{t}</h3>" in self.h]
        self.assertEqual(seen, titles)
        self.assertEqual([self.h.index(f"<h3>{t}</h3>") for t in titles],
                         sorted(self.h.index(f"<h3>{t}</h3>") for t in titles))

    def test_fields_become_label_value_rows(self):
        self.assertIn("<span class=k>Current dose</span>", self.h)
        self.assertIn("<span class=v>500 mg/day (19.2 mg/kg/day)</span>", self.h)

    def test_no_clinical_value_is_lost(self):
        # Every dose figure in the stored text survives into the rendering.
        for value in ("500 mg/day", "780–1248 mg/day", "30–48 mg/kg/day", "~1248 mg/day"):
            self.assertIn(value, self.h, f"{value} missing from the rendered note")

    def test_safety_section_and_alert_are_marked(self):
        self.assertIn("note-sec danger", self.h)
        self.assertIn("<p class=note-alert>An urgent safety concern was flagged.</p>", self.h)
        # A flag listed in Safety must not read like an ordinary observation.
        self.assertIn("<p class='note-stmt danger'>Possible serious skin reaction (SJS/TEN), urgent</p>",
                      self.h)

    def test_all_three_urgency_tiers_get_the_right_pill(self):
        # Vocabulary is non_urgent | urgent | emergent, written hyphenated. The
        # underscore form once broke the badge (captured "NON", left "-URGENT").
        for tier, cls in (("EMERGENT", "urgent"), ("URGENT", "review"),
                          ("NON-URGENT", "routine")):
            h = clinic._clinical_note_html(
                NOTE.replace("Urgency tier: URGENT", f"Urgency tier: {tier}"))
            self.assertIn(f"<span class='pill {cls}'>{tier.lower()}</span>", h)
            self.assertNotIn(f"Urgency tier: {tier}", h.split("<details")[0])

    def test_note_writes_the_tier_hyphenated_not_as_a_raw_token(self):
        from engine_core.outputs import build_clinical_summary  # noqa: F401
        self.assertNotIn("NON_URGENT", clinic._clinical_note_html(
            NOTE.replace("URGENT", "NON-URGENT")))

    def test_unknown_tier_is_left_as_plain_text(self):
        h = clinic._clinical_note_html(NOTE.replace("Urgency tier: URGENT",
                                                    "Urgency tier: WHATEVER"))
        self.assertIn("Urgency tier: WHATEVER", h)
        self.assertNotIn("pill WHATEVER", h)

    def test_trailing_colon_bullet_is_a_subheading_not_a_fact(self):
        self.assertIn("<p class=note-lbl>By type</p>", self.h)
        self.assertIn("<p class=note-lbl>Why this is suggested</p>", self.h)

    def test_plain_text_is_kept_verbatim_for_the_chart(self):
        # The physician pastes the note into the EHR, so the exact text must stay
        # available (the review notice included, even though the laid-out view
        # drops it because the decision card already states the caution).
        self.assertIn("Plain text for the chart", self.h)
        self.assertIn("No recommendation is final", self.h)

    def test_review_notice_is_not_repeated_in_the_laid_out_view(self):
        before_details = self.h.split("<details")[0]
        self.assertNotIn("No recommendation is final", before_details)

    def test_patient_free_text_is_escaped(self):
        evil = NOTE + '\n  - In their words: "<script>alert(1)</script> & <b>x</b>"'
        h = clinic._clinical_note_html(evil)
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("&amp;", h)

    def test_unrecognized_note_falls_back_to_plain_text(self):
        # An older stored note (or a physician-edited one) must still display.
        old = "Between-Visit Summary\nPatient X\n\nWhat the family reported\n  • Current dose: 5 mg"
        h = clinic._clinical_note_html(old)
        self.assertTrue(h.startswith("<pre>"))
        self.assertIn("Current dose: 5 mg", h)

    def test_empty_note_does_not_crash(self):
        for value in (None, ""):
            self.assertEqual(clinic._clinical_note_html(value), "<pre></pre>")


if __name__ == "__main__":
    unittest.main()
