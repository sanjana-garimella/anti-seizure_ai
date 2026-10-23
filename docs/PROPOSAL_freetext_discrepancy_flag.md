# Proposal - Catch seizures/side-effects described in free text but not captured structurally

**Status:** DRAFT proposal for PI + engineering review · 2026-07-16 ·
**not built as of 2026-08-04** - re-verified against the code: no `discrepanc*`
logic exists anywhere in `aim2_engine/`, `categorize.py` and `decision.py` still
contain zero `free_text` references (so the guardrail below holds), and `Q3`
remains a binary no/yes defaulting to `no` (`clinic.py`, the `one("seizures","no")`
mapping). Everything described below still describes the intended change, not the
current behavior.
**Owner:** engineering (Sanjana) · **Clinical decisions:** Dr. Barbour
**Guardrail:** everything below stays *advisory / human-in-the-loop*. No NLP output
ever enters `engine_core/`; the recommendation stays a deterministic function of the
structured survey answers.

---

## The gap

The engine's whole "breakthrough vs. seizure-free" split rests on **one structured
field**: Q3 ("has your child had any seizures?"). Verified in code:

- `snapshot.py` sets `seizure_control` straight from `answers["Q3"]` via the `_SEIZ`
  map - a binary **No / Yes, defaulting to No** (`clinic.py` builds it as
  `one("seizures", "no")`, ~line 1695 as of 2026-08-04). There is no "not sure /
  maybe something happened" option at Q3.
- `categorize.py` trusts that field completely - the entire breakthrough branch is
  gated on `if seizure_control == BREAKTHROUGH`. It never infers a seizure from the
  described episodes or the free text.
- The seizure-**type** question (Q4) *is* forgiving - it offers "acting off /
  not responding…" and "Not sure → describe in your own words" - **but Q4 only
  appears once Q3 is already "Yes."**
- Free text (Q13 catch-all, and the Q4 "describe" box) is **stored and shown to the
  physician, never read by the decision** (confirmed: `categorize.py`/`decision.py`
  contain no `free_text` reference).

**Consequence:** a family who doesn't recognize an event as a seizure answers Q3
"No", never sees the forgiving type box, and describes the event only in Q13. The
engine then follows the **seizure-free** path and can output "no change / continue."
The red-flag scanner won't catch it either, because a calmly-described routine
breakthrough contains no urgent term - so the check-in is triaged **routine** and
sits low in the queue. The only backstop is the physician happening to read the
verbatim free text on a case the system has labeled low-priority.

This is an **efficacy** miss, not an urgent-safety one: the tool effectively says
"seizures are controlled" for a child who is still seizing, so the dose never gets
escalated. That quiet failure mode is the concern.

---

## Clinical decisions needed from the PI

1. **Should Q3 allow "not sure / maybe"?** Add a third option to the seizure-
   occurrence question ("Not sure - something happened but I'm not certain it was a
   seizure") that reveals the same "describe in your own words" box Q4 already has.
   Confirm the exact wording.
2. **How should "not sure" route?** Options: (a) treat it as a possible breakthrough
   and **defer** to physician review (`INSUFFICIENT_DATA`), or (b) keep it seizure-
   free but flag for review. (Ties into the existing "not sure handling" item in
   `NEEDS_PI_SIGNOFF.md` #21.)
3. **How loud should the discrepancy be?** The note itself *will* appear in the
   physician's clinical summary (design decision - Sanjana). Remaining PI calls: should
   the case be bumped to **"needs review"** (recommended) or escalated **"urgent"**, and
   should a discrepancy ever **withhold the draft family message** pending review, or
   only re-prioritize it for the doctor?

None of these let the engine act on prose - they either add a structured option
(1–2) or raise an advisory flag for a human (3).

---

## Proposed mechanism (engineering - advisory only)

The NLP **structurer** already parses free text into suggested findings; today it
only writes to the audit log. Reuse it as a **review-time discrepancy check**:

1. On submission, run the structurer over the free-text fields (Q13 + any "describe"
   box) - this already happens for the audit trail.
2. Compare its findings against the structured answers. If it finds a **seizure
   mention while Q3 = No** (or a side-effect while none was reported), record a
   `discrepancy` note: *"Free text mentions a possible seizure, but 'no seizures'
   was selected - please confirm."*
3. **Include that note in the physician-facing clinical summary**, clearly labeled as
   advisory / unverified NLP - e.g. *"⚠ NLP advisory (unverified): free text mentions a
   possible seizure, but 'no seizures' was selected - please confirm."* - and also feed
   it into `triage()` so the case is raised to at least **"needs review"** (triage level
   per decision 3).
4. The engine's category and rationale are **unchanged**. Crucially, the line is
   appended **where `clinic.py` assembles the physician's summary for display/EHR - NOT
   inside `engine_core.build_clinical_summary`** - so no NLP output enters the rules
   engine (see guardrail). The "advisory/unverified" label keeps it from being read as a
   rules-derived or confirmed clinical finding.

This is deliberately the *advisory* half of the NLP layer (Point A/B), not the
decision path. It cannot change or lower a recommendation; it can only draw a human's
eye to a mismatch.

---

## Why this respects the non-negotiables

- **Rule-based decision only:** `engine_core.build_clinical_summary` stays NLP-free;
  the advisory line is appended by `clinic.py` when it composes the physician's note for
  display/EHR. The structurer's output still never enters `engine_core/` and never
  changes the recommendation - it is display context + triage priority only, always
  labeled advisory/unverified so it can't be mistaken for a rules-derived finding.
- **Human-in-the-loop:** the physician still approves every recommendation; this
  only changes what the physician *sees* and how the case is *prioritized*.
- **Defer on ambiguity:** decision 2 (route "not sure" to `INSUFFICIENT_DATA`) makes
  the *structured* ambiguity a defer - which the engine is allowed to act on.

## Testing

- Keep the existing invariant `test_nlp.py::…decision_path_untouched_by_free_text`
  (the category must not change when only free text changes).
- Add: a submission with Q3 = No + a seizure described in free text → engine category
  unchanged (seizure-free) **and** a discrepancy flag is raised + triage ≥ "needs
  review".
- Add: Q3 = "not sure" (if adopted) → `INSUFFICIENT_DATA` / defer, per decision 2.

## Scope / effort

Small. The structurer, the audit hook, the red-flag→triage pattern, and the
review-screen flag rendering all already exist; this wires them together for the
"described-but-not-checked" case. The survey-wording change (decision 1) is a
`clinic.py` form edit + the `_SEIZ` mapping in `snapshot.py`.
