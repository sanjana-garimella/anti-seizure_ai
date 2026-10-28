"""
clinic_store.py
---------------
Local, self-contained storage for the role-based app (doctor + patient).
Python standard library ONLY - uses sqlite3, so everything lives in a single
local file inside this folder and NOTHING leaves the machine. Includes an
audit log of every action and PBKDF2-hashed doctor credentials.

This is the persistence layer the stateless engine intentionally does not have
(see ../aim2_engine/COMPLIANCE.md). Synthetic data only until IRB/security
sign-off; the DB file is not encrypted at rest by this module (rely on the
OS/disk encryption in your security plan).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any, Iterator

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "clinic_data")
DB_PATH = os.environ.get("ASM_DB", os.path.join(DATA_DIR, "clinic.db"))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """`with _conn() as c:` commits on success, rolls back on error, and closes.
    A bare sqlite3.Connection used as a context manager only commits; the handle
    stayed open until garbage collection (ResourceWarning under -W error)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        with c:
            yield c
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, role TEXT, username TEXT UNIQUE,
  pw_salt TEXT, pw_hash TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS patients(
  id INTEGER PRIMARY KEY, external_id TEXT, age REAL, weight REAL, sex TEXT,
  epilepsy_type TEXT, medication TEXT, dose_amount REAL, dose_unit TEXT,
  frequency INTEGER, concentration REAL, can_increase INTEGER, switch_to TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS instances(
  id INTEGER PRIMARY KEY, patient_id INTEGER, token TEXT UNIQUE,
  status TEXT, created_at TEXT, submitted_at TEXT);
CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY, instance_id INTEGER, patient_id INTEGER,
  answers_json TEXT, priority TEXT, flags_json TEXT, created_at TEXT,
  -- The RESOLVED MedicationContext the engine actually ran on, as submitted.
  -- The family confirms their real dose/strength in Q1a and those fields live
  -- only in the raw POST form; without this column a later re-run falls back to
  -- the doctor's intake record and silently changes a clinical input (2026-08-04:
  -- one clobazam row could not be regenerated for exactly this reason). Stored so
  -- a submission can be re-run losslessly.
  med_context_json TEXT);
CREATE TABLE IF NOT EXISTS recommendations(
  id INTEGER PRIMARY KEY, submission_id INTEGER, category TEXT,
  safety_flag INTEGER, recognized INTEGER, urgent_flags_json TEXT,
  rationale_json TEXT, clinical_summary TEXT, titration_json TEXT,
  draft_message TEXT, switch_target TEXT, engine_version TEXT, created_at TEXT,
  sources_json TEXT,
  -- Triage signals the review queue classifies on, stored as columns so the
  -- queue never has to parse them back out of the note text:
  -- breakthrough urgency tier (non_urgent | urgent | emergent), whether the
  -- reported dose is above the recommended maximum, and the engine's one-line
  -- "why" (shown on the queue card so a clinician sees what the check-in needs
  -- without opening it).
  urgency_tier TEXT, dose_exceeds_max INTEGER, reason_summary TEXT);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, recommendation_id INTEGER, decision TEXT,
  physician_note TEXT, final_message TEXT, reviewed_by TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS med_selections(
  id INTEGER PRIMARY KEY, submission_id INTEGER, patient_id INTEGER,
  questions_json TEXT, engine_result_json TEXT, family_ranking_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, detail TEXT,
  prev_hash TEXT, entry_hash TEXT);
CREATE TABLE IF NOT EXISTS gold_labels(
  case_id TEXT PRIMARY KEY, label TEXT, updated_at TEXT);
