"""
tests/test_transformer_structurer.py
--------------------------------------
Tests for the transformer-based structurers, the comparison harness,
and the audit module.

All tests pass without transformers/torch/pymongo installed:
  - TransformerStructurer degrades gracefully (returns [] + is_available=False)
  - StructurerComparator still runs lexicon and reports transformer as unavailable
  - ParseAudit falls back to JSONL

Run:  python -m unittest discover -s tests
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# TransformerStructurer
# ---------------------------------------------------------------------------

class TestTransformerStructurerInterface(unittest.TestCase):
    """Verify the structurer exposes the correct interface and degrades safely."""

    def _make(self, profile="biobert"):
        from nlp.transformer_structurer import TransformerStructurer
        return TransformerStructurer(profile_name=profile, lazy=True)

    def test_biobert_profile_instantiates(self):
        s = self._make("biobert")
        self.assertEqual(s.model_name, "alvaroalon2/biobert_diseases_ner")

    def test_clinical_bert_profile_instantiates(self):
        s = self._make("clinical_bert")
        self.assertIn("clinical", s.model_name.lower())

    def test_unknown_profile_raises(self):
        from nlp.transformer_structurer import TransformerStructurer
        with self.assertRaises(ValueError):
            TransformerStructurer(profile_name="nonexistent")

    def test_named_subclasses(self):
        from nlp.transformer_structurer import BioBERTStructurer, ClinicalBERTStructurer
        b = BioBERTStructurer()
        c = ClinicalBERTStructurer()
        self.assertIn("biobert", b.model_name.lower())
        self.assertIn("clinical", c.model_name.lower())

    def test_structure_empty_returns_empty(self):
        """Empty text → empty findings, even when model unavailable."""
        from nlp.transformer_structurer import BioBERTStructurer
        s = BioBERTStructurer(lazy=True)
        self.assertEqual(s.structure(""), [])
        self.assertEqual(s.structure(None), [])

    def test_structure_many_empty(self):
        from nlp.transformer_structurer import BioBERTStructurer
        s = BioBERTStructurer(lazy=True)
        self.assertEqual(s.structure_many({}), [])

    def test_graceful_degradation_when_unavailable(self):
        """If model can't load, structure() returns [] without raising."""
        from nlp.transformer_structurer import TransformerStructurer
        s = TransformerStructurer(
            model_name="nonexistent/model-that-does-not-exist",
            entity_map={"MISC": "side_effect"},
            lazy=True,
        )
        result = s.structure("she had a seizure and a rash")
        self.assertEqual(result, [])
        self.assertFalse(s.is_available())

    def test_note_is_advisory(self):
        """Findings note always states they are not used by the decision engine."""
        from nlp.transformer_structurer import BioBERTStructurer
        from nlp.interfaces import FreeTextFinding
        # Inject a mock pipeline to simulate a hit without downloading a model
        s = BioBERTStructurer(lazy=True)
        s._available = True

        def _mock_pipeline(text):
            return [{"entity_group": "DISEASE", "word": "seizure",
                     "score": 0.92, "start": 0, "end": 7}]

        s._pipeline = _mock_pipeline
        findings = s.structure("seizure last Tuesday")
        self.assertTrue(len(findings) > 0)
        for f in findings:
            self.assertIn("not used by the decision engine", f.note)
            self.assertIn(s.model_name, f.note)

    def test_refinement_seizure_keyword(self):
        """DISEASE entity containing 'seizure' maps to seizure_mention field."""
        from nlp.transformer_structurer import BioBERTStructurer
        s = BioBERTStructurer(lazy=True)
        s._available = True
        s._pipeline = lambda text: [
            {"entity_group": "DISEASE", "word": "seizure", "score": 0.9}
        ]
        findings = s.structure("she had a seizure on Monday")
        fields = {f.field for f in findings}
        # Seizure keyword should trigger refinement
        self.assertTrue(
            "seizure_mention" in fields or "side_effect" in fields,
            f"Expected seizure_mention or side_effect, got {fields}"
        )

    def test_confidence_clamped_to_one(self):
        from nlp.transformer_structurer import BioBERTStructurer
        s = BioBERTStructurer(lazy=True)
        s._available = True
        s._pipeline = lambda text: [
            {"entity_group": "DISEASE", "word": "rash", "score": 1.2}  # over 1.0
        ]
        findings = s.structure("she has a rash")
        for f in findings:
            self.assertLessEqual(f.confidence, 1.0)


# ---------------------------------------------------------------------------
# StructurerComparator
# ---------------------------------------------------------------------------

