"""
audit
-----
Evidence and audit trail for NLP parsing events.

ParseAudit  - log every free-text parse call (model, findings, confidence,
              timing, source question) to MongoDB or a local JSONL fallback.
              Used by the comparator and by production parsing calls.

Nothing in this module touches the decision path.
"""

from .mongo_audit import ParseAudit, AuditRecord

__all__ = ["ParseAudit", "AuditRecord"]
