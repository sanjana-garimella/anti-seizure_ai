"""
tests/test_verification_audit.py
--------------------------------
The build-time verification proofs are recorded into the hash-chained audit
evidence store (audit.ParseAudit.log_verification) with a timestamp + the
sha256 of the file that was proved. Tests use an isolated temp log so the real
audit_nlp.jsonl is untouched.
"""
import os
import tempfile
import unittest
from pathlib import Path

from audit.mongo_audit import ParseAudit
from verification import audit_proofs


class TestVerificationAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "audit_test.jsonl"
        self.audit = ParseAudit(use_mongo=False, fallback_path=self.tmp)

    def test_log_verification_writes_record_with_provenance(self):
        tok = self.audit.log_verification(
            target="data/asm_rules.json", target_sha256="abc123",
            checker="exact", counts={"FAIL": 0, "WARN": 2, "INFO": 1},
            hard_properties_proved=True, findings=[])
        self.assertIsNotNone(tok)
        recs = [r for r in self.audit.list_recent(limit=10)
                if r.get("record_type") == "verification"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["target_sha256"], "abc123")
        self.assertTrue(recs[0]["hard_properties_proved"])
        self.assertIn("timestamp", recs[0])

    def test_hash_chain_intact_after_multiple_records(self):
        for i in range(3):
            self.audit.log_verification(
                target=f"t{i}", target_sha256=f"h{i}", checker="exact",
                counts={"FAIL": 0, "WARN": 0, "INFO": 0},
                hard_properties_proved=True)
        self.assertTrue(self.audit.verify_chain().get("ok"))

    def test_record_all_writes_three_proofs(self):
        toks = audit_proofs.record_all(self.audit)
        self.assertEqual(len([t for t in toks if t]), 3)
        recs = [r for r in self.audit.list_recent(limit=20)
                if r.get("record_type") == "verification"]
        targets = {r["target"] for r in recs}
        self.assertTrue(any("asm_rules" in t for t in targets))
        self.assertTrue(any("ladder" in t for t in targets))


if __name__ == "__main__":
    unittest.main()