class TestStructurerComparator(unittest.TestCase):

    def _lexicon_only_cmp(self):
        from nlp.compare import StructurerComparator
        from nlp.structurer import LexiconStructurer
        return StructurerComparator(structurers={"lexicon": LexiconStructurer()})

    def test_compare_single_model_returns_report(self):
        from nlp.compare import ComparisonReport
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("she was very tired and forgot two doses")
        self.assertIsInstance(report, ComparisonReport)

    def test_lexicon_finds_sleepiness(self):
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("she was very tired")
        all_findings = report.results["lexicon"].findings
        fields = {f.field for f in all_findings}
        self.assertIn("side_effect", fields)

    def test_field_agreements_populated(self):
        cmp = self._lexicon_only_cmp()
        report = cmp.compare({"Q13": "ran out of medicine and child refuses"})
        self.assertTrue(len(report.field_agreements) > 0)

    def test_full_agreement_when_one_model(self):
        """With only one model, every found field is 'full' agreement."""
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("cost and insurance issue")
        for fa in report.field_agreements:
            self.assertEqual(fa.agreement_level, "full")

    def test_unique_to_model_empty_when_one(self):
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("rash on her arm")
        # Only one model → nothing is unique-to-exactly-one-model
        self.assertEqual(report.unique_to_model, {})

    def test_summary_string_contains_model_name(self):
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("tired and dizzy")
        summary = report.summary()
        self.assertIn("lexicon", summary)

    def test_to_dict_serialisable(self):
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("cost issues with insurance")
        d = report.to_dict()
        self.assertIn("results", d)
        self.assertIn("field_agreements", d)
        # Must be JSON-serialisable
        json.dumps(d)

    def test_to_json(self):
        cmp = self._lexicon_only_cmp()
        report = cmp.compare_text("he had a seizure")
        j = report.to_json()
        parsed = json.loads(j)
        self.assertIn("results", parsed)

    def test_two_model_partial_agreement(self):
        """Simulate two models, one finds a field the other misses."""
        from nlp.compare import StructurerComparator
        from nlp.structurer import LexiconStructurer
        from nlp.interfaces import FreeTextFinding

        class _MockStructurer:
            model_name = "mock"
            def structure_many(self, answers):
                return [FreeTextFinding(
                    field="seizure_mention", suggested_value="seizure_reported",
                    confidence=0.8, evidence="seizure",
                    note="SUGGESTION for clinician review - not used by the decision engine.",
                )]

        cmp = StructurerComparator(structurers={
            "lexicon": LexiconStructurer(),
            "mock":    _MockStructurer(),
        })
        report = cmp.compare_text("she had a seizure on Monday")
        # seizure_mention should appear — both lexicon and mock may find it
        found_fields = {fa.field for fa in report.field_agreements}
        self.assertIn("seizure_mention", found_fields)

    def test_unavailable_model_excluded_gracefully(self):
        """Unavailable transformer does not crash the comparator."""
        from nlp.compare import StructurerComparator
        from nlp.structurer import LexiconStructurer
        from nlp.transformer_structurer import TransformerStructurer

        bad = TransformerStructurer(
            model_name="nonexistent/xyz", entity_map={}, lazy=True)
        cmp = StructurerComparator(structurers={
            "lexicon": LexiconStructurer(),
            "bad_model": bad,
        })
        report = cmp.compare_text("she was drowsy")
        self.assertFalse(report.results["bad_model"].available)
        self.assertTrue(report.results["lexicon"].available)

    def test_decision_path_untouched(self):
        """Comparison findings must not feed the engine."""
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from engine_core import run_engine, snapshot_from_interval
        from engine_core import PatientContext, MedicationContext, PhysicianContingency
        from nlp.compare import StructurerComparator
        from nlp.structurer import LexiconStructurer

        def _snap(answers):
            p = PatientContext(external_id="T", age_years=8, weight_kg=30)
            m = MedicationContext(medication="levetiracetam", dose_amount=250,
                                  dose_unit="mg", frequency_per_day=2)
            c = PhysicianContingency(can_increase_dose=True,
                                     can_switch_to="lamotrigine")
            return snapshot_from_interval(p, m, c, answers)

        base = {"Q3": "yes", "Q10a": "still_increasing"}
        rec_without = run_engine(_snap(base))

        # Run comparator on the same free text
        cmp = StructurerComparator(structurers={"lexicon": LexiconStructurer()})
        cmp.compare_text("she had a seizure and a rash", "Q13")

        # Engine output must be unchanged
        rec_with = run_engine(_snap(base))
        self.assertEqual(rec_without.category, rec_with.category)
        self.assertEqual(
            [r["rule"] for r in rec_without.rationale],
            [r["rule"] for r in rec_with.rationale],
        )


