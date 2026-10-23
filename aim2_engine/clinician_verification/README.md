# Aim 2 - drug data + proposed improvements

Delivered as **proposals**. Everything here is confidential study material and stays in this folder.

*(This folder was the former top-level `aim2_proposed/`, folded into
`aim2_engine/clinician_verification/` in the 2026-07-16 cleanup.)*

## What's here

| File | What it is |
|---|---|
| `asm_rules.draft.json` | **DRAFT** titration data for all 11 ASMs in the engine's (extended) schema. Formulations from RxNorm; dose bands from FDA prescribing info / standard pediatric references. **Every value `verified: false` - needs Dr. Barbour's sign-off.** |
| `ASM_data_clinician_verification.xlsx` | The clinician review workbook: 139 values to confirm/correct (Y/N dropdowns), plus Formulations and Safety tabs. This is the artifact for Dr. Barbour. |
| `verification_rows.json` | Machine-readable source for the workbook. |
| `provenance/` | The build + verification scripts and the raw RxNorm pull (reproducible). |

## Important caveats

- **Nothing here is clinically authoritative.** It's a draft to make your rule-setting session efficient - "something to edit rather than a blank page." Dr. Barbour verifies/corrects every value.
- Decision logic stays **rule-based and clinician-defined**; this delivery is data + plumbing, not new clinical rules.
- Two drugs need a clinical scope decision: **zonisamide** (<16 y is off-label) and **ethosuximide** (absence only).
- Pulled 2026-06-17. openFDA label updates were reported suspended in 2025, so currency was cross-checked against FDA PI pages but still needs verification.

## How to use the draft data
1. Dr. Barbour reviews `ASM_data_clinician_verification.xlsx`.
2. Confirmed values → `aim2_engine/data/asm_rules.json`; sign-off in `../NEEDS_PI_SIGNOFF.md`.
   Note that file is **generated** by `../data/_build_asm_rules.py` - put confirmed
   values into the builder, not the JSON, then re-run it.
3. The band-selection fix and weight-based dosing from the original proposal have since landed in the engine (`engine_core/asm_rules.py` + `dose_ladder.py`), including the lamotrigine concomitant-valproate band selection (a `verified:false` LTG+VPA regimen exists in the data, but the study is monotherapy, so that path is out of scope in practice).
4. The live engine's dosing has since moved past this draft: the exact per-band
   rungs now come from `../data/dose_titration_ladder.json`, transcribed from
   `kristen/final_doses_titration_v6.xlsx`. **The `kristen/` workbooks are the source
   of truth**; where this FDA-derived draft disagrees with them, reconcile to the
   workbook, not to this folder.

Reproduce: `cd provenance && python3 build_draft.py && python3 verify_in_engine.py && python3 build_verification_xlsx.py`
