# Contributing

Read the ground rules in [`NON_NEGOTIABLE_RULES.md`](../NON_NEGOTIABLE_RULES.md) first.

## Everyday commands (run from `aim2_engine/`)

| Command | What it does | Expect |
|---|---|---|
| `python3 clinic.py` | Doctor + family web app at http://127.0.0.1:8000 (`ASM_PORT` to change). The first run creates the database and prints a one-time `doctor` password. | |
| `python3 tools/seed_demo_queue.py --apply` | Fill the review queue with the synthetic demo patients (dry run without `--apply`) | |
| `python3 tools/regen_notes.py --apply --with-drafts` | Rebuild saved notes and drafts after an engine change | |
| `python3 tools/reset_doctor_pw.py` | Reset the `doctor` password (you type it, nothing is printed) | |
| `python3 -m unittest discover -s tests` | Full test suite | `Ran 532 tests ... OK` |
| `python3 validation/harness.py` | Agreement against gold cases (Cohen's κ) | `κ = 1.0, MET` |
| `python3 verification/verify_all.py` | Independent proof of the rule tables and safety net | `RESULT: PASS` |
| `python3 validation/holdout_test.py` | Fresh cases scored against the decision tables + fuzz test | Held-out: PASS, stress/fuzz: PASS (0 safety message leaks). |
| `python3 demo.py` | Command-line demo of the engine | |

All four checks (tests, κ, verify_all, holdout) should be green before a change is merged.

## Making a change

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

More detail: [`aim2_engine/README.md`](../aim2_engine/README.md), [`aim2_engine/RUNBOOK.md`](../aim2_engine/RUNBOOK.md).
