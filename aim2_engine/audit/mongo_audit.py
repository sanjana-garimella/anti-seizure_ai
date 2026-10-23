"""
audit/mongo_audit.py
---------------------
Evidence and audit trail for every NLP free-text parse event.

Design
------
ParseAudit logs each parse call to MongoDB when available, and falls back
transparently to a newline-delimited JSON file (audit_log.jsonl in the
clinic_data folder) when MongoDB is not running or not configured.

Every record carries:
  * timestamp (ISO-8601 UTC)
  * session_id    — ties records from a single check-in together
  * patient_id    — simulated ID only; no PHI permitted here
  * model_name    — which structurer produced the findings
  * source_question — which question the text came from
  * input_text    — full text in simulated phase;
                    MUST be replaced with a SHA-256 hash before any
                    real EHR data is used (marked with PHI_PHASE_TODO)
  * findings      — list of FreeTextFinding dicts (advisory only)
  * engine_version
  * processing_ms

MongoDB schema
--------------
Collections:
  parse_events    — one doc per model-call (use to audit individual model runs)
  comparisons     — one doc per multi-model ComparisonReport
  confirmations   — clinician accept/reject of a suggested field (future)

Connection
----------
Set MONGO_URI environment variable, e.g.:
  export MONGO_URI="mongodb://localhost:27017"
Database name defaults to "aim2_audit"; override with MONGO_DB env var.

If MONGO_URI is unset or connection fails, audit silently writes to JSONL.
PHI note: all data here is SIMULATED. When real EHR data is used, input_text
must be replaced with a hash (see PHI_PHASE_TODO comment below).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_ENGINE_ROOT = _HERE.parent
_DATA_DIR = _ENGINE_ROOT / "clinic_data"
_FALLBACK_LOG = _DATA_DIR / "audit_nlp.jsonl"

# Default MongoDB settings (override with env)
_MONGO_URI = os.environ.get("MONGO_URI", "")
_MONGO_DB  = os.environ.get("MONGO_DB", "aim2_audit")

# Attempt to import engine version (best-effort; won't crash if absent)
try:
    sys.path.insert(0, str(_ENGINE_ROOT))
    from engine_core.version import ENGINE_VERSION
except Exception:                  # noqa: BLE001
    ENGINE_VERSION = "unknown"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AuditRecord:
    """One NLP parse event — what the model saw and what it suggested."""
    timestamp: str                   # ISO-8601 UTC
    session_id: str
    patient_id: str                  # simulated ID only
    model_name: str
    source_question: Optional[str]
    input_text: str                  # PHI_PHASE_TODO: replace with sha256 hash
    findings: List[Dict[str, Any]]  # serialised FreeTextFinding objects
    engine_version: str
    processing_ms: float
    record_type: str = "parse_event"
    # SHA-256 of input_text — always stored so we can verify text hasn't changed
    input_hash: str = ""

    def __post_init__(self):
        if not self.input_hash:
            self.input_hash = hashlib.sha256(
                self.input_text.encode("utf-8", errors="replace")
            ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ComparisonAuditRecord:
    """One multi-model comparison run."""
    timestamp: str
    session_id: str
    patient_id: str
    inputs_hash: str                 # SHA-256 of JSON-serialised inputs
    model_names: List[str]
    agreement_summary: Dict[str, str]   # field → agreement_level
    unique_to_model: Dict[str, int]     # model_name → count of unique findings
    engine_version: str
    record_type: str = "comparison"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# ParseAudit
# ---------------------------------------------------------------------------

class ParseAudit:
    """
    Audit logger for NLP parse events.

    Usage:
        audit = ParseAudit()                       # auto-detects MongoDB
        audit = ParseAudit(mongo_uri="mongodb://localhost:27017")
        audit = ParseAudit(use_mongo=False)        # force JSONL fallback

    All writes are best-effort: a failure logs a warning but never raises,
    so the application keeps running even if the audit store is unavailable.
    """

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        db_name: Optional[str] = None,
        use_mongo: Optional[bool] = None,   # None = auto-detect
        fallback_path: Optional[Path] = None,
    ):
        self._fallback_path = fallback_path or _FALLBACK_LOG
        self._mongo_client = None
        self._db = None
        self._using_mongo = False

        if use_mongo is False:
            log.debug("ParseAudit: MongoDB disabled; using JSONL fallback.")
            return

        uri = mongo_uri or _MONGO_URI
        if not uri:
            log.debug("ParseAudit: MONGO_URI not set; using JSONL fallback.")
            return

        self._connect(uri, db_name or _MONGO_DB)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_parse(
        self,
        *,
        session_id: str,
        patient_id: str,
        model_name: str,
        source_question: Optional[str],
        input_text: str,
        findings: List[Any],
        processing_ms: float,
    ) -> Optional[str]:
        """
        Log a single parse event.

        findings: list of FreeTextFinding objects (or dicts).
        input_text: the text to persist. Callers should pass a redacted
            placeholder (e.g. "[redacted sha256:... len:N]") when raw free-text
            logging is disabled, so no caregiver prose / PHI reaches the store
            (clinic.py gates this behind ASM_LOG_FREETEXT; see S8). input_hash on
            the stored record is the hash of whatever is passed here.
        Returns the inserted document ID (str) or None on failure.
        """
        record = AuditRecord(
            timestamp=_now_iso(),
            session_id=session_id,
            patient_id=patient_id,
            model_name=model_name,
            source_question=source_question,
            input_text=input_text,      # PHI_PHASE_TODO: sha256 hash only
            findings=_serialise_findings(findings),
            engine_version=ENGINE_VERSION,
            processing_ms=round(processing_ms, 2),
        )
        return self._write("parse_events", record.to_dict())

    def log_comparison(
        self,
        *,
        session_id: str,
        patient_id: str,
        comparison_report: Any,
    ) -> Optional[str]:
        """
        Log a ComparisonReport (from nlp.compare).
        """
        from nlp.compare import ComparisonReport  # type: ignore[import]
        if not isinstance(comparison_report, ComparisonReport):
            log.warning("log_comparison: expected ComparisonReport, got %s",
                        type(comparison_report))
            return None

        inputs_hash = hashlib.sha256(
            json.dumps(comparison_report.inputs, sort_keys=True).encode()
        ).hexdigest()

        record = ComparisonAuditRecord(
            timestamp=_now_iso(),
            session_id=session_id,
            patient_id=patient_id,
            inputs_hash=inputs_hash,
            model_names=list(comparison_report.results.keys()),
            agreement_summary={
                fa.field: fa.agreement_level
                for fa in comparison_report.field_agreements
            },
            unique_to_model={
                name: len(findings)
                for name, findings in comparison_report.unique_to_model.items()
            },
            engine_version=ENGINE_VERSION,
        )
        return self._write("comparisons", record.to_dict())

    def log_recommendation(
        self,
        *,
        session_id: str,
        patient_id: str,
        patient_name: str = "",
        proposed_drug: str,
        current_dose_mg: Optional[float] = None,
        recommendation_category: str,
        safety_flag: bool,
        rationale: List[str],
        titration_schedule: Optional[Any] = None,
        engine_version: str = ENGINE_VERSION,
    ) -> Optional[str]:
        """
        Log a full engine recommendation run to MongoDB for traceability.

        Pattern from: Chen 2024 · drug_safety_and_recommendation
        (github.com/eemilycchen/drug_safety_and_recommendation)
        mongo_queries.log_safety_check() — stores inputs + outputs + versions
        so every recommendation is reproducible and searchable by patient.
        """
        doc = {
            "record_type": "recommendation",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "patient_id": patient_id,
            "patient_name": patient_name,   # stored for search; no PHI in simulated phase
            "inputs": {
                "proposed_drug": proposed_drug,
                "current_dose_mg": current_dose_mg,
            },
            "outputs": {
                "recommendation_category": recommendation_category,
                "safety_flag": safety_flag,
                "rationale": rationale,
                "titration_schedule": titration_schedule,
            },
            "engine_version": engine_version,
        }
        return self._write("recommendations", doc)

    def log_verification(
        self,
        *,
        target: str,
        target_sha256: str,
        checker: str,
        counts: Dict[str, int],
        hard_properties_proved: bool,
        findings: Optional[List[Any]] = None,
        engine_version: str = ENGINE_VERSION,
    ) -> Optional[str]:
        """Record a BUILD-TIME machine-verification run (verify_all / verify_*)
        into the same hash-chained evidence store, so every proof is
        provenance-tracked (timestamp + the sha256 of the exact rule file
        checked) for IRB/PI review. Contains NO PHI - only rule-table metadata.
        """
        doc = {
            "record_type": "verification",
            "timestamp": _now_iso(),
            "target": target,                    # e.g. "data/asm_rules.json"
            "target_sha256": target_sha256,      # hash of the file that was proved
            "checker": checker,                  # e.g. "z3 (SMT)" / exact
            "counts": counts,                    # {FAIL, WARN, INFO}
            "hard_properties_proved": hard_properties_proved,
            "findings": _serialise_findings(findings or []),
            "engine_version": engine_version,
        }
        return self._write("verifications", doc)

    def search_by_patient(
        self,
        patient_id: str = "",
        patient_name: str = "",
        collection: str = "recommendations",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Return audit records matching patient_id or patient_name (case-insensitive).
        Pattern from: Chen 2024 · drug_safety_and_recommendation
        (search_safety_checks_by_patient — MongoDB $regex query on patient_name)
        """
        if self._using_mongo:
            try:
                query: Dict[str, Any] = {}
                if patient_id:
                    query["patient_id"] = patient_id
                elif patient_name:
                    query["patient_name"] = {"$regex": patient_name, "$options": "i"}
                docs = list(
                    self._db[collection]        # type: ignore[index]
                    .find(query, {"_id": 0})
                    .sort("timestamp", -1)
                    .limit(limit)
                )
                return docs
            except Exception as exc:            # noqa: BLE001
                log.warning("ParseAudit.search_by_patient error: %s", exc)

        # JSONL fallback — linear scan
        records = self.list_recent(limit=10000, collection=collection)
        out = []
        for r in records:
            if patient_id and r.get("patient_id") == patient_id:
                out.append(r)
            elif patient_name and patient_name.lower() in str(
                    r.get("patient_name", "")).lower():
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def log_clinician_confirmation(
        self,
        *,
        session_id: str,
        patient_id: str,
        field: str,
        suggested_value: str,
        confirmed: bool,
        corrected_value: Optional[str] = None,
        model_name: str = "",
    ) -> Optional[str]:
        """
        Log a clinician's accept/reject/correction of a suggested field.
        (Placeholder for future UI integration.)
        """
        doc = {
            "record_type": "confirmation",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "patient_id": patient_id,
            "field": field,
            "suggested_value": suggested_value,
            "confirmed": confirmed,
            "corrected_value": corrected_value,
            "model_name": model_name,
            "engine_version": ENGINE_VERSION,
        }
        return self._write("confirmations", doc)

    def is_using_mongo(self) -> bool:
        return self._using_mongo

    def list_recent(self, limit: int = 100,
                    collection: str = "parse_events") -> List[Dict[str, Any]]:
        """Return up to `limit` recent audit records, newest first."""
        if self._using_mongo:
            try:
                docs = list(
                    self._db[collection]                    # type: ignore[index]
                    .find({}, {"_id": 0})
                    .sort("timestamp", -1)
                    .limit(limit)
                )
                return docs
            except Exception as exc:                        # noqa: BLE001
                log.warning("ParseAudit.list_recent MongoDB error: %s", exc)
                # fall through to JSONL

        # JSONL fallback — read last `limit` lines
        if not self._fallback_path.exists():
            return []
        try:
            with self._fallback_path.open("r", encoding="utf-8") as fh:
                lines = [l.strip() for l in fh if l.strip()]
            recent = lines[-limit:]
            recent.reverse()
            out = []
            for raw in recent:
                try:
                    doc = json.loads(raw)
                    doc.pop("_hash", None)
                    doc.pop("_prev_hash", None)
                    out.append(doc)
                except Exception:                           # noqa: BLE001
                    pass
            return out
        except Exception as exc:                            # noqa: BLE001
            log.warning("ParseAudit.list_recent JSONL error: %s", exc)
            return []

    def verify_chain(self) -> Dict[str, Any]:
        """
        Verify JSONL audit log integrity (hash chain check).
        Returns {"ok": True, "count": N} or {"ok": False, "broken_at": N}.
        Only meaningful for the JSONL fallback.
        """
        if self._using_mongo:
            return {"ok": True, "note": "MongoDB — chain verification not applicable"}
        return _verify_jsonl(self._fallback_path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _connect(self, uri: str, db_name: str) -> None:
        try:
            from pymongo import MongoClient              # type: ignore[import]
            from pymongo.errors import ConnectionFailure
            client = MongoClient(uri, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")                # raises on failure
            self._mongo_client = client
            self._db = client[db_name]
            # Ensure indexes
            self._db["parse_events"].create_index(
                [("session_id", 1), ("timestamp", -1)])
            self._db["comparisons"].create_index(
                [("session_id", 1), ("timestamp", -1)])
            self._using_mongo = True
            log.info("ParseAudit: connected to MongoDB at %s (db=%s)", uri, db_name)
        except ImportError:
            log.warning("ParseAudit: pymongo not installed; using JSONL fallback. "
                        "Install with: pip install pymongo --break-system-packages")
        except Exception as exc:                        # noqa: BLE001
            log.warning("ParseAudit: MongoDB connection failed (%s); "
                        "using JSONL fallback.", exc)

    def _write(self, collection: str, doc: Dict[str, Any]) -> Optional[str]:
        """Write doc to MongoDB or append to JSONL fallback."""
        if self._using_mongo:
            try:
                result = self._db[collection].insert_one(doc)  # type: ignore[index]
                return str(result.inserted_id)
            except Exception as exc:                    # noqa: BLE001
                log.warning("ParseAudit: MongoDB write failed (%s); "
                            "falling back to JSONL.", exc)
                # Fall through to JSONL

        return self._write_jsonl(doc)

    def _write_jsonl(self, doc: Dict[str, Any]) -> Optional[str]:
        """Append one JSON line to the fallback log with a hash chain."""
        try:
            self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
            # Read last line hash for chaining
            prev_hash = _last_line_hash(self._fallback_path)
            doc["_prev_hash"] = prev_hash
            line = json.dumps(doc, ensure_ascii=False)
            doc["_hash"] = hashlib.sha256(
                (prev_hash + line).encode()
            ).hexdigest()
            final_line = json.dumps(doc, ensure_ascii=False)
            with self._fallback_path.open("a", encoding="utf-8") as fh:
                fh.write(final_line + "\n")
            return doc["_hash"][:12]    # short token as pseudo-ID
        except Exception as exc:        # noqa: BLE001
            log.error("ParseAudit: JSONL write failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _serialise_findings(findings: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for f in findings:
        if isinstance(f, dict):
            out.append(f)
        elif hasattr(f, "__dataclass_fields__"):
            from dataclasses import asdict as _asdict
            out.append(_asdict(f))
        else:
            out.append({"raw": str(f)})
    return out


def _last_line_hash(path: Path) -> str:
    """Return the _hash of the last JSONL line, or empty string."""
    if not path.exists():
        return ""
    try:
        with path.open("rb") as fh:
            # Seek to last non-empty line
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return ""
            fh.seek(max(size - 4096, 0))
            lines = fh.read().splitlines()
            for line in reversed(lines):
                if line.strip():
                    doc = json.loads(line)
                    return doc.get("_hash", "")
    except Exception:               # noqa: BLE001
        pass
    return ""


def _verify_jsonl(path: Path) -> Dict[str, Any]:
    """Walk the JSONL hash chain and return integrity status."""
    if not path.exists():
        return {"ok": True, "count": 0, "note": "no log file yet"}
    count = 0
    prev_hash = ""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for i, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                doc = json.loads(raw)
                stored_hash = doc.pop("_hash", "")
                # Keep _prev_hash in doc — it was part of the line when hashed
                recorded_prev = doc.get("_prev_hash", "")
                if recorded_prev != prev_hash:
                    return {"ok": False, "broken_at": i,
                            "reason": "prev_hash mismatch"}
                recomputed = hashlib.sha256(
                    (prev_hash + json.dumps(doc, ensure_ascii=False)).encode()
                ).hexdigest()
                if recomputed != stored_hash:
                    return {"ok": False, "broken_at": i,
                            "reason": "hash mismatch (record may be altered)"}
                prev_hash = stored_hash
                count += 1
    except Exception as exc:        # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "count": count}
