# Architecture - Aim 2 Medication Assistant (hybrid: rule engine + language models)

Synthetic data only. See `NON_NEGOTIABLE_RULES.md`.

---

## Folder map

```
amt/
├── aim2_engine/          ← LIVE deliverable (the engine, the clinic app, validation + verification)
├── kristen/              ← Dr. Barbour's workbooks & notes - SOURCE OF TRUTH for clinical values
├── decision_map/         ← Generated Obsidian decision-flow canvas + knowledge graph (+ build scripts)
├── docs/                 ← ARCHITECTURE, OFFICIAL_FDA_DOSING_REFERENCE, VALIDATION_PLAN,
│                            CLINICAL_REVIEW, NEUROSYMBOLIC_RATIONALE,
│                            PROPOSAL_freetext_discrepancy_flag, ai-engineering-applicability
├── synthetic_validation/ ← synthetic-case generation + validation scratch work
├── _archive/             ← superseded files kept for reference (old decision tables, review cases, templates)
└── README.md / NON_NEGOTIABLE_RULES.md / LICENSE / NOTICE
```

*Removed in the 2026-07-16 cleanup: the `review/` packet folder, `figs/`,
`Kristen_Review_Packet.tex`, `PROJECT_LOG.md`, the dated review snapshots, and the
standalone GUARDRAILS/COMPLIANCE docs (merged into NON_NEGOTIABLE_RULES §6).
`aim2_proposed/` became `aim2_engine/clinician_verification/`. Version control:
the folder is a git repository (consolidated to one commit on 2026-10-23); the
2026-07-16 deletions predate it and are not recoverable.*

---

## End-to-end data flow

```
Clinician-supplied dosing tables          Between-visit survey answers
(data/asm_rules.json - 11 DRAFT drugs,    (§7 question codes, from Aim 1 platform)
 verified:false, pending PI sign-off)
          │                                           │
          ▼                                           ▼
  engine_core/asm_rules.py             engine_core/snapshot.py
  ASMRule (dose bands, ceilings,       IntervalSnapshot
  titration steps, interactions)       (structured patient context)
          │                                           │
          └──────────────┬────────────────────────────┘
                         ▼
              engine_core/categorize.py
              PatientStatus   (field names + actual enum values)
              ┌─ seizure_control      seizure_free | breakthrough
              ├─ side_effect_severity none | manageable_but_unwanted |
              │                       intolerable | unknown
              ├─ side_effect_level    0–5 (Barbour 2026-06-25 6-level scale)
              ├─ phase                titrating | holding | steady  (plain str)
              ├─ breakthrough_tier    non_urgent | urgent | emergent (per type)
              ├─ adherence            almost_never_misses | sometimes_misses |
              │                       stopped
              ├─ family_preference    no_strong_preference | change_dose |
              │                       try_different_medicine | other
              ├─ dose_goal_status     still_increasing | at_goal | not_sure
              │                       (+ at_goal / dose_maximized booleans)
              └─ safety signals       sjs_triggered (bool) · urgent_safety (list)
                                      · whole_body_danger · escalate_faster
                                      · the sjs_* rationale tags (sjs_rash,
                                        sjs_risk_comed, sjs_restart_risk, …)
                         │
                         ▼
              engine_core/decision.py
              Decision  ← ordered flat rule list (first match wins) + Barbour
              │           2026-06-25 table-driven branches (no-seizures + breakthrough)
              ┌─ category   ESCALATE | SLOWER_UPTITRATION | SWITCH_MEDICATION |
              │             ADD_MEDICATION | LOWER_DOSE | HOLD | NO_CHANGE | INSUFFICIENT_DATA
              ├─ lab_orders / dosing_note / guidance_steps / urgency_tier
              ├─ rationale  [{rule, description, inputs}, …]   ← always emitted
              ├─ pace        faster | standard | slower
              ├─ followup_weeks
              ├─ safety_concern_flag
              ├─ safety_notes        (advisory, not decisions)
              └─ switch_target / switch_exclusions
                         │
          ┌──────────────┼──────────────────────────┐
          ▼              ▼                           ▼
  outputs/build_    outputs/build_           outputs/build_
  clinical_summary  titration_schedule       patient_message
  (EHR-ready text)  (dose numbers only       (template A1..H selected
                     from the clinician       from the STRUCTURED
                     ladder / asm_rules;      decision; doses only from
                     never invented)          the clinician ladder)
                            │
                            └─ engine_core/dose_ladder.resolve_plan()
                               exact next rungs from the PI's ladder
          │              │                           │
          └──────────────┴───────────────────────────┘
                         │
              engine_core/api.py  →  Recommendation
              (run_engine() assembles all outputs + drug-identity annotation)
                         │
                         ▼
              ┌── Physician reviews & approves ──┐
              │   (REQUIRED - no bypass)         │
              └──────────────────────────────────┘
                         │
                         ▼
                   Family receives message
```

