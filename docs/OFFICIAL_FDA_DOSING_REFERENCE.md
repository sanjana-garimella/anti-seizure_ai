# Official FDA Dosing Reference - 11 ASMs (for PI verification)

Date: 2026-06-23 · Source of "official" numbers: **FDA prescribing information via DailyMed** (fetched and read directly; URLs per drug). Pediatric = ages 2–17, weight-based where the label is.

**Purpose:** side-by-side of the engine's DRAFT values (`aim2_engine/data/asm_rules.json`, all tagged `verified:false`) vs the FDA label, so Dr. Barbour can confirm/correct each. **No engine values were changed** - clinical sign-off is hers (NEEDS_PI_SIGNOFF.md). Where a number is not in the FDA label (off-label peds use), it is flagged as needing a literature/guideline source, not the label.

> **Read this first (added 2026-08-04).** This document reflects
> `asm_rules.json` as of 2026-06-23. Since then the engine's **operative** dosing
> moved to `aim2_engine/data/dose_titration_ladder.json`, transcribed from
> **`kristen/final_doses_titration_v6.xlsx`** - and the workbooks in `kristen/` are
> the project's source of truth. So:
>
> - The FDA-derived values below are a **cross-check**, not the target. Where this
>   document and Dr. Barbour's workbook disagree, reconcile **to the workbook** and
>   raise the difference with her; do not "correct" the engine toward the label.
> - Some `asm_rules.json` values are correspondingly stale relative to the ladder.
> - Known contradictions live *inside* the workbook itself (e.g. levetiracetam
>   60 vs 80 mg/kg/day - item 1 below and `NEEDS_PI_SIGNOFF.md` #6/#36) and only
>   she can settle them.
> - Verify against the current label revision before any clinical use; the numbers
>   below were read 2026-06-23.

---

## Discrepancies to resolve first (DRAFT ≠ FDA label)

> **⚠ This list describes the DRAFT as it stood on 2026-06-23. `asm_rules.json` was
> regenerated on 2026-07-08 and reconciled to Dr. Barbour's ladder, so most of these
> specific DRAFT values no longer exist in the file.** Verified against the current
> `data/asm_rules.json` on 2026-08-04:
>
> | # | Then (this doc's "DRAFT") | Now in `asm_rules.json` |
> |---|---|---|
> | 1 | levetiracetam max 80 mg/kg/day | **60** mg/kg/day (reconciled to the v6 sheet; the 80 survives only as a note. **The 60-vs-80 question is still hers to settle** - `NEEDS_PI_SIGNOFF.md` #6/#36) |
> | 2 | oxcarbazepine absolute max 2400 mg/day | "2400" no longer appears; **weight-tiered bands** (<20 kg through ≥70 kg) + 60 mg/kg/day |
> | 3 | carbamazepine fixed day-8/day-15 jumps | **weekly increments** (`uptitration_interval_weeks: 1`), age bands 2–<6 / ≥6–12 / 13–18 / >18. Note the pediatric caps are now **800 mg/day**, below both the label (1000/1200) and this doc's "maxes match" claim - that gap is hers to confirm |
> | 4 | valproic acid absolute max ~3000 mg/day | no absolute cap; **60 mg/kg/day** (now matches the label) |
> | 5 | lacosamide ≥50 kg day-8 → 200 mg/day | no day-8 jump; weekly steps, max 400 mg/day |
> | 6 | zonisamide target 4–8, max 12 mg/kg/day, 600 mg/day | target **5–8**, max **8** mg/kg/day, absolute max **500** mg/day. Still `<16y` **off-label** - the provenance re-tag (item 6 below) is still open |
> | 7 | clobazam ≤30 kg / >30 kg weight bands, day 8/15 | **age** bands (<5 y max 30 mg/day; ≥5 y max 40) with weekly 2.5/5 mg steps |
>
> The **FDA-label numbers below are unchanged and still the useful part** of this
> document - use them as the cross-check. Where the label and Dr. Barbour's workbook
> disagree, the workbook wins and the difference goes to her.

Read the rest of this section as the 2026-06-23 snapshot it is.

1. **Levetiracetam - DRAFT max 80 mg/kg/day is not in the label.** Keppra's recommended pediatric focal dose is **60 mg/kg/day** (absolute max **3000 mg/day** is correct). The "80 mg/kg/day" is the clinician(Barbour)-supplied ceiling already tagged in the file - confirm whether you intend a dose **above the FDA label** or want it pulled back to 60.

2. **Oxcarbazepine - DRAFT "absolute max 2400 mg/day" is an adult value.** 2400 mg/day is the *adult monotherapy-conversion* target. The pediatric label uses **weight-tiered maintenance** (20–29 kg → 900; 29.1–39 kg → 1200; >39 kg → 1800 mg/day) and a **60 mg/kg/day** ceiling (for ages 2–<4y). Replace 2400 with the weight-tiered targets / 60 mg/kg/day.

3. **Carbamazepine - DRAFT fixed day-8/day-15 jump schedule is not in the label.** Tegretol titrates by **weekly increments** (add up to 100 mg/day for 6–12y; up to 200 mg/day for >12y) to response - not fixed 200→300→400 / 400→600→800. Targets (400–800; 800–1200) and maxes (1000; 1000/1200) match. Reconcile the titration mechanic.

4. **Valproic acid - DRAFT "absolute max ~3000 mg/day" not found in the label** for seizures; the label gives only **60 mg/kg/day** (no absolute mg/day cap for the seizure indications). Either remove the 3000 or cite it separately. Also: complex-partial indication is labeled **down to age 10** - ages 2–9 is off-label (engine treats 2–17).

5. **Lacosamide ≥50 kg - DRAFT "day-8 → 200 mg/day" is not a label jump.** Vimpat goes 100 mg/day start, **+100 mg/day weekly** to 300–400 mg/day maintenance. Max **400 mg/day** (DRAFT's "300 mg/day caveat" is the low end of the range, not a max - drop it).

6. **Zonisamide <16y - OFF-LABEL.** FDA approval is **≥16y** (confirmed). The DRAFT pediatric mg/kg values (start 1.5, target 4–8, max 12 mg/kg/day, 600 mg/day) are **not in the FDA label** - they must be tagged with a literature/guideline source, not "zonegran." The 16–17y schedule (100→200→300→400, max effective 400, up to 600) matches the label (label interval is q2wk).

7. **Clobazam - cosmetic only.** Dose levels match exactly (≤30 kg: 5→10→20, max 20; >30 kg: 10→20→40, max 40). The label labels the steps **"Starting Day 7 / Day 14"**; the DRAFT says "day 8 / day 15." Off-by-one labeling, same doses.

**Match, no action:** lamotrigine (all 3 conmed regimens), topiramate, brivaracetam, ethosuximide.

---

## Per-drug official numbers (FDA label)

### 1. Levetiracetam (Keppra)
Focal adjunctive ≥1 mo; PGTC ≥6y; myoclonic/JME ≥12y (myoclonic <12y & PGTC <6y are off-label). Start **20 mg/kg/day** (10 mg/kg BID); maintenance **60 mg/kg/day** (focal, 4–<16y); step **+20 mg/kg/day q2wk**; absolute max **3000 mg/day**.
Source: DailyMed - Keppra (UCB). https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=3ca9df05-a506-4ec8-a4fe-320f1219ab21
**DRAFT vs label:** start ✓, step ✓, absolute max 3000 ✓ · **max mg/kg/day: DRAFT 80 vs label 60.**

### 2. Lamotrigine (Lamictal)
Adjunctive ≥2y. Weight-based (ages 2–12), titrate q1–2wk:
- No valproate / no inducer: 0.3 → 0.6 → +0.6 mg/kg/day; maintenance **4.5–7.5 mg/kg/day, max 300 mg/day**
- With valproate: 0.15 → 0.3 → +0.3 mg/kg/day; maintenance **1–5 mg/kg/day, max 200 mg/day** (highest rash risk)
- With inducer (no valproate): 0.6 → 1.2 → +1.2 mg/kg/day; maintenance **5–15 mg/kg/day, max 400 mg/day**

Source: DailyMed - Lamictal (GSK). https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=d7e3572d-56fe-4727-2bb4-013ccca22678
**DRAFT vs label:** exact match (all three regimens).

### 3. Topiramate (Topamax)
Monotherapy ≥2y; adjunctive 2–16y. Start **25 mg/day** (1–3 mg/kg/day) nightly; maintenance **5–9 mg/kg/day**; step **+1–3 mg/kg/day q1–2wk**; max **9 mg/kg/day** (adjunctive) / **400 mg/day** (monotherapy ≥10y; weight-tiered below that).
Source: DailyMed - Topamax (Janssen). https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=88c9d1ca-f3a8-4078-8de7-21fd9040dd20
**DRAFT vs label:** match (400 mg/day is the monotherapy ceiling; adjunctive label states no separate absolute cap).

### 4. Oxcarbazepine (Trileptal)
Adjunctive ≥2y; monotherapy ≥4y. Start **8–10 mg/kg/day** (≤600 mg/day); maintenance weight-tiered **900 / 1200 / 1800 mg/day** (20–29 / 29.1–39 / >39 kg); trial target range **30–46 mg/kg/day**; max **60 mg/kg/day** (2–<4y); peds increment **+10 mg/kg/day weekly**.
Source: DailyMed - Trileptal (Novartis). https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=d9741f36-0abd-49b7-9f43-e9700663122b
**DRAFT vs label:** start ✓, target ✓, 60 mg/kg/day ✓, step ✓ · **"absolute max 2400 mg/day" is an adult value - replace with weight-tiered targets.**

### 5. Carbamazepine (Tegretol)
Partial/GTC/mixed. <6y: **10–20 mg/kg/day**, max **35 mg/kg/day**. 6–12y: start **200 mg/day**, +≤100 mg/day weekly, target **400–800**, max **1000 mg/day**. >12y: start **400 mg/day**, +≤200 mg/day weekly, target **800–1200**, max **1000 (12–15y) / 1200 (>15y) mg/day**.
Source: DailyMed - Tegretol (Novartis). https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=8d409411-aa9f-4f3a-a52c-fbcb0c3ec053&type=display
**DRAFT vs label:** targets/maxes ✓ · **fixed day-8/day-15 schedule not in label (label = weekly increments).**

### 6. Valproic acid / divalproex (Depakote/Depakene)
Complex partial (≥10y), absence, multiple types. Start **10–15 mg/kg/day** (CPS) / 15 (absence); step **+5–10 mg/kg/week**; max **60 mg/kg/day** (no absolute mg/day cap stated for seizures).
Source: DailyMed - Depakote (AbbVie). https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=08a65cf4-7749-4ceb-6895-8f4805e2b01f&type=display
**DRAFT vs label:** start/step/60 mg/kg/day ✓ · **"absolute max 3000 mg/day" NOT in label · ages 2–9 CPS off-label.**

### 7. Clobazam (Onfi/Sympazan)
Adjunctive LGS ≥2y. ≤30 kg: **5 → 10 (Day 7) → 20 (Day 14) mg/day, max 20**. >30 kg: **10 → 20 (Day 7) → 40 (Day 14) mg/day, max 40**. Doses >5 mg/day divided BID.
Source: DailyMed - Onfi (Lundbeck). https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=de03bd69-2dca-459c-93b4-541fd3e9571c&type=display
**DRAFT vs label:** doses/maxes ✓ · day labels Day 7/14 (label) vs day 8/15 (draft).

### 8. Lacosamide (Vimpat)
Focal ≥1 mo. 11–<30 kg: start **2 mg/kg/day**, +2 mg/kg/day weekly, maintenance **6–12 mg/kg/day**. 30–<50 kg: start **2**, maintenance **4–8 mg/kg/day**. ≥50 kg: start **100 mg/day**, +100 mg/day weekly, maintenance **200–400 mg/day**, max **400 mg/day**.
Source: DailyMed - Vimpat (UCB). https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=9e79b42c-38a3-4b2c-a196-a5a1948250e2&type=display
**DRAFT vs label:** <50 kg bands ✓, step ✓, max 400 ✓ · ≥50 kg "day-8→200" not a label jump; drop "300 mg/day" caveat.

### 9. Brivaracetam (Briviact)
Focal ≥1 mo; no forced titration. 11–<20 kg: initial 0.5–1.25 mg/kg BID, maintenance **1–5 mg/kg/day**. 20–<50 kg: initial 0.5–1 mg/kg BID, maintenance **1–4 mg/kg/day**. ≥50 kg: **50–200 mg/day** (25–100 mg BID), start ~100 mg/day.
Source: DailyMed - Briviact (UCB). https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=3cf2f439-0e97-443e-8e33-25ecef616f6c&type=display
**DRAFT vs label:** match (minor: 11–<20 kg label initial is 0.5–1.25 mg/kg BID vs maintenance up to 2.5).

### 10. Zonisamide (Zonegran/Zonisade) - **<16y OFF-LABEL**
FDA approval **≥16y** (adjunctive focal). 16y+: **100 → 200 (q2wk) → 300 → 400 mg/day**; no added benefit >400; effective range 100–600 mg/day; interval **q2wk**. **<16y dosing is NOT in the FDA label** - DRAFT mg/kg values need a literature/guideline source.
Source: DailyMed - Zonegran. https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=d12de43e-3ac3-4335-bc85-70d7366a91eb&type=display
**DRAFT vs label:** 16–17y schedule ✓ · **<16y off-label - re-tag provenance (not "zonegran").**

### 11. Ethosuximide (Zarontin)
**Absence (petit mal) only**, ≥3y. 3–6y: start **250 mg/day**; >6y: start **500 mg/day**; +250 mg every 4–7 days; optimal ~**20 mg/kg/day**; ceiling **1500 mg/day**.
Source: DailyMed - Zarontin (Pfizer). https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=0e008f33-70a1-4bc6-b3a0-d45214418ab6&type=display
**DRAFT vs label:** exact match; absence-only confirmed.

---

## Caveats

These are **FDA-label reference numbers gathered for the PI to verify** - not a clinical recommendation, and not a substitute for Dr. Barbour's sign-off. Labels were read from DailyMed (public FDA documents - no project/PHI data was sent off-machine). Off-label pediatric uses (zonisamide <16y; valproate CPS 2–9y; levetiracetam myoclonic <12y / PGTC <6y) require a guideline/literature source rather than the label. Verify against the most current label revision before any clinical use.

Sources: all per-drug DailyMed URLs listed above.
