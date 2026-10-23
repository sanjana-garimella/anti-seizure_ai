# Pediatric Epilepsy AI Medication Assistant (Aim 2)

A hybrid decision-support tool for children on anti-seizure medicines (ASMs).
A family fills in a short check-in survey between clinic visits. A rule engine
built from the PI's decision tables chooses the recommendation and every dose;
machine-learning language models read the family's free-text notes and flag
concerns for the doctor. Together they draft three things for the doctor to review:

1. a **clinical summary** for the chart,
2. a **medication recommendation** (keep the dose, increase, slow down, hold, add or
   switch), with the exact next doses from the clinician's dosing ladder, and
3. a **family message** in plain language.

Nothing reaches a family without a physician approving it. Every dose is tagged
`verified:false` until the PI signs it off.

PI: Kristen Barbour, MD · Scripps Research. **Synthetic data only.**

> **Research prototype, not a medical device.** All patient data here is synthetic.
> The software has not been cleared or approved by any regulator and must not be used
> to make clinical decisions. Every dose and clinical value is a draft
> (`verified:false`) pending physician sign-off.

---

## Ground rules (read before contributing)

These are absolute. Full text: [`NON_NEGOTIABLE_RULES.md`](NON_NEGOTIABLE_RULES.md).

- **Synthetic data only.** No real patient data or PHI until IRB approval and
  HIPAA/CITI training are complete. Never put names, dates of birth or record
  numbers in files, commits, issues or chat.
- **Rules choose the dose and the category.** Machine learning and LLMs can read
  free text, flag concerns and help with wording, but never pick a dose or a
  recommendation category. Every recommendation must trace to a specific rule.
- **Physician in the loop.** Every output is a draft for clinician review.
- **When in doubt, defer.** Ambiguous or incomplete input goes to the physician; the
  tool does not guess.
- **Never exceed the maximum dose.** Dose ceilings are enforced in code.
- **Clinical values come from the PI.** Engineers never invent or "fix" a dose,
  threshold or rule. Clinical changes go through the PI (see below).
- **Keep local data local.** This repo's contents are approved for public release,
  but the git-ignored files (`clinic_data/` database and keys, `validation/private/`)
  never leave the machine they were created on. Public LLMs are fine for this repo's
  contents; anything local-only stays with PI-approved AI tools (rule §3).

---

## For clinicians

### What the tool decides

The survey asks about seizures since the last visit (how many, how long, what they
looked like), side effects (6 levels, from none to "not a fit"), whether the child is
still increasing toward a goal dose, and how things are going overall. The tool
sorts the child into a situation and applies Dr. Barbour's decision tables:

| Situation | Decision table |
|---|---|
| No seizures | `kristen/no-seizures_decision_tables.xlsx` |
| Breakthrough seizures: non-urgent / urgent / emergent | `kristen/breakthrough-*_decision_tables.xlsx` |
| Dose steps (mg/kg by weight band) | `kristen/final_doses_titration_v9.xlsx` |
| Starting a new medicine (switch or add) | the "Initiation" tabs in the same v9 sheet |
| Tablets vs liquid | `kristen/tablet_dosing_logic_refinement.xlsx` |

Safety concerns (a serious rash on a high-risk medicine, a seizure longer than
3 minutes, a dose above the maximum) are always flagged to the physician and never
hidden.

### How to review

1. **See it working.** Open the app (ask the engineering team to start it, or
   double-click `Start Clinic.command` on the study Mac) and log in as `doctor`.
   The review queue holds about 30 synthetic demo patients, each marked "(demo)".
2. **Walk through the demo cases.** [`kristen/walkthrough_2026-10-23.md`](kristen/walkthrough_2026-10-23.md)
   lists which cases show which feature, and what to ask about each.
