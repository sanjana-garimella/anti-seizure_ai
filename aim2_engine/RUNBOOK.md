# Runbook - How to Run the Aim 2 Engine

Plain steps to run the engine, the clinician app, the tests, and the validation
harness - plus fixes for the common problems. **Run every command from the
`aim2_engine/` directory.**

Requirements: **Python 3.10+**, no third-party packages (pure standard library).
Check with `python3 --version`.

---

## Quickstart (about 1 minute)

Run these four commands in order. The "you should see" lines tell you it worked.

```bash
cd aim2_engine

# 1. Unit tests
python3 -m unittest discover -s tests
#    you should see:  Ran 532 tests ... OK
#    (transformer tests self-skip if transformers/torch aren't installed - expected,
#     and they print a "TransformerStructurer unavailable" line. Not a failure.)

# 2. (Re)build the two generated data files (clinician dosing + gold cases)
python3 data/_build_asm_rules.py          # you should see: wrote ... with 11 drugs
python3 validation/_build_gold_cases.py   # you should see: wrote 63 cases -> ...

# 3. Validation against Dr. Barbour's 63 reviewed cases
python3 validation/harness.py
#    The no-argument run now DEFAULTS to the gold set (gold_cases.json) and
#    auto-loads data/asm_rules.json, so you should see:
#      cases 63 · concordance 1.0 · Cohen's kappa 1.0 ... MET
#    (Explicit form still works: python3 validation/harness.py validation/gold_cases.json --asm data/asm_rules.json)

# 4. Independent proof suite (rule + ladder proofs, red-team safety cases)
python3 verification/verify_all.py
#    you should see:  RESULT: PASS
#      asm_rules 0 FAIL / 19 WARN · ladder 0 FAIL / 0 WARN / 17 INFO · red-team 0 misses, 0 false alarms
#    WARNs are hardening/PI-review targets, not breakage. Artifacts land in
#    verification/artifacts/.
```

If step 1 says OK, step 3 says **MET**, and step 4 says **PASS**, the engine is healthy.

> Two files in step 2 are **generated, never hand-edited**:
> `data/asm_rules.json` (built from `data/_build_asm_rules.py`) and
> `validation/gold_cases.json` (built from `validation/_build_gold_cases.py`).
> If you change clinical values or cases, edit the *builder* and re-run it.

---

## Run the clinician app (UI + backend together)

**There is no separate frontend and backend to connect.** `clinic.py` is one
local program that does all three jobs at once:

- serves the **web pages** (the UI you see in the browser),
- runs the **real engine** (`engine_core`) to produce each recommendation,
- stores patients/answers/reviews in a local database (`clinic_data/clinic.db`).

So "view the UI connected to the backend" = **start `clinic.py` and open the
browser**. The page you see is already talking to the live engine.

### Fastest way to see it working (recommended)

Use the **demo launcher** - it loads synthetic example patients so the screens
are populated right away.

1. Double-click **`Demo - Start Clinic.command`** (in the project root, the
   `amt/` folder). A terminal window opens and seeds demo data.
2. Your browser opens at `http://127.0.0.1:8000/login`.
3. Log in:  username **`doctor`**  ·  password **`demo1234`**.
4. You land on the **Queue** - the list of check-ins waiting for review (this
   list comes from the backend/database).
5. Click **Review** on any row to open the recommendation screen: the engine's
   suggested next step with an **"AI draft · rules-based"** badge, a collapsible
   **"Why - the rule(s) that fired"** trace, the titration schedule (tagged
   *draft/unverified*), the full clinical note, and Approve / Edit / Reject.
6. Keep the terminal window open while you use the app. Close it (or Ctrl-C) to
   stop the server.

### Start empty and create your own case

1. Double-click **`Start Clinic.command`** (same folder), or run
   `python3 clinic.py` from `aim2_engine/`.
2. Open `http://127.0.0.1:8000/login` and log in as `doctor` (first run prints a
   one-time password in the terminal; change it under the "Password" menu).
3. From the top nav: **"Family survey"** (`/survey`) opens the patient check-in
   form. Fill it in and submit - **this is the moment the backend runs the
   engine** on your answers.
4. Go back to the **Queue** (`/`); the new submission appears. Click **Review**
   to see the engine's recommendation rendered in the UI.

The pages you'll actually use: `/login`, `/` (doctor queue), `/survey` (open
family check-in), `/review/<id>` (the recommendation screen). Nothing reaches a
family without the doctor pressing Approve.

