"""
clinic.py - local, role-based web app for the Aim 2 assistant.

Two roles, one local server (Python stdlib only; binds to 127.0.0.1).

External network calls: by default the server makes NO external calls - nothing
leaves the machine. Optional advisory lookups (openFDA FAERS adverse-event
summaries in the review view, and PubMed/RxNorm source logging) are gated OFF by
default behind ASM_ENABLE_EXTERNAL. When ASM_ENABLE_EXTERNAL=1, the ONLY thing
that leaves the machine is the medication name (a drug identifier, never PHI),
sent to openFDA to fetch public adverse-event reaction counts; no patient data
is ever transmitted. Leave the flag off for a fully offline, air-gapped run.

  * DOCTOR (admin, password login): dashboard + triage queue, add patients
    (intake), generate a patient survey link, review each submission (engine
    output + flags), approve/edit/reject the draft message, view the audit log.
  * PATIENT (family, tokenized link - no password): fill the between-visit
    survey, submit; later see ONLY a physician-approved message.

Every submission is run through the engine AND the free-text red-flag scanner,
then triaged (urgent / needs review / routine) with reasons, so anything
unusual is flagged and queued to the doctor.

Run from the aim2_engine/ directory:
    python3 clinic.py
SYNTHETIC DATA ONLY until IRB/security sign-off.
"""

import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clinic_store as db
from engine_core import (
    run_engine, snapshot_from_interval, PatientContext, MedicationContext,
    PhysicianContingency, DrugClassifier,
)
from engine_core.asm_rules import load_asm_rules
from engine_core.drug_identity import known_study_medications
from engine_core.humanize import category_label, human_reasons, humanize, URGENT_FLAG_HUMAN
from engine_core.med_selection import (
    select as select_meds, load_selection_ruleset,
)

try:
    from nlp import LexiconRedFlagDetector
except Exception:
    LexiconRedFlagDetector = None

# ---------------------------------------------------------------------------
# Configuration  (deployment seam)
# ---------------------------------------------------------------------------
# The defaults below reproduce the original local behaviour EXACTLY:
# 127.0.0.1:8000, plain HTTP, no Secure cookie, secret persisted in the data
# folder. Every knob is overridable by an environment variable so the SAME code
# can later be deployed behind a domain + TLS-terminating reverse proxy without
# editing source. See .env.example.
#
# SYNTHETIC DATA ONLY. Serving real patient data requires IRB + HIPAA/CITI
# sign-off first (see NON_NEGOTIABLE_RULES.md / NEEDS_PI_SIGNOFF.md) - these knobs do not
# change that gate.
def _env_bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")

HOST = os.environ.get("ASM_HOST", "127.0.0.1")        # bind address; "0.0.0.0" to serve on a network
PORT = int(os.environ.get("ASM_PORT", "8000"))
# Dev convenience: auto-restart the server when a source file changes, so edits
# take effect without a manual restart (prevents stale in-memory code serving an
# old UI). On by default for local runs; set ASM_AUTORELOAD=0 to disable.
AUTORELOAD = _env_bool("ASM_AUTORELOAD", True)
# Absolute base for shareable links (e.g. the patient survey link a clinician
# copies). Leave blank locally -> links are derived from the request's Host
# header, falling back to http://HOST:PORT. Set e.g. https://asm.example.org
# in production (behind a proxy).
PUBLIC_BASE_URL = os.environ.get("ASM_PUBLIC_URL", "").rstrip("/")
BEHIND_PROXY = _env_bool("ASM_BEHIND_PROXY", False)   # trust X-Forwarded-Proto/Host from a front proxy
COOKIE_SECURE = _env_bool("ASM_COOKIE_SECURE", False)  # HTTPS-only session cookie; keep OFF for local HTTP
# Off-by-default gate for ALL external network lookups (openFDA FAERS in the
# review view + PubMed/RxNorm source logging). Off = fully offline; the docstring
# above describes exactly what leaves the machine when this is turned on. (S2)
ENABLE_EXTERNAL = _env_bool("ASM_ENABLE_EXTERNAL", False)
# Doctor session lifetime in seconds; sessions older than this are rejected even
# if the signature is valid (default 12h). (S4)
SESSION_MAX_AGE = int(os.environ.get("ASM_SESSION_MAX_AGE", str(12 * 60 * 60)))
# Off-by-default gate for persisting raw family free-text to the NLP audit log.
# When off, only a sha256 hash + char length is stored (parsing stays auditable
# without persisting caregiver prose, which can carry PHI). (S8)
LOG_FREETEXT = _env_bool("ASM_LOG_FREETEXT", False)

CLASSIFIER = DrugClassifier(default_by="mechanism")
_APP = os.path.dirname(os.path.abspath(__file__))
_ASM_FILE = os.path.join(_APP, "data", "asm_rules.json")
_DECISION_MAP_JSON = os.path.join(_APP, os.pardir, "decision_map", "knowledge_graph.json")
_REDFLAG_FILE = os.path.join(_APP, "nlp", "data", "redflag_terms.json")
_REDFLAG_TEMPLATE = os.path.join(_APP, "nlp", "data", "redflag_terms.template.json")
_CASES_FILE = os.path.join(_APP, "validation", "cases.json")
MEDS = sorted(known_study_medications())

# NLP audit helper - runs point-A parsing on free-text fields and logs to audit store.
# Best-effort: never raises; never feeds the decision engine.
# Free-text parser selector. Default is humadex (HUMADEX/english_medical_ner):
# it beat ClinicalBERT (the prior default) on TWO independent real-text evals -
# the ADE-Corpus-V2 external benchmark (validation/nlp_external_eval.py,
# epilepsy-adjacent slice: F1 0.761 vs 0.720) and a real ASM-specific PubMed
# corpus of your 11 study drugs (validation/nlp_pubmed_eval.py, n=1000:
# drug-recall/side_effect-rate/seizure-rate 0.998/0.977/0.978 vs
# 0.992/0.958/0.978) - on top of already beating BioBERT (the original
# default) on synthetic caregiver-phrasing evals. See validation/nlp_*.py for
# the numbers. Options: ASM_NLP=lexicon|biobert|clinical_bert|humadex|d4data.
# Falls back to the deterministic lexicon baseline if the chosen model can't
# load. Advisory layer only - never the engine.
_NLP_STRUCTURER = None


def _nlp_structurer():
    global _NLP_STRUCTURER
    if _NLP_STRUCTURER is not None:
        return _NLP_STRUCTURER
    from nlp.structurer import LexiconStructurer
    choice = os.environ.get("ASM_NLP", "humadex").lower()
    if choice != "lexicon":
        try:
            if choice == "biobert":
                from nlp.transformer_structurer import BioBERTStructurer
                candidate = BioBERTStructurer(lazy=False)
            elif choice == "clinical_bert":
                from nlp.transformer_structurer import ClinicalBERTStructurer
                candidate = ClinicalBERTStructurer(lazy=False)
            else:                                        # default: humadex
                from nlp.transformer_structurer import GenericNERAdapter
                model_name = ("d4data/biomedical-ner-all" if choice == "d4data"
                              else "HUMADEX/english_medical_ner")
                candidate = GenericNERAdapter(model_name, lazy=False)
            if candidate.is_available():
                _NLP_STRUCTURER = candidate
                return _NLP_STRUCTURER
        except Exception:   # noqa: BLE001
            pass
    _NLP_STRUCTURER = LexiconStructurer()              # deterministic fallback
    return _NLP_STRUCTURER


def _run_nlp_audit(answers: dict, patient_id: str, session_id: str) -> None:
    try:
        import time
        from audit.mongo_audit import ParseAudit
        free_fields = {k: v for k, v in answers.items()
                       if isinstance(v, str) and v.strip()}
        if not free_fields:
            return
        pa = ParseAudit()
        s = _nlp_structurer()
        for q, text in free_fields.items():
            t0 = time.perf_counter()
            findings = s.structure(text, source_question=q)
            ms = (time.perf_counter() - t0) * 1000
            # PHI-in-logs guard (S8): by default persist only a sha256 hash +
            # char length, never the raw caregiver prose. The parse still ran on
            # the real text; only what's WRITTEN to the audit store is redacted.
            # ASM_LOG_FREETEXT=1 restores raw-text logging (synthetic-only).
            if LOG_FREETEXT:
                logged_text = text
            else:
                digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
                logged_text = f"[redacted sha256:{digest} len:{len(text)}]"
            # Record the parser that actually ran (HUMADEX by default; the lexicon
            # only on fallback). This was hardcoded to "lexicon".
            pa.log_parse(session_id=session_id, patient_id=patient_id,
                         model_name=getattr(s, "model_name", "lexicon"), source_question=q,
                         input_text=logged_text, findings=findings, processing_ms=ms)
    except Exception:   # noqa: BLE001
        pass  # audit failure must never block submission

def _log_external_source(kind: str, query: str, source_url: str) -> None:
    """Log an external-source lookup (FAERS/PubMed/RxNorm) so cited sources are
    verifiable. Best-effort: never raises; never feeds the decision engine.
    No-op unless ASM_ENABLE_EXTERNAL is set: with external lookups disabled there
    is nothing to log, and this keeps the run fully offline. (S2)"""
    if not ENABLE_EXTERNAL:
        return
    try:
        from audit.mongo_audit import ParseAudit
        ParseAudit().log_parse(
            session_id="external", patient_id="-", model_name=f"external:{kind}",
            source_question=query, input_text=source_url, findings=[], processing_ms=0.0)
    except Exception:   # noqa: BLE001
        pass


def _log_recommendation_audit(rec, patient,
                               session_id: str) -> None:
    """Log engine recommendation to MongoDB (pattern: log_safety_check).
    Architecture credit: Chen 2024 · drug_safety_and_recommendation."""
    try:
        from audit.mongo_audit import ParseAudit
        pa = ParseAudit()
        pa.log_recommendation(
            session_id=session_id,
            patient_id=str(patient.get("id", "")),
            patient_name=str(patient.get("external_id", "")),
            proposed_drug=str(patient.get("medication", "")),
            current_dose_mg=float(patient.get("dose_amount") or 0) or None,
            recommendation_category=getattr(rec, "category", ""),
            safety_flag=bool(getattr(rec, "safety_concern_flag", False)),
            rationale=[r.get("rule", "") for r in (getattr(rec, "rationale", []) or [])],
            titration_schedule=getattr(rec, "titration_schedule", None),
        )
    except Exception:   # noqa: BLE001
        pass

# Recommendation choices shown to the clinician (value = engine category code).
CATS = [("escalate", "Increase the dose"), ("slower_uptitration", "Increase more slowly"),
        ("switch_medication", "Switch medicine"), ("add_medication", "Add a second medicine"),
        ("no_change", "No change"), ("insufficient_data", "Need more info / review")]

# Urgent safety triggers the engine enforces (shown for review in Clinical
# reference). These are ALWAYS ON: a distinct safety check surfaces each to the
# physician and withholds the family message. Per the project's non-negotiable
# rules, safety flags are never suppressed, so these cannot be toggled off here.
SAFETY_TRIGGERS = [
    ("Possible serious skin reaction (SJS/TEN)",
     "A spreading or blistering rash, or a rash involving the mouth or eyes, on a higher-risk medicine."),
    ("Seizure lasting longer than 5 minutes",
     "A single seizure of 5 minutes or longer (status epilepticus risk)."),
    ("Whole-body seizure longer than 3 minutes",
     "A generalized, whole-body seizure lasting 3 minutes or longer."),
    ("Repeated seizures without recovery",
     "Two or more seizures in a row without returning to normal in between."),
    ("Breathing trouble or not waking",
     "Any difficulty breathing, or not waking up, after a seizure."),
]


def current_asm():
    """Load clinician dosing fresh each time, so Settings edits take effect live."""
    return load_asm_rules(_ASM_FILE) if os.path.isfile(_ASM_FILE) else {}


def current_redflag():
    try:
        return LexiconRedFlagDetector() if LexiconRedFlagDetector else None
    except Exception:
        return None


def _kappa(pred, gold):
    cats = [c for c, _ in CATS]
    n = len(pred)
    if not n:
        return float("nan")
    po = sum(a == b for a, b in zip(pred, gold)) / n
    pe = sum((sum(a == c for a in pred) / n) * (sum(b == c for b in gold) / n) for c in cats)
    return 1.0 if pe >= 1.0 else (po - pe) / (1.0 - pe)


def _load_json(path, default):
    try:
        if not os.path.isfile(path):
            return default
        with open(path) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# --- local secret for signing cookies / CSRF (persisted in the data folder) ---
_SECRET_FILE = os.path.join(db.DATA_DIR, "secret.key")


def _secret():
    # In production, inject a stable secret via ASM_SECRET (so cookies survive
    # restarts/replicas and the key is not written to disk). Locally, fall back
    # to a persisted random key in the data folder.
    env = os.environ.get("ASM_SECRET")
    if env:
        return env.encode()
    os.makedirs(db.DATA_DIR, exist_ok=True)
    if not os.path.isfile(_SECRET_FILE):
        with open(_SECRET_FILE, "wb") as f:
            f.write(os.urandom(32))
    with open(_SECRET_FILE, "rb") as f:
        return f.read()


SECRET = _secret()


def sign(value: str) -> str:
    mac = hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{value}.{mac}"


def unsign(signed: str):
    if not signed or "." not in signed:
        return None
    value, _, mac = signed.rpartition(".")
    good = hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()[:32]
    return value if hmac.compare_digest(mac, good) else None


def csrf_for(key: str) -> str:
    return sign(key + "|csrf")


def csrf_ok(key: str, token: str) -> bool:
    return unsign(token or "") == key + "|csrf"


def base_url(handler=None) -> str:
    """Best absolute base URL for building shareable links.

    Priority: explicit ASM_PUBLIC_URL  >  the live request's (forwarded) Host
    header  >  http://HOST:PORT. This lets the same code serve correct links
    locally, on a LAN, or behind a TLS reverse proxy with no source changes.
    """
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    if handler is not None:
        h = handler.headers
        proto = host = ""
        if BEHIND_PROXY:
            proto = (h.get("X-Forwarded-Proto") or "").split(",")[0].strip()
            host = (h.get("X-Forwarded-Host") or "").split(",")[0].strip()
        host = host or (h.get("Host") or "").strip() or f"{HOST}:{PORT}"
        proto = proto or ("https" if COOKIE_SECURE else "http")
        return f"{proto}://{host}"
    return f"http://{HOST}:{PORT}"


