# For clinicians

How to see the tool working, review its drafts, and send corrections.

## What the tool decides

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

## How to review

1. **See it working.** Open the app (ask the engineering team to start it, or
   double-click `Start Clinic.command` on the study Mac) and log in as `doctor`.
   The review queue holds about 30 synthetic demo patients, each marked "(demo)".
2. **Walk through the demo cases.** [`kristen/walkthrough_2026-10-23.md`](../kristen/walkthrough_2026-10-23.md)
   lists which cases show which feature, and what to ask about each.
3. **Answer open questions.** Decisions waiting for the PI:
   - [`aim2_engine/NEEDS_PI_SIGNOFF.md`](../aim2_engine/NEEDS_PI_SIGNOFF.md): every open item, numbered (#51, #60–70, ...)
   - [`kristen/logic_tradeoffs_for_discussion.md`](../kristen/logic_tradeoffs_for_discussion.md): how the tool decides, with the trade-offs
   - [`kristen/questions_round_2.md`](../kristen/questions_round_2.md): the current question list
4. **Check the numbers.** [`docs/OFFICIAL_FDA_DOSING_REFERENCE.md`](OFFICIAL_FDA_DOSING_REFERENCE.md)
   compares the tool's draft doses with the FDA labels. The per-medicine workbook is
   [`aim2_engine/clinician_verification/ASM_data_clinician_verification.xlsx`](../aim2_engine/clinician_verification/ASM_data_clinician_verification.xlsx).

## How to send changes

Send corrections as an edited spreadsheet or document (as with the decision tables
and family-message templates), or as a comment on a GitHub issue that names the item
number. The engineering team transcribes it into the data files, adds a test, and
points you to the case in the app that shows the change.
