#!/usr/bin/env python3
"""
audit_proofs.py  -  record verification proofs into the audit evidence store
============================================================================

Runs verify_all and writes one hash-chained audit record per rule table (plus
the red-team eval) via audit.ParseAudit.log_verification. Each record carries a
timestamp and the sha256 of the exact file that was proved, so a reviewer can
tie a proof to a specific version of the clinician tables (IRB/PI evidence).

Build-time only; no engine_core import at runtime. No PHI is written - records
hold rule-table metadata (counts, findings, file hashes) only.

USAGE
  python3 verification/audit_proofs.py            # run + append audit records
  python3 verification/audit_proofs.py --verify   # then check hash-chain integrity
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification import verify_all               # noqa: E402
from audit.mongo_audit import ParseAudit          # noqa: E402

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _sha256(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def record_all(audit: ParseAudit) -> list:
    res = verify_all.run_all(write=True)
    tokens = []

    rules_sha = _sha256(os.path.join(_DATA, "asm_rules.json"))
    ladder_sha = _sha256(os.path.join(_DATA, "dose_titration_ladder.json"))

    tokens.append(audit.log_verification(
        target="data/asm_rules.json", target_sha256=rules_sha,
        checker=res["rules"]["checker"], counts=res["rules"]["counts"],
        hard_properties_proved=res["rules"]["hard_properties_proved"],
        findings=[f for f in res["rules"]["findings"] if f["severity"] == "FAIL"]))

    tokens.append(audit.log_verification(
        target="data/dose_titration_ladder.json+titration_thresholds.json",
        target_sha256=ladder_sha, checker="exact-interval",
        counts=res["ladder"]["counts"],
        hard_properties_proved=res["ladder"]["hard_properties_proved"],
        findings=[f for f in res["ladder"]["findings"] if f["severity"] == "FAIL"]))

    t = res["redteam"]
    tokens.append(audit.log_verification(
        target="redflag_safety_net(redteam)", target_sha256="",
        checker="adversarial-eval",
        counts={"FAIL": t["must_catch_misses"], "WARN": t["misses"], "INFO": 0},
        hard_properties_proved=(t["must_catch_misses"] == 0),
        findings=[r for r in t["results"] if r["status"] != "OK"]))

    return tokens


def main() -> int:
    p = argparse.ArgumentParser(description="Record verification proofs to audit log.")
    p.add_argument("--verify", action="store_true", help="check hash-chain after")
    args = p.parse_args()

    audit = ParseAudit(use_mongo=False)   # deterministic JSONL evidence store
    tokens = record_all(audit)
    print("Recorded verification proofs (hash-chain tokens):")
    for tok in tokens:
        print(f"  - {tok}")

    if args.verify:
        integ = audit.verify_chain()
        print(f"audit hash-chain integrity: {integ}")
        return 0 if integ.get("ok") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