def session_cookie(value: str, max_age: int = None) -> str:
    """Build the Set-Cookie value, adding Secure only when configured (TLS)."""
    parts = [f"sess={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if COOKIE_SECURE:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


# ===========================================================================
# HTML
# ===========================================================================
# CSS now lives in static/clinic.css (served via /static/).


# Client JS now lives in static/clinic.js (served via /static/).


def _asset_url(name):
    """/static/<name> with a version tag from the file's mtime+size so the
    browser always refetches after an edit (kills stale CSS/JS)."""
    fp = os.path.join(_APP, "static", name)
    try:
        st = os.stat(fp)
        ver = f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        ver = "0"
    return f"/static/{name}?v={ver}"


def page(title, body, nav="", heading="Aim 2 Medication Assistant"):
    masthead = ("<div class=topbar><div class=spectrum></div>"
                "<header><div class=brand>"
                "<span class=org>SCRIPPS RESEARCH</span><span class=vsep></span>"
                "<span class=sub>Pediatric Epilepsy &middot; Aim 2 Medication Assistant</span>"
                "</div>"
                + (f"<nav class=tabs role=navigation aria-label='Primary'>{nav}</nav>" if nav else "")
                + "</header></div>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title>"
            f"<link rel=stylesheet href='{_asset_url('clinic.css')}'>"
            # Per-medicine formulation map, defined in <head> so it is guaranteed
            # available before the deferred clinic.js runs (fixes empty dropdowns).
            f"{_med_forms_script()}</head><body>"
            "<a class=skip href='#main'>Skip to content</a>"
            f"{masthead}"
            f"<main id=main class=wrap>{body}<p class=foot>Local app &middot; synthetic data only &middot; "
            "no data leaves this computer &middot; every output is a draft for physician approval.</p>"
            f"</main><script src='{_asset_url('clinic.js')}' defer></script></body></html>")


def opts(pairs, sel=None):
    return "".join(f"<option value='{html.escape(v)}'{' selected' if v==sel else ''}>{html.escape(l)}</option>" for v, l in pairs)


def checks(name, pairs):
    # Wrap in .checks so the bordered, full-width row styling (design) applies.
    inner = "".join(
        f"<label><input type=checkbox name='{name}' value='{html.escape(v)}'>"
        f"<span>{html.escape(l)}</span></label>" for v, l in pairs)
    return f"<div class=checks>{inner}</div>"


# Doctor top nav: pill tabs matching the approved design (Scripps masthead).
# Family check-in opens the patient survey in a new tab; the deep research views
# (validation, text-mining) stay in the de-emphasized tools strip on the Queue.
DOC_NAV = ("<a href='/survey' target='_blank'>Family check-in</a>"
           "<a href='/'>Review queue</a>"
           "<a href='/settings'>Clinical reference</a>"
           "<a href='/audit'>Audit log</a>"
           "<a class=util href='/logout'>Log out</a>")

# Research / QA tools - reachable but kept out of the clinical nav.
TOOLS_BAR = ("<p class=toolsbar>Tools (non-clinical): "
             "<a href='/validation'>validation</a> &middot; "
             "<a href='/nlp-audit'>text-mining audit</a></p>")

# Simple in-memory login throttle. Keyed on (client_ip, username) so an attacker
# can't lock out the sole 'doctor' account for everyone by hammering one username
# (S9). Value: [fail_count, locked_until_epoch]. Dict size is capped to bound
# memory against a flood of distinct keys.
_LOGIN_FAILS = {}
_MAX_FAILS, _LOCK_SECONDS = 5, 120
_LOGIN_FAILS_MAX = 1024


# ===========================================================================
# Triage - decide priority + reasons for a submission (flag anything unusual)
# ===========================================================================
def triage(rec, redflag_cats):
    reasons = []
    if rec.safety_concern_flag:
        reasons.append("Urgent safety signal. Physician review required.")
    for f in rec.urgent_flags:
        reasons.append(humanize(f, URGENT_FLAG_HUMAN))
    if redflag_cats:
        reasons.append("The family's free-text note was flagged for review")
    if reasons:
        return "urgent", reasons

    if getattr(rec, "dose_exceeds_max", False):
        reasons.append("Reported dose is ABOVE the recommended maximum. Verify the entry.")
    if not rec.medication_recognized:
        reasons.append("Medication name not recognized")
    if rec.category != "no_change":
        reasons.append("Suggested next step: " + category_label(rec.category))
    if rec.wants_med_change:
        reasons.append("Family would like a medication change")
    if reasons:
        return "review", reasons
    return "routine", ["No changes suggested; nothing unusual detected"]


_DECISION_LOGIC = [
    # NOTE: kept in the same order decision.py evaluates them. This table is
    # hand-maintained documentation for the Clinical reference tab - it does
    # NOT drive the actual recommendation (that always comes live from
    # decision.py's rationale, via human_reasons() in outputs.py, so the real
    # per-patient summary can never drift from the code). This table had
    # fallen behind the 2026-06-25 breakthrough/no-seizures-table additions
    # (13 rule IDs were missing) - resynced against decision.py's actual
    # _fire() calls.
    ("R0", "Spreading/blistering rash -> stop + switch (urgent, message withheld)"),
    ("R0b-sw", "Prolonged/repeated seizures + medicine making things worse after a trial -> urgent switch"),
    ("R0b-sw2", "Prolonged/repeated seizures + medicine not helping after a trial -> urgent switch"),
    ("R0b", "Prolonged/repeated or whole-body >3 min seizure -> urgent; faster if helping, else switch"),
    ("R0c", "Other urgent safety signal (e.g. ER visit, breathing) -> withhold recommendation, urgent review"),
    ("R1", "Dose recently changed + ongoing seizures -> defer (confirm regimen)"),
    ("R2", "Stopped for side effects / cost -> switch"),
    ("R3", "Drug doesn't cover the seizure type (e.g. ethosuximide = absence only) -> switch"),
    ("R4", "Small rash on an SJS-risk drug + a workup criterion -> switch"),
    ("R4b", "Can't reach a minimum effective dose due to side effects -> switch"),
    ("R5", "Breakthrough during missed doses -> address adherence, no change"),
    ("BT", "Breakthrough -> Dr. Barbour's 2026-06-25 breakthrough table (urgency x phase x side-effect level)"),
    ("R5b", "Helping but side effects block further increase + still seizing -> ADD a second med"),
    ("R6", "Breakthrough + intolerable side effects -> switch"),
    ("R7", "Breakthrough + family prefers a slower/changed dose -> slower uptitration"),
    ("R8", "Breakthrough, no duration captured -> defer (need duration)"),
    ("R9-add", "At max dose, helping, still seizing -> ADD a second med"),
    ("R9-switch", "Frequent/long breakthrough at max dose, not clearly helping -> switch"),
    ("R9", "Frequent/long breakthrough -> increase faster"),
    ("R10", "Breakthrough short/infrequent while titrating -> continue current plan"),
    ("R11", "Breakthrough below the increase-faster thresholds -> continue current plan"),
    ("R12g", "At goal + medium impact + breakthrough -> ask family (increase vs add)"),
    ("R12", "Breakthrough + manageable side effects, duration not captured -> defer (need duration)"),
    ("R13", "Titrating + medium impact -> slower + 1-week adjustment question"),
    ("NS", "Seizure-free -> Dr. Barbour's 2026-06-25 No-Seizures table (phase x side-effect level)"),
    ("NS-defer", "No-Seizures table had no matching action -> defer to physician"),
    ("R14", "Seizure-free + intolerable side effects -> switch"),
    ("R15", "Family wants a different medicine, no clinical trigger -> offer switch options"),
    ("R16", "Seizure-free, still titrating, family prefers slower/changed dose -> slower uptitration"),
    ("R17/R18", "Seizure-free (+/- tolerable side effects) -> no change"),
]
_SURVEY_MAP = [
    # NOTE: hand-maintained documentation, same caveat as _DECISION_LOGIC -
    # does not drive parsing (parse_answers() is the real source of truth),
    # but had fallen out of sync with the survey's actual fields (missing
    # rash/SJS follow-ups, seizure triggers, per-type episode detail, and
    # the medication-preference ranking added 2026-06-25). Resynced against
    # parse_answers().
    ("Q1a medicine + dose", "current medicine, amount, unit/strength, times per day"),
    ("Q2 adherence / Q2a reasons", "adherence (good / partial / stopped) + missed-dose reason(s)"),
    ("Q3 seizures + Q4 types", "any seizures this interval; which type(s) (check all that apply)"),
    ("per-seizure episodes", "per type: duration (<1/1-3/3-5/>5 min buckets), how many in 4 weeks, unresponsive"),
    ("seizure triggers", "possible trigger (missed dose/illness/poor sleep/other) + present before ALL of them?"),
    ("Q9 / Q9 other", "side effects present (check all that apply) + free text for \"other\""),
    ("Q9a rash severity", "small / large / causing blisters (large or blisters = urgent SJS trigger; Barbour 2026-07-01)"),
    ("SJS small-rash follow-ups", "Southeast/East Asian ancestry, other current meds (count), recent viral illness"),
    ("Q10b impact", "side-effect severity (none / manageable / difficult-but-tolerable / intolerable)"),
    ("Q10a dose goal + Q10c", "still titrating / at goal / not sure; if at goal, how long + free-text update"),
    ("Q11 overall", "is the medicine helping (better / about the same / worse)"),
    ("Q12 preference", "family preference (slower dose / different medicine) - no longer asked in the survey (2026-10-15); engine default = no strong preference"),
    ("Medication-direction ranking", "1st/2nd choice among increase / add / switch (not drug-specific yet)"),
    ("Q13 free text", "anything else - also folds in: reason dose changed, seizure description, goal-dose update"),
]


def _reference_html(raw):
    """Clinical-reference sub-sections, generated from the dosing data + the
    engine rule list. Read-only views for the clinician."""
    def _per_drug(getter):
        rows = ""
        for med in MEDS:
            info = raw.get(med) or {}
            val = getter(info)
            if val:
                rows += (f"<tr><td>{html.escape(med.replace('_',' ').title())}</td>"
                         f"<td class=muted>{html.escape(str(val))}</td></tr>")
        return rows or "<tr><td colspan=2 class=muted>No data entered yet.</td></tr>"

    def _formul(info):
        out = []
        for fm in info.get("formulations_rxnorm", []) or []:
            if fm.get("strengths_mg"):
                out.append(f"{fm.get('form','')}: " + "/".join(str(s) for s in fm['strengths_mg']) + " mg")
            elif fm.get("mg_per_ml"):
                out.append(f"{fm.get('form','')}: {fm['mg_per_ml']} mg/mL")
        return "; ".join(out)

    def _titr(info):
        b = (info.get("dose_bands") or [{}])[0]
        st, iv = b.get("standard_uptitration_step_mg") or b.get("uptitration_step_mg_per_kg_per_day"), \
            b.get("standard_uptitration_interval_weeks") or b.get("uptitration_interval_weeks")
        return f"+{st} every {iv} wk" if st and iv else ""

    def _sec(title, body):
        return (f"<details class=trace><summary>{title}</summary>"
                f"<table>{body}</table></details>")

    logic = "".join(f"<tr><td><b>{r}</b></td><td class=muted>{html.escape(d)}</td></tr>"
                    for r, d in _DECISION_LOGIC)
    smap = "".join(f"<tr><td><b>{html.escape(q)}</b></td><td class=muted>{html.escape(v)}</td></tr>"
                   for q, v in _SURVEY_MAP)
    return (
        "<div class=card><h2>Clinical reference - details</h2>"
        "<p class=muted>Read-only views generated from the engine data. Edit dosing above.</p>"
        + _sec("Decision logic (rules R0-R18)", logic)
        + _sec("Seizure-type coverage", _per_drug(lambda i: ", ".join(i.get("indications", []) or [])))
        + _sec("Drug interactions", _per_drug(lambda i: i.get("key_interactions")))
        + _sec("Monitoring &amp; labs", _per_drug(lambda i: ", ".join(i.get("labs", []) or []) or ("yes" if i.get("requires_lab_monitoring") else "")))
        + _sec("Titration pacing", _per_drug(_titr))
        + _sec("Formulations", _per_drug(_formul))
        + _sec("Survey &rarr; engine map", smap)
        + "</div>")


# ---------------------------------------------------------------------------
# Clinical note -> HTML
#
# The stored note is plain text with a strict grammar (engine_core.outputs._Note):
# a two-line header, numbered "N. TITLE" sections, "  - " bullets, "      "
# continuation detail, "      - " sub-bullets, "  ! " safety lines, then any
# note-level footnotes and the closing review notice. That grammar is what makes
# this renderer safe - it re-lays-out the SAME text for reading on screen and
# invents nothing. The plain text stays available verbatim (copy block below, and
# the "Edit the clinical note" textarea), because that is what gets pasted into
# the chart.
# ---------------------------------------------------------------------------

_NOTE_SEC_RE = re.compile(r"^(\d+)\.\s+(.+)$")
#: All three breakthrough tiers, hyphenated by the note writer (NON-URGENT |
#: URGENT | EMERGENT). The character class MUST include the hyphen - "[A-Z]+"
#: alone captured only "NON" and left "-URGENT" dangling beside the badge.
_TIER_RE = re.compile(r"\bUrgency tier:\s*([A-Z][A-Z-]*)")
#: Three-step visual scale off the existing pill palette, so a clinician reads the
#: severity without parsing the word: emergent red, urgent amber, non-urgent green.
_TIER_PILL = {"EMERGENT": "urgent", "URGENT": "review", "NON-URGENT": "routine"}
# Only treat "label: value" as a field when the label is short and is not the tail
# of a sentence. Keeps "Current dose: 500 mg/day" and "Taking medicine in the 2
# days before: yes" as fields, while leaving "Breakthrough seizures
# (family-reported). Urgency tier: URGENT" as a statement.
_MAX_LABEL = 40


def _note_value(text):
    """Escape a value and give the urgency tier a pill so it reads at a glance.
    An unrecognized tier keeps its plain text rather than guessing a colour."""
    out = html.escape(text)
    m = _TIER_RE.search(text)
    if m and m.group(1) in _TIER_PILL:
        tier = m.group(1)
        out = out.replace(f"Urgency tier: {tier}",
                          f"<span class='pill {_TIER_PILL[tier]}'>"
                          f"{html.escape(tier.lower())}</span>")
    return out


def _note_line_html(body, kind, danger=False):
    """One rendered body line. `kind` is bullet | detail | subbullet | alert.
    `danger` marks lines inside the Safety section so an urgent flag listed there
    does not read like an ordinary observation."""
    if kind == "alert":
        return f"<p class=note-alert>{_note_value(body)}</p>"
    if danger and kind == "bullet":
        return f"<p class='note-stmt danger'>{_note_value(body)}</p>"
    if kind == "bullet" and body.endswith(":"):
        # A bullet that only introduces the detail lines under it ("By type:",
        # "Why this is suggested:") is a sub-heading, not a fact.
        return f"<p class=note-lbl>{html.escape(body[:-1])}</p>"
    if kind == "bullet" and ": " in body:
        k, v = body.split(": ", 1)
        if len(k) <= _MAX_LABEL and ". " not in k:
            return (f"<div class=note-row><span class=k>{html.escape(k)}</span>"
                    f"<span class=v>{_note_value(v)}</span></div>")
    if kind == "subbullet":
        return f"<p class=note-sub>{_note_value(body)}</p>"
    if kind == "detail":
        return f"<p class=note-det>{_note_value(body)}</p>"
    return f"<p class=note-stmt>{_note_value(body)}</p>"


def _clinical_note_html(text):
    """Render a stored clinical note for reading. Falls back to the plain-text
    block for anything that does not match the grammar (an older stored note, or a
    physician-edited one), so no note can ever fail to display."""
    text = text or ""
    lines = text.split("\n")
    if not lines or not lines[0].startswith("BETWEEN-VISIT SUMMARY"):
        return f"<pre>{html.escape(text)}</pre>"

    from engine_core.outputs import REVIEW_NOTICE
    header, sections, footnotes = [], [], []
    cur = None
    for ln in lines[1:]:
        if not ln.strip() or ln.strip() == REVIEW_NOTICE:
            continue                      # the page states the review caution once, in the decision card
        m = _NOTE_SEC_RE.match(ln)
        if m:
            # Title passes through as the engine wrote it (already upper case; the
            # stylesheet upper-cases anyway). Do NOT .title() it - that yields
            # "Medication And Dose".
            cur = {"title": m.group(2), "lines": []}
            sections.append(cur)
            continue
        if ln.startswith("      - "):
            kind, body = "subbullet", ln[8:]
        elif ln.startswith("  - "):
            kind, body = "bullet", ln[4:]
        elif ln.startswith("  ! "):
            kind, body = "alert", ln[4:]
        elif ln.startswith("      "):
            kind, body = "detail", ln[6:]
        elif cur is None:
            header.append(ln.strip())
            continue
        else:
            footnotes.append(ln.strip())
            continue
        cur["lines"].append(
            _note_line_html(body, kind, danger=cur["title"].upper() == "SAFETY"))

    out = ""
    if header:
        out += f"<p class=note-who>{html.escape(header[0])}</p>"
    for s in sections:
        safety = s["title"].lower() == "safety"
        out += (f"<section class='note-sec{' danger' if safety else ''}'>"
                f"<h3>{html.escape(s['title'])}</h3>{''.join(s['lines'])}</section>")
    for f in footnotes:
        out += f"<p class=note-foot>{html.escape(f)}</p>"
    # The verbatim text, one click away, for pasting into the chart.
    out += ("<details class=trace><summary>Plain text for the chart</summary>"
            f"<pre>{html.escape(text)}</pre></details>")
    return out


# ---------------------------------------------------------------------------
# Queue triage chips
#
# Each check-in card carries its classification as discrete chips rather than one
# run-together sentence, so a clinician can scan the column: the breakthrough
# urgency tier (emergent | urgent | non-urgent), then anything that needs a second
# look. All of it is read from STORED engine output - no re-deciding, no parsing
# note prose.
# ---------------------------------------------------------------------------

#: tier -> (label, existing pill class). Three-step scale: emergent red, urgent
#: amber, non-urgent green - the same mapping the clinical note's tier badge uses.
_TIER_CHIP = {"emergent": ("Emergent", "urgent"),
              "urgent": ("Urgent", "review"),
              "non_urgent": ("Non-urgent", "routine")}


#: Triage reasons that only restate a chip already shown, or say nothing a
#: clinician can act on. Matched case-insensitively as substrings.
_CHIP_SKIP = ("above the recommended maximum", "not recognized",
              "urgent safety signal", "suggested next step",
              "no changes suggested")


def _queue_chips(it):
    """Classification chips for one queue card, most severe first."""
    chips, seen = [], set()

    def chip(label, cls):
        # The engine's urgent_flags also come back through triage()'s reason list,
        # so the same finding can arrive twice. Keep the first (more severe) one.
        label = re.sub(r",\s*(urgent|emergent)$", "", label.strip().rstrip("."))
        key = label.lower()
        if not label or key in seen:
            return
        seen.add(key)
        chips.append(f"<span class='pill {cls}'>{html.escape(label)}</span>")

    # 1. Safety first: an urgent safety signal outranks everything else.
    if it.get("safety_flag"):
        chip("Safety", "urgent")
    # 2. The breakthrough urgency tier, when this check-in reported breakthrough.
    tier = _TIER_CHIP.get(it.get("urgency_tier"))
    if tier:
        chip(*tier)
    elif not it.get("safety_flag"):
        chip("No seizures", "routine")
    # 3. Everything else that warrants a second look before acting.
    if it.get("dose_exceeds_max"):
        chip("Dose above max", "urgent")
    if it.get("recognized") == 0:
        chip("Med not recognized", "review")
    for fl in (it.get("urgent_flags") or []):
        chip(humanize(fl, URGENT_FLAG_HUMAN), "urgent")
    # Triage reasons the chips above do not already cover (e.g. the free-text
    # red-flag scan, a formulation mismatch, family wants a change).
    for reason in (it.get("flags") or []):
        if not any(c in reason.lower() for c in _CHIP_SKIP):
            chip(reason, "review")
    return "<span class=qchipset>" + "".join(chips) + "</span>" if chips else ""


def _dose_position(ref, current_mg_per_day):
    """Where the reported dose sits against this child's band, as a short phrase.

    A queue card is read in a second, so the numbers alone ("current 1000 · typical
    930-1860 · absolute max 3000") make a clinician do the comparison themselves.
    This states the conclusion. Uses only clinician-supplied band values; returns
    None when the band or the dose is unavailable, so nothing is ever implied.
    """
    cur = current_mg_per_day
    if not isinstance(ref, dict) or not isinstance(cur, (int, float)):
        return None
    lo = ref.get("min_maintenance_mg_per_day")
    hi = ref.get("effective_max_mg_per_day")
    absmax = ref.get("absolute_max_mg_per_day")
    if isinstance(absmax, (int, float)) and cur > absmax:
        return ("above the absolute maximum", "urgent")
    if isinstance(hi, (int, float)) and cur >= hi:
        return ("at or above the usual effective max", "review")
    if isinstance(lo, (int, float)) and cur < lo:
        return ("below the typical range", "review")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return ("within the typical range", "routine")
    return None


def _dosing_examples_html():
    """Worked "expected dose for this child" examples by age/weight band, for the
    clinician Clinical-reference tab. Generated from Dr. Barbour's dose ladder via
    dose_ladder.reference_for() - the SAME source the engine and the clinician
    report use; no values are invented here. Weight-based (mg/kg) medicines show
    the dose scaling with weight; weight-/age-banded medicines show one example per
    band. DRAFT until PI sign-off. Clinician-only (this tab is behind doctor auth)."""
    from engine_core import dose_ladder
    pts = [(2, 10), (4, 15), (6, 21), (8, 27), (10, 33), (12, 42), (14, 52), (16, 62), (18, 72)]
    LB = 0.45359237
    dv = dose_ladder.dose_value_str
    blocks = []
    for med in MEDS:
        key = med.lower().replace(" ", "_")
        rows, seen = "", set()
        for age, wt in pts:
            r = dose_ladder.reference_for(key, age, wt, [])
            if not r or r.get("conmed_suppressed"):
                continue
            g = r.get("min_maintenance_mg_per_day")
            dedup = (r.get("band_label"), g)   # mg/kg -> g varies (scaling shown); banded -> collapses
            if dedup in seen:
                continue
            seen.add(dedup)
            # Standardized cells: mg/day (+ mg/kg/day for weight-based bands), via
            # the shared formatter so this table can't drift from the report.
            per_kg = (r.get("dose_basis") == "mg_per_kg_per_day")
            ge = html.escape(dv(g, wt, per_kg) or "-")
            ee = html.escape(dv(r.get("effective_max_mg_per_day"), wt, per_kg) or "-")
            ae = html.escape(dv(r.get("absolute_max_mg_per_day"), wt, per_kg) or "-")
            step = html.escape(str(r.get("step_raw") or "-"))
            iv = html.escape(str(r.get("interval") or ""))
            rows += (f"<tr><td class=muted>age {age}, ~{wt} kg ({round(wt / LB)} lb)</td>"
                     f"<td>{html.escape(str(r.get('band_label') or ''))}</td>"
                     f"<td>{ge}</td><td>{ee}</td><td>{ae}</td>"
                     f"<td>{step}{(' &middot; ' + iv) if iv else ''}</td></tr>")
        if rows:
            blocks.append(
                f"<details class=trace><summary>{html.escape(med.replace('_', ' ').title())}</summary>"
                "<table><tr><th>Example child</th><th>Band</th><th>Goal (min maintenance)</th>"
                f"<th>Usual effective max</th><th>Absolute max</th><th>Increase step / interval</th>"
                f"</tr>{rows}</table></details>")
    note = ("<p class=muted><b>Dosing and pacing by the child's age/weight</b>, all generated from "
            "Dr. Barbour's dosing table (the one source the engine uses, so this can't drift from "
            "the recommendations). Goal = minimum maintenance dose; usual effective max = "
            "typically-tolerated ceiling; absolute max = hard ceiling; increase step / interval = "
            "how much to raise each step and how often. Weight-based medicines show the dose scaling "
            "with weight. DRAFT - the clinician confirms every value. Lamotrigine taken with valproate "
            "or an enzyme-inducing medicine uses a separate, slower regimen and is not shown here.</p>")
    return note + ("".join(blocks) or "<p class=muted>No dosing-ladder data available.</p>")


_MAP_COLORS = {
    "input": "#e8eef7", "rule": "#e9f2ec", "branch": "#eee8fb", "table": "#e6f4f6",
    "drug": "#eef0f2", "band": "#f3f0e8", "recommendation": "#e3f0ff",
    "validation": "#eef7e8", "case": "#eef7e8", "labs": "#f0eef7",
    "level": "#fff3e0", "phase": "#efeafb",
}


def _decision_map_svg():
    """Static SVG render of the decision-map knowledge graph for the clinician tab.

    CSP-safe by construction: pure SVG (rects/lines/text) with NO JavaScript, so the
    app's strict `script-src 'self'` has nothing to block. Node positions come from
    the graph's own deterministic layout in decision_map/knowledge_graph.json
    (regenerate it with decision_map/build_knowledge_graph.py after editing rule
    data). Read-only; the interactive HTML version remains in decision_map/."""
    try:
        with open(_DECISION_MAP_JSON) as fh:
            g = json.load(fh)
    except (OSError, ValueError):
        return ("<p class=muted>Decision map not generated yet. Build it with "
                "<code>python3 decision_map/build_knowledge_graph.py</code>.</p>")
    nodes, edges = g.get("nodes", []), g.get("edges", [])
    if not nodes:
        return "<p class=muted>Decision map is empty.</p>"
    NW, NH = 170, 34
    xs, ys = [n["x"] for n in nodes], [n["y"] for n in nodes]
    minx, miny = min(xs) - NW, min(ys) - NH
    W, H = (max(xs) + NW) - minx, (max(ys) + NH) - miny
    pos = {n["id"]: n for n in nodes}
    parts = []
    for e in edges:
        a, b = pos.get(e.get("source")), pos.get(e.get("target"))
        if not a or not b:
            continue
        minor = e.get("minor")
        stroke, sw = ("#ccd2dc", 1) if minor else ("#98a2b3", 1.4)
        dash = ' stroke-dasharray="4 4"' if minor else ""
        parts.append(f'<line x1="{a["x"]+NW/2:.0f}" y1="{a["y"]:.0f}" '
                     f'x2="{b["x"]-NW/2:.0f}" y2="{b["y"]:.0f}" '
                     f'stroke="{stroke}" stroke-width="{sw}"{dash}/>')
    for n in nodes:
        x, y = n["x"], n["y"]
        fill = _MAP_COLORS.get(n.get("type"), "#eef0f2")
        full = html.escape(str(n.get("label", "")))
        disp = full if len(full) <= 26 else full[:25] + "…"
        parts.append(
            f'<g><title>{full}</title>'
            f'<rect x="{x-NW/2:.0f}" y="{y-NH/2:.0f}" width="{NW}" height="{NH}" rx="7" '
            f'fill="{fill}" stroke="#8a94a6"/>'
            f'<text x="{x:.0f}" y="{y+4:.0f}" text-anchor="middle" font-size="11" '
            f'font-family="system-ui,Arial" fill="#16191c">{disp}</text></g>')
    # Interactive + responsive. The SVG fills its container (viewBox + 100% w/h,
    # preserveAspectRatio); an inner <g id=dm-viewport> is pan/zoomed by clinic.js
    # (decisionMap()). ALL interactivity lives in the EXTERNAL clinic.js
    # (script-src 'self'), so the strict CSP is satisfied - no inline script here.
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:12px">'
        f'<span style="width:12px;height:12px;border-radius:3px;background:{c};'
        f'border:1px solid #8a94a6;display:inline-block"></span>{lbl}</span>'
        for lbl, c in [("Inputs / branch", "#e8eef7"), ("Rules", "#e9f2ec"),
                       ("Levels / phase", "#fff3e0"), ("Tables &amp; ladder", "#e6f4f6"),
                       ("Drugs &amp; bands", "#f3f0e8"), ("Recommendations", "#e3f0ff"),
                       ("Validation", "#eef7e8")])
    svg = (f'<svg id="dm-svg" viewBox="{minx:.0f} {miny:.0f} {W:.0f} {H:.0f}" '
           f'preserveAspectRatio="xMidYMid meet" '
           f'style="width:100%;height:100%;display:block;cursor:grab;touch-action:none" '
           f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Engine decision map">'
           f'<g id="dm-viewport">' + "".join(parts) + "</g></svg>")
    controls = (
        '<div style="position:absolute;top:8px;right:8px;display:flex;gap:6px;z-index:2">'
        '<button type=button class="btn sm ghost" data-dm="out" aria-label="Zoom out">-</button>'
        '<button type=button class="btn sm ghost" data-dm="in" aria-label="Zoom in">+</button>'
        '<button type=button class="btn sm ghost" data-dm="reset">Reset</button></div>')
    return (
        "<p class=muted>The whole engine decision path - inputs &rarr; safety gates &rarr; "
        "branch &rarr; rules (R0-R18) &rarr; tables &amp; dose ladder &rarr; per-drug bands &rarr; "
        "recommendations &rarr; validation. Drag to pan, scroll or use +/- to zoom, Reset to fit; "
        "hover a box for its full label. Read-only, generated from the engine data (rebuild with "
        "<code>build_knowledge_graph.py</code> after rule edits).</p>"
        f'<div style="margin:0 0 8px;font-size:12px;color:#647084">{legend}</div>'
        f'<div style="position:relative;height:72vh;overflow:hidden;border:1px solid #e5e7eb;'
        f'border-radius:8px;background:#fff;resize:vertical">{controls}{svg}</div>')


def _formulation_reason(f):
    """Advisory: flag when the family's entered strength doesn't match a known
    formulation for that medicine (cross-check vs data/asm_rules.json)."""
    one = lambda k: (f.get(k) or [""])[0]
    med = _form_med(f)
    raw = _load_json(_ASM_FILE, {}).get(med) if med else None
    strength = _strength_from_form(one)
    if not raw or not strength:
        return None
    known = set()
    for form in raw.get("formulations_rxnorm", []) or []:
        for s in form.get("strengths_mg", []) or []:
            known.add(float(s))
        if form.get("mg_per_ml"):
            known.add(float(form["mg_per_ml"]))
    if known and strength not in known:
        ks = ", ".join("%g" % s for s in sorted(known))
        return (f"Entered strength {strength:g} may not match a known "
                f"{med.replace('_', ' ')} formulation ({ks} mg). Verify with the family.")
    return None