The rest of the app:

- **Pages you can open (GET):** `/patients/new` (which also renders the patient
  list), `/audit`, `/nlp-audit`, `/validation`, `/settings`, `/change-password`,
  `/logout`.
- **Form targets (POST only - opening these in the address bar 404s):**
  `/patients` (add a patient), `/patients/<id>/invite`, `/bulk-triage`,
  `/settings/asm`, `/settings/med-edit`, `/settings/redflag`.
- **Family-facing token routes:** `/s/<token>` (the invited check-in;
  `POST /s/<token>/rank` records the family's medication-preference ranking) and
  `/m/<token>` (the approved message).

- **Queue filters and tiles** use the clinical classification, not the internal
  priority band: **Emergent · Urgent · Non-urgent · Safety · No seizures**
  (`/?f=emergent|urgent|nonurgent|safety|noseizures|reviewed`). Safety is not
  exclusive with a tier, so the counts overlap and do not sum to the total; only
  "No seizures" excludes safety. Cards are sorted priority first, then tier.

> **The `/review` page renders CACHED drafts and summaries**, not a fresh engine
> run. If a draft looks stale after an engine or output-format change, regenerate it
> rather than assuming the engine is wrong:
>
> ```bash
> python3 tools/regen_notes.py             # dry run: what would change, and why
> python3 tools/regen_notes.py --apply     # refresh the cached notes
> ```
>
> It re-runs the real engine per submission and refreshes every recommendation
> column **except the family draft** (`--with-drafts` opts in - a note refresh should
> not silently replace text a clinician is about to send). It **refuses to rewrite a
> row unless the rebuild reproduces the stored total daily dose and category**, so a
> submission recorded before `submissions.med_context_json` existed - whose Q1a dose
> is no longer recoverable - is reported and skipped instead of being quietly
> rewritten against the doctor's intake dose.

> **Shaping the demo queue:** `tools/seed_demo_queue.py` (dry run by default,
> `--apply`, idempotent) enforces one submission per patient and 3-4 worked examples
> per recommendation category, including emergent cases. Every row is produced by
> running the actual engine over survey answers, never hand-written into the
> recommendation columns, so a seeded case cannot drift from the rules. Simulated
> data only; ids are prefixed `sim-`.

> **Never write `clinic_data/clinic.db` in place through a file-sync mount.**
> SQLite journaling plus FUSE semantics have corrupted it (the evidence is still on
> disk as `clinic.db.CORRUPT-20260728-162740`). Both tools above do the safe thing:
> copy the DB off-mount with `sqlite3.Connection.backup()`, work there, run
> `PRAGMA integrity_check`, write a timestamped `.bak-<ts>`, stage the new file
> beside the target, then swap it in with an atomic rename. Follow the same pattern
> for any ad-hoc DB fix.

> **The UI is JavaScript-free by design.** A strict CSP (`script-src 'self'`)
> blocks inline `<script>` blocks and `on*=` attributes - an inline script once
> silently emptied the dose dropdowns for two days. Pass data to `static/clinic.js`
> through a JSON data block; never add an inline script or handler.

> **Troubleshooting the app**
> - *Browser says "can't connect" / page won't load:* the server isn't running.
>   Make sure the terminal window from the launcher is still open, then reload
>   `http://127.0.0.1:8000`.
> - *"Address already in use" / port 8000 busy:* an old copy is still running.
>   Close the other terminal window, or run on another port:
>   `ASM_PORT=8001 python3 clinic.py` then open `http://127.0.0.1:8001`.
> - *Queue is empty:* you started clean - either use the **Demo** launcher, or
>   create a case via **Family survey** (above).
> - *Forgot the demo password:* it's `demo1234` (set by the demo launcher).

### Quick, login-free form (no database)

For trying single inputs without the queue/login/database, `ui.py` serves one
page that also calls the real engine:

```bash
python3 ui.py                # then open http://127.0.0.1:8000
```

---

## Use the engine as a library

```python
from engine_core import (run_engine, snapshot_from_interval,
                         PatientContext, MedicationContext, PhysicianContingency)

patient = PatientContext(external_id="PT01", age_years=8, weight_kg=30)
medication = MedicationContext(medication="levetiracetam", dose_amount=250,
                               dose_unit="mg", frequency_per_day=2)
contingency = PhysicianContingency()        # no chart context needed
answers = {"Q3": "yes", "Q10a": "still_increasing",
           "seizure_episodes": [{"look": ["focal"], "duration": "2_5min",
                                 "frequency": 2, "frequency_unit": "per_week"}]}

snap = snapshot_from_interval(patient, medication, contingency, answers)
rec = run_engine(snap)
print(rec.category)        # e.g. "escalate"
print(rec.reason_summary)  # one-line "why" for the physician
print(rec.rationale)       # which rule fired + the inputs it used
```

To get **dose numbers** in `rec.titration_schedule`, load clinician dosing data:

```python
from engine_core.asm_rules import load_asm_rules
rules = load_asm_rules("data/asm_rules.json")
rec = run_engine(snap, asm_registry=rules)
```

Without it the engine still returns a category and rationale, but **no dose
numbers** (by design - it never invents clinical values).

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'engine_core'`.**
You're not in `aim2_engine/`. `cd aim2_engine` first (tests and the harness add
the path themselves, so they run from anywhere).

**`python3 -m unittest` says "Ran 0 tests".**
Keep the flag: `python3 -m unittest discover -s tests`, from `aim2_engine/`.

**Validation κ dropped, or J4/H6 suddenly "switch → no_change/insufficient".**
`data/asm_rules.json` is stale or was overwritten without the clinical fields
(`treats_only`, `sjs_risk`, …). **Rebuild it:** `python3 data/_build_asm_rules.py`,
then re-run the harness. (`test_gold_validation.py` now rebuilds it automatically,
so this can't silently pass anymore.)

**Want the legacy 12-case proposal set?**
As of the 2026-06 harness fix, the no-argument run DEFAULTS to the gold set
(63 cases, κ = 1.0, MET) and auto-loads `data/asm_rules.json` - that's the
canonical check. The 12 legacy proposal cases, whose labels predate Dr. Barbour's
2026-06 rules, are still reachable explicitly (note the `validation/` prefix):

```bash
python3 validation/harness.py validation/simulated_cases.json --no-asm
#    12 cases · concordance 0.5 · κ = 0.3793 (NOT MET) - expected, by design.
```

A low κ there is not a regression: those labels describe the pre-2026-06 rules.
It is a self-consistency check only.

**The harness reports a disagreement.**
J2 now agrees (offer switch, `NEEDS_PI_SIGNOFF.md` #14). Any disagreement is
worth investigating.

**Titration schedule says "unavailable" / no dose numbers.**
No clinician dosing data loaded, or the patient's age/weight matches no dose
band, or the band lacks a step/ceiling. Load `data/asm_rules.json` and pass
`asm_registry=`.

**A recommendation category looks wrong.**
Usually a mis-mapped input - unknown answer codes fall back to safe defaults, so
a typo (e.g. `"Yes"` vs `"yes"`) changes the result. Inspect:
`from engine_core import survey_from_answers; print(survey_from_answers(answers))`,
cross-check codes in `DATA_CONTRACT.md`, and read `rec.rationale` for the rule
that fired.

**`JSONDecodeError` loading a data file.**
Validate the JSON (no trailing commas, quoted keys). Keys starting with `_` (e.g.
`_README`) are ignored by the loaders - leave them in.

**`SyntaxError` / dataclass errors on start.**
You're on an old Python. Use 3.10+; on some systems the interpreter is
`python3.12`.

**`database disk image is malformed` / the queue suddenly loses rows.**
`clinic_data/clinic.db` was written in place through a synced/mounted view of the
folder, which corrupts SQLite. Recover from the newest good copy in
`clinic_data/` (`clinic.db.bak-20260728-144520` is the clean recovery point;
`clinic.db.CORRUPT-*` is the damaged one kept for reference). When a tool or
script has to modify the DB, copy it somewhere local, edit the copy, then swap it
back by atomic rename - never edit it in place through the mount.

**Scripts that are expected to report failures right now** (none of these are the
unit suite, and none affect the gold-set results):

- `validation/confusion_analysis.py` - crashes on start. Path bug, not missing
  data: the answer key lives at
  `validation/private/INTERNAL_engine_answer_key_DO_NOT_SEND.csv`, but line 32
  looks for it at the project root.
- `validation/holdout_test.py` - held-out half passes; the fuzz half reports 0
  crashes and 0 safety message leaks (#51 decided 2026-10-25: no family message on a
  safety flag).
- `validation/creative_tests.py` - 14/14.
- `manual_check.py` - 37/37.

Use `python3 -m unittest discover -s tests` and `verification/verify_all.py` as the
health signal, not these.