"""


# ---------------------------------------------------------------------------
# Auth helpers (PBKDF2)
# ---------------------------------------------------------------------------

def hash_pw(pw: str, salt: Optional[str] = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return salt, h


def verify_pw(pw: str, salt: str, expected: str) -> bool:
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return hmac.compare_digest(h, expected)


def init_db(default_doctor_pw: Optional[str] = None) -> Optional[str]:
    """Create tables and seed a doctor account if none exists.
    Returns the seeded password if a new doctor was created, else None."""
    with _conn() as c:
        c.executescript(SCHEMA)
        try:  # migrate older DBs that predate the dose-strength column
            c.execute("ALTER TABLE patients ADD COLUMN concentration REAL")
        except sqlite3.OperationalError:
            pass
        try:  # migrate older DBs that predate the sex column (medication-selection flow)
            c.execute("ALTER TABLE patients ADD COLUMN sex TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # migrate older DBs that predate the decision-provenance column
            c.execute("ALTER TABLE recommendations ADD COLUMN sources_json TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # migrate older DBs that predate the override-category column
            c.execute("ALTER TABLE reviews ADD COLUMN override_category TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # migrate older DBs that predate the edited-clinical-note column
            c.execute("ALTER TABLE reviews ADD COLUMN final_note TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # migrate older DBs that predate the as-submitted medication context
            c.execute("ALTER TABLE submissions ADD COLUMN med_context_json TEXT")
        except sqlite3.OperationalError:
            pass
        for _col, _type in (("urgency_tier", "TEXT"), ("dose_exceeds_max", "INTEGER"),
                            ("reason_summary", "TEXT")):
            try:  # migrate older DBs that predate the queue triage columns
                c.execute(f"ALTER TABLE recommendations ADD COLUMN {_col} {_type}")
            except sqlite3.OperationalError:
                pass
        row = c.execute("SELECT COUNT(*) n FROM users WHERE role='doctor'").fetchone()
        if row["n"] == 0:
            # No known default: use the env password if set, else generate a
            # strong random one (printed once at startup) - never ship a default.
            pw = default_doctor_pw or os.environ.get("ASM_DOCTOR_PASSWORD") or secrets.token_urlsafe(12)
            salt, h = hash_pw(pw)
            c.execute("INSERT INTO users(role,username,pw_salt,pw_hash,created_at) "
                      "VALUES('doctor','doctor',?,?,?)", (salt, h, _now()))
            return pw
    return None


def get_user(username: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(r) if r else None


def update_password(username: str, new_pw: str):
    salt, h = hash_pw(new_pw)
    with _conn() as c:
        c.execute("UPDATE users SET pw_salt=?, pw_hash=? WHERE username=?", (salt, h, username))


# ---------------------------------------------------------------------------
# Patients & survey instances
# ---------------------------------------------------------------------------

def add_patient(d: Dict[str, Any]) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO patients(external_id,age,weight,sex,epilepsy_type,medication,"
            "dose_amount,dose_unit,frequency,concentration,can_increase,switch_to,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.get("external_id"), d.get("age"), d.get("weight"), d.get("sex") or None,
             d.get("epilepsy_type"),
             d.get("medication"), d.get("dose_amount"), d.get("dose_unit"),
             d.get("frequency"), d.get("concentration"), 1 if d.get("can_increase") else 0,
             d.get("switch_to") or None, _now()))
        return cur.lastrowid


def list_patients() -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM patients ORDER BY id DESC")]


def get_patient(pid: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def get_patient_by_external_id(external_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM patients WHERE external_id=? ORDER BY id DESC LIMIT 1",
                      (external_id,)).fetchone()
        return dict(r) if r else None


def create_instance(patient_id: int) -> Dict[str, Any]:
    token = secrets.token_urlsafe(16)
    with _conn() as c:
        cur = c.execute("INSERT INTO instances(patient_id,token,status,created_at) "
                        "VALUES(?,?,?,?)", (patient_id, token, "pending", _now()))
        return {"id": cur.lastrowid, "token": token, "patient_id": patient_id}


def get_instance_by_token(token: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM instances WHERE token=?", (token,)).fetchone()
        return dict(r) if r else None


def latest_pending_token(patient_id: int) -> Optional[str]:
    """Token of this patient's most recent un-submitted survey, if any."""
    with _conn() as c:
        r = c.execute("SELECT token FROM instances WHERE patient_id=? AND status='pending' "
                      "ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
        return r["token"] if r else None


def mark_submitted(instance_id: int):
    with _conn() as c:
        c.execute("UPDATE instances SET status='submitted', submitted_at=? WHERE id=?",
                  (_now(), instance_id))


def mark_reviewed(instance_id: int):
    with _conn() as c:
        c.execute("UPDATE instances SET status='reviewed' WHERE id=?", (instance_id,))


# ---------------------------------------------------------------------------
# Submissions + recommendations + reviews
# ---------------------------------------------------------------------------

def add_submission(instance_id: int, patient_id: int, answers: Dict[str, Any],
                   priority: str, flags: List[str],
                   med_context: Optional[Dict[str, Any]] = None) -> int:
    """Record a family submission. `med_context` is the RESOLVED medication context
    the engine ran on (see the submissions schema comment): pass it so the
    submission can be re-run losslessly later, without falling back to the doctor's
    intake dose and quietly changing a clinical input."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO submissions(instance_id,patient_id,answers_json,priority,flags_json,"
            "created_at,med_context_json) VALUES(?,?,?,?,?,?,?)",
            (instance_id, patient_id, json.dumps(answers), priority, json.dumps(flags), _now(),
             json.dumps(med_context) if med_context else None))
        return cur.lastrowid


def add_recommendation(submission_id: int, rec: Dict[str, Any]) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO recommendations(submission_id,category,safety_flag,recognized,"
            "urgent_flags_json,rationale_json,clinical_summary,titration_json,draft_message,"
            "switch_target,engine_version,created_at,sources_json,urgency_tier,"
            "dose_exceeds_max,reason_summary) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (submission_id, rec.get("category"), 1 if rec.get("safety_concern_flag") else 0,
             1 if rec.get("medication_recognized") else 0,
             json.dumps(rec.get("urgent_flags", [])), json.dumps(rec.get("rationale", [])),
             rec.get("clinical_summary"), json.dumps(rec.get("titration_schedule")),
             rec.get("draft_patient_message"), rec.get("switch_target"),
             rec.get("engine_version"), _now(), json.dumps(rec.get("sources")),
             rec.get("urgency_tier"), 1 if rec.get("dose_exceeds_max") else 0,
             rec.get("reason_summary")))
        return cur.lastrowid


def add_review(recommendation_id: int, decision: str, note: str,
               final_message: Optional[str], reviewed_by: str,
               override_category: Optional[str] = None, final_note: Optional[str] = None):
    with _conn() as c:
        c.execute("INSERT INTO reviews(recommendation_id,decision,physician_note,"
                  "final_message,reviewed_by,reviewed_at,override_category,final_note) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (recommendation_id, decision, note, final_message, reviewed_by, _now(),
                   override_category, final_note))


# ---------------------------------------------------------------------------
# Medication-SELECTION (switch / add-a-second-medicine flow). Stored separately
# from recommendations: this is the drug-preference ranking layer, a DRAFT for
# physician review. Rule-based (engine_core.med_selection); no clinical value
# is fabricated here.
# ---------------------------------------------------------------------------

def save_med_selection(submission_id: int, patient_id: int,
                       questions: Dict[str, Any], engine_result: Dict[str, Any]) -> int:
    """Persist the selection questions the family answered + the rule-based
    top-3 engine output for one submission (idempotent: replaces any prior row
    for that submission)."""
    with _conn() as c:
        c.execute("DELETE FROM med_selections WHERE submission_id=?", (submission_id,))
        cur = c.execute(
            "INSERT INTO med_selections(submission_id,patient_id,questions_json,"
            "engine_result_json,family_ranking_json,created_at) VALUES(?,?,?,?,?,?)",
            (submission_id, patient_id, json.dumps(questions),
             json.dumps(engine_result), None, _now()))
        return cur.lastrowid


def save_med_ranking(submission_id: int, ranking: Dict[str, Any]) -> None:
    """Store the family's expressed 1st/2nd/3rd/avoid ranking for the shown
    options. Left as a DRAFT preference for the neurologist to review."""
    with _conn() as c:
        c.execute("UPDATE med_selections SET family_ranking_json=? WHERE submission_id=?",
                  (json.dumps(ranking), submission_id))


def get_med_selection(submission_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        r = c.execute("SELECT * FROM med_selections WHERE submission_id=? "
                      "ORDER BY id DESC LIMIT 1", (submission_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["questions"] = json.loads(d.get("questions_json") or "{}")
    d["engine_result"] = json.loads(d.get("engine_result_json") or "{}")
    d["family_ranking"] = json.loads(d.get("family_ranking_json") or "null")
    return d


_PRIORITY_RANK = {"urgent": 0, "review": 1, "routine": 2}
#: Breakthrough urgency tier, most severe first. Secondary sort inside a priority
#: band; rows with no breakthrough (tier None) fall to the end of their band.
_TIER_RANK = {"emergent": 0, "urgent": 1, "non_urgent": 2}


def review_queue() -> List[Dict[str, Any]]:
    """Submissions joined with patient, recommendation, and review status.
    Urgent first, then newest."""
    with _conn() as c:
        rows = c.execute("""
            SELECT s.id sub_id, s.priority, s.flags_json, s.created_at,
                   p.external_id, p.medication, p.age, p.weight,
                   p.dose_amount, p.dose_unit, p.frequency, p.concentration,
                   r.id rec_id, r.category, r.safety_flag, r.recognized,
                   r.urgency_tier, r.dose_exceeds_max, r.urgent_flags_json,
                   r.reason_summary,
                   (SELECT decision FROM reviews v WHERE v.recommendation_id=r.id
                    ORDER BY v.id DESC LIMIT 1) AS decision
            FROM submissions s
            JOIN patients p ON p.id=s.patient_id
            LEFT JOIN recommendations r ON r.submission_id=s.id
            ORDER BY s.id DESC""").fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        r["flags"] = json.loads(r.get("flags_json") or "[]")
        r["urgent_flags"] = json.loads(r.get("urgent_flags_json") or "[]")
    # Priority first, then the breakthrough tier inside a priority band, so an
    # emergent check-in is never sitting below a non-urgent one.
    out.sort(key=lambda r: (_PRIORITY_RANK.get(r.get("priority"), 3),
                            _TIER_RANK.get(r.get("urgency_tier"), 3)))
    return out


def get_submission_bundle(sub_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        s = c.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
        if not s:
            return None
        s = dict(s)
        p = c.execute("SELECT * FROM patients WHERE id=?", (s["patient_id"],)).fetchone()
        r = c.execute("SELECT * FROM recommendations WHERE submission_id=?", (sub_id,)).fetchone()
        inst = c.execute("SELECT * FROM instances WHERE id=?", (s["instance_id"],)).fetchone()
        rec = dict(r) if r else None
        review = None
        if rec:
            v = c.execute("SELECT * FROM reviews WHERE recommendation_id=? ORDER BY id DESC LIMIT 1",
                          (rec["id"],)).fetchone()
            review = dict(v) if v else None
    s["answers"] = json.loads(s.get("answers_json") or "{}")
    s["flags"] = json.loads(s.get("flags_json") or "[]")
    # The medication context as submitted (None on rows recorded before the column
    # existed) - pass it to clinic.build_snapshot(med_context=...) to re-run the
    # submission on its original dose rather than the doctor's intake dose.
    s["med_context"] = json.loads(s.get("med_context_json") or "null")
    return {"submission": s, "patient": dict(p) if p else None,
            "recommendation": rec, "instance": dict(inst) if inst else None, "review": review}


def released_message_for_token(token: str) -> Optional[str]:
    """The physician-approved final message for this patient's latest reviewed
    submission, or None if nothing has been released yet."""
    inst = get_instance_by_token(token)
    if not inst:
        return None
    with _conn() as c:
        r = c.execute("""
            SELECT v.final_message FROM reviews v
            JOIN recommendations rc ON rc.id=v.recommendation_id
            JOIN submissions s ON s.id=rc.submission_id
            WHERE s.patient_id=? AND v.decision='approved' AND v.final_message IS NOT NULL
            ORDER BY v.id DESC LIMIT 1""", (inst["patient_id"],)).fetchone()
        return r["final_message"] if r else None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

# --- keyed audit-chain secret (mirrors clinic.py's _secret pattern) -----------
_SECRET_FILE = os.path.join(DATA_DIR, "secret.key")


def _audit_secret() -> bytes:
    """Key for the audit HMAC. Prefer ASM_SECRET (injected in production so the
    key isn't on disk); otherwise fall back to the persisted random key file in
    the data folder, creating it if missing. Same source clinic.py uses for
    cookie signing, so a single secret protects both."""
    env = os.environ.get("ASM_SECRET")
    if env:
        return env.encode()
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(_SECRET_FILE):
        with open(_SECRET_FILE, "wb") as f:
            f.write(os.urandom(32))
    with open(_SECRET_FILE, "rb") as f:
        return f.read()


def _entry_hash(prev: str, ts: str, actor: str, action: str, detail: str) -> str:
    """Keyed (HMAC-SHA256) chain link so an attacker with only DB access cannot
    recompute a consistent chain after tampering (S5). NOTE: any pre-existing
    (synthetic) audit rows written under the old UNKEYED sha256 scheme will no
    longer verify - acceptable during the synthetic phase before real data."""
    msg = f"{prev}|{ts}|{actor}|{action}|{detail}".encode()
    return hmac.new(_audit_secret(), msg, hashlib.sha256).hexdigest()


def audit(actor: str, action: str, detail: str = ""):
    """Append a tamper-evident audit entry. Each row chains to the previous
    one's hash, so any later edit/deletion breaks the chain (see
    verify_audit_chain). Best-effort integrity for a local single-writer app."""
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, "
                  "actor TEXT, action TEXT, detail TEXT, prev_hash TEXT, entry_hash TEXT)")
        last = c.execute("SELECT entry_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last["entry_hash"] if last else "GENESIS"
        ts = _now()
        eh = _entry_hash(prev, ts, actor, action, detail)
        c.execute("INSERT INTO audit(ts,actor,action,detail,prev_hash,entry_hash) "
                  "VALUES(?,?,?,?,?,?)", (ts, actor, action, detail, prev, eh))


def verify_audit_chain() -> Dict[str, Any]:
    """Recompute the hash chain; report whether the audit log is intact."""
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, "
                  "actor TEXT, action TEXT, detail TEXT, prev_hash TEXT, entry_hash TEXT)")
        rows = [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id ASC")]
    prev = "GENESIS"
    for r in rows:
        expect = _entry_hash(prev, r["ts"], r["actor"], r["action"], r["detail"])
        if r.get("prev_hash") != prev or r.get("entry_hash") != expect:
            return {"ok": False, "broken_at": r["id"], "count": len(rows)}
        prev = r["entry_hash"]
    return {"ok": True, "broken_at": None, "count": len(rows)}


def list_audit(limit: int = 200) -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------------------
# Gold-standard validation labels (clinician's recommendation per case)
# ---------------------------------------------------------------------------

def _gold_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS gold_labels("
              "case_id TEXT PRIMARY KEY, label TEXT, updated_at TEXT)")


def set_gold_label(case_id: str, label: str):
    with _conn() as c:
        _gold_table(c)
        if label:
            c.execute("INSERT INTO gold_labels(case_id,label,updated_at) VALUES(?,?,?) "
                      "ON CONFLICT(case_id) DO UPDATE SET label=excluded.label, "
                      "updated_at=excluded.updated_at", (case_id, label, _now()))
        else:
            c.execute("DELETE FROM gold_labels WHERE case_id=?", (case_id,))


def get_gold_labels() -> Dict[str, str]:
    with _conn() as c:
        _gold_table(c)
        return {r["case_id"]: r["label"] for r in c.execute("SELECT case_id,label FROM gold_labels")}