def _why_html(rec, sub, medication):
    """The single 'Why' card: one plain-language reason on top, with the rule
    that fired and the sources it used tucked into one collapsed expander
    (provenance stays one click away, per the non-negotiable rule trace)."""
    try:
        rat = json.loads(rec.get("rationale_json") or "[]")
    except Exception:
        rat = []
    reason = (html.escape(str(rat[-1].get("description", "")))
              if rat else "No rule description was recorded for this recommendation.")

    # Extra triage notes worth surfacing, minus anything already shown elsewhere:
    # the 'Suggested next step' echo of the recommendation, the routine filler,
    # and the safety items that render as alerts at the top of the page. On an
    # urgent case every flag is already an alert up top, so nothing repeats here.
    def _shown_as_alert(x):
        return (x.startswith("Urgent safety signal")
                or x == "Medication name not recognized"
                or "ABOVE the recommended maximum" in x)
    if (sub.get("priority") or "routine") == "urgent":
        extra = []
    else:
        extra = [x for x in (sub.get("flags") or [])
                 if not x.startswith("Suggested next step:")
                 and x != "No changes suggested; nothing unusual detected"
                 and not _shown_as_alert(x)]
    extra_html = ("<p class=muted style='margin-top:14px'>Also noted</p><ul>"
                  + "".join(f"<li>{html.escape(x)}</li>" for x in extra)
                  + "</ul>") if extra else ""

    rule_items = "".join(
        f"<div class=rule><b>{html.escape(str(r.get('rule', '')))}</b> "
        f"{html.escape(str(r.get('description', '')))}"
        + (f"<br><span class=muted style='font-size:12px'>inputs: "
           f"{html.escape(json.dumps(r.get('inputs', {}), default=str))}</span>"
           if r.get("inputs") else "")
        + "</div>" for r in rat) or "<p class=muted>No rule trace recorded.</p>"
    provenance = (
        "<details class=trace><summary>The rule that fired and the sources it used</summary>"
        f"{rule_items}{_sources_inner(rec.get('sources_json'), medication)}</details>")
    return (f"<div class=card><h2>Why</h2>"
            f"<p class=reason>{reason}</p>{extra_html}{provenance}</div>")


def _verified_pill(verified):
    return ("<span class='pill routine'>verified</span>" if verified
            else "<span class='pill review'>DRAFT &middot; unverified</span>")


def _sources_inner(sources_json, medication):
    """Provenance detail for the sources the engine used (dosing, thresholds,
    drug facts, data provenance, optional FAERS). Returns inner HTML only, meant
    to sit inside the collapsed Why expander; the rule trace is rendered by the
    Why card, so it is not repeated here. Decision values are clinician-supplied
    and DRAFT until sign-off."""
    try:
        s = json.loads(sources_json) if sources_json and sources_json != "null" else None
    except Exception:
        s = None
    if not s:
        return ""

    # Dosing source
    d = s.get("dosing") or {}
    if d.get("available"):
        ceil = d.get("ceiling_mg_per_day")
        dosing_html = (
            f"<div style='margin-top:10px'>Dosing data: <b>{html.escape(str(d.get('source') or 'n/a'))}</b> "
            f"{_verified_pill(d.get('verified'))}<br>"
            f"<span class=muted>band: {html.escape(str(d.get('band_label') or '-'))}"
            + (f" &middot; ceiling {ceil:g} mg/day" if isinstance(ceil, (int, float)) else "")
            + f" &middot; {html.escape(str(d.get('note') or ''))}</span></div>")
    else:
        dosing_html = (f"<div class=muted style='margin-top:10px'>"
                       f"{html.escape(str(d.get('note') or 'No dosing data.'))}</div>")

    # Thresholds source
    t = s.get("thresholds") or {}
    secs = ", ".join(t.get("sections_used") or []) or "none consulted"
    thr_html = (f"<div style='margin-top:6px'>Decision thresholds: <b>{html.escape(str(t.get('source') or 'n/a'))}</b> "
                f"{_verified_pill(t.get('verified'))}<br>"
                f"<span class=muted>sections used: {html.escape(secs)} &middot; "
                f"{html.escape(str(t.get('note') or ''))}</span></div>")

    # Drug facts consulted
    facts = s.get("drug_facts") or {}
    facts_html = ("<div class=muted style='margin-top:6px'>Drug facts used: "
                  + html.escape(json.dumps(facts, default=str)) + "</div>") if facts else ""

    prov = html.escape(str(s.get("data_provenance") or ""))

    # Live external source: FAERS reactions for the current medication (logged).
    # Skipped entirely unless ASM_ENABLE_EXTERNAL is set, so by default nothing
    # (not even the medication name) leaves the machine. (S2)
    faers_html = ""
    try:
        from audit.faers_lookup import get_faers_reaction_summary
        summ = (get_faers_reaction_summary(medication, limit=5)
                if (ENABLE_EXTERNAL and medication) else None)
        if summ:
            rxns = "".join(f"<li>{html.escape(rx['reaction'])} "
                           f"<span class=muted>({rx['count']})</span></li>"
                           for rx in summ.get("top_reactions", []))
            faers_html = (f"<div style='margin-top:8px'><b>FAERS adverse-event reports</b> "
                          f"for {html.escape(medication)}: {summ.get('total','?')} reports "
                          f"<span class=muted>(openFDA; advisory, never used by the engine)</span>"
                          f"<ul style='margin:4px 0'>{rxns}</ul>"
                          f"<p class=muted style='font-size:11px'>{html.escape(str(summ.get('source','')))}</p></div>")
            _log_external_source("faers", medication, summ.get("source", ""))
    except Exception:
        faers_html = ""

    return (f"<div style='margin-top:12px'><b>Sources behind the values</b></div>"
            f"{dosing_html}{thr_html}{facts_html}"
            f"<p class=muted style='font-size:12px;margin-top:6px'>{prov}</p>"
            f"{faers_html}")


def _ladder_html(lad):
    """Render Dr. Barbour's EXACT dose-ladder rung(s) (final_doses v9). Shown even
    when the asm_rules schedule is unavailable, since the ladder resolves from the
    drug/weight band independently. DRAFT until PI sign-off."""
    if not lad:
        return ""
    nxt = lad.get("next_up") or []
    if nxt:
        rungs = "".join(
            f"<tr><td>+{x.get('step')}</td><td>{x.get('target_mg_per_day')} mg/day</td>"
            f"<td>{html.escape(str(x.get('tier') or ''))}</td></tr>" for x in nxt)
        table = ("<table><tr><th>Increase</th><th>Target</th><th>Range</th></tr>"
                 f"{rungs}</table>")
    else:
        table = "<p class=muted>Already at the top listed rung for this band.</p>"
    hi = ("<p class=notetag>&#9888; Next increase enters the higher, often-less-tolerated "
          f"range. Order: {html.escape(', '.join(lad.get('high_dose_labs') or []))}.</p>"
          if lad.get("reaches_high_tier") else "")
    dq = (f"<p class=notetag>&#9888; Data-quality flag for this band: "
          f"{html.escape(str(lad.get('data_quality_flag')))} (PI to confirm).</p>"
          if lad.get("data_quality_flag") else "")
    prov = "" if lad.get("verified") else ("<span class=notetag>&#9888; Draft ladder values. "
            "UNVERIFIED, pending PI confirmation (NEEDS_PI_SIGNOFF.md).</span>")
    lvl = ""
    if lad.get("current_level_index"):
        rounded = (f" (rounded from {lad.get('rounded_from')} mg/day)"
                   if lad.get("rounded_from") is not None else "")
        lvl = (f"<p class=muted>Current level: <strong>{lad['current_level_index']} of "
               f"{lad.get('total_levels')}</strong> &middot; ~{lad.get('current_rung_mg_per_day')} mg/day "
               f"({html.escape(str(lad.get('current_tier') or ''))}){rounded}</p>")
    from engine_core import dose_ladder
    # Standardized reference line (current · goal · effective max · absolute max,
    # mg/day + mg/kg/day for weight-based bands) - same formatter every surface
    # uses. weight_kg travels inside `lad` (resolve_plan) so mg/kg/day resolves.
    ref_line = dose_ladder.format_dose_reference(lad)
    return (f"<div class=card><h2>Exact dose ladder</h2>"
            f"<p class=muted>{html.escape(str(lad.get('band_label') or ''))} &middot; "
            f"{html.escape(ref_line)}</p>"
            f"{lvl}{table}{hi}{dq}{prov}</div>")


def _titration_html(titration_json):
    """Trustworthiness: render the dose steps, clearly tagged when unverified."""
    try:
        t = json.loads(titration_json) if titration_json and titration_json != "null" else None
    except Exception:
        t = None
    if not t:
        return ""
    ladder_block = _ladder_html(t.get("ladder"))
    if t.get("status") != "ok":
        return ("<div class=card><h2>Titration schedule</h2>"
                f"<p class=muted>{html.escape(t.get('note', 'Not available.'))}</p></div>"
                + ladder_block)
    rows = "".join(
        f"<tr><td>{s.get('step')}</td><td>{s.get('target_total_mg_per_day')} mg/day</td>"
        f"<td>{(str(s.get('per_dose_mg')) + ' mg') if s.get('per_dose_mg') else '-'}</td>"
        f"<td>{html.escape(str(s.get('after_each') or '-'))}</td></tr>"
        for s in t.get("steps", []))
    cad = html.escape((t.get("cadence") or {}).get("label", ""))
    prov = "" if t.get("verified") else ("<span class=notetag>&#9888; Draft dosing values. "
            "UNVERIFIED, pending PI confirmation (NEEDS_PI_SIGNOFF.md). Not authoritative.</span>")
    return (f"<div class=card><h2>Titration schedule</h2>"
            f"<p class=muted>Current {t.get('current_total_mg_per_day')} mg/day &middot; "
            f"ceiling {t.get('ceiling_mg_per_day')} mg/day &middot; step {t.get('step_mg_per_day')} mg "
            f"&middot; {cad}</p>"
            f"<table><tr><th>Step</th><th>Target</th><th>Per dose</th><th>Increase after</th></tr>"
            f"{rows}</table>{prov}</div>"
            + ladder_block)


def _age_weight(patient, answers):
    """Prefer the age/weight the family entered on the form (answers), falling
    back to the doctor's intake record. Weight already converted to kg upstream."""
    age = answers.get("child_age_years")
    wkg = answers.get("child_weight_kg")
    return (age if age is not None else patient["age"],
            wkg if wkg is not None else patient["weight"])


def _ancestry_and_conmeds(answers):
    """SJS small-rash workup inputs (T11/R4) - see the fs-rash form comment
    in the survey HTML for why these exist. answers["Q_ancestry"] is already
    normalized to "yes"/"no"/None by parse. concomitant_asms/other_meds now
    carry the actual selected medicine names (not just a count), so the
    small-rash workup's "other significant meds" criterion reflects real
    co-prescriptions, and the names are available for future interaction
    cross-checking against each drug's key_interactions text (Barbour B3)."""
    ancestry = {"yes": True, "no": False}.get(answers.get("Q_ancestry"))
    concomitant_asms = list(answers.get("Q_other_asms") or [])
    concomitant_other_meds = list(answers.get("Q_other_meds_text") or [])
    return ancestry, concomitant_asms, concomitant_other_meds


#: The medication fields a family can confirm/override in Q1a. These are persisted
#: with the submission (submissions.med_context_json) because they arrive on the raw
#: POST form and are NOT all recoverable from the patient intake record - a liquid's
#: mg/mL in particular. See med_context_of() / build_snapshot(med_context=...).
MED_CONTEXT_FIELDS = ("medication", "dose_amount", "dose_unit", "frequency_per_day",
                      "concentration", "dose_amounts")


def med_context_of(snapshot):
    """The resolved medication fields of a snapshot, as a plain dict for storage."""
    m = snapshot.medication
    return {k: getattr(m, k, None) for k in MED_CONTEXT_FIELDS}


def build_snapshot(patient, answers, med_context=None):
    """Snapshot from the doctor's intake record + the family's answers.

    `med_context` (a stored `med_context_of()` dict) overrides the intake
    medication fields, so re-running a past submission reproduces exactly what the
    engine saw instead of silently reverting to the intake dose. Absent -> intake
    record, which is the correct default for a fresh doctor-side run.
    """
    age_years, weight_kg = _age_weight(patient, answers)
    ancestry, other_asms, other_meds = _ancestry_and_conmeds(answers)
    med = MedicationContext(
        medication=patient["medication"], dose_amount=patient["dose_amount"],
        dose_unit=patient["dose_unit"], frequency_per_day=patient["frequency"],
        concentration=patient.get("concentration"))
    for k in MED_CONTEXT_FIELDS:
        if (med_context or {}).get(k) is not None:
            setattr(med, k, med_context[k])
    return snapshot_from_interval(
        PatientContext(external_id=patient["external_id"], age_years=age_years,
                       weight_kg=weight_kg, epilepsy_etiology=patient["epilepsy_type"],
                       southeast_asian_ancestry=ancestry, concomitant_asms=other_asms,
                       concomitant_other_meds=other_meds),
        med,
        PhysicianContingency(can_increase_dose=bool(patient["can_increase"]),
                             can_switch_to=patient["switch_to"]),
        answers)


def _split_from_form(f, freq):
    """Q1a 'same amount each time?' = no: one amount per dose, morning first
    (amt_1 .. amt_<freq>). Returns the list, or None for 'same each time'. A blank or
    non-number slot is kept as None, so the engine treats the total as not
    computable and defers, rather than guessing a dose."""
    one = lambda k: ((f.get(k) or [""])[0] or "").strip()
    if one("same_each") != "no" or not isinstance(freq, int) or freq < 2:
        return None
    out = []
    for i in range(1, freq + 1):
        try:
            out.append(float(one(f"amt_{i}")))
        except ValueError:
            out.append(None)
    return out


def build_family_snapshot(patient, f, answers):
    """Snapshot for a family submission: use the dose the family confirmed in
    Q1a when given, otherwise fall back to the doctor's intake record."""
    one = lambda k, d="": (f.get(k) or [d])[0]
    chosen = _form_med(f)
    nd = CLASSIFIER.normalize(chosen or patient["medication"])
    med = nd.canonical or chosen or patient["medication"]
    try:
        amt = float(one("amt")) if one("amt") else patient["dose_amount"]
    except ValueError:
        amt = patient["dose_amount"]
    try:
        freq = int(one("freq")) if one("freq") else patient["frequency"]
    except ValueError:
        freq = patient["frequency"]
    unit = one("unit") or patient["dose_unit"]
    split = _split_from_form(f, freq)
    if split and isinstance(split[0], float):
        amt = split[0]                     # legacy single-amount field: the first dose
    try:
        if (unit or "").lower() == "ml":
            # Liquid: prefer the per-medicine concentration dropdown (mg/mL); the
            # "mg in mL" pair is now the 'Other' free-text fallback for that select.
            csel = one("conc_mgml")
            if csel and csel != "__other__":
                conc = float(csel)
            elif one("conc_mg") and one("conc_ml"):
                cm, cl = float(one("conc_mg")), float(one("conc_ml"))
                conc = (cm / cl) if cl else patient.get("concentration")
            else:
                conc = patient.get("concentration")
        else:
            sv = _strength_from_form(one)
            conc = sv if sv is not None else patient.get("concentration")
    except (ValueError, ZeroDivisionError):
        conc = patient.get("concentration")
    age_years, weight_kg = _age_weight(patient, answers)
    ancestry, other_asms, other_meds = _ancestry_and_conmeds(answers)
    return snapshot_from_interval(
        PatientContext(external_id=patient["external_id"], age_years=age_years,
                       weight_kg=weight_kg, epilepsy_etiology=patient["epilepsy_type"],
                       southeast_asian_ancestry=ancestry, concomitant_asms=other_asms,
                       concomitant_other_meds=other_meds),
        MedicationContext(medication=med, dose_amount=amt, concentration=conc,
                          dose_unit=one("unit") or patient["dose_unit"], frequency_per_day=freq,
                          dose_amounts=split),
        PhysicianContingency(can_increase_dose=bool(patient["can_increase"]),
                             can_switch_to=patient["switch_to"]),
        answers)


# ===========================================================================
# Patient survey form - mirrors the between-visit survey (Q1-Q13) + parsing
# ===========================================================================

# Skip logic: only show a follow-up section when the answer calls for it.


# Option sets reused across the survey (defined once).
_LOOK_OPTS = [('brief_staring', 'Brief staring or pauses'), ('sudden_jerks', 'Quick jerks'),
              ('whole_body', 'Whole-body shaking or stiffening'),
              ('focal', 'One part of the body shaking or twitching'),
              ('acting_off', 'Acting off or not responding appropriately, without shaking or stiffening'),
              ('not_sure', 'Not sure')]
# Per-seizure-type follow-ups (design): each checked type reveals its own
# frequency + duration + unresponsiveness. (token, family-facing phrase)
_SEIZ_DETAIL = [
    ("brief_staring", "brief staring spells"),
    ("sudden_jerks", "quick jerks"),
    ("whole_body", "whole-body shaking or stiffening"),
    ("focal", "shaking or twitching in one part of the body"),
    ("acting_off", "times acting off or not responding appropriately"),
]
_FREQ_BUCKET_OPTS = [('', 'Choose'), ('daily', 'Daily (i.e., more than 28 per 4 weeks)'),
                     ('weekly', 'Weekly (more than 4 in 4 weeks)'),
                     ('monthly', 'Monthly (1 or more in 4 weeks)'),
                     ('less_than_monthly', 'Less than once a month')]
_UNRESP_OPTS = [('', 'Choose'), ('yes', 'Yes'), ('no', 'No'), ('not_sure', 'Not sure')]
_LAST_OPTS = [('', 'Choose'), ('under_2_weeks', 'Less than 2 weeks ago'),
              ('2_4_weeks', '2 to 4 weeks ago'), ('over_4_weeks', 'More than 4 weeks ago')]
# 6-level side-effect impact scale (Barbour 2026-06-25 "No Seizures" table). The
# option VALUES map to side-effect levels L0-L5 in engine_core/categorize.py
# (IMPACT_TO_LEVEL). DRAFT wording - pending PI confirmation (NEEDS_PI_SIGNOFF.md).
_IMPACT_OPTS = [('', 'Choose'),
                ('no_impact', 'Level 0 - No side effects'),
                ('small', 'Level 1 - Small impact (noticeable, but not interfering with daily life)'),
                ('medium_tolerable', 'Level 2 - Medium impact, but tolerable (affects parts of daily life, feels manageable)'),
                ('medium_alot', 'Level 3 - Medium impact (feeling like a lot)'),
                ('big', 'Level 4 - Big impact (significantly affecting daily life)'),
                ('not_a_fit', 'Level 5 - Not a fit for my child')]
_MED_OPTS = [('', 'Choose your medicine'),
             ('brivaracetam', 'Brivaracetam (Briviact)'), ('carbamazepine', 'Carbamazepine (Tegretol)'),
             ('clobazam', 'Clobazam (Onfi)'), ('ethosuximide', 'Ethosuximide (Zarontin)'),
             ('lacosamide', 'Lacosamide (Vimpat)'), ('lamotrigine', 'Lamotrigine (Lamictal)'),
             ('levetiracetam', 'Levetiracetam (Keppra)'), ('oxcarbazepine', 'Oxcarbazepine (Trileptal)'),
             ('topiramate', 'Topiramate (Topamax)'), ('valproic_acid', 'Valproic acid (Depakote)'),
             ('zonisamide', 'Zonisamide (Zonegran)'), ('other', 'Other (not listed)')]
# 2026-06-25: threshold-aligned duration buckets so the PRECISE per-seizure-type
# urgency cutoffs (20 / 30 / 90 / 180 / 300 seconds) can be evaluated exactly.
_DUR_OPTS = [('', 'Choose'),
             ('under_20s', 'Less than 20 seconds'),
             ('20_30s', '20 to 30 seconds'),
             ('30_90s', '30 to 90 seconds'),
             ('90s_3min', '90 seconds to 3 minutes'),
             ('3_5min', '3 to 5 minutes'),
             ('over_5min', 'More than 5 minutes'),
             ('not_sure', 'Not sure')]
_TIME_ON_DOSE_OPTS = [('', 'Choose'), ('under_2_weeks', 'Less than 2 weeks'), ('2_4_weeks', '2 to 4 weeks'),
                      ('1_3_months', '1 to 3 months'), ('over_3_months', 'More than 3 months')]


def _med_formulations():
    """Per-medicine available formulations for the survey dose dropdowns.

    Built from data/asm_rules.json (the drug-facts source), so the strength
    options a family sees are the medicine's REAL tablet/capsule strengths and
    liquid concentration - not a free-text box they can mistype. Returns:
        {med: {"tablets": [sorted mg], "mgml": [sorted mg/mL]}}
    'tablets' unions every solid oral form (tablet/capsule/chewable/ODT/film);
    'mgml' lists each oral-solution/suspension concentration.
    """
    raw = _load_json(_ASM_FILE, {})
    out = {}
    for med, info in raw.items():
        if med.startswith("_") or not isinstance(info, dict):
            continue
        tablets, mgml = set(), set()
        for fm in info.get("formulations_rxnorm", []) or []:
            for s in fm.get("strengths_mg", []) or []:
                try:
                    tablets.add(float(s))
                except (TypeError, ValueError):
                    pass
            if fm.get("mg_per_ml") is not None:
                try:
                    mgml.add(float(fm["mg_per_ml"]))
                except (TypeError, ValueError):
                    pass
        if tablets or mgml:
            out[med] = {"tablets": sorted(tablets), "mgml": sorted(mgml)}
    return out


def _med_forms_script():
    """Per-medicine formulation map for clinic.js, delivered as a NON-executable
    JSON DATA BLOCK (type="application/json"), NOT an inline <script>.

    Why: the app's strict Content-Security-Policy sends `script-src 'self'`, which
    blocks the EXECUTION of any inline <script>. The previous inline
    `window.__MED_FORMS__ = {...}` was therefore silently blocked by the browser,
    leaving the map undefined and emptying every dose-strength/amount dropdown
    (families saw a blank free-text box for every medicine). A data block is parsed
    by the browser as text and never executed, so CSP allows it; clinic.js reads
    and JSON.parses it at startup (see loadMedForms())."""
    data = json.dumps(_med_formulations(), separators=(",", ":"))
    # Escape '<' as its JSON unicode form so no value could ever close the block
    # early (e.g. a stray "</script>"); JSON.parse restores it in the browser.
    data = data.replace("<", "\\u003c")
    return f'<script type="application/json" id="__MED_FORMS_DATA__">{data}</script>'


def _strength_from_form(one):
    """Resolve the tablet-strength value, honoring the 'Other' free-text escape.

    'one' is the per-key accessor lambda used across the form parsers. Returns a
    float or None. When the dropdown value is the '__other__' sentinel, the
    typed 'strength_other' number is used instead.
    """
    s = one("strength")
    if s == "__other__":
        s = one("strength_other")
    try:
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def _form_med(f):
    """Resolve the chosen medicine from the survey form, honoring 'Other'."""
    one = lambda k, d="": (f.get(k) or [d])[0]
    m = one("med")
    return one("med_other") if m == "other" else m