---

## Language-model / NLP layer (outside the decision path)

The system is hybrid: the rule engine above decides the category and doses; this
layer uses machine learning on the family's free text. In the app, free text runs
through a transformer NER model by default (`ASM_NLP=humadex`; `clinical_bert`,
`biobert` or the `lexicon` word-list fallback are options) and through the red-flag
detector, whose hits can only raise triage priority. LLM rewording of the family
message (`nlp/generate.py`) is optional and guarded. None of it changes the
recommendation category or a dose. A proposed retrieval (RAG) extension is in the
README roadmap.

```
Free-text survey field
        │
        ├─► nlp/structurer.py   (A) suggests structured field values → clinician reviews
        └─► nlp/redflags.py     (B) detects urgent phrases → escalates to physician only

After the decision is made:
        └─► nlp/generate.py     (D) drafts the family message from the engine's
                                     already-decided outputs; guardrails in
                                     nlp/guardrails.py block fabrication/contradiction
```

---

## Key files

| File | Role |
|---|---|
| `engine_core/api.py` | `run_engine()` - the single public entry point |
| `engine_core/snapshot.py` | Input contract: `IntervalSnapshot` + `snapshot_from_interval()` |
| `engine_core/categorize.py` | Survey answers → `PatientStatus` categories |
| `engine_core/decision.py` | Ordered rule list → `Decision` |
| `engine_core/outputs.py` | Three outputs (summary, schedule, message) + `family_message_allowed_doses()` - the single source of the mg numbers a draft may contain. The clinician summary is built by `_Note` into the fixed numbered sections of `SUMMARY_SECTIONS`; that stable grammar is what lets `clinic.py` lay it out for reading (format documented in `aim2_engine/DATA_CONTRACT.md`) |
| `engine_core/dose_ladder.py` | `resolve_plan()` - the exact next dose rungs up from the patient's current dose, using only the clinician's explicit rungs |
| `engine_core/med_selection.py` | Rule-based switch/add ranking; drops avoided meds, defers when <3 remain, always `requires_physician_review` |
| `engine_core/drug_identity.py` | Drug-name normalizer + same-family annotation |
| `engine_core/humanize.py` | Plain-language rendering of the structured output |
| `engine_core/titration_config.py` | Loader for the numeric thresholds + urgency config |
| `engine_core/formulation.py` | Converts ladder doses to tablet counts or liquid mL (age rule, one tablet strength, -10%/+30% window, never above the absolute max); rules in `data/formulation_rules.json` |
| `engine_core/initiation.py` | Week-by-week starting schedule for a switch/add medicine from the PI's v9 "Initiation" tabs (`data/initiation_schedules.json`); defers on no band, two bands, or a missing tab |
| `engine_core/version.py` | `ENGINE_VERSION` (currently `0.3.0`) |
| `engine_core/asm_rules.py` | `ASMRule` dataclass + JSON loader |
| `data/asm_rules.json` | **Clinician-supplied dosing** - 11 DRAFT drugs (`verified:false`), built by `_build_asm_rules.py`, pending PI sign-off |
| `data/asm_rules.template.json` | Blank template for the PI to fill |
| `data/titration_thresholds.json` | Numeric thresholds referenced by rules |
| `data/no_seizures_decision_table.json` | **Barbour 2026-06-25** seizure-free action grid (phase × side-effect level), `verified:false` |
| `data/breakthrough_decision_tables.json` | **Barbour 2026-06-25** breakthrough tables (non_urgent/urgent/emergent × phase × level), `verified:false` |
| `data/breakthrough_urgency_thresholds.json` | **Barbour 2026-06-25** per-seizure-type urgency cutoffs (duration s + frequency) |
| `data/dose_titration_ladder.json` | The exact per-band dose rungs from `kristen/final_doses_titration_v9.xlsx`, built by `_build_dose_ladder.py`, `verified:false`; `data_quality_flags` mark suspected source copy-errors (band-specific) |
| `data/medication_selection_rules.json` | 9 ranked medicines + 21 favor/avoid rules from `kristen/Switching to a different medication.xlsx`, `verified:false` (kidney-stones rule encoded but inactive) |
| `validation/harness.py` | Cohen's κ runner (+ weighted κ/PABAK/macro-F1 via metrics.py); default = gold set (κ = 0.976) |
| `validation/metrics.py` | Stdlib metrics toolkit: weighted κ, PABAK, macro P/R/F1, safety sens/spec/FNR + Wilson CIs, Bland-Altman/ICC, bootstrap, Fisher, McNemar (self-test) |
| `validation/holdout_test.py` | Held-out generalization (independent spec-oracle - passes) + stress/fuzz over ~4,000 malformed inputs. The fuzz half reports **0 crashes** and **0 safety message leaks** (the engine withholds the family message on a safety flag, `NEEDS_PI_SIGNOFF.md` #51, decided 2026-10-25) |
| `validation/creative_tests.py` | Metamorphic/invariant properties + adversarial clinical traps + exact-boundary checks. Currently **14/14** |
| `validation/confusion_analysis.py` | Safety-flag + recommendation confusion matrices vs the answer key. Fixed 2026-09-30 (reads the key from `validation/private/`) |
| `validation/gold_cases.json` | Gold-standard labels (Dr. Barbour's picks on the 63 synthetic cases - not a held-out chart review) |
| `verification/verify_all.py` | Independent proof suite: re-derives the ASM rules + ladder from source, runs red-team safety cases, writes `verification/artifacts/`. Currently **PASS** (0 FAIL; 19 + 2 WARN; 0 must-catch misses) |
| `clinic.py` / `clinic_store.py` | Local stdlib web app (doctor queue, family check-in, review + approval) and its SQLite layer. JS-free by design under a strict CSP |
| `manual_check.py` | One-command confirm of the recent changes (37 scenarios; **37/37**) |
| `tests/` | 499 offline unit tests across 25 files (stdlib only; transformer tests self-skip without torch) |
| `tools/regen_notes.py` | Refreshes the **cached** `/review` notes after an engine or output-format change (the page renders stored text, not a fresh run). Dry-run default; preserves the family draft; refuses to rewrite a row unless the rebuild reproduces the stored dose + category |
| `tools/seed_demo_queue.py` | Shapes the demo queue: one submission per patient, 3-4 worked examples per category including emergent. Rows are produced by running the real engine, never hand-written |

---

## Invariants (never violate)

1. **Decision path is rule-based only.** `categorize.py` → `decision.py` → `outputs.py` contains zero ML/LLM calls, zero network I/O, zero invented clinical values.
2. **Same input → same output.** `run_engine()` is a pure function.
3. **Every output carries provenance.** `rationale` always lists which rule fired and which inputs it used.
4. **Physician approval is mandatory.** `requires_physician_review = True` on every `Recommendation`; no output goes to families without approval.
5. **Defer on ambiguity.** Insufficient or contradictory inputs → `INSUFFICIENT_DATA`, not a guess.
6. **No dose numbers without clinician data.** `build_titration_schedule()` returns `None` when `asm_rules.json` is empty, and the family message holds off on mg whenever the ladder or the planned-end dose is unavailable. `outputs.family_message_allowed_doses()` is the single source of the numbers a draft may contain - a fabricated dose is rejected by `nlp/guardrails`.
7. **Safety flags always surface and are never suppressed.** The one documented exception - dropping the HLA-B*1502 screening advisory from a *no-change* clinician summary - is PI-directed and retained in the structured `safety_notes` (`NEEDS_PI_SIGNOFF.md` #45).

> **Note on the safety-flag family message.** On any safety flag the engine gives
> **no** family message (`draft_patient_message` is `None`); the physician handles
> the case per her urgent protocol. Dr. Barbour decided this on 2026-10-25
> (`NEEDS_PI_SIGNOFF.md` #51).

---

## Running the engine

```bash
cd aim2_engine

# Run all tests  (expect: Ran 499 tests ... OK)
python3 -m unittest discover -s tests

# Run validation (Cohen's κ) - no-arg default = gold set, expect κ = 0.976, MET
python3 validation/harness.py

# Run the independent proof suite  (expect: RESULT: PASS)
python3 verification/verify_all.py

# Run the demo CLI
python3 demo.py
```

See `../aim2_engine/RUNBOOK.md` for the full quickstart and the clinician app. The
FDA-label dosing comparison lives in `OFFICIAL_FDA_DOSING_REFERENCE.md`; the
pre-registered evaluation method is in `VALIDATION_PLAN.md`; the rationale for the
rule-based decision path is in `NEUROSYMBOLIC_RATIONALE.md`.

---

## What's still needed before clinical use

See `../aim2_engine/NEEDS_PI_SIGNOFF.md` for the full checklist. Short version:

1. PI verifies/corrects the 11 DRAFT drugs in `data/asm_rules.json` (all
   `verified:false`). Use `OFFICIAL_FDA_DOSING_REFERENCE.md` (FDA-label
   numbers + the DRAFT-vs-label discrepancies) as the worksheet.
2. Replace provisional validation labels with real chart-review gold standard.
3. IRB + institutional security sign-off before any real patient data.
4. Aim 1 integration: map real survey submissions → `IntervalSnapshot` (contract in `DATA_CONTRACT.md`).

## How one check-in is processed

1. **Snapshot.** `snapshot_from_interval()` turns survey answers plus the intake record
   (age, weight, medicine, dose, frequency) into a typed `IntervalSnapshot`.
2. **Categorize.** `categorize.py` derives the patient's situation: seizure-free or
   breakthrough (with a non-urgent / urgent / emergent tier for each seizure type),
   side-effect level 0–5, phase (titrating / holding / steady), adherence, and
   safety signals (serious rash on a high-risk medicine, whole-body seizure over
   3 minutes).
3. **Decide.** `decision.py` runs an ordered rule list (first match wins) and the PI's
   decision tables. It returns a category (`ESCALATE`, `SLOWER_UPTITRATION`, `HOLD`,
   `LOWER_DOSE`, `NO_CHANGE`, `SWITCH_MEDICATION`, `ADD_MEDICATION`,
   `INSUFFICIENT_DATA`) plus pace, follow-up, labs and a `rationale` naming every
   rule that fired and the inputs it used. Safety checks run on every case and can
   only add flags, never remove them.
4. **Build outputs.** `outputs.py` writes the clinical summary in fixed numbered
   sections, the dose steps (`dose_ladder.resolve_plan()` picks the next rungs from the
   PI's ladder and never invents a number; `formulation.py` converts them to tablets
   or liquid; `initiation.py` adds the starting schedule for a switch or add), and the
   family message from a fixed template chosen by the structured decision.
5. **Read the free text (ML).** In parallel, the family's own words go through the
   transformer entity model (results logged for the doctor and for evaluation) and
   the red-flag detector, whose hits can only raise the case to urgent.
6. **Check.** `nlp/guardrails.validate_message()` rejects a family draft that names a
   dose not in `family_message_allowed_doses()`, contradicts the category, or lacks
   the draft notice. This applies to the template draft and to any LLM rewording.
7. **Review.** The app saves the `Recommendation` and shows it in the doctor's queue,
   triaged by urgency. Nothing is sent until a physician approves it.

## Design decisions worth knowing

- **Data, not code, holds the clinical content.** Doses, thresholds and tables live in
  JSON generated from the PI's spreadsheets, so a clinical change is usually a data
  change plus a test, not new logic.
- **One source for every number a family sees.** The message builder and the guardrail
  share `family_message_allowed_doses()`, so they can't drift apart.
- **Defer instead of guess.** Missing or conflicting answers give `INSUFFICIENT_DATA`;
  an ambiguous dosing band or starting schedule is handed to the doctor.
- **Re-runnable.** Each submission stores the medication context the engine saw, so
  `tools/regen_notes.py` can rebuild saved notes after an engine change and refuses
  rows it can't reproduce.
- **Independent checks.** `verification/verify_all.py` re-derives the rule tables from
  source rather than trusting the engine, and `validation/holdout_test.py` scores the
  engine against answers computed straight from the decision tables.