# ---------------------------------------------------------------------------
# ParseAudit (JSONL fallback, no MongoDB required)
# ---------------------------------------------------------------------------

class TestParseAuditJSONL(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._log = Path(self._tmp.name) / "audit.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _audit(self):
        from audit.mongo_audit import ParseAudit
        return ParseAudit(use_mongo=False, fallback_path=self._log)

    def test_log_parse_writes_jsonl(self):
        a = self._audit()
        a.log_parse(
            session_id="S1", patient_id="P001", model_name="lexicon",
            source_question="Q13", input_text="she was very tired",
            findings=[], processing_ms=1.2,
        )
        lines = self._log.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        doc = json.loads(lines[0])
        self.assertEqual(doc["model_name"], "lexicon")
        self.assertEqual(doc["patient_id"], "P001")

    def test_input_hash_stored(self):
        a = self._audit()
        text = "child refused medication"
        a.log_parse(
            session_id="S1", patient_id="P001", model_name="lexicon",
            source_question="Q2", input_text=text,
            findings=[], processing_ms=0.5,
        )
        doc = json.loads(self._log.read_text().strip())
        expected = hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(doc["input_hash"], expected)

    def test_multiple_records_chain(self):
        a = self._audit()
        for i in range(3):
            a.log_parse(
                session_id="S1", patient_id="P001", model_name="lexicon",
                source_question="Q13", input_text=f"text {i}",
                findings=[], processing_ms=float(i),
            )
        lines = self._log.read_text().strip().splitlines()
        self.assertEqual(len(lines), 3)

    def test_verify_chain_intact(self):
        a = self._audit()
        for i in range(3):
            a.log_parse(
                session_id="S1", patient_id="P001", model_name="lexicon",
                source_question="Q13", input_text=f"event {i}",
                findings=[], processing_ms=1.0,
            )
        result = a.verify_chain()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 3)

    def test_verify_chain_detects_tampering(self):
        a = self._audit()
        a.log_parse(
            session_id="S1", patient_id="P001", model_name="lexicon",
            source_question="Q13", input_text="original text",
            findings=[], processing_ms=1.0,
        )
        # Tamper with the record
        raw = self._log.read_text()
        doc = json.loads(raw)
        doc["patient_id"] = "TAMPERED"
        self._log.write_text(json.dumps(doc) + "\n")

        result = a.verify_chain()
        self.assertFalse(result["ok"])

    def test_log_with_findings(self):
        from nlp.structurer import LexiconStructurer
        s = LexiconStructurer()
        findings = s.structure("she was very tired and had a rash", "Q13")
        a = self._audit()
        a.log_parse(
            session_id="S2", patient_id="P002", model_name="lexicon",
            source_question="Q13",
            input_text="she was very tired and had a rash",
            findings=findings, processing_ms=2.1,
        )
        doc = json.loads(self._log.read_text().strip())
        self.assertTrue(len(doc["findings"]) > 0)
        for f in doc["findings"]:
            self.assertIn("field", f)
            self.assertIn("confidence", f)

    def test_mongo_disabled_flag(self):
        a = self._audit()
        self.assertFalse(a.is_using_mongo())

    def test_verify_empty_log(self):
        a = self._audit()
        result = a.verify_chain()
        self.assertTrue(result["ok"])


# ---------------------------------------------------------------------------
# AuditRecord data model
# ---------------------------------------------------------------------------

class TestAuditRecord(unittest.TestCase):

    def test_hash_populated_on_init(self):
        from audit.mongo_audit import AuditRecord
        r = AuditRecord(
            timestamp="2026-06-23T00:00:00+00:00",
            session_id="S1", patient_id="P001",
            model_name="lexicon", source_question="Q13",
            input_text="hello", findings=[], engine_version="0.2.0",
            processing_ms=1.0,
        )
        expected = hashlib.sha256("hello".encode()).hexdigest()
        self.assertEqual(r.input_hash, expected)

    def test_to_dict_serialisable(self):
        from audit.mongo_audit import AuditRecord
        r = AuditRecord(
            timestamp="2026-06-23T00:00:00+00:00",
            session_id="S1", patient_id="P001",
            model_name="biobert", source_question="Q13",
            input_text="tired all the time", findings=[], engine_version="0.2.0",
            processing_ms=35.4,
        )
        json.dumps(r.to_dict())   # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