def _survey_cards(patient):
    p = patient or {}
    med_raw = (p.get("medication") or "").strip().lower()
    _med_vals = {v for v, _ in _MED_OPTS}
    if med_raw and med_raw in _med_vals and med_raw not in ("", "other"):
        med_sel, med_other_v = med_raw, ""
    elif med_raw:
        med_sel, med_other_v = "other", (p.get("medication") or "")
    else:
        med_sel, med_other_v = "", ""
    amt = p.get("dose_amount")
    amt_v = ("%g" % amt) if isinstance(amt, (int, float)) else ""
    conc = p.get("concentration")
    strength_v = ("%g" % conc) if isinstance(conc, (int, float)) else ""
    unit_v = p.get("dose_unit") or "mg"
    # Liquid strength is entered as a "mg in mL" pair (e.g. 100 mg in 5 mL). When a
    # record already holds a single mg/mL value, prefill it as "<conc> mg in 1 mL".
    _liquid_conc = unit_v == "mL" and isinstance(conc, (int, float))
    conc_mg_v = ("%g" % conc) if _liquid_conc else ""
    conc_ml_v = "1" if _liquid_conc else ""
    freq_v = str(p.get("frequency") or 2)
    # Age + weight (Barbour ask): prefill from the record when present. Weight is
    # stored in kg internally; the family enters/sees pounds, so convert kg->lb.
    age = p.get("age")
    age_v = ("%g" % age) if isinstance(age, (int, float)) else ""
    wkg = p.get("weight")
    weight_lb_v = ("%g" % round(wkg / 0.45359237, 1)) if isinstance(wkg, (int, float)) else ""
    sex_v = str(p.get("sex") or "").strip().lower()
    return f"""
<div id=progress></div>
<div style="margin-bottom:22px">
  <div style="font-size:12px;font-weight:700;letter-spacing:.12em;color:#647084;text-transform:uppercase">Between-visit check-in</div>
  <h1 style="margin:8px 0 0;font-size:30px;line-height:1.15;font-weight:700;letter-spacing:-.01em;color:#16191c">How has your child been doing?</h1>
  <p style="margin:12px 0 0;font-size:17px;line-height:1.6;color:#3a4654">Complete the following questions to send an update to your medical team.</p>
  <div class=intro-meta>
    <span>About 2-3 minutes</span>
    <span>Goes only to your care team</span>
    <span>Optional questions can be skipped</span>
  </div>
</div>
<div class=card><h2>About your child</h2>
  <div class=row>
    <div><label class=muted>Child's age (years)</label><input type=number step=any min=0 max=25 inputmode=decimal name=child_age value='{html.escape(age_v)}' placeholder="for example, 7"></div>
    <div><label class=muted>Child's sex</label><select name=sex>{opts([('', 'Prefer not to say'), ('female', 'Female'), ('male', 'Male')], sex_v)}</select></div>
    <div><label class=muted>Child's weight (lb)</label><input type=number step=any min=0 inputmode=decimal name=child_weight_lb value='{html.escape(weight_lb_v)}' placeholder="for example, 48"></div>
  </div>
  <p class=muted style='margin-top:6px'>Helps your care team review the dose. Weight in pounds (lb); we convert it for the record.</p>
</div>
<div class=card><h2>The medicine &amp; dose</h2>
  <label>1. Is your child still taking their seizure medicine at the prescribed dose?</label>
  <select name=q1>{opts([('same','Yes, the same as prescribed'),('changed','The dose has changed'),('stopped','They have stopped taking it')], 'same')}</select>
  <div id=fs-q1change class=subq>
  <label>If the dose changed or they stopped, what changed?</label>
  <input type=text name=q1_change placeholder="for example, we lowered it because of sleepiness"></div>

  <label>The medicine and dose your child is taking now</label>
  <div class=row>
    <div><label class=muted>Medicine<span class=req>*</span></label><select name=med required>{opts(_MED_OPTS, med_sel)}</select></div>
    <div><label class=muted>How is it given?</label><select name=unit>{opts([('mL','Liquid from a bottle'),('tablets','Tablet or pill'),('mg','Milligrams (mg)')], unit_v)}</select></div>
  </div>
  <div id=fs-medother class=subq><label class=muted>If you chose Other, type the medicine name</label><input type=text name=med_other value='{html.escape(med_other_v)}'></div>
  <div class=row>
    <div id=strengthbox><label class=muted id=strengthlbl>Strength of one tablet (mg)</label>
      <select name=strength id=strength_sel data-selected='{html.escape(strength_v)}'><option value=''>Choose the strength</option></select>
      <div id=fs-strength-other class=subq style='display:none'><label class=muted>Type the strength (mg per tablet)</label><input type=number step=any min=0 inputmode=decimal name=strength_other value='' placeholder="how many mg in one tablet"></div>
      <span class=muted id=strength_hint></span></div>
    <div id=concbox><label class=muted>Strength of the liquid</label>
      <select name=conc_mgml id=conc_sel data-selected='{html.escape(conc_mg_v)}'><option value=''>Choose the strength</option></select>
      <div id=fs-conc-other class=subq style='display:none'><label class=muted>Strength on the bottle (mg/mL)</label>
        <div style='display:flex;align-items:center;gap:8px'>
          <input type=number step=any min=0 inputmode=decimal name=conc_mg value='{html.escape(conc_mg_v)}' placeholder="100" style='max-width:84px' aria-label="milligrams on the bottle label">
          <span class=muted>mg /</span>
          <input type=number step=any min=0 inputmode=decimal name=conc_ml value='{html.escape(conc_ml_v)}' placeholder="5" style='max-width:84px' aria-label="millilitres on the bottle label">
          <span class=muted>mL</span>
        </div>
        <span class=muted>for example, the label says 100 mg/5 mL</span></div></div>
    <div id=amtbox><label class=muted>Amount each time <span id=amtunit class=muted></span></label>
      <select id=amt_mg_sel style='display:none'><option value=''>Choose the amount</option></select>
      <input type=number step=any min=0 inputmode=decimal name=amt value='{html.escape(amt_v)}' data-selected='{html.escape(amt_v)}'></div>
    <div><label class=muted>Times per day</label><select name=freq>{opts([('1','1'),('2','2'),('3','3'),('4','4')], freq_v)}</select></div>
  </div>
  <div id=fs-sameeach class=subq>
    <label class=muted>Is it the same amount each time?</label>
    <select name=same_each>{opts([('yes', 'Yes, the same amount each time'), ('no', 'No, the amount is different at different times of day')], 'yes')}</select>
    <div id=fs-splitamts style='display:none'>
      <p class=muted style='margin:6px 0'>How much at each dose <span class=splitunit></span></p>
      <div class=row>
        <div data-slot=1><label class=muted>Morning</label><input type=number step=any min=0 inputmode=decimal name=amt_1></div>
        <div data-slot=2><label class=muted>Night</label><input type=number step=any min=0 inputmode=decimal name=amt_2></div>
        <div data-slot=3><label class=muted>Night</label><input type=number step=any min=0 inputmode=decimal name=amt_3></div>
        <div data-slot=4><label class=muted>Night</label><input type=number step=any min=0 inputmode=decimal name=amt_4></div>
      </div>
    </div>
  </div>
  <div class=dosecalc id=dosebox><span class=dc-eyebrow>In milligrams, that is</span><strong id=mgout aria-live=polite>Fill in the amount and times per day above.</strong></div>

  <label>2. In the past week, how often did your child miss a dose?</label>
  <select name=adherence>{opts([('almost_never','Almost never misses a dose'),('sometimes','Sometimes misses a dose'),('stopped','Stopped taking it')], 'almost_never')}</select>
  <fieldset id=fs-missed><legend>If they missed doses, what was the main reason? (check any)</legend>
    {checks('q2a',[('cost_or_insurance','Cost or insurance'),('side_effects','Side effects'),('hard_to_remember','Hard to remember to take it'),('child_refuses','Child refuses to take it'),('ran_out','Ran out of medication'),('other','Other')])}
    <div id=fs-q2aother class=subq><label class=muted>If you chose Other, please describe</label><input type=text name=q2a_other placeholder="the main reason doses were missed"></div>
  </fieldset>
</div>

<div class=card><h2>Seizures</h2>
  <label>3. Since your last update, has your child had any seizures?</label>
  <select name=seizures>{opts([('no','No'),('yes','Yes')], 'no')}</select>
  <fieldset id=fs-seiz><legend>If yes</legend>
    <label>4. What did the seizure(s) look like? (check all that apply)</label>
    <p class=muted>If your child has more than one kind of seizure, check all that apply.</p>
    <div class=checks>
    {"".join(
      f"<label><input type=checkbox name=q4 value={tok}><span>{cbl}</span></label>"
      f"<div id=fs-seizd-{tok} class=subq style='display:none'>"
      f"<label>How often are the {hum} happening?</label>"
      f"<select name=freq_{tok}>{opts(_FREQ_BUCKET_OPTS)}</select>"
      f"<label>How long do they usually last?</label>"
      f"<select name=dur_{tok}>{opts(_DUR_OPTS)}</select>"
      f"<label class=muted>If you can, about how many of these in the past 4 weeks? (optional)</label>"
      f"<input type=number min=0 step=1 inputmode=numeric name=count_{tok} placeholder='e.g., 12'>"
      + (f"<label>During these, is your child unresponsive (cannot be woken or does not respond)?</label>"
         f"<select name=unresp_{tok}>{opts(_UNRESP_OPTS)}</select>"
         if tok not in ('brief_staring', 'sudden_jerks') else "")
      + "</div>"
      for tok, cbl, hum in [
        ('brief_staring', 'Brief staring or pauses', 'brief staring spells'),
        ('sudden_jerks', 'Quick jerks', 'quick jerks'),
        ('whole_body', 'Whole-body shaking or stiffening', 'whole-body shaking or stiffening'),
        ('focal', 'One part of the body shaking or twitching', 'shaking or twitching in one part of the body'),
        ('acting_off', 'Acting off or not responding appropriately, without shaking or stiffening', 'times acting off or not responding appropriately')])}
      <label><input type=checkbox name=q4 value=not_sure><span>Not sure</span></label>
      <div id=fs-seizd-not_sure class=subq style='display:none'><label class=muted>Describe what it looked like, in your own words</label><input type=text name=q_seizure_note placeholder="for example, eyes rolling up, a sudden fall, staring and unresponsive"></div>
    </div>
    <label>6. Compared with your last visit, the seizures are:</label>
    <select name=q6>{opts([('','Choose'),('fewer','Fewer'),('about_same','About the same'),('more','More frequent'),('new_type','A new type has appeared')])}</select>
    <label>7. When the seizure happened, had your child been taking their medicine as usual in the two days before it?</label>
    <select name=q7>{opts([('','Choose'),('yes','Yes'),('no','No')])}</select>
    <label>Prior to these recent seizures, were there any possible triggers? (check all that apply)</label>
    {checks('q_triggers',[('lack_of_sleep','Unusual lack of sleep the day before'),('missed_dose','Missed a dose of medication in the 2 days before a seizure'),('fever_illness','Fever or illness'),('other','Other')])}
    <label class=muted>If you chose Other, please describe</label>
    <input type=text name=q_triggers_other placeholder="the possible trigger">
    <label>If there were triggers, was one of them present before <strong>all</strong> of the breakthrough seizures?</label>
    <select name=q_triggers_all>{opts([('','Choose / not applicable'),('yes','Yes'),('no','No'),('not_sure','Not sure')])}</select>
    <label>Additional note for doctor (optional)</label>
    <input type=text name=q_seizure_doctor_note placeholder="anything about the seizures you want the doctor to know">
  </fieldset>
</div>

<div class=card id=card-dosegoal><h2>Dose goal</h2>
  <p class=muted>The "goal dose" is the target dose your care team is working toward.</p>
  <label>7. Are you still increasing the medicine toward a goal dose, or have you reached the goal dose?</label>
  <select name=q10a>{opts([('','Choose'),('still_increasing','Still increasing toward the goal dose'),('at_goal','At the goal dose'),('not_sure','Not sure')])}</select>
  <div id=fs-atgoal class=subq>
    <label>Has your child been on this goal dose for more than 2 weeks?</label>
    <select name=q10c_time>{opts([('','Choose'),('over_2_weeks','Yes'),('under_2_weeks','No')])}</select>
    <label>How is it going on this dose?</label>
    <textarea name=q10c_note rows=2 placeholder="how your child is doing now that they have reached the goal dose"></textarea>
  </div>
  <div id=fs-plannedend>
  <label>What total daily dose is your child working up to on the current plan (the goal dose)? Optional &mdash; enter the total milligrams per day if you know it.</label>
  <input type=number name=q10f_planned_end_mg min=0 step=1 placeholder="e.g. 1500 (mg per day)">
  </div>
</div>

<div class=card><h2>Side effects</h2>
  <label>8. Is your child having any side effects from the past two weeks?</label>
  <select name=se>{opts([('no','No'),('yes','Yes')], 'no')}</select>
  <fieldset id=fs-se><legend>If yes</legend>
    <label>9. Which ones? (check any)</label>
    <div class=checks>
      <label><input type=checkbox name=q9 value=sleepiness><span>Sleepiness or tiredness</span></label>
      <label><input type=checkbox name=q9 value=irritability><span>Irritability or mood changes</span></label>
      <label><input type=checkbox name=q9 value=behavior><span>Behavior changes</span></label>
      <label><input type=checkbox name=q9 value=acting_confused><span>Acting confused or "off"</span></label>
      <label><input type=checkbox name=q9 value=dizziness><span>Dizziness or unsteadiness</span></label>
      <label><input type=checkbox name=q9 value=rash><span>Rash</span></label>
      <div id=fs-rash style='display:none;margin-top:10px;background:#fdeceb;border:1px solid #f3b7b2;border-radius:8px;padding:14px 16px'>
        <label style='color:#8a2018;margin-top:0'>How significant is the rash?</label>
        <select name=q9a>{opts([('','Choose'),('small','It is a small rash'),('large','It is a large rash'),('blisters','It is causing blisters')])}</select>
        <!-- Serious-rash (Stevens-Johnson) workup follow-ups, T11/R4 (Barbour
             case H6). Previously computed in engine_core but never asked here -
             southeast_asian_ancestry / concomitant meds / recent viral illness
             always defaulted to unknown, so this logic could never fire on a
             real submission. Asked whenever Rash is checked (not gated to
             "small" specifically) since it's simplest and harmless for the
             large/blister cases too (those are already handled by the
             hard-stop urgent rule regardless of these answers). -->
        <label style='color:#8a2018;margin-top:10px'>Is your child of Southeast or East Asian ancestry? (relevant for rash risk with certain medicines)</label>
        <select name=q_ancestry>{opts([('','Prefer not to say / not sure'),('yes','Yes'),('no','No')])}</select>
        <label style='color:#8a2018;margin-top:10px'>Besides this seizure medicine, is your child on any OTHER seizure medications? (check any - helps check for interactions)</label>
        {checks('q_other_asms', [(m, m.replace('_',' ').title()) for m in MEDS])}
        <label style='color:#8a2018;margin-top:10px'>Is your child currently on any of these? (check any - these are specifically linked to serious rash reactions)</label>
        {checks('q_sjs_risk_comeds', [('antibiotic','An antibiotic (including a sulfa drug, penicillin/amoxicillin, or a cephalosporin)'),('nsaid','Ibuprofen or another NSAID pain reliever'),('allopurinol','Allopurinol (a gout medication)')])}
        <label style='color:#8a2018;margin-top:10px'>Any other regular medications not listed above? List them, separated by commas.</label>
        <input type=text name=q_other_meds_text placeholder="e.g. melatonin, an inhaler">
        <label style='color:#8a2018;margin-top:10px'>Has your child had a cold, fever, or other viral illness in the past few weeks?</label>
        <select name=q_viral_illness>{opts([('','Not sure'),('yes','Yes'),('no','No')])}</select>
      </div>
      <label><input type=checkbox name=q9 value=nausea><span>Nausea or appetite change</span></label>
      <label><input type=checkbox name=q9 value=other><span>Other</span></label>
      <div id=fs-q9other class=subq style='display:none'><label class=muted>If you chose Other, please describe the side effect</label><input type=text name=q9_other placeholder="for example, headaches, stomach aches"></div>
    </div>
    <label>10. Children can have more side effects right after a dose increase. After about 5 to 10 days on a new dose, how much are the side effects affecting your child's day?</label>
    <select name=impact>{opts(_IMPACT_OPTS)}</select>
  </fieldset>
</div>

<div class=card id=card-pref style='display:none'><h2>A few more questions</h2>
  {_dosing_choice_block(p)}
  <div id=fs-pref-adjust style='display:none'>
    <p style='font-size:16px;line-height:1.6;color:#3a4654'>Side effects can be the most pronounced immediately after a dose increase and improve as the body adjusts to the medicine. From your observations, do you think your child is showing an adjustment to the medication after being on a stable dose for 1 week, and side effects are manageable after this 1 week adjustment period?</p>
    <select name=q_adjust>{opts([('','Choose'),('manageable','Yes, side effects are manageable'),('too_much','No, side effects are having too much of an impact on daily life and I would prefer avoiding more dose increases')])}</select>
  </div>
</div>

<div class=card><h2>Overall</h2>
  <label>11. Overall, how have your child's seizures changed on this medicine?<span class=req>*</span></label>
  <select name=q11 required>{opts([('','Choose'),('better','Better'),('about_same','About the same'),('worse','Worse'),('not_sure','Not sure')])}</select>
  <div id=fs-conflict class=subq style='display:none'><label class=muted>You said things are <strong>better overall</strong> but also <strong>more frequent</strong> seizures - can you tell us a little more so the team understands?</label><input type=text name=q_conflict placeholder="for example, shorter but more often"></div>
  <label>12. Anything else you would like the care team to know? (optional)</label>
  <textarea name=q13 rows=3></textarea>
  <button class='btn cta big' type=submit>Send to my care team</button>
  <p class=muted style='text-align:center;margin-top:10px'>Your care team reviews every answer before anything is decided.</p>
</div>"""


def patient_survey_form(token, patient):
    csrf = csrf_for(token)
    return page("Between-visit check-in",
                f"<form method=post action='/s/{html.escape(token)}'>"
                f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
                f"{_survey_cards(patient)}</form>",
                heading="Between-Visit Check-In")


def open_survey_form(err=""):
    """Self-serve survey: a family can fill it with no pre-existing record.
    They enter their study ID; only the survey data is sent to the doctor."""
    csrf = csrf_for("open")
    banner = f"<div class='banner review'>{html.escape(err)}</div>" if err else ""
    top = ("<div class=card><h2>Between-visit check-in</h2>"
           "<p class=lead>This quick check-in lets your care team know how your child has been "
           "doing on their seizure medicine since your last visit.</p>"
           "<label>Your study ID (from the clinic)</label>"
           "<input type=text name=study_id required placeholder='for example EPIL-0123'></div>")
    return page("Between-visit check-in",
                f"{banner}<form method=post action='/survey'>"
                f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
                f"{top}{_survey_cards({})}</form>",
                heading="Between-Visit Check-In")


# ---------------------------------------------------------------------------
# Medication-SELECTION flow (switch / add-a-second-medicine): WHICH drug.
# Rule-based (engine_core.med_selection); every option is a DRAFT for the
# neurologist. Pages are JS-FREE (plain forms + <select>s) to stay within the
# app's strict CSP (no inline <script>/on*= handlers).
# ---------------------------------------------------------------------------

SELECTION_RULESET = load_selection_ruleset()

# Sheet-2 comorbidity questions the between-visit survey does not already capture.
_SELECTION_COMORBIDITIES = [
    ("kidney_disease", "Kidney disease or reduced kidney function"),
    ("kidney_stones", "Kidney stones"),
    ("liver_disease", "Liver disease"),
    ("diabetes", "Diabetes"),
    ("obesity", "Overweight or obesity"),
    ("heart_issue", "Heart disease or a heart-rhythm problem"),
    ("bleeding_disorder", "A bleeding disorder"),
    ("sulfa_allergy", "Allergy to sulfa drugs"),
    ("prior_chemo", "Previous chemotherapy treatment"),
]


def _current_med_selection_id(cur):
    """Resolve a stored medication string to a med_selection ruleset id, so the
    current medicine can be excluded from BOTH the 'already tried' checklist and
    the suggested switch/add options (never offer / never re-ask about the drug
    the child is already on). Handles canonical ids ('valproic_acid'), plain
    names ('Lamotrigine'), and the 'Name (Brand)' display form
    ('Valproic acid (Depakote)') that DrugClassifier does not itself normalize.
    Returns '' when it cannot be mapped."""
    if not cur:
        return ""
    raw = str(cur).strip()
    base = raw.split("(")[0].strip()          # drop any "(Brand)" parenthetical
    ids = SELECTION_RULESET.med_ids
    cands = []
    for s in (raw, base):
        if not s:
            continue
        try:
            can = (CLASSIFIER.normalize(s).canonical or "").strip().lower()
        except Exception:
            can = ""
        if can:
            cands.append(can)
        cands.append(s.lower())
    for c in cands:
        if c in ids:
            return c
    for m in SELECTION_RULESET.medications:      # last resort: match display name
        dn = m.display_name.strip().lower()
        if raw.lower() == dn or base.lower() == dn.split("(")[0].strip():
            return m.id
    return ""


def _selection_med_checklist(exclude_id=None):
    """(id, display_name) in Sheet-1 preference order for the 'already tried'
    list, omitting the current medication (exclude_id) - the child is already on
    it, so it is neither a 'have you tried?' choice nor a switch/add option."""
    return [(m.id, m.display_name) for m in SELECTION_RULESET.medications
            if m.id != exclude_id]


def phase2_applies(rec_dict):
    """Show medication options only for switch/add when no safety flag is set.
    This gate does not inspect the queue urgency tier; safety_concern_flag is
    the explicit condition that withholds the family-facing path."""
    return (rec_dict.get("category") in ("switch_medication", "add_medication")
            and not rec_dict.get("safety_concern_flag"))


def _carried_over(patient, answers):
    """Selection inputs already known from intake / the check-in - reused, not
    re-asked (per the tool design: 'some were already asked earlier')."""
    q4 = answers.get("Q4") or []
    adh = answers.get("Q2")
    anc = answers.get("Q_ancestry")
    sex = (patient or {}).get("sex")
    # Prefer the age the family entered on the check-in (like _age_weight), else
    # fall back to the intake record.
    age = answers.get("child_age_years")
    if not isinstance(age, (int, float)):
        age = (patient or {}).get("age")
    return {
        "age": int(age) if isinstance(age, (int, float)) else None,
        "sex": (str(sex).strip().lower() if sex else None),
        "staring_seizures": "brief_staring" in q4,
        "whole_body_single_jerk": "sudden_jerks" in q4,
        # Conservative DRAFT mapping: only a STOPPED medicine counts as poor
        # adherence for the avoid rule (threshold pending PI - NEEDS_PI_SIGNOFF #41).
        "med_nonadherence": (adh == "stopped"),
        "se_asian_han_chinese": (anc == "yes"),
        "_ancestry_asked": anc in ("yes", "no"),
    }