3. **Answer open questions.** Decisions waiting for the PI:
   - [`aim2_engine/NEEDS_PI_SIGNOFF.md`](aim2_engine/NEEDS_PI_SIGNOFF.md): every open item, numbered (#51, #60–70, ...)
   - [`kristen/logic_tradeoffs_for_discussion.md`](kristen/logic_tradeoffs_for_discussion.md): how the tool decides, with the trade-offs
   - [`kristen/questions_round_2.md`](kristen/questions_round_2.md): the current question list
4. **Check the numbers.** [`docs/OFFICIAL_FDA_DOSING_REFERENCE.md`](docs/OFFICIAL_FDA_DOSING_REFERENCE.md)
   compares the tool's draft doses with the FDA labels. The per-medicine workbook is
   [`aim2_engine/clinician_verification/ASM_data_clinician_verification.xlsx`](aim2_engine/clinician_verification/ASM_data_clinician_verification.xlsx).

### How to send changes

Send corrections as an edited spreadsheet or document (as with the decision tables
and family-message templates), or as a comment on a GitHub issue that names the item
number. The engineering team transcribes it into the data files, adds a test, and
points you to the case in the app that shows the change.

---

## For engineers

### Setup

Python 3.10+, standard library only. No `pip install` is needed to run the engine,
the app or the tests.

```bash
cd aim2_engine
python3 data/_build_asm_rules.py      # one-time: builds data/asm_rules.json (not stored in git)
python3 -m unittest discover -s tests # expect: Ran 478 tests ... OK
```

### Everyday commands (run from `aim2_engine/`)

| Command | What it does | Expect |
|---|---|---|
| `python3 clinic.py` | Doctor + family web app at http://127.0.0.1:8000 (`ASM_PORT` to change). The first run creates the database and prints a one-time `doctor` password. | |
| `python3 tools/seed_demo_queue.py --apply` | Fill the review queue with the synthetic demo patients (dry run without `--apply`) | |
| `python3 tools/regen_notes.py --apply --with-drafts` | Rebuild saved notes and drafts after an engine change | |
| `python3 tools/reset_doctor_pw.py` | Reset the `doctor` password (you type it, nothing is printed) | |
| `python3 -m unittest discover -s tests` | Full test suite | `Ran 478 tests ... OK` |
| `python3 validation/harness.py` | Agreement against gold cases (Cohen's κ) | `κ = 0.976, MET` |
| `python3 verification/verify_all.py` | Independent proof of the rule tables and safety net | `RESULT: PASS` |
| `python3 validation/holdout_test.py` | Fresh cases scored against the decision tables + fuzz test | Held-out: PASS. Its "safety message leaks" line is open PI question #51, not a bug. |
| `python3 demo.py` | Command-line demo of the engine | |

All four checks (tests, κ, verify_all, holdout) should be green before a change is merged.

### Architecture

The system is **hybrid (neurosymbolic)**: a symbolic rule engine and statistical
language models, each doing the job it is good at.

- **Rule engine (symbolic).** Chooses the recommendation category and every dose from
  the PI's decision tables and dose ladder. Deterministic and traceable: same input,
  same output, and a rationale naming each rule that fired. It has the final say on
  anything clinical.
- **Language models (neural / ML).** Read what families type in their own words. A
  transformer named-entity model (HUMADEX `english_medical_ner` by default;
  ClinicalBERT, BioBERT or a word-list fallback are options) pulls out drugs, side
  effects and seizure details for the doctor. A red-flag detector raises urgent
  phrases, which can only move a case *up* the queue. An LLM can optionally redraft
  the family message, but only from facts the engine already decided, and the
  guardrail rejects any dose it makes up.
- **Physician.** Approves everything before a family sees it.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. APP  (aim2_engine/clinic.py + clinic_store.py, stdlib HTTP + SQLite)      │
│    family check-in survey ──► submission ──► doctor review queue ──► approve │
└───────────────┬──────────────────────────────────────────▲───────────────────┘
                │ answers + intake record                  │ Recommendation
                ▼                                          │ (saved, then shown)
┌──────────────────────────────────────────────────────────┴───────────────────┐
│ 2. ENGINE  (aim2_engine/engine_core/, deterministic, the only decision path) │
│                                                                              │
│  snapshot.py ─► categorize.py ─► decision.py ─────────► outputs.py            │
│  IntervalSnapshot  PatientStatus   Decision               ├─ clinical summary │
│  (typed input)     seizure tier,   category + pace +      ├─ titration steps  │
│                    side-effect     follow-up + rationale  │   dose_ladder.py  │
│                    level, phase,   (ordered rules, first  │   formulation.py  │
│                    safety signals   match wins, + PI       │   initiation.py   │
│                                     decision tables)      └─ family message   │
│                                                                              │
│  api.run_engine(snapshot) ─► Recommendation   (requires_physician_review=True)│
└───────────────▲──────────────────────────────────────────┬───────────────────┘
                │ reads (never writes)                     │ facts + free text
┌───────────────┴──────────────────┐   ┌───────────────────▼───────────────────┐
│ DATA  (aim2_engine/data/*.json)  │   │ 3. LANGUAGE MODELS (aim2_engine/nlp/) │
│ decision tables, dose ladder,    │   │ NER model reads family free text;     │
│ starting schedules, tablet rules │   │ red-flag hits only raise priority;    │
│ all verified:false, built from   │   │ optional LLM rewording of drafts;     │
│ kristen/ by data/_build_*.py     │   │ guardrail rejects any made-up dose    │
└──────────────────────────────────┘   └───────────────────────────────────────┘
```

**How one check-in is processed**

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

**Design decisions worth knowing**

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

Full diagrams, every key file and the invariants: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Input/output format for the Aim 1 platform: [`aim2_engine/DATA_CONTRACT.md`](aim2_engine/DATA_CONTRACT.md).

### Where things live

| Path | What lives there |
|---|---|
| `aim2_engine/engine_core/` | The decision engine. **Single source of truth; no ML, no network.** `decision.py` (rules), `dose_ladder.py` (next doses), `formulation.py` (tablets/liquid), `initiation.py` (starting schedules), `outputs.py` (summary + family message), `api.py` (`run_engine`) |
| `aim2_engine/data/` | Clinical values as JSON, all `verified:false`. Several are **generated**: edit the `_build_*.py` builder, never the JSON by hand |
| `aim2_engine/nlp/` | The ML / language side: transformer entity models (`transformer_structurer.py`, choose with `ASM_NLP=humadex\|clinical_bert\|biobert\|lexicon`), red-flag detector, optional guarded LLM drafting (`generate.py`), calibration and evaluation helpers. Its output informs the doctor but never sets a dose or category. `guardrails.py` checks every family draft for invented numbers. Models are optional installs (`transformers`, `torch`); without them it falls back to word lists |
| `aim2_engine/clinic.py` | The web app (doctor queue + family survey); `clinic_store.py` is its SQLite layer |
| `aim2_engine/tests/` | 25 test files. Every rule change needs a test |
| `aim2_engine/validation/` · `verification/` | κ harness, gold cases, held-out oracle, proof suite, red-team safety checks |
| `aim2_engine/tools/` | Maintenance scripts. Dry run by default; `--apply` to write |
| `kristen/` | The PI's source documents: **source of truth for every clinical value** |
| `docs/` | [`ARCHITECTURE.md`](docs/ARCHITECTURE.md), validation plan, design rationale, FDA dosing reference; `docs/study/` has the study proposal |
| `synthetic_validation/` | Scripts that generate the synthetic review cases sent to the PI (current: v3) |
| `Start Clinic.command` · `Demo - Start Clinic.command` | Double-click launchers for the app on a Mac. `Start Clinic` opens the real local database (`clinic_data/clinic.db`). `Demo` uses its own `clinic_data/demo.db`, reset to the `demo_seed.py` patients each launch (login `doctor` / `demo1234`), and never touches the real database |
| `decision_map/` | Generated decision-flow graph (re-run the build scripts; don't hand-edit) |
| `_archive/` | Superseded files kept for reference (old decision tables, review cases, templates) |

More detail: [`aim2_engine/README.md`](aim2_engine/README.md),
[`aim2_engine/RUNBOOK.md`](aim2_engine/RUNBOOK.md),
[`aim2_engine/DATA_CONTRACT.md`](aim2_engine/DATA_CONTRACT.md) (input/output format for the Aim 1 platform).

### Making a change

- **Branch and pull request.** Don't push straight to `main`. Name the PI item (`#67`)
  in the PR when it applies.
- **Clinical value or rule changes** need the PI's written source (an edited sheet,
  email or sign-off item), a test, and a demo case that shows it. Record the open
  question in `NEEDS_PI_SIGNOFF.md` if the answer isn't in yet.
- **Never edit generated files by hand:** `data/asm_rules.json` (`_build_asm_rules.py`),
  `data/dose_titration_ladder.json` (`_build_dose_ladder.py`),
  `data/initiation_schedules.json` (`_build_initiation_schedules.py`).
- **Never write `clinic_data/clinic.db` in place** through a synced folder. It has been
  corrupted that way before. The `tools/` scripts copy, check, back up and swap safely.
- **Not in git on purpose:** `clinic_data/` (database, key, audit log),
  `validation/private/`, `asm_rules.json` (generated).

---

## Roadmap: hybrid RAG for a growing body of clinical context (proposed, not built)

**The problem.** The clinical context keeps growing: four decision tables, the dosing
sheet (now v9, with ten starting-schedule tabs), tablet rules, message templates, PI
emails and notes, 70+ sign-off items, FDA label references and trade-off docs. Today
people transcribe all of it by hand into JSON rules and Markdown. That gets harder to
keep consistent (for example, the template F taper table vs the 2026-10-23 email),
harder for a new collaborator to navigate, and too large to paste into an LLM prompt.

**The idea.** Keep the rule engine as the only thing that decides doses and
categories, and add **retrieval** over the source material for everything around the
decision: explaining, finding, checking and wording. "Hybrid" here means two things:

1. **Hybrid retrieval.** Combine keyword search (BM25, good for drug names, rule IDs and
   numbers), embedding search (good for "how do we handle a rash on lamotrigine?"
   phrasing) and exact structured lookup (drug + weight band + rule ID straight from
   the JSON), then re-rank.
2. **Hybrid reasoning.** Rules decide; retrieval supplies the cited source; an LLM (if
   used at all) only phrases what the rules and sources already say.

```
question / decision ──► rule engine (decides) ──► rationale: rule IDs + inputs
                                │
                                ▼
                  hybrid retriever (BM25 + embeddings + exact JSON lookup)
                  over a provenance-tagged corpus: kristen/ sheets & notes,
                  NEEDS_PI_SIGNOFF, FDA reference, data/*.json rows, templates
                                │
                                ▼
          cited passages ──► (optional) LLM phrasing ──► guardrails ──► physician
```

**Where it would help, from lowest to highest risk**

| Use | Who | Notes |
|---|---|---|
| "Why?" panel: next to each recommendation, the PI source passage behind the rule that fired (sheet, tab, row, version) | clinicians | Retrieval only, no generation |
| Search and Q&A over the project with citations ("where does the 30/70 split come from?") | engineers, clinicians | Answers must cite; abstain when no source is found |
| Conflict finder: flag where sources disagree (v6 vs v9, template vs email, sheet vs JSON) | engineers, PI | Turns surprises into sign-off items |
| Grounded entity normalization: match free-text mentions to a reference list of drugs and side effects | ML | Already on the to-do list in `docs/ai-engineering-applicability.md` |
| Grounded wording drafts for family messages, with each sentence tied to a template or fact | PI review | Highest risk; last, and only behind the existing guardrail |

**To do**

- [ ] Build the corpus: chunk each source with provenance (file, sheet/tab, row, version,
      date, `verified` flag). Synthetic and source documents only; nothing private.
- [ ] Local hybrid index: a stdlib BM25 baseline first, then optional
      `sentence-transformers` embeddings (already an optional dependency), plus exact
      lookup keyed on the rule and ladder IDs the engine already emits.
- [ ] Evaluation set: 30–50 real questions taken from the sign-off items and review
      meetings, with the correct source passages. Measure retrieval recall@k and
      citation accuracy before adding any generation.
- [ ] Ship the "Why?" panel and the conflict finder first (no LLM needed).
- [ ] Only then try LLM phrasing, on an approved enterprise model, with
      `guardrails.py` and physician review unchanged.

**Guardrails that stay fixed**

- Retrieval and LLMs never choose a dose or category, and never write into engine inputs.
- Every generated sentence cites a source; no source means no answer.
- Any number in generated text must pass `family_message_allowed_doses()`.
- No PHI in the index. Any LLM, embedding service or connector must be on the approved
  list or get the PI's approval first (`NON_NEGOTIABLE_RULES.md` §3–4).

---

## Status (2026-10-23)

- Engine 0.3.0. Decision tables, v9 dosing ladder, starting schedules, tablet/liquid
  conversion and family-message templates are built as drafts.
- 478 tests pass · κ 0.976 (against synthetic picks, not yet the PI's chart-reviewed
  cases) · proof suite PASS · held-out conformance PASS.
- Before any real-patient use: tamper-evident audit log, encryption at rest, login
  hardening, κ on real chart-reviewed cases, IRB + security review. Full list in
  `NON_NEGOTIABLE_RULES.md` §6.

---

## License

Licensed under the [Apache License 2.0](LICENSE). Copyright 2026 Sanjana Garimella and
Kristen Barbour, MD; see [`NOTICE`](NOTICE). Public release approved by the PI on
2026-10-23. The research-use disclaimer above applies to all use.