def _info_table_html(opt):
    """Sheet-1 attribute table shown to the family for one medicine (JS-free)."""
    a = opt.get("attributes") or {}
    rows = [("Effect on mood", a.get("mood_effect", "")),
            ("Effect on sleep", a.get("sleep_effect", "")),
            ("Blood tests needed", a.get("labs", "")),
            ("Once-a-day option", a.get("once_daily_option", ""))]
    trs = "".join(
        "<tr><th style='text-align:left;padding:4px 10px 4px 0;vertical-align:top'>"
        f"{html.escape(k)}</th>"
        "<td style='padding:4px 0;border-top:1px solid #e8eaed'>"
        f"{html.escape(v)}</td></tr>" for k, v in rows)
    return (f"<table style='border-collapse:collapse;width:100%;margin:6px 0'>{trs}</table>"
            f"<p class=muted>{html.escape(opt.get('quick_summary', ''))}</p>")


def _dosing_choice_block(patient):
    """Level-3 side effects: the FAMILY chooses how to handle them (Dr. Barbour
    2026-10-25, #52). Wording from her template H. Uneven, three-times-a-day and
    watch-and-wait are offered for any medicine and in any phase (her decision-table
    L3 cells list all three); 'lower the dose and add a second medication' only shows
    with breakthrough seizures, because only those cells list it - and the engine acts
    on it only where the matching cell does (decision.py _dosing_plan). The answer is
    a structured survey field (Q10g_dosing_choice) the decision tables read."""
    return (
        "<div id=fs-dosing style='display:none'>"
        "<p style='font-size:16px;line-height:1.6;color:#3a4654'>We have a few options to handle "
        "side effects. Here are some options, in no particular order.</p>"
        "<ul style='font-size:15px;line-height:1.6;color:#3a4654'>"
        "<li data-opt=uneven><strong>Uneven dosing.</strong> Sometimes it is helpful to have a smaller dose in the "
        "morning and larger dose in the night-time if you are concerned about the day-time side "
        "effects.</li>"
        "<li data-opt=tid><strong>Three times a day.</strong> Sometimes it is helpful to give the medication in "
        "smaller doses three times a day, instead of twice a day. This is not my favorite, because "
        "three times a day is a lot to remember. It seems to be most helpful for reducing stomach "
        "upset (when taking valproic acid and ethosuximide) or reducing dizziness (when taking "
        "oxcarbazepine or carbamazepine).</li>"
        "<li><strong>Watch and wait.</strong> Sometimes, giving the body more time to adjust is all "
        "that is needed.</li>"
        "<li data-opt=add><strong>Lower the dose and add a second medication.</strong> Sometimes two "
        "medications, at lower doses each, is a better fit for both seizures and side effects.</li>"
        "</ul>"
        "<label>Which would you prefer?</label>"
        "<select name=q_dosing_choice>"
        "<option value=''>Choose</option>"
        "<option value=uneven data-opt=uneven>Uneven dosing</option>"
        "<option value=tid data-opt=tid>Three times a day dosing</option>"
        "<option value=wait>Watch and wait</option>"
        "<option value=add data-opt=add>Lower the dose and add a second medication</option>"
        "</select></div>")


def _medsel_detail_fields(patient):
    """The medication-SELECTION questions (Sheet 2). Age/sex/seizure-type/adherence
    are reused from the check-in, so they are not re-asked here."""
    yn = lambda name: f"<select name={name}>{opts([('no', 'No'), ('yes', 'Yes')])}</select>"
    # Exclude the child's CURRENT medicine from the 'already tried' checklist -
    # they are on it, so it is not a "have you tried?" choice (and won't be
    # offered as a switch/add option). See _current_med_selection_id.
    cur_id = _current_med_selection_id((patient or {}).get("medication"))
    cur_med = SELECTION_RULESET.med_by_id.get(cur_id)
    cur_note = (f"<p class=muted>Your child's current medicine "
                f"({html.escape(cur_med.display_name)}) isn't listed here.</p>"
                if cur_med else "")
    return (
        "<label>Has your child already tried any of these other seizure medicines? "
        "(check all that apply)</label>"
        f"{checks('prior_tried', _selection_med_checklist(exclude_id=cur_id))}"
        f"{cur_note}"
        "<label>Is it important to you to AVOID a medicine that needs regular blood tests?</label>"
        f"{yn('prefers_no_labs')}"
        "<label>Does your child have difficulty with mood - feeling down, nervous/on edge, "
        "anxious, or irritable?</label>"
        f"{yn('mood_difficulties')}"
        "<label>Does your child have difficulty with behavior - calming down when upset, or "
        "aggressive behavior?</label>"
        f"{yn('behavioral_difficulties')}"
        "<label>Does your child have trouble sleeping at night?</label>"
        f"{yn('trouble_sleeping')}"
        "<label>Does your child have frequent migraines or significant headaches?</label>"
        f"{yn('migraines')}"
        "<label>Is your child of Southeast Asian or Han Chinese ancestry? (affects the risk of a "
        "rare rash with some medicines)</label>"
        f"{yn('se_asian_han_chinese')}"
        "<label>Does your child have any of these health conditions? (check all that apply)</label>"
        f"{checks('comorbid', _SELECTION_COMORBIDITIES)}")


def medsel_questions_page(token, sub_id, patient):
    """Second page of the check-in (Dr. Barbour 2026-10-25): the medicine-options
    questions appear ONLY when the engine's decision is to switch or add a medicine
    (phase2_applies), after the main check-in is saved. Families can skip it."""
    csrf = csrf_for(token)
    body = ("<div class=card><h2>A few questions about medicine options</h2>"
            "<p class=lead>Thanks - your check-in is saved. Your care team may talk with you about "
            "changing or adding a seizure medicine. A few quick answers let us suggest the options "
            "most likely to suit your child. Your care team decides with you.</p></div>"
            f"<form method=post action='/s/{html.escape(token)}/medsel'>"
            f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
            f"<input type=hidden name=sub value='{int(sub_id)}'>"
            f"<div class=card id=card-medsel>{_medsel_detail_fields(patient)}</div>"
            "<div class=card><button class='btn cta big' type=submit>Show me the options</button> "
            "<button class=btn type=submit name=skip value=1>Skip for now</button></div>"
            "</form>")
    return page("Between-visit check-in", body, heading="Between-Visit Check-In")


def _selection_attrs_from_form(patient, answers, f):
    """Build the med_selection input from the ONE survey submission: reuse
    age/sex/seizure-type/adherence from the check-in (`_carried_over`) and read
    the selection card's own fields. No separate questionnaire."""
    co = _carried_over(patient, answers)
    one = lambda k, d="": (f.get(k) or [d])[0]
    many = lambda k: f.get(k, [])
    yes = lambda k: one(k) == "yes"
    # The current medication counts as "already tried": never switch/add TO the
    # drug the child is already on. Resolve it ROBUSTLY (handles the 'Name (Brand)'
    # display form the classifier alone does not normalize) so the exclusion can't
    # silently miss - a stored "Lamotrigine (Lamictal)" previously failed to map to
    # a ruleset id and the current medicine leaked back into the options. Also keeps
    # the treatment-naive brivaracetam combination rule from firing for an already-
    # treated patient (a switch/add patient is, by definition, not first-line).
    tried = set(many("prior_tried"))
    cur_id = _current_med_selection_id((patient or {}).get("medication"))
    if cur_id:
        tried.add(cur_id)
    attrs = {
        "age": co["age"],
        "sex": ((one("sex") or "").strip().lower() or co["sex"]),   # prefer the family's entry
        "staring_seizures": co["staring_seizures"],
        "whole_body_single_jerk": co["whole_body_single_jerk"],
        "med_nonadherence": co["med_nonadherence"],
        "prior_tried": sorted(tried),
        "se_asian_han_chinese": co["se_asian_han_chinese"] or yes("se_asian_han_chinese"),
        "prefers_no_labs": yes("prefers_no_labs"),
        "mood_difficulties": yes("mood_difficulties"),
        "behavioral_difficulties": yes("behavioral_difficulties"),
        "trouble_sleeping": yes("trouble_sleeping"),
        "migraines": yes("migraines"),
    }
    picked = set(many("comorbid"))
    for cid, _label in _SELECTION_COMORBIDITIES:
        attrs[cid] = cid in picked
    return attrs


def med_options_result(token, sub_id, result):
    """Phase 2 (family): show the top-3 + info tables and capture 1st/2nd/3rd/avoid."""
    csrf = csrf_for(token)
    options = result.get("options") or []
    rank_choices = [('', 'No preference'), ('first', 'This is my 1st choice'),
                    ('second', 'This is my 2nd choice'), ('third', 'This is my 3rd choice'),
                    ('avoid', 'I would like to avoid this medicine')]
    cards = ""
    for o in options:
        cards += (f"<div class=card><h3>{html.escape(o['display_name'])}</h3>"
                  "<span class='pill review'>DRAFT &middot; your care team decides</span>"
                  f"{_info_table_html(o)}"
                  "<label>Your preference for this medicine</label>"
                  f"<select name='rank_{html.escape(o['id'])}'>{opts(rank_choices)}</select>"
                  "</div>")
    note = ""
    if result.get("deferred") or not options:
        note = ("<div class='banner review'>Based on your answers, only "
                f"{len(options)} option(s) clearly fit right now; your care team will discuss "
                "any further choices with you directly.</div>")
    body = ("<div class=card><h2>Your medication options</h2>"
            "<p class=lead>Thanks - your check-in is saved. Based on the answers you just gave, "
            "these are the medicines that best fit your child's situation, most recommended "
            "first. For each one, tell us your preference. Your care team reviews everything "
            "before anything is decided.</p></div>"
            f"{note}"
            f"<form method=post action='/s/{html.escape(token)}/rank'>"
            f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
            f"<input type=hidden name=sub value='{int(sub_id)}'>"
            f"{cards}"
            "<div class=card><button class=btn type=submit>Send my preferences to my care team</button></div>"
            "</form>")
    # Same masthead as the survey so this reads as the next step of the check-in,
    # not a separate screen.
    return page("Between-visit check-in", body, heading="Between-Visit Check-In")


def _new_med_comeds(current_med, answers):
    """Co-medications for a NEW medicine's starting schedule: the family's listed
    seizure medicines plus the child's current one (raw and canonical name, so the
    valproate / enzyme-inducer match works for 'Depakote' or 'valproic_acid')."""
    from engine_core import initiation
    cur = str(current_med or "").strip()
    names = initiation.comeds_while_starting(_ancestry_and_conmeds(answers)[1], cur)
    try:
        can = (CLASSIFIER.normalize(cur).canonical or "") if cur else ""
    except Exception:
        can = ""
    return initiation.comeds_while_starting(names, can)


def _starting_schedule_html(drug_id, age, weight, conmeds):
    """Collapsed per-option view of Dr. Barbour's starting schedule (her v9
    '<Drug> Initiation' tab, cells verbatim) for this child's band. Display only."""
    from engine_core import initiation
    s = initiation.schedule_for(drug_id, age, weight, conmeds)
    if not s.get("available"):
        body = f"<p class=muted>{html.escape(s.get('reason') or '')}</p>"
    else:
        body = ""
        for f in s["forms"]:
            pref = " <span class=muted>(preferred)</span>" if f["kind"] == s.get("preferred_kind") else ""
            head = "".join(f"<th style='padding:2px 8px;text-align:left'>{html.escape(c or '')}</th>"
                           for c in f["columns"])
            cells = "".join(f"<td style='padding:2px 8px'>{html.escape(v or '')}</td>" for v in f["cells"])
            body += (f"<p style='margin:6px 0 2px'><strong>{html.escape(f['form_title'] or 'Starting dose')}"
                     f"</strong> &middot; {html.escape(f['band_label'] or '')}{pref}</p>"
                     f"<div style='overflow-x:auto'><table style='border-collapse:collapse'>"
                     f"<tr>{head}</tr><tr>{cells}</tr></table></div>")
        body += ("<p class=notetag>&#9888; Dr. Barbour's starting schedule (DRAFT, unverified). "
                 "When switching, the current medicine is reduced week by week and stopped after "
                 "week 4 while this one starts; your care team goes over the plan with you.</p>")
    return (f"<details style='margin:2px 0 8px'><summary>Starting schedule</summary>{body}</details>")


def med_selection_review_html(ms, child=None):
    """Doctor-facing DRAFT card: rule-based top-3 + the family's ranking. With
    `child` = (age, weight_kg, concomitant_asms), each option also shows its
    starting schedule for this child's band."""
    if not ms:
        return ""
    er = ms.get("engine_result") or {}
    options = er.get("options") or []
    ranking = ms.get("family_ranking") or {}
    rank_h = {"first": "1st choice", "second": "2nd choice",
              "third": "3rd choice", "avoid": "AVOID"}
    rows = ""
    for o in options:
        fam = rank_h.get(ranking.get(o["id"]), "-")
        rows += (f"<tr><td style='padding:3px 10px'>{o['rank']}</td>"
                 f"<td style='padding:3px 10px'>{html.escape(o['display_name'])}</td>"
                 f"<td style='padding:3px 10px'>{o.get('favor_count', 0)}</td>"
                 f"<td style='padding:3px 10px'><strong>{html.escape(fam)}</strong></td></tr>")
        if child is not None:
            rows += (f"<tr><td></td><td colspan=3 style='padding:0 10px'>"
                     f"{_starting_schedule_html(o['id'], *child)}</td></tr>")
    defer = (f"<p class='banner review'>{html.escape(er.get('defer_reason') or '')}</p>"
             if er.get("deferred") else "")
    fam_avoids = [mid for mid, v in ranking.items() if v == "avoid"]
    fam_note = (f"<p class=muted>Family asked to avoid: {html.escape(', '.join(fam_avoids))}</p>"
                if fam_avoids else "")
    ranked_any = bool(ranking)
    fam_status = "" if ranked_any else "<p class=muted>Family has not submitted preferences yet.</p>"
    return (
        "<div class=card><h2>Medication options (DRAFT)</h2>"
        f"{defer}"
        "<table style='border-collapse:collapse;margin:8px 0'>"
        "<tr><th style='padding:3px 10px;text-align:left'>Rank</th>"
        "<th style='padding:3px 10px;text-align:left'>Medicine</th>"
        "<th style='padding:3px 10px;text-align:left'>Favor signals</th>"
        "<th style='padding:3px 10px;text-align:left'>Family preference</th></tr>"
        f"{rows}</table>{fam_note}{fam_status}</div>")


def parse_answers(f):
    one = lambda k, d="": (f.get(k) or [d])[0]
    many = lambda k: f.get(k, [])
    adherence = one("adherence", "almost_never")
    if one("q1") == "stopped":
        adherence = "stopped"

    # Q2a missed-dose reasons: replace the bare "other" token with the typed reason.
    q2a = list(many("q2a"))
    q2a_other = one("q2a_other").strip()
    if "other" in q2a and q2a_other:
        q2a = [q2a_other if r == "other" else r for r in q2a]

    free_text = one("q13") or None
    conflict = one("q_conflict").strip()
    if conflict:   # "better overall + more seizures" clarification (in-survey)
        free_text = ((free_text + "\n") if free_text else "") + "[Better-overall vs more-seizures] " + conflict
    q1_change_note = one("q1_change").strip()
    if q1_change_note:   # "what changed" when dose changed/stopped - was previously dropped, never read
        free_text = ((free_text + "\n") if free_text else "") + "[Reason dose changed/stopped] " + q1_change_note

    # Age + weight (Barbour ask): captured on the family form. Weight is entered
    # in POUNDS and converted to kg for the engine/record. These are patient
    # facts, not survey answers - survey_from_answers ignores these keys, and
    # build_*_snapshot reads them to populate PatientContext (falling back to the
    # record when blank).
    def _num(k):
        v = one(k).strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None
    child_age = _num("child_age")
    weight_lb = _num("child_weight_lb")
    child_weight_kg = round(weight_lb * 0.45359237, 1) if weight_lb else None

    a = {"Q2": adherence, "Q2a": q2a, "Q3": one("seizures", "no"),
         "Q11": one("q11") or None,
         "Q13": free_text,
         "child_age_years": child_age, "child_weight_lb": weight_lb,
         "child_weight_kg": child_weight_kg}
    # Q12 preference + 12a/12b ranking removed from the survey (Kristen 2026-10-15:
    # "Remove the option asking patients if they prefer increasing or switching");
    # the engine treats an unanswered Q12 as no strong preference.

    if a["Q3"] == "yes":
        q4 = list(many("q4"))                      # seizure types (check all that apply)
        q4_other = one("q4_other").strip()
        if "other" in q4 and q4_other:             # replace bare "other" with what the family typed
            q4 = [q4_other if t == "other" else t for t in q4]
        a["Q4"] = q4
        a.update({"Q6": one("q6") or None, "Q7": one("q7") or None})
        # Per-seizure-type episodes (design): each checked type carries its own
        # frequency bucket + duration + unresponsiveness, so the engine's per-type
        # logic (incl. the whole-body >3-min danger rule) applies per type.
        eps = []
        for tok in ("brief_staring", "sudden_jerks", "whole_body", "focal", "acting_off"):
            if tok not in q4:
                continue
            u = one(f"unresp_{tok}")
            ep = {"look": [tok]}
            if one(f"dur_{tok}"):
                ep["duration"] = one(f"dur_{tok}")
            if one(f"freq_{tok}"):
                ep["frequency_bucket"] = one(f"freq_{tok}")
            cnt = one(f"count_{tok}").strip()
            if cnt:
                try:
                    ep["count_4wk"] = float(cnt)   # exact per-4-week count (precise urgency)
                except ValueError:
                    pass
            ep["unresponsive"] = True if u == "yes" else False if u == "no" else None
            eps.append(ep)
        if eps:
            a["seizure_episodes"] = eps
        # Possible triggers before the breakthrough seizures (Barbour 2026-06-25):
        # sleep / missed dose / fever-illness / other, plus whether a trigger was
        # present before ALL of them.
        trig = list(many("q_triggers"))
        trig_other = one("q_triggers_other").strip()
        if "other" in trig and trig_other:
            trig = [trig_other if t == "other" else t for t in trig]
        if trig:
            a["seizure_triggers"] = trig
            a["seizure_triggers_all"] = one("q_triggers_all") or None
        # Fold the free-text seizure notes into Q13 for the clinician (advisory).
        for fld, tag in (("q_seizure_note", "Seizure description"),
                         ("q_seizure_doctor_note", "Note for doctor")):
            v = one(fld).strip()
            if v:
                free_text = ((free_text + "\n") if free_text else "") + f"[{tag}] {v}"
        a["Q13"] = free_text

    impact = None
    if one("se") == "yes":
        q9 = many("q9") or ["other"]
        a["Q9"] = q9
        a["Q9_other"] = one("q9_other").strip() or None   # "Other" side-effect free text (Barbour 2026-06)
        if "rash" in q9:                       # Q9a only when Rash is checked
            a["Q9a"] = one("q9a") or None
            # SJS small-rash workup follow-ups (T11/R4) - see the matching
            # comment on the fs-rash form fields above for why these were
            # added; without them, engine_core's workup logic always saw
            # unknown/empty values and could never actually fire.
            anc = one("q_ancestry")
            a["Q_ancestry"] = anc if anc in ("yes", "no") else None
            # Actual medication names (not just a count) - lets the small-rash
            # workup criterion be based on real co-prescriptions, and gives
            # the engine names to eventually cross-check against
            # key_interactions text (Barbour B3: "avoid combining too many
            # sedating medications", "prior medications tried").
            a["Q_other_asms"] = list(many("q_other_asms"))
            # Specific SJS/TEN-associated drug classes (antibiotics - esp.
            # sulfonamides/penicillins/cephalosporins, NSAIDs, allopurinol) -
            # asked explicitly rather than relying on free text, since a
            # family is far more likely to check a labeled box than think to
            # type "amoxicillin" into a generic "any other meds" field. Feeds
            # a dedicated workup criterion (on_known_sjs_risk_comed) AND still
            # counts toward the general other-meds total below.
            sjs_comeds = list(many("q_sjs_risk_comeds"))
            a["Q_sjs_risk_comeds"] = sjs_comeds
            other_text = one("q_other_meds_text").strip()
            other_list = [m.strip() for m in other_text.split(",") if m.strip()] if other_text else []
            a["Q_other_meds_text"] = sjs_comeds + other_list
            vi = one("q_viral_illness")
            a["Q14_viral_illness"] = vi if vi in ("yes", "no") else None
        impact = one("impact") or None         # side-effect impact drives severity
        if impact:
            a["Q10b_impact"] = impact

    # Dose goal is its own card (design), always asked - it drives the at-goal vs
    # still-titrating branches regardless of side-effect impact.
    a["Q10a"] = one("q10a") or "not_sure"
    if a["Q10a"] == "at_goal":                 # >2-week window + how it's going
        a["Q10c_time"] = one("q10c_time") or None
        note = one("q10c_note").strip()
        if note:                               # surface the free-text update to the clinician
            a["Q13"] = ((a.get("Q13") + "\n") if a.get("Q13") else "") + "[Goal-dose update] " + note

    # B3 (Kristen 2026-07-16): the dose the family is building up to (total mg/day),
    # used to compute "end +1/+2 higher" on urgent/emergent titrating plans. Optional.
    # Not asked once the child is AT the goal dose (hidden in the survey); ignore a
    # value left over from before the family switched their answer to at_goal.
    pe = one("q10f_planned_end_mg").strip()
    if pe and one("q10a") != "at_goal":
        try:
            a["Q10f_planned_end_mg_per_day"] = float(pe.replace(",", ""))
        except ValueError:
            pass

    # Level-3 choice (Dr. Barbour 2026-10-25, #52): a structured answer the decision
    # tables read; the summary shows it via the dosing note.
    dc = one("q_dosing_choice").strip().lower()
    if dc in ("uneven", "tid", "wait", "add"):
        a["Q10g_dosing_choice"] = dc
    # Contextual preference answers (design): surface to the clinician.
    for fld, tag in (("q_adjust", "1-week adjustment"),):
        v = one(fld).strip()
        if v:
            a["Q13"] = ((a.get("Q13") + "\n") if a.get("Q13") else "") + f"[{tag}] {v}"

    return a


# ===========================================================================
# Request handler
# ===========================================================================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # -- helpers --
    def _session(self):
        """Return the verified session value, or None. Rejects sessions whose
        embedded issued-at timestamp is older than SESSION_MAX_AGE, so a
        captured/forged-but-valid-signature cookie eventually expires. (S4)"""
        c = SimpleCookie(self.headers.get("Cookie", ""))
        if "sess" not in c:
            return None
        val = unsign(c["sess"].value)
        if not val:
            return None
        # Session value format: "doctor:<user>:<issued_at_epoch>". Reject if the
        # timestamp is missing/malformed or older than the max age.
        parts = val.split(":")
        if len(parts) < 3:
            return None
        try:
            issued_at = int(parts[-1])
        except ValueError:
            return None
        if (time.time() - issued_at) > SESSION_MAX_AGE:
            return None
        return val

    def _doc_csrf(self):
        """CSRF token bound to the live doctor session value, so a token is only
        valid for the exact session it was issued to (S6). Falls back to the
        static "doctor" key if there's no session (e.g. token embedded before
        login completes) so forms still render safely."""
        return csrf_for(self._session() or "doctor")

    def _doc_csrf_ok(self, f):
        """Validate a doctor-side CSRF token against the live session value,
        accepting the static "doctor" fallback used when no session exists."""
        token = (f.get("csrf") or [""])[0]
        return csrf_ok(self._session() or "doctor", token) or csrf_ok("doctor", token)

    def _security_headers(self):
        """Baseline hardening headers on every response (S7). The CSP allows the
        app's external /static/clinic.css + /static/clinic.js, inline style=""
        attributes, and data: images, so it won't break the UI (no inline
        <script> blocks exist in the templates). HSTS only when serving TLS."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self'; img-src 'self' data:; object-src 'none'; "
                         "base-uri 'none'; frame-ancestors 'none'")
        if COOKIE_SECURE:
            self.send_header("Strict-Transport-Security",
                             "max-age=31536000; includeSubDomains")

    def _send(self, body, code=200, set_cookie=None, location=None):
        data = body.encode("utf-8") if body else b""
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Never let the browser show a stale page (avoids "I changed it but see the old one").
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        # Content-Length frames the body exactly. Without it, a keep-alive
        # connection has no body boundary and the browser can render the NEXT
        # response's raw headers as trailing text at the foot of the page.
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def _form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(n).decode("utf-8")) if n else {}

    def _send_static(self, path):
        """Serve the CSS/JS assets (presentation layer) from the static/ folder."""
        name = os.path.basename(path)          # no directory traversal
        ctype = {"css": "text/css", "js": "application/javascript"}.get(
            name.rsplit(".", 1)[-1], "text/plain")
        fp = os.path.join(_APP, "static", name)
        if not (name and os.path.isfile(fp)):
            return self._send(page("Not found", "<div class=card>Asset not found.</div>"), 404)
        with open(fp, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _is_doctor(self):
        s = self._session()
        return bool(s and s.startswith("doctor:"))

    # -- GET --
    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/static/"):
            return self._send_static(path)
        if path == "/login":
            return self._send(self._login_form())
        if path == "/logout":
            return self._send("", 303, set_cookie=session_cookie("", max_age=0), location="/login")
        if path == "/survey":
            return self._send(open_survey_form())
        if path.startswith("/s/"):
            return self._patient_survey(path[3:])
        if path.startswith("/m/"):
            return self._patient_message(path[3:])
        # doctor area
        if not self._is_doctor():
            return self._send("", 303, location="/login")
        if path == "/":
            return self._queue()
        if path == "/patients/new":
            return self._send(self._patient_intake_form())
        if path == "/audit":
            return self._audit()
        if path == "/nlp-audit":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            return self._nlp_audit(search_patient=(qs.get("patient") or [""])[0])
        if path == "/change-password":
            return self._send(self._change_pw_form())
        if path == "/settings":
            return self._send(self._settings_form())
        if path == "/validation":
            return self._validation_view()
        if path.startswith("/review/"):
            return self._review_view(path.split("/")[2])
        return self._send(page("Not found", "<div class=card>Not found. <a href='/'>Home</a></div>"), 404)

    # -- POST --
    def do_POST(self):
        path = self.path.split("?")[0]
        f = self._form()
        if path == "/login":
            return self._do_login(f)
        if path == "/survey":
            return self._do_open_submit(f)
        if path.startswith("/s/"):
            parts = path[3:].split("/")
            tok = parts[0]
            action = parts[1] if len(parts) > 1 else ""
            if action == "rank":
                return self._do_med_rank(tok, f)
            if action == "medsel":
                return self._do_medsel(tok, f)
            return self._do_submit(tok, f)
        if not self._is_doctor():
            return self._send("", 303, location="/login")
        if path == "/patients":
            return self._do_add_patient(f)
        if path.startswith("/patients/") and path.endswith("/invite"):
            return self._do_invite(path.split("/")[2])
        if path == "/change-password":
            return self._do_change_pw(f)
        if path == "/settings/asm":
            return self._do_add_band(f)
        if path == "/settings/med-edit":
            return self._do_med_edit(f)
        if path == "/settings/redflag":
            return self._do_add_redflag(f)
        if path == "/validation":
            return self._do_validation(f)
        if path == "/bulk-triage":
            return self._do_bulk_triage(f)
        if path.startswith("/review/"):
            return self._do_review(path.split("/")[2], f)
        return self._send(page("Not found", "<div class=card>Not found.</div>"), 404)

    def _do_bulk_triage(self, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        n = 0
        for it in db.review_queue():
            if it.get("decision") or (it.get("priority") or "routine") != "routine":
                continue
            b = db.get_submission_bundle(it["sub_id"])
            if b and b.get("recommendation"):
                db.add_review(b["recommendation"]["id"], "triaged",
                              "bulk-triaged (routine)", None, "doctor")
                db.mark_reviewed(b["submission"]["instance_id"])
                n += 1
        db.audit("doctor", "bulk_triage", f"{n} routine check-ins")
        return self._send("", 303, location="/")

    # ---- auth ----
    def _login_form(self, err=""):
        # NOTE (S6): the login POST has no CSRF token (login-CSRF / session-
        # fixation). Known low-risk gap for a single local account; out of scope
        # here because a pre-auth token can't be bound to a session yet.
        e = f"<div class='banner urgent'>{html.escape(err)}</div>" if err else ""
        return page("Doctor login", f"""{e}<div class=card><h2>Doctor login</h2>
<form method=post action='/login'>
  <label>Username</label><input name=username value='doctor'>
  <label>Password</label><input type=password name=password>
  <p></p><button class=btn type=submit>Log in</button></form>
<p class=muted>Families do not log in. They take the check-in here:
<a href='/survey'>open the family check-in survey</a>.</p></div>""")

    def _do_login(self, f):
        u = (f.get("username") or [""])[0]
        p = (f.get("password") or [""])[0]
        now = time.time()
        # Throttle per (client IP, username) so hammering one username can't lock
        # the account out for other clients (S9).
        ip = self.client_address[0] if self.client_address else "?"
        key = (ip, u)
        fa = _LOGIN_FAILS.get(key)
        if fa and fa[1] > now:
            db.audit(f"doctor:{u}", "login_locked")
            return self._send(self._login_form(
                f"Too many attempts. Try again in {int(fa[1]-now)}s."), 429)
        user = db.get_user(u)
        if user and user["role"] == "doctor" and db.verify_pw(p, user["pw_salt"], user["pw_hash"]):
            _LOGIN_FAILS.pop(key, None)
            db.audit(f"doctor:{u}", "login")
            # Embed issued-at epoch so the session can expire (S4). Value format:
            # "doctor:<user>:<epoch>". Set Max-Age too so the browser also drops it.
            sess_val = f"doctor:{u}:{int(time.time())}"
            cookie = session_cookie(sign(sess_val), max_age=SESSION_MAX_AGE)
            return self._send("", 303, set_cookie=cookie, location="/")
        cnt = (fa[0] + 1) if fa else 1
        # Bound memory: evict the oldest entry if the table grows too large (S9).
        if key not in _LOGIN_FAILS and len(_LOGIN_FAILS) >= _LOGIN_FAILS_MAX:
            _LOGIN_FAILS.pop(next(iter(_LOGIN_FAILS)), None)
        _LOGIN_FAILS[key] = [cnt, now + _LOCK_SECONDS if cnt >= _MAX_FAILS else 0]
        db.audit(f"doctor:{u}", "login_failed", f"attempt {cnt}")
        return self._send(self._login_form("Invalid credentials."), 401)

    # ---- doctor: change password ----
    def _change_pw_form(self, msg="", ok=False):
        cls = "routine" if ok else "urgent"
        m = f"<div class='banner {cls}'>{html.escape(msg)}</div>" if msg else ""
        return page("Change password", f"""{m}<div class=card><h2>Change doctor password</h2>
<form method=post action='/change-password'>
<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>
  <label>Current password</label><input type=password name=current>
  <label>New password (min 10 chars)</label><input type=password name=new>
  <label>Confirm new password</label><input type=password name=confirm>
  <p></p><button class=btn type=submit>Update password</button></form></div>""", DOC_NAV)

    def _do_change_pw(self, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        # Session value is "doctor:<user>:<epoch>" (S4); the username is the
        # middle field. Fall back safely to "doctor" if the format is unexpected.
        sess = self._session() or "doctor:doctor:0"
        _sp = sess.split(":")
        username = _sp[1] if len(_sp) >= 2 and _sp[1] else "doctor"
        cur = (f.get("current") or [""])[0]
        new = (f.get("new") or [""])[0]
        confirm = (f.get("confirm") or [""])[0]
        user = db.get_user(username)
        if not (user and db.verify_pw(cur, user["pw_salt"], user["pw_hash"])):
            return self._send(self._change_pw_form("Current password is incorrect."), 400)
        if len(new) < 10:
            return self._send(self._change_pw_form("New password must be at least 10 characters."), 400)
        if new != confirm:
            return self._send(self._change_pw_form("New passwords do not match."), 400)
        db.update_password(username, new)
        db.audit(f"doctor:{username}", "change_password")
        return self._send(self._change_pw_form("Password updated.", ok=True))

    # ---- doctor: queue ----
    def _queue(self):
        from urllib.parse import parse_qs, urlparse
        q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].strip().lower()
        queue = db.review_queue()
        if q:
            queue = [it for it in queue if q in (str(it.get("external_id", "")) + " "
                     + str(it.get("medication", ""))).lower()]
        waiting = [it for it in queue if not it.get("decision")]
        reviewed = [it for it in queue if it.get("decision")]
        # Dashboard stats + filters share ONE vocabulary (Sanjana 2026-08-05): the
        # clinical classification, not the internal priority band. The tiles used to
        # read "Awaiting review / Urgent / Reviewed", which mixed a workflow state
        # with a severity and never named the tier at all.
        #
        # Safety is deliberately NOT mutually exclusive with a tier - a check-in can
        # be both - so these counts overlap and are not meant to sum to the total.
        # "No seizures" is the one that excludes safety, matching _queue_chips().
        _FILTERS = [
            ("all", "All", lambda it: True),
            ("emergent", "Emergent", lambda it: it.get("urgency_tier") == "emergent"),
            ("urgent", "Urgent", lambda it: it.get("urgency_tier") == "urgent"),
            ("nonurgent", "Non-urgent", lambda it: it.get("urgency_tier") == "non_urgent"),
            ("safety", "Safety", lambda it: bool(it.get("safety_flag"))),
            ("noseizures", "No seizures",
             lambda it: not it.get("urgency_tier") and not it.get("safety_flag")),
        ]
        _MATCH = {fk: pred for fk, _lbl, pred in _FILTERS}

        def _stat(n, label, tone=""):
            colour = {"Emergent": "#ED4036", "Safety": "#ED4036", "Urgent": "#9a6700",
                      "Non-urgent": "#1b7a52"}.get(label, "#16191c")
            return (f"<div style='flex:1 1 110px;background:#fff;border:1px solid #e1e5e8;"
                    f"border-radius:10px;padding:14px 16px'>"
                    f"<div style='font-size:30px;font-weight:700;line-height:1;color:{colour}'>{n}</div>"
                    f"<div class=muted style='margin-top:4px'>{label}</div></div>")
        stats = ("<div class=row style='margin-bottom:18px'>"
                 + "".join(_stat(len([it for it in waiting if pred(it)]), lbl)
                           for fk, lbl, pred in _FILTERS if fk != "all")
                 + "</div>")
        # --- filter chips: same vocabulary as the tiles, plus All / Reviewed ---
        fsel = (parse_qs(urlparse(self.path).query).get("f") or ["all"])[0]
        def _count(fk):
            if fk == "reviewed":
                return len(reviewed)
            return len([it for it in waiting if _MATCH.get(fk, lambda _it: True)(it)])
        # sort control: Priority (default) | Newest | Oldest, by check-in time.
        sortsel = (parse_qs(urlparse(self.path).query).get("sort") or ["priority"])[0]
        if sortsel not in ("priority", "newest", "oldest"):
            sortsel = "priority"
        qstr = (f"q={q}&" if q else "")
        chip_base = qstr + (f"sort={sortsel}&" if sortsel != "priority" else "")
        sort_base = qstr + (f"f={fsel}&" if fsel != "all" else "")
        chips = "".join(
            f"<a class='{'on' if fsel == fk else ''}' href='/?{chip_base}f={fk}'>{lbl} "
            f"<span style='opacity:.7'>{_count(fk)}</span></a>"
            for fk, lbl in ([(fk, lbl) for fk, lbl, _p in _FILTERS] + [("reviewed", "Reviewed")]))
        chips = f"<div class=qchips>{chips}</div>"
        sort_chips = "".join(
            f"<a class='{'on' if sortsel == sk else ''}' href='/?{sort_base}sort={sk}'>{lbl}</a>"
            for sk, lbl in [("priority", "Priority"), ("newest", "Newest first"), ("oldest", "Oldest first")])
        sort_chips = ("<div class=qchips style='margin-top:6px'>"
                      "<span class=muted style='align-self:center;margin-right:4px'>Sort:</span>"
                      f"{sort_chips}</div>")

        if fsel == "reviewed":
            shown = reviewed
        elif fsel in _MATCH:
            shown = [it for it in waiting if _MATCH[fsel](it)]
        else:
            shown = waiting
        # Date/time sort by check-in time; default (priority) keeps the triage order.
        if sortsel == "newest":
            shown = sorted(shown, key=lambda it: it.get("created_at") or "", reverse=True)
        elif sortsel == "oldest":
            shown = sorted(shown, key=lambda it: it.get("created_at") or "")

        # --- card-list inbox ---
        from engine_core import dose_ladder
        LB = 0.45359237
        cards = ""
        for it in shown:
            pr = it.get("priority") or "routine"
            done = it.get("decision")
            # NOT `chips` - that name holds the filter-tab row built above, and
            # shadowing it here silently replaced the filter tabs with the last
            # card's chipset (`summary, table = chips, cards` below).
            card_chips = _queue_chips(it)
            if done:
                st = ("approved", "Sent &#10003;") if done == "approved" else ("approved", html.escape(done).title())
                action = f"<span class='qstatus {st[0]}'>{st[1]}</span>"
            else:
                action = (f"<span class='qstatus pending'>Pending review</span>"
                          f"<a class='btn sm' href='/review/{it['sub_id']}'>Review</a>")
            # patient age/weight (weight stored in kg; families/doctors read lb)
            age = it.get("age") if isinstance(it.get("age"), (int, float)) else None
            wkg = it.get("weight") if isinstance(it.get("weight"), (int, float)) else None
            aw = []
            if age is not None:
                aw.append(f"{age:g}y")
            if wkg is not None:
                aw.append(f"{round(wkg / LB)}lb")
            aw_txt = (" &middot; " + ", ".join(aw)) if aw else ""
            # The engine's one-line "why". This is the single most useful sentence on
            # the card: it says what the check-in needs (e.g. "Need seizure duration
            # to decide continue vs increase faster") instead of leaving the reviewer
            # to open the note to find out. Older rows have no stored reason, so the
            # line is simply omitted for them.
            why = ""
            if it.get("reason_summary"):
                why = (f"<div class=qwhy>{html.escape(it['reason_summary'])}</div>")
            # Dose for THIS child against Dr. Barbour's band (DRAFT): the reported
            # dose, where it sits in the band stated as a conclusion, then the band
            # itself. Same shared formatter as the note, ladder card and reference
            # table, so the surfaces cannot drift.
            exp = ""
            medkey = (it.get("medication") or "").strip().lower().replace(" ", "_")
            ref = dose_ladder.reference_for(medkey, age, wkg, [])
            if ref and not ref.get("conmed_suppressed"):
                cur_mgday = MedicationContext(
                    medication=it.get("medication") or "",
                    dose_amount=it.get("dose_amount"), dose_unit=it.get("dose_unit"),
                    frequency_per_day=it.get("frequency"),
                    concentration=it.get("concentration")).total_daily_dose
                parts = dict((k, v) for k, _l, v in dose_ladder.dose_reference_parts(
                    ref, weight_kg=wkg, current_mg_per_day=cur_mgday))
                pos = _dose_position(ref, cur_mgday)
                if parts.get("current"):
                    band = " &middot; ".join(
                        x for x in (f"typical {parts['typical_range']}" if parts.get("typical_range") else "",
                                    f"max {parts['absolute_max']}" if parts.get("absolute_max") else "")
                        if x)
                    exp = (f"<div class=qdose><b>{html.escape(parts['current'])}</b>"
                           + (f" <span class='pill {pos[1]}'>{html.escape(pos[0])}</span>" if pos else "")
                           + (f"<div class=muted style='font-size:12px;margin-top:3px'>"
                              f"{band} (DRAFT)</div>" if band else "")
                           + "</div>")
            # No priority pill in the header any more: its vocabulary
            # (urgent/review/routine) collided with the clinical tier vocabulary
            # (emergent/urgent/non-urgent) now shown in the chip row, so a card could
            # read "Review" and "Urgent" at once. Priority still colours the left
            # border and drives the sort.
            cards += (
                f"<div class='qcard lvl-{pr} {'done' if done else ''}'>"
                f"<div class=qtop>"
                f"<span class=who>{html.escape(str(it['external_id']))}</span>"
                f"<span class=muted>{html.escape((it.get('medication') or '').title())}{aw_txt}</span>"
                f"<span class=right>{action}</span></div>"
                f"<div class=qcat>{html.escape(category_label(it.get('category') or ''))}</div>"
                f"{why}"
                f"{card_chips}"
                f"{exp}</div>")
        if not cards:
            cards = ("<div class=empty>Nothing here.<br><span class=muted>"
                     "New check-ins appear when a family submits their survey.</span></div>")
        summary, table = chips, cards
        search = (f"<form method=get action='/' class=qsum style='margin:0 0 14px'>"
                  f"<input name=q value='{html.escape(q)}' placeholder='Search patient or medicine' aria-label='Search the queue by patient or medicine' "
                  f"style='max-width:280px'>"
                  f"<button class='btn sm ghost' type=submit>Search</button>"
                  + (" <a class='btn sm ghost' href='/'>Clear</a>" if q else "") + "</form>")
        routine_waiting = [it for it in waiting if (it.get("priority") or "routine") == "routine"]
        bulk = ""
        if routine_waiting:
            bulk = (f"<form method=post action='/bulk-triage' style='margin-top:10px'>"
                    f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
                    f"<button class='btn sm ghost' type=submit "
                    f"data-confirm=\"Mark {len(routine_waiting)} routine check-in(s) as reviewed?\">"
                    f"Mark {len(routine_waiting)} routine as reviewed</button></form>")
        body = (f"{stats}"
                f"<div class=card><h2>Patients to review</h2>{search}{summary}{sort_chips}{table}{bulk}</div>"
                f"<div class=card><h2>Your patients</h2>"
                f"<p style='margin:0 0 14px'><a class='btn sm' href='/patients/new'>+ Add patient</a></p>"
                f"{self._patient_list()}</div>"
                f"{TOOLS_BAR}")
        return self._send(page("Queue", body, DOC_NAV))

    def _patient_list(self):
        out = ""
        for p in db.list_patients():
            tok = db.latest_pending_token(p["id"])
            open_btn = (f"<a class='btn sm ghost' href='/s/{html.escape(tok)}' target='_blank' "
                        f"style='margin-right:8px'>Open survey</a>" if tok else "")
            out += (f"<div class=row style='align-items:center'><div><strong>{html.escape(p['external_id'])}</strong> "
                    f"<span class=muted>{('%g yr' % p['age']) if p.get('age') is not None else 'age n/r'} · {html.escape((p['medication'] or '').title())}</span></div>"
                    f"<div style='flex:0;display:flex;align-items:center'>{open_btn}"
                    f"<form method=post action='/patients/{p['id']}/invite'>"
                    f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
                    f"<button class='btn sm' type=submit>Generate survey link</button></form></div></div>")
        return out or "<p class=muted>No patients yet.</p>"

    # ---- doctor: intake ----
    def _patient_intake_form(self):
        mo = opts([("", "- select -")] + [(m, m.replace("_", " ").title()) for m in MEDS])
        so = opts([("", "- none -")] + [(m, m.replace("_", " ").title()) for m in MEDS])
        return page("Add patient", f"""<div class=card><h2>Add patient (intake)</h2>
<form method=post action='/patients'>
<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>
  <div class=row><div><label>Patient ID</label><input name=external_id required></div>
    <div><label>Age (yr)</label><input type=number step=0.1 name=age></div>
    <div><label>Weight (kg)</label><input type=number step=0.1 name=weight></div>
    <div><label>Sex</label><select name=sex>{opts([('','- select -'),('female','Female'),('male','Male')])}</select></div>
    <div><label>Epilepsy type</label><input name=epilepsy_type value='focal'></div></div>
  <div class=row><div><label>Medication</label><select name=medication required>{mo}</select></div>
    <div><label>Amount/dose</label><input type=number step=any name=dose_amount></div>
    <div><label>Given as</label><select name=dose_unit>{opts([('mg','mg'),('tablets','tablet(s)'),('mL','mL (liquid)')])}</select></div>
    <div><label>Strength (mg per unit)</label><input type=number step=any name=concentration placeholder="if tablets or mL"></div>
    <div><label>Times/day</label><select name=frequency>{opts([('1','1'),('2','2'),('3','3'),('4','4')],'2')}</select></div></div>
  <label class=muted><input type=checkbox name=can_increase value=1 checked style='width:auto'> Approves further dose increase</label>
  <label>Approved switch target</label><select name=switch_to>{so}</select>
  <p></p><button class=btn type=submit>Save patient</button></form></div>""", DOC_NAV)

    def _do_add_patient(self, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        one = lambda k, d="": (f.get(k) or [d])[0]
        num = lambda v: (float(v) if v else None)
        try:
            record = {
                "external_id": one("external_id"), "age": float(one("age", "0") or 0),
                "weight": float(one("weight", "0") or 0), "sex": one("sex") or None,
                "epilepsy_type": one("epilepsy_type", "unspecified"),
                "medication": one("medication"), "dose_amount": float(one("dose_amount", "0") or 0),
                "dose_unit": one("dose_unit", "mg"), "frequency": int(one("frequency", "2") or 2),
                "concentration": num(one("concentration")),
                "can_increase": one("can_increase") == "1", "switch_to": one("switch_to") or None}
        except ValueError:
            return self._send(page("Error", "<div class=card>Age, weight, dose, frequency, and "
                                    "concentration must be numbers. Go back and check those "
                                    "fields.</div>"), 400)
        pid = db.add_patient(record)
        db.audit("doctor", "add_patient", f"patient {pid}")
        return self._send("", 303, location="/")

    def _do_invite(self, pid):
        if not self._is_doctor():
            return self._send("", 303, location="/login")
        inst = db.create_instance(int(pid))
        db.audit("doctor", "create_invite", f"patient {pid} token {inst['token'][:6]}…")
        link = base_url(self) + f"/s/{inst['token']}"
        body = (f"<div class=card><h2>Patient survey link created</h2>"
                f"<p>Share this private link with the family (one patient). It points to "
                f"wherever this app is currently reachable. On a local-only setup it works "
                f"on this computer; once the app is deployed behind a network address or "
                f"domain, the link will use that automatically.</p>"
                f"<pre>{html.escape(link)}</pre>"
                f"<p></p>"
                f"<a class=btn href='/s/{html.escape(inst['token'])}' target='_blank'>Open the survey (family view)</a> "
                f"<a class='btn ghost' href='/'>Back to queue</a>"
                f"<p class=muted style='margin-top:12px'>When the family submits, it's triaged "
                f"and appears in your queue.</p></div>")
        return self._send(page("Survey link", body, DOC_NAV))

    # ---- doctor: review ----
    def _review_view(self, sub_id):
        b = db.get_submission_bundle(int(sub_id))
        if not b or not b["recommendation"]:
            return self._send(page("Not found", "<div class=card>Submission not found.</div>"), 404)
        db.audit("doctor", "view_submission", f"submission {sub_id}")
        ms = db.get_med_selection(int(sub_id))       # switch/add drug options, if any
        rec, sub, pat = b["recommendation"], b["submission"], b["patient"]
        pr = sub.get("priority") or "routine"
        _flags = sub.get("flags") or []
        draft = rec.get("draft_message") or ""
        blocked = rec.get("safety_flag")
        msg_area = ("<p class='banner urgent'>Safety concern. No family message was drafted. "
                    "Handle per urgent protocol.</p>" if blocked else
                    f"<label>Message to family (edit before approving)</label>"
                    f"<textarea name=final_message rows=6>{html.escape(draft)}</textarea>")
        existing = b["review"]
        done = (f"<div class='banner routine'>Already {html.escape(existing['decision'])} "
                f"on {html.escape(existing['reviewed_at'])}.</div>" if existing else "")
        cat_label = category_label(rec["category"])
        target = (f" &rarr; {html.escape(rec.get('switch_target'))}"
                  if rec.get("switch_target") else "")

        # SAFETY ALERTS pinned at the very top (surface first, per the safety rule).
        def _callout(inner):
            return f"<div class=callout><div class=ico>&#9888;</div><div>{inner}</div></div>"
        alerts = ""
        if pr == "urgent":
            # On an urgent case every triage reason is a safety reason; surface each.
            for x in _flags:
                alerts += _callout(html.escape(x))
            if blocked:
                alerts += _callout("<strong>Family message withheld.</strong> Handle per urgent "
                                   "protocol.")
        else:
            if not rec.get("recognized"):
                alerts += _callout("The medicine name was not recognized as one of the 11 study "
                                   "drugs. Verify the entry before acting.")
            if any("ABOVE the recommended maximum" in x for x in _flags):
                alerts += _callout("<strong>Dose above maximum.</strong> The reported dose is above "
                                   "the recommended maximum. Verify the entry.")

        # WHAT: the recommendation, stated plainly. No badges; a single draft line
        # replaces the old three-badge stack and the noisy engine footer.
        rec_card = (
            f"<div class=card><h2>Recommendation</h2>"
            f"<p class=muted style='margin-top:-8px'>Patient {html.escape(pat['external_id'])} "
            f"<span class='pill {pr}'>{pr}</span></p>"
            f"<div class=reccat>{html.escape(cat_label)}{target}</div></div>")

        # PLAN: the concrete dose plan and medication options.
        _ans = sub.get("answers") or {}
        _age, _wt = _age_weight(pat, _ans)
        # Options exist only for a switch or an add; either way the current medicine
        # is still taken while the new one starts (kept on an add, tapered over 4 weeks
        # on a switch), so it counts as a co-medication for the starting schedule.
        _child = (_age, _wt, _new_med_comeds(pat.get("medication"), _ans))
        plan = f"{_titration_html(rec.get('titration_json'))}{med_selection_review_html(ms, _child)}"

        # WHY: one plain reason, with rule trace + sources collapsed one click away.
        why_card = _why_html(rec, sub, pat.get("medication"))

        # CLINICAL NOTE: what the family reported, shown up front (not hidden).
        note_card = (f"<div class=card><h2>Clinical note</h2>"
                     f"<p class=muted style='margin-top:-8px'>What the family reported this visit.</p>"
                     f"<div class=note>{_clinical_note_html(rec.get('clinical_summary'))}</div></div>")

        # Order = Clinical note -> Recommendation -> Medication option / plan -> Why
        # -> Decision (safety alerts pinned above all).
        body = (f"<p><a class=muted href='/'>&larr; Back to queue</a></p>"
                f"{done}{alerts}{note_card}{rec_card}{plan}{why_card}"
                f"<div class=card><h2>Your decision</h2>"
                f"<p class=notetag>&#9888; This recommendation is AI-based and may be wrong. "
                f"Double-check it before approving.</p>"
                f"<p class=muted>You approve, edit the message, or override the recommendation. "
                f"Every choice is logged.</p>"
                f"<form method=post action='/review/{sub_id}'>"
                f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
                f"{msg_area}"
                f"<details class=trace><summary>Edit the clinical note before approving</summary>"
                f"<textarea name=final_note rows=8>{html.escape(rec.get('clinical_summary') or '')}</textarea>"
                f"<p class=muted>Your edits are saved with the approval for the chart.</p></details>"
                f"<label>Private note (optional)</label><input name=note placeholder='note for the record'>"
                f"<p></p><button class=btn type=submit name=decision value=approved>Approve &amp; release message</button> "
                f"<button class='btn sm ghost' type=submit name=decision value=rejected "
                f"style='margin-left:8px'>Reject</button>"
                # Override: choose a different recommendation + reason (logged).
                f"<details class=trace style='margin-top:16px'><summary>Override the recommendation</summary>"
                f"<p class=muted>Disagree with the engine? Choose what you'd recommend instead and why. "
                f"This is recorded with your name for traceability; the engine's rules are unchanged.</p>"
                f"<label>Your recommendation instead</label>"
                f"<select name=ov_category>{opts([('','choose one')] + CATS)}</select>"
                f"<label>Why you're overriding</label><input name=ov_reason placeholder='clinical reason for the override'>"
                f"<p></p><button class='btn sm' type=submit name=decision value=override "
                f"style='background:#9a6700'>Save override</button></details>"
                f"</form></div>")
        return self._send(page("Review", body, DOC_NAV))

    def _do_review(self, sub_id, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        b = db.get_submission_bundle(int(sub_id))
        if not b or not b["recommendation"]:
            return self._send(page("Not found", "<div class=card>Not found.</div>"), 404)
        decision = (f.get("decision") or ["approved"])[0]
        note = (f.get("note") or [""])[0]
        final = (f.get("final_message") or [None])[0]
        override_cat = None
        if decision == "override":
            # Doctor recommends a different category than the engine, with a reason.
            override_cat = (f.get("ov_category") or [""])[0] or None
            ov_reason = (f.get("ov_reason") or [""])[0]
            note = (f"[OVERRIDE -> {override_cat}] {ov_reason}".strip() if override_cat
                    else note)
            final = None
        elif decision != "approved":
            final = None
        final_note = (f.get("final_note") or [None])[0] if decision == "approved" else None
        db.add_review(b["recommendation"]["id"], decision, note, final, "doctor",
                      override_category=override_cat, final_note=final_note)
        db.mark_reviewed(b["submission"]["instance_id"])
        det = decision + (f" -> {override_cat}" if override_cat else "")
        db.audit("doctor", "review_" + decision, f"submission {sub_id} ({det})")
        return self._send("", 303, location="/")

    # ---- doctor: audit ----
    def _audit(self):
        chk = db.verify_audit_chain()
        if chk["ok"]:
            banner = (f"<div class='banner routine'>&#10003; Audit chain intact "
                      f"({chk['count']} entries, tamper-evident).</div>")
        else:
            banner = (f"<div class='banner urgent'>&#9888; Audit chain BROKEN at entry "
                      f"{chk['broken_at']} - log may have been altered.</div>")
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        q = (qs.get("q") or [""])[0].strip().lower()
        d0 = (qs.get("from") or [""])[0].strip()
        d1 = (qs.get("to") or [""])[0].strip()
        entries = db.list_audit()
        if q:
            entries = [a for a in entries if q in (a["actor"] + " " + a["action"] + " " + a["detail"]).lower()]
        if d0:
            entries = [a for a in entries if (a["ts"][:10] >= d0)]
        if d1:
            entries = [a for a in entries if (a["ts"][:10] <= d1)]
        searchf = (f"<form method=get action='/audit' class=qsum style='margin:0 0 14px'>"
                   f"<input name=q value='{html.escape(q)}' placeholder='patient / action' aria-label='Search audit log' style='max-width:220px'>"
                   f"<input name=from type=date value='{html.escape(d0)}' aria-label='From date' style='max-width:160px'>"
                   f"<input name=to type=date value='{html.escape(d1)}' aria-label='To date' style='max-width:160px'>"
                   f"<button class='btn sm ghost' type=submit>Filter</button>"
                   + (" <a class='btn sm ghost' href='/audit'>Clear</a>" if (q or d0 or d1) else "") + "</form>")
        rows = "".join(f"<tr><td class=muted>{html.escape(a['ts'])}</td><td>{html.escape(a['actor'])}</td>"
                       f"<td>{html.escape(a['action'])}</td><td class=muted>{html.escape(a['detail'])}</td></tr>"
                       for a in entries) or "<tr><td colspan=4 class=muted>No matching events.</td></tr>"
        body = (f"{banner}<div class=card><h2>Audit log</h2>{searchf}<table><tr><th>When</th><th>Actor</th>"
                f"<th>Action</th><th>Detail</th></tr>{rows}</table></div>")
        return self._send(page("Audit", body, DOC_NAV))

    # ---- doctor: NLP audit ----
    def _nlp_audit(self, search_patient: str = ""):
        try:
            from audit.mongo_audit import ParseAudit
            from audit.faers_lookup import get_faers_reaction_summary
            pa = ParseAudit()
            if search_patient:
                records = pa.search_by_patient(patient_name=search_patient)
            else:
                records = pa.list_recent(limit=100)
            chk = pa.verify_chain()
            store_label = "MongoDB" if pa.is_using_mongo() else "JSONL fallback"
            if chk.get("ok"):
                banner = (f"<div class='banner routine'>&#10003; NLP audit chain intact "
                          f"({chk.get('count', len(records))} entries) &middot; store: {store_label}</div>")
            else:
                banner = (f"<div class='banner urgent'>&#9888; NLP audit chain issue: "
                          f"{html.escape(str(chk))} &middot; store: {store_label}</div>")
        except Exception as exc:
            return self._send(page("NLP Audit",
                f"<div class='banner urgent'>NLP audit unavailable: {html.escape(str(exc))}</div>",
                DOC_NAV))

        def _conf_pill(conf):
            c = float(conf) if conf else 0
            cls = "routine" if c >= 0.7 else ("review" if c >= 0.4 else "urgent")
            return f"<span class='pill {cls}'>{c:.2f}</span>"

        rows = ""
        for rec in records:
            findings = rec.get("findings") or []
            finding_html = "".join(
                f"<div style='font-size:12px;margin:2px 0'>"
                f"<b>{html.escape(str(f.get('field','')))}</b> = "
                f"{html.escape(str(f.get('suggested_value','')))} "
                f"{_conf_pill(f.get('confidence', 0))} "
                f"<span class=muted>{html.escape(str(f.get('evidence','')))}</span></div>"
                for f in findings
            ) or "<span class=muted>no findings</span>"
            _ms = rec.get("processing_ms")
            ms_cell = f"{_ms:.0f} ms" if isinstance(_ms, (int, float)) else "-"
            rows += (
                f"<tr>"
                f"<td class=muted style='white-space:nowrap'>{html.escape(str(rec.get('timestamp',''))[:19])}</td>"
                f"<td><span class='pill review'>{html.escape(str(rec.get('model_name',''))[:20])}</span></td>"
                f"<td class=muted>{html.escape(str(rec.get('source_question') or ''))}</td>"
                f"<td style='max-width:200px;overflow:hidden;font-size:13px'>"
                f"{html.escape(str(rec.get('input_text',''))[:80])}</td>"
                f"<td>{finding_html}</td>"
                f"<td class=muted>{ms_cell}</td>"
                f"</tr>"
            )

        if not rows:
            rows = "<tr><td colspan=6 class=muted>No parse events logged yet.</td></tr>"

        # FAERS panel - fetch top reactions for drugs referenced in recent records.
        # External lookup: skipped unless ASM_ENABLE_EXTERNAL is set. (S2)
        faers_html = ""
        seen_drugs = (list({r.get("inputs", {}).get("proposed_drug") or r.get("model_name", "")
                            for r in records if r.get("record_type") == "recommendation"})[:3]
                      if ENABLE_EXTERNAL else [])
        for drug in seen_drugs:
            if not drug or drug == "lexicon":
                continue
            summary = get_faers_reaction_summary(drug, limit=5)
            if summary:
                rxns = "".join(
                    f"<li>{html.escape(rx['reaction'])} <span class=muted>({rx['count']})</span></li>"
                    for rx in summary["top_reactions"]
                )
                faers_html += (
                    f"<div style='margin-bottom:12px'>"
                    f"<b>{html.escape(drug)}</b> - {summary['total']} FAERS reports"
                    f"<ul style='margin:4px 0 0'>{rxns}</ul>"
                    f"<p class=muted style='font-size:11px'>{html.escape(summary['source'])}</p>"
                    f"</div>"
                )
        if faers_html:
            faers_card = (f"<div class=card><h2>FAERS adverse events (openFDA)</h2>"
                          f"<p class=muted>Top reported reactions - draft for clinician verification. "
                          f"Never used by the decision engine.</p>{faers_html}</div>")
        else:
            faers_card = ""

        search_val = html.escape(search_patient)
        search_form = (f"<form method=get action='/nlp-audit' style='margin-bottom:14px'>"
                       f"<div class=row><div><label>Search by patient ID</label>"
                       f"<input type=text name=patient value='{search_val}' "
                       f"placeholder='patient ID or name'></div>"
                       f"<div style='align-self:flex-end'>"
                       f"<button class=btn type=submit>Search</button></div></div></form>")

        body = (f"{banner}{faers_card}"
                f"<div class=card><h2>NLP parse events</h2>{search_form}"
                f"<p class=muted>Advisory suggestions from free-text parsing - "
                f"never used by the decision engine.</p>"
                f"<div style='overflow-x:auto'><table>"
                f"<tr><th>Time</th><th>Model</th><th>Question</th>"
                f"<th>Input</th><th>Findings</th><th>ms</th></tr>"
                f"{rows}</table></div></div>")
        return self._send(page("NLP Audit", body, DOC_NAV))

    # ---- patient ----
    def _patient_survey(self, token):
        inst = db.get_instance_by_token(token)
        if not inst:
            return self._send(page("Invalid link", "<div class=card>This link is not valid.</div>"), 404)
        if inst["status"] != "pending":
            return self._send(page("Already submitted",
                "<div class=card><h2>Thank you</h2><p>Your answers were received. "
                "Your care team will review them.</p></div>"))
        return self._send(patient_survey_form(token, db.get_patient(inst["patient_id"])))

    def _do_submit(self, token, f):
        inst = db.get_instance_by_token(token)
        if not inst:
            return self._send(page("Invalid link", "<div class=card>This link is not valid.</div>"), 404)
        if not csrf_ok(token, (f.get("csrf") or [""])[0]):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        if inst["status"] != "pending":
            return self._send("", 303, location=f"/m/{token}")
        patient = db.get_patient(inst["patient_id"])
        answers = parse_answers(f)
        snap = build_family_snapshot(patient, f, answers)
        rec = run_engine(snap, asm_registry=current_asm(),
                         drug_classifier=CLASSIFIER, physician_name="your care team")
        # free-text red-flag scan (point B) - flags anything unusual in prose
        # Scans Q13 (general free text, includes folded-in notes) AND Q9_other
        # (the "other side effect" description), which is its own field and was
        # previously skipped by this scan even though it's patient-typed prose.
        rf_cats = []
        rf = current_redflag()
        if rf:
            cats = set()
            for fld in ("Q13", "Q9_other"):
                txt = answers.get(fld)
                if txt:
                    cats.update(fl.category for fl in rf.scan(txt))
            rf_cats = sorted(cats)
        priority, reasons = triage(rec, rf_cats)
        fr = _formulation_reason(f)
        if fr:
            reasons = reasons + [fr]
            priority = "review" if priority == "routine" else priority
        sub_id = db.add_submission(inst["id"], patient["id"], answers, priority, reasons,
                                   med_context=med_context_of(snap))
        db.add_recommendation(sub_id, rec.to_dict())
        db.mark_submitted(inst["id"])
        db.audit("patient", "submit", f"patient {patient['id']} priority {priority}")
        # NLP free-text parsing + audit (point A - advisory only, never fed to engine)
        _run_nlp_audit(answers, patient_id=str(patient["id"]),
                       session_id=f"sub_{sub_id}")
        # Log full recommendation to MongoDB for traceability (pattern: log_safety_check)
        _log_recommendation_audit(rec, patient, session_id=f"sub_{sub_id}")
        # Switch/add with no safety flag -> compute options from THIS submission's
        # answers and show them as the next step of the same check-in (DRAFT for
        # the neurologist). No separate questionnaire.
        if phase2_applies(rec.to_dict()):
            return self._send(medsel_questions_page(token, sub_id, patient))
        body = ("<div class=card><h2>Thank you</h2><p>Your answers were sent to your care team, "
                "who will review them.</p></div>")
        return self._send(page("Submitted", body))

    def _do_open_submit(self, f):
        """Self-serve survey submit: no pre-existing record needed. The family
        gives a study ID; we create the record if new and queue the submission
        for the doctor. Only the survey data is passed on for review."""
        if not csrf_ok("open", (f.get("csrf") or [""])[0]):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        one = lambda k, d="": (f.get(k) or [d])[0]
        study_id = one("study_id").strip()
        if not study_id:
            return self._send(open_survey_form(
                "Please enter your study ID so your care team can match your check-in."), 400)
        answers = parse_answers(f)
        patient = db.get_patient_by_external_id(study_id)
        if not patient:
            def _num(v, cast):
                try:
                    return cast(v) if v else None
                except ValueError:
                    return None
            chosen = _form_med(f)
            nd = CLASSIFIER.normalize(chosen)
            pid = db.add_patient({
                "external_id": study_id, "age": None, "weight": None, "epilepsy_type": "unspecified",
                "medication": nd.canonical or chosen or None,
                "dose_amount": _num(one("amt") or one("amt_1"), float), "dose_unit": one("unit") or "mg",
                "frequency": _num(one("freq"), int), "concentration": _num(one("strength"), float),
                "can_increase": True, "switch_to": None})
            patient = db.get_patient(pid)
        inst = db.create_instance(patient["id"])
        snap = build_family_snapshot(patient, f, answers)
        rec = run_engine(snap, asm_registry=current_asm(),
                         drug_classifier=CLASSIFIER, physician_name="your care team")
        # Same two prose fields as _do_submit (Q13 and the "other side effect" box);
        # this path used to scan Q13 only.
        rf_cats = []
        rf = current_redflag()
        if rf:
            cats = set()
            for fld in ("Q13", "Q9_other"):
                txt = answers.get(fld)
                if txt:
                    cats.update(fl.category for fl in rf.scan(txt))
            rf_cats = sorted(cats)
        priority, reasons = triage(rec, rf_cats)
        fr = _formulation_reason(f)
        if fr:
            reasons = reasons + [fr]
            priority = "review" if priority == "routine" else priority
        sub_id = db.add_submission(inst["id"], patient["id"], answers, priority, reasons,
                                   med_context=med_context_of(snap))
        db.add_recommendation(sub_id, rec.to_dict())
        db.mark_submitted(inst["id"])
        db.audit("patient", "open_submit", f"study_id {study_id} priority {priority}")
        if phase2_applies(rec.to_dict()):
            return self._send(medsel_questions_page(inst["token"], sub_id, patient))
        body = ("<div class=card><h2>Thank you</h2><p>Your answers were sent to your care team, "
                "who will review them.</p></div>")
        return self._send(page("Submitted", body))

    def _patient_message(self, token):
        inst = db.get_instance_by_token(token)
        if not inst:
            return self._send(page("Invalid link", "<div class=card>This link is not valid.</div>"), 404)
        msg = db.released_message_for_token(token)
        if not msg:
            body = ("<div class=card><h2>No message yet</h2><p>Your care team has not sent a "
                    "message yet. You'll see it here once they have reviewed your answers.</p></div>")
        else:
            body = f"<div class=card><h2>Message from your care team</h2><pre>{html.escape(msg)}</pre></div>"
        return self._send(page("Care team message", body))

    # ---- patient: medication-selection ranking (options are shown right after
    # the survey submit; this records the family's 1st/2nd/3rd/avoid choices) ----
    def _own_submission(self, inst, f):
        """The submission named in the form, only if it belongs to this link's check-in."""
        try:
            sub_id = int((f.get("sub") or ["0"])[0] or 0)
        except ValueError:
            return None, None
        b = db.get_submission_bundle(sub_id) if sub_id else None
        if not b or b["submission"].get("instance_id") != inst["id"]:
            return None, None
        return sub_id, b

    def _do_medsel(self, token, f):
        """Page 2 of the check-in: the medicine-options answers -> rule-based options
        (engine_core.med_selection), then the family's 1st/2nd/3rd/avoid page."""
        inst = db.get_instance_by_token(token)
        if not inst:
            return self._send(page("Invalid link", "<div class=card>This link is not valid.</div>"), 404)
        if not csrf_ok(token, (f.get("csrf") or [""])[0]):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        sub_id, b = self._own_submission(inst, f)
        if not sub_id:
            return self._send(page("Not found", "<div class=card>No check-in to add to.</div>"), 404)
        r = b.get("recommendation") or {}
        if not phase2_applies({"category": r.get("category"),
                               "safety_concern_flag": bool(r.get("safety_flag"))}):
            # Page 2 exists only for switch/add with no safety flag.
            return self._send(page("Not found", "<div class=card>No medicine options for this "
                                   "check-in.</div>"), 404)
        if (f.get("skip") or [""])[0]:
            db.audit("patient", "med_options_skipped", f"submission {sub_id}")
            body = ("<div class=card><h2>Thank you</h2><p>Your answers were sent to your care team, "
                    "who will review them.</p></div>")
            return self._send(page("Submitted", body))
        patient = b["patient"] or db.get_patient(inst["patient_id"])
        attrs = _selection_attrs_from_form(patient, b["submission"]["answers"], f)
        result = select_meds(attrs, SELECTION_RULESET)
        db.save_med_selection(sub_id, patient["id"], attrs, result.to_dict())
        db.audit("patient", "med_options",
                 f"submission {sub_id} eligible {len(result.ranked_eligible)}"
                 + (" (deferred)" if result.deferred else ""))
        return self._send(med_options_result(token, sub_id, result.to_dict()))

    def _do_med_rank(self, token, f):
        """Family submitted their 1st/2nd/3rd/avoid choices among the options."""
        inst = db.get_instance_by_token(token)
        if not inst:
            return self._send(page("Invalid link", "<div class=card>This link is not valid.</div>"), 404)
        if not csrf_ok(token, (f.get("csrf") or [""])[0]):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        sub_id, _b = self._own_submission(inst, f)     # must be this link's own check-in
        ms = db.get_med_selection(sub_id) if sub_id else None
        if not ms:
            return self._send(page("Not found", "<div class=card>No options to record.</div>"), 404)
        ranking = {}
        for o in (ms["engine_result"].get("options") or []):
            v = (f.get(f"rank_{o['id']}") or [""])[0]
            if v:
                ranking[o["id"]] = v
        db.save_med_ranking(sub_id, ranking)
        db.audit("patient", "med_ranking", f"submission {sub_id} choices {len(ranking)}")
        body = ("<div class=card><h2>Thank you</h2><p>Your medication preferences were sent to "
                "your care team, who will review them along with your check-in before deciding "
                "on any change.</p></div>")
        return self._send(page("Between-visit check-in", body, heading="Between-Visit Check-In"))

    # ---- doctor: validation (label cases, see kappa) ----
    def _snap_ctx(self, pc, ans):
        return snapshot_from_interval(
            PatientContext(external_id=pc.get("external_id", "case"), age_years=pc.get("age_years"),
                           weight_kg=pc.get("weight_kg"), epilepsy_etiology=pc.get("epilepsy_type")),
            MedicationContext(medication=pc.get("medication"), dose_amount=pc.get("dose_amount"),
                              dose_unit=pc.get("dose_unit", "mg"), frequency_per_day=pc.get("frequency_per_day")),
            PhysicianContingency(can_increase_dose=pc.get("can_increase_dose"),
                                 can_switch_to=pc.get("can_switch_to")),
            ans)

    def _score_validation(self, labels):
        cases = _load_json(_CASES_FILE, {"cases": []}).get("cases", [])
        asm = current_asm()
        pred, gold, dis, safety, scored = [], [], [], 0, 0
        for c in cases:
            rec = run_engine(self._snap_ctx(c["patient_context"], c["answers"]),
                             asm_registry=asm, drug_classifier=CLASSIFIER)
            if rec.safety_concern_flag:
                safety += 1
            g = labels.get(c["case_id"])
            if not g:
                continue
            scored += 1
            pred.append(rec.category)
            gold.append(g)
            if g != rec.category:
                dis.append((c["case_id"], g, rec.category))
        if not scored:
            return "<p class=muted>No cases labeled yet - pick a recommendation for some cases below and Save.</p>"
        conc = sum(a == b for a, b in zip(pred, gold)) / scored
        k = _kappa(pred, gold)
        lab = {c: l for c, l in CATS}
        cls = "routine" if k >= 0.8 else "review"
        out = (f"<div class='banner {cls}'>Cohen's &kappa; = {k:.2f} "
               f"(target &ge; 0.80 - {'MET' if k >= 0.8 else 'NOT MET'}) &middot; "
               f"agreement {conc*100:.0f}% on {scored} case(s) &middot; {safety} safety case(s)</div>")
        if dis:
            rows = "".join(f"<li>{html.escape(cid)}: you said <b>{html.escape(lab.get(g,g))}</b>, "
                           f"engine said <b>{html.escape(lab.get(p,p))}</b></li>" for cid, g, p in dis)
            out += f"<p class=muted>Disagreements to review with your team:</p><ul>{rows}</ul>"
        else:
            out += "<p class=muted>No disagreements on the labeled cases.</p>"
        return out

    def _validation_view(self):
        cases = _load_json(_CASES_FILE, {"cases": []}).get("cases", [])
        saved = db.get_gold_labels()
        report = self._score_validation(saved)
        items = ""
        for c in cases:
            cid = c["case_id"]
            sel = saved.get(cid, "")
            opts = "<option value=''>- your recommendation -</option>" + "".join(
                f"<option value='{v}'{' selected' if v == sel else ''}>{html.escape(l)}</option>"
                for v, l in CATS)
            items += (f"<div class=card style='padding:14px 16px'>"
                      f"<div class=muted style='margin-bottom:6px'>{html.escape(cid)}</div>"
                      f"<div style='margin-bottom:10px'>{html.escape(c.get('summary', ''))}</div>"
                      f"<select name='case_{html.escape(cid)}'>{opts}</select></div>")
        body = (f"<div class=card><h2>Validation - your calls vs. the engine</h2>"
                f"<p class=lead>Read each case and choose what you would do, then Save to see "
                f"Cohen's &kappa;. These are synthetic cases - no patient data.</p>{report}</div>"
                f"<form method=post action='/validation'>"
                f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
                f"{items}<button class='btn big' type=submit>Save &amp; score</button></form>")
        return self._send(page("Validation", body, DOC_NAV))

    def _do_validation(self, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        for c in _load_json(_CASES_FILE, {"cases": []}).get("cases", []):
            db.set_gold_label(c["case_id"], (f.get(f"case_{c['case_id']}") or [""])[0])
        db.audit("doctor", "validation_labels_saved")
        return self._validation_view()

    # ---- doctor: settings (clinician data, no JSON editing) ----
    def _settings_form(self, msg=""):
        raw = _load_json(_ASM_FILE, {})
        rf = _load_json(_REDFLAG_FILE, None) or _load_json(_REDFLAG_TEMPLATE, {"terms": []})
        med_opts = "".join(f"<option value='{m}'>{html.escape(m.replace('_',' ').title())}</option>" for m in MEDS)
        title = lambda med: html.escape(med.replace('_', ' ').title())
        verified = sum(1 for med in MEDS
                       if any(b.get("verified") for b in (raw.get(med) or {}).get("dose_bands", [])))
        pct = round(100 * verified / len(MEDS))

        def drug_list(getter):
            out = ""
            for med in MEDS:
                v = getter(raw.get(med) or {})
                if v:
                    out += (f"<div class=refcard><div style='font-weight:700;font-size:16px'>{title(med)}</div>"
                            f"<div class=muted style='margin-top:4px'>{html.escape(str(v))}</div></div>")
            return out or "<p class=muted>No data entered yet.</p>"

        # ---- Medications tab: per-drug cards ----
        med_cards = ""
        for med in MEDS:
            info = raw.get(med) or {}
            bands = info.get("dose_bands", [])
            b0 = bands[0] if bands else {}
            ver = any(b.get("verified") for b in bands)
            boxed = bool(info.get("SAFETY_PRIORITY")) or ("BOXED" in (info.get("key_safety", "") or "").upper())
            offlabel = bool(info.get("off_label_in_study_ages"))
            badges = ""
            if boxed:
                badges += "<span class='pill urgent' style='font-size:11px'>BOXED WARNING</span> "
            if offlabel:
                badges += "<span class='pill review' style='font-size:11px'>OFF-LABEL &lt;16y</span> "
            ceiling = b0.get("absolute_max_mg_per_day") or b0.get("max_daily_mg") or b0.get("dose_range_max_mg_per_day")
            step = b0.get("standard_uptitration_step_mg") or b0.get("uptitration_step_mg_per_kg_per_day")
            interval = b0.get("standard_uptitration_interval_weeks") or b0.get("uptitration_interval_weeks")
            status = ("<span class='pill routine'>verified</span>" if ver
                      else "<span class='pill review'>DRAFT</span>")
            ev = lambda x: html.escape(str(x)) if x not in (None, "") else ""
            inds = html.escape(", ".join(info.get("indications", []) or []))
            labs = html.escape(", ".join(info.get("labs", []) or []))
            verify_btn = (f"<button class='btn sm ghost' type=submit name=action value=unverify>Unverify</button>"
                          if ver else
                          f"<button class='btn sm' type=submit name=action value=verify "
                          f"style='background:#1b7a52'>Mark verified</button>")
            med_cards += (
                f"<div class='refcard {'' if ver else 'draft'}'>"
                f"<form method=post action='/settings/med-edit'>"
                f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
                f"<input type=hidden name=med value='{med}'>"
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap'>"
                f"<div><span style='font-size:18px;font-weight:700'>{title(med)}</span> {badges}</div>{status}</div>"
                f"<div class=refgrid>"
                f"<div><label>Ceiling (mg/day)</label><input name=ceiling value='{ev(ceiling)}' inputmode=decimal></div>"
                f"<div><label>Step</label><input name=step value='{ev(step)}' inputmode=decimal></div>"
                f"<div><label>Interval (wk)</label><input name=interval value='{ev(interval)}' inputmode=numeric></div>"
                f"</div>"
                f"<label class=muted style='font-weight:700;font-size:13px;margin-top:14px'>Key safety</label>"
                f"<textarea name=key_safety rows=2>{ev(info.get('key_safety',''))}</textarea>"
                f"<label class=muted style='font-weight:700;font-size:13px'>Interactions</label>"
                f"<textarea name=key_interactions rows=2>{ev(info.get('key_interactions',''))}</textarea>"
                f"<div class=row><div><label class=muted style='font-weight:700;font-size:13px'>Seizure-type coverage (comma-separated)</label>"
                f"<input name=indications value='{inds}'></div>"
                f"<div><label class=muted style='font-weight:700;font-size:13px'>Labs to monitor (comma-separated)</label>"
                f"<input name=labs value='{labs}'></div></div>"
                f"<p style='margin-top:14px'><button class='btn sm' type=submit name=action value=save>Save (DRAFT)</button> "
                f"{verify_btn}</p>"
                f"</form></div>")
        meds_progress = (
            f"<div class=banner review style='display:flex;gap:16px;align-items:center;flex-wrap:wrap'>"
            f"<div style='flex:1 1 240px'>DRAFT - needs PI sign-off &middot; {verified} of {len(MEDS)} verified</div>"
            f"<div style='flex:1 1 160px;max-width:300px;height:9px;background:#f3e2b0;border-radius:999px;overflow:hidden'>"
            f"<div style='height:100%;width:{pct}%;background:#77A57C'></div></div></div>")
        add_dosing = (
            f"<div class=refcard><div style='font-weight:700'>Add / edit dosing</div>"
            f"<form method=post action='/settings/asm' style='margin-top:12px'>"
            f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
            f"<div class=row><div><label>Medicine</label><select name=med>{med_opts}</select></div>"
            f"<div><label>Age from (yr)</label><input type=number step=0.1 name=age_min></div>"
            f"<div><label>Age to (yr)</label><input type=number step=0.1 name=age_max></div></div>"
            f"<div class=row><div><label>Max dose (mg/day)</label><input type=number step=any name=max_dose></div>"
            f"<div><label>Increase step (mg)</label><input type=number step=any name=step></div>"
            f"<div><label>Every (weeks)</label><input type=number name=interval></div></div>"
            f"<p></p><button class='btn sm' type=submit>Save dosing (DRAFT)</button></form></div>")

        # ---- Red-flag criteria tab ----
        flag_cards = "".join(
            f"<div class=refcard><div style='display:flex;justify-content:space-between;align-items:center;gap:12px'>"
            f"<div style='font-weight:700;font-size:16px'>{html.escape(lbl)}</div>"
            f"<span class='pill routine' title='Always on'>&#128274; Active</span></div>"
            f"<div class=muted style='margin-top:8px'>{html.escape(desc)}</div></div>"
            for lbl, desc in SAFETY_TRIGGERS)
        rf_rows = "".join(f"<li>{html.escape(t.get('phrase',''))} "
                          f"<span class=muted>({html.escape(t.get('category',''))})</span></li>"
                          for t in rf.get("terms", [])) or "<li class=muted>none</li>"
        flag_words = (
            f"<div class=refcard><div style='font-weight:700'>Urgent words in free text</div>"
            f"<p class=muted>If a family types one of these, the check-in is flagged urgent (safety net; "
            f"never changes a recommendation).</p><ul>{rf_rows}</ul>"
            f"<form method=post action='/settings/redflag'>"
            f"<input type=hidden name=csrf value='{html.escape(self._doc_csrf())}'>"
            f"<div class=row><div><label>Phrase</label><input name=phrase placeholder='e.g. trouble breathing'></div>"
            f"<div><label>Group (optional)</label><input name=category value='clinician_added'></div></div>"
            f"<p></p><button class='btn sm' type=submit>Add phrase</button></form></div>")

        # ---- Decision logic / survey map ----
        logic_cards = "".join(
            f"<div class=refcard><div style='font-weight:700;font-family:ui-monospace,Menlo,Consolas,monospace'>{r}</div>"
            f"<div class=muted style='margin-top:6px'>{html.escape(d)}</div></div>" for r, d in _DECISION_LOGIC)
        survey_cards = "".join(
            f"<div class=refcard><div style='font-weight:700'>{html.escape(qq)}</div>"
            f"<div class=muted style='margin-top:4px'>{html.escape(vv)}</div></div>" for qq, vv in _SURVEY_MAP)

        def _formul(i):
            out = []
            for fm in i.get("formulations_rxnorm", []) or []:
                if fm.get("strengths_mg"):
                    out.append(f"{fm.get('form','')}: " + "/".join(str(s) for s in fm['strengths_mg']) + " mg")
                elif fm.get("mg_per_ml"):
                    out.append(f"{fm.get('form','')}: {fm['mg_per_ml']} mg/mL")
            return "; ".join(out)

        def _pace(i):
            b = (i.get("dose_bands") or [{}])[0]
            st = b.get("standard_uptitration_step_mg") or b.get("uptitration_step_mg_per_kg_per_day")
            iv = b.get("standard_uptitration_interval_weeks") or b.get("uptitration_interval_weeks")
            return f"+{st} every {iv} wk" if st and iv else ""

        tabs = [("meds", "Medications"), ("flags", "Red-flag criteria"), ("logic", "Decision logic"),
                ("interactions", "Interactions"), ("coverage", "Seizure-type coverage"),
                ("monitoring", "Monitoring & labs"),
                ("dosing", "Dosing & pacing"),
                ("formulations", "Formulations"),
                ("map", "Decision map")]
        tabbar = "<div class=reftabs>" + "".join(
            f"<button class=reftab data-tab={k}>{html.escape(v)}</button>" for k, v in tabs) + "</div>"

        def sec(k, inner):
            return f"<section class=refsection data-tab={k}>{inner}</section>"

        sections = (
            sec("meds", meds_progress + med_cards + add_dosing)
            + sec("flags", flag_cards + flag_words)
            + sec("logic", logic_cards)
            + sec("interactions", drug_list(lambda i: i.get("key_interactions")))
            + sec("coverage", drug_list(lambda i: ", ".join(i.get("indications", []) or [])))
            + sec("monitoring", drug_list(lambda i: ", ".join(i.get("labs", []) or []) or ("yes" if i.get("requires_lab_monitoring") else "")))
            + sec("dosing", _dosing_examples_html())
            + sec("formulations", drug_list(_formul))
            + sec("map", _decision_map_svg()))

        m = f"<div class='banner routine'>{html.escape(msg)}</div>" if msg else ""
        header = (
            "<div style='font-size:12px;font-weight:700;letter-spacing:.12em;color:#647084;text-transform:uppercase'>Known info the engine uses</div>"
            "<h1 style='margin:8px 0 0;font-size:28px;font-weight:700;letter-spacing:-.01em;color:#16191c'>Clinical reference</h1>"
            "<p class=muted style='margin:10px 0 0;max-width:64ch'>The dosing facts and rules the engine relies on. "
            "Everything here is DRAFT until the PI verifies it. "
            "<a href='/change-password'>Change password</a>.</p>")
        return self._send(page("Clinical reference", m + header + tabbar + sections, DOC_NAV))

    def _do_add_band(self, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        one = lambda k, d="": (f.get(k) or [d])[0]
        med = one("med")
        try:
            amin = float(one("age_min", "0") or 0)
            amax = float(one("age_max", "0") or 0)
            maxd = float(one("max_dose", "0") or 0)
            step = float(one("step", "0") or 0)
            interval = int(one("interval", "0") or 0)
        except ValueError:
            return self._send(self._settings_form("Please enter numbers for ages, dose, step, and weeks."), 400)
        if not med or maxd <= 0 or step <= 0 or interval <= 0:
            return self._send(self._settings_form("Medicine, max dose, step, and interval are required (> 0)."), 400)
        raw = _load_json(_ASM_FILE, {})
        raw.setdefault(med, {}).setdefault("dose_bands", []).append({
            "label": f"{amin:g}-{amax:g} yr", "min_age_years": amin, "max_age_years": amax,
            "dose_range_max_mg_per_day": maxd, "max_daily_mg": maxd,
            "standard_uptitration_step_mg": step, "standard_uptitration_interval_weeks": interval})
        _save_json(_ASM_FILE, raw)
        db.audit("doctor", "add_asm_dosing", f"{med} {amin:g}-{amax:g}y")
        return self._send(self._settings_form(f"Added dosing for {med.replace('_',' ').title()}."))

    def _do_med_edit(self, f):
        """Clinician edits a medicine's clinical facts in the Clinical reference.
        Writes to data/asm_rules.json; values stay DRAFT (verified:false) unless
        the clinician explicitly marks the medicine verified."""
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        one = lambda k, d="": (f.get(k) or [d])[0]
        med = one("med")
        if not med:
            return self._send(self._settings_form("Pick a medicine to edit."), 400)
        raw = _load_json(_ASM_FILE, {})
        info = raw.setdefault(med, {})
        action = one("action", "save")

        def _num(v):
            try:
                return float(v) if v not in ("", None) else None
            except ValueError:
                return None

        if action in ("verify", "unverify"):
            ver = (action == "verify")
            for b in info.get("dose_bands", []):
                b["verified"] = ver
            info["verified"] = ver
        else:
            # Free-text clinical facts
            for fld in ("key_safety", "key_interactions"):
                v = one(fld).strip()
                if v:
                    info[fld] = v
            inds = one("indications").strip()
            if inds:
                info["indications"] = [s.strip() for s in inds.split(",") if s.strip()]
            labs = one("labs").strip()
            if labs:
                info["labs"] = [s.strip() for s in labs.split(",") if s.strip()]
                info["requires_lab_monitoring"] = True
            # Dosing numbers -> first band (created if needed); stays DRAFT.
            ceiling, step, interval = _num(one("ceiling")), _num(one("step")), _num(one("interval"))
            if any(v is not None for v in (ceiling, step, interval)):
                bands = info.setdefault("dose_bands", [])
                b = bands[0] if bands else {}
                if not bands:
                    bands.append(b)
                if ceiling is not None:
                    b["absolute_max_mg_per_day"] = ceiling
                    b["max_daily_mg"] = ceiling
                    b["dose_range_max_mg_per_day"] = ceiling
                if step is not None:
                    b["standard_uptitration_step_mg"] = step
                if interval is not None:
                    b["standard_uptitration_interval_weeks"] = int(interval)
                b["verified"] = False          # edited numbers are DRAFT
                info["verified"] = False
        _save_json(_ASM_FILE, raw)
        db.audit("doctor", "edit_reference", f"{med} ({action})")
        return self._send(self._settings_form(f"Saved {med.replace('_',' ').title()} ({action})."))

    def _do_add_redflag(self, f):
        if not self._doc_csrf_ok(f):
            return self._send(page("Error", "<div class=card>Bad request (CSRF).</div>"), 400)
        phrase = ((f.get("phrase") or [""])[0]).strip()
        cat = ((f.get("category") or ["clinician_added"])[0]).strip() or "clinician_added"
        if not phrase:
            return self._send(self._settings_form("Please enter a phrase."), 400)
        raw = _load_json(_REDFLAG_FILE, None) or _load_json(_REDFLAG_TEMPLATE, {"terms": []})
        raw.setdefault("terms", []).append({"phrase": phrase.lower(), "category": cat, "severity": "urgent"})
        _save_json(_REDFLAG_FILE, raw)
        db.audit("doctor", "add_redflag", phrase[:40])
        return self._send(self._settings_form(f"Added urgent phrase: '{phrase}'."))


def main():
    seeded = db.init_db()
    local = HOST in ("127.0.0.1", "localhost")
    print(f"\n  Aim 2 Medication Assistant - clinic app")
    print(f"  Binding:       {HOST}:{PORT}  ({'local only' if local else 'network'})")
    print(f"  Doctor login:  {base_url()}/login")
    if PUBLIC_BASE_URL:
        print(f"  Public URL:    {PUBLIC_BASE_URL}")
    if seeded:
        print(f"  Seeded doctor account - username 'doctor', password '{seeded}'  (CHANGE THIS)")
    print(f"  Medications: {len(MEDS)} | ASM dosing loaded: {'yes' if current_asm() else 'no (titration shows unavailable)'}")
    if not local and not COOKIE_SECURE:
        print(f"  [!] Serving on a network without TLS/Secure cookies - put a TLS reverse")
        print(f"      proxy in front and set ASM_COOKIE_SECURE=1 before any real use.")
    print(f"  Synthetic data only - real patient data needs IRB + HIPAA/CITI sign-off first.")
    if AUTORELOAD:
        print(f"  Auto-reload:   on  (editing a source file restarts the server)")
    print(f"  Ctrl+C to stop\n")

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    state = {"reload": False}
    if AUTORELOAD:
        _start_reloader(httpd, state)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping.")
        return
    finally:
        httpd.server_close()
    if state.get("reload"):
        # Replace this process with a fresh one so the edited code is loaded.
        # Use sys.executable (full path) as argv0; orig_argv[1:] (3.10+) preserves
        # interpreter flags like -u and the script path across the restart.
        rest = list(getattr(sys, "orig_argv", []) or [])[1:] or list(sys.argv)
        os.execv(sys.executable, [sys.executable] + rest)


def _watched_files():
    """Source files whose edits should trigger an auto-restart."""
    import glob
    files = set()
    for pat in ("*.py", "**/*.py", "data/*.json", "static/*"):
        for f in glob.glob(os.path.join(_APP, pat), recursive=True):
            if "__pycache__" not in f and os.path.isfile(f):
                files.add(f)
    return files


def _start_reloader(server, state):
    """Watch source files; on any change, cleanly stop the server so main() can
    re-exec a fresh process. Stdlib-only, ~1s polling, daemon thread."""
    import threading

    def snapshot():
        snap = {}
        for f in _watched_files():
            try:
                snap[f] = os.stat(f).st_mtime
            except OSError:
                pass
        return snap

    baseline = snapshot()

    def loop():
        while True:
            time.sleep(1.0)
            cur = snapshot()
            changed = [f for f in cur if baseline.get(f) != cur[f]] + \
                      [f for f in baseline if f not in cur]
            if changed:
                rel = os.path.relpath(sorted(changed)[0], _APP)
                print(f"\n  [auto-reload] {rel} changed - restarting...\n")
                state["reload"] = True
                server.shutdown()          # unblocks serve_forever() in main()
                return

    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    main()
