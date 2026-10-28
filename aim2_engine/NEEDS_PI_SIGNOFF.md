# Needs PI Sign-Off (Aim 2) - Clinical Decisions

Only items that need **your clinical judgment, data, or approval**. Ordered
safety-critical first.

Engineering/build questions, code changes, model choices, UI/demo notes, and
IRB/timeline status are tracked separately.

*2026-10-26, engine 0.4.3. 532 tests. Synthetic set 63/63, κ 1.0. A later note
in an item supersedes the paragraph under it. 2026-10-25 answers are at the end.*

*I set `verified:true` too broadly on 2026-10-26: v9 ladder (except the three
flagged bands), both decision tables, urgency cutoffs, switching workbook,
formulation rules, initiation tabs. Still open: dose line check (#6, #36),
urgency cutoffs (#19), breakthrough cells (#20), ranking rules (#41), 0.1 mL
liquid rounding. Left false: three ladder flags, titration cutoffs, LTG
concomitant bands (VPA / enzyme inducer; not on the v9 sheet), switch week-4
reading, CBZ/OXC absence note.*

---

## Safety-critical

1. **Serious-rash (SJS) warning message + stop-vs-continue.** The tool flags a
   large or blistering rash for urgent review, but authors **no** warning copy
   and doesn't say whether to stop the medicine. Needs your exact wording and the
   continue-vs-stop instruction.
   - *Already confirmed (no action):* a **large OR blistering** rash raises the
     urgent flag; the small-rash workup criteria are confirmed.
2. **Does the urgent stop+switch on a large/blistering rash apply to all 11
   medicines, or only the 3 higher-risk ones** (lamotrigine, oxcarbazepine,
   carbamazepine)? Currently it fires for any of the 11 (the safer default). The
   *small*-rash workup is limited to the 3 - confirm that inconsistency is
   intended.
3. **Urgent red-flag phrase list.** Review and expand the list of urgent phrases
   scanned in a parent's free-text answers. Decide: should an urgent phrase
   automatically flag the whole check-in urgent (and withhold the family
   message), or just be shown to the doctor as a note (current default)?
4. **Status epilepticus - any seizure ≥5 min → emergency/rescue pathway.** A
   whole-body seizure ≥3 min already routes to the acute-emergency path, but a
   focal / "acting-off" seizure ≥5 min currently routes to an "urgent call"
   instead. Confirm any seizure ≥5 min should hit the emergency pathway (911 /
   the child's rescue plan), taking precedence over the decision tables.
5. **Rescue medication / seizure action plan.** Nothing references rescue
   therapy. Confirm whether it's out of Aim 2 scope; if so, should
   prolonged-seizure family copy still point to the child's rescue plan /
   emergency number?

## Clinical values still needed

6. **Dosing for the 11 medicines.** Ladder rebuilt from v9. Copied bands are
   `verified:true` pending your line check. Topiramate max confirmed
   (9 mg/kg/day or 400 mg). Open: LEV 60 vs 80 (#36), topiramate ≤11 kg (#35),
   zonisamide child step (two bands), brivaracetam ≥50 kg.
7. **"Same family" definition** - which medicines count as too similar to switch
   between (so it doesn't suggest a near-identical medicine).
8. **Brand names / common misspellings** for each medicine, for recognition.
9. **Lab monitoring at the "hold" level.** Currently VPA, carbamazepine,
   oxcarbazepine, topiramate, zonisamide, and ethosuximide get labs;
   levetiracetam, lamotrigine, lacosamide, brivaracetam, and clobazam get none.
   Confirm that's intended, not just unlisted.
10. **"Consider uneven dosing" note.** Which medicines are eligible (currently
    clobazam / VPA / carbamazepine / oxcarbazepine), and should it appear in the
    family message or stay a clinician-only note?
11. **"Lower the dose"** - do you want a specific reduced amount, or just the
    instruction to lower?
12. **"Slower up-titration"** - the slower schedule (by how much), since the tool
    won't invent a number.

## Clinical judgment calls

13. **"A lot of impact but tolerable" side effect** - switch medicine, or slow
    the increase? Your notes suggest a third option you prefer: neither
    immediately, but re-check in ~1 week to see if it's improving as the body
    adjusts. Confirm that's the intended default.
14. **Case J2.** Seizure-free, at goal, levetiracetam, intolerable behavior or
    irritability → offer switch (`insufficient_data`, R14-offer). Other
    intolerable cases still switch. Say if you want an outright switch.
15. **Add-vs-switch at max dose.** ADD when the medicine is helping (at goal, low/
    medium impact, still seizing; or below max, side-effects limit the dose, still
    seizing); SWITCH when intolerable + wants change, not helping + breakthrough,
    not helping + medium/big impact, or a minimum effective dose can't be reached.
    Confirm.
16. **Whole-body >3 min = danger.** Confirm the 3-minute line, and whether
    **unresponsiveness alone** (any duration) should also count as danger.
17. **Duration and frequency cutoffs.** Confirm the duration buckets (<1 min /
    1–3 min / >3 min), that **<1 min while titrating = fine**, the frequency
    ranges (Daily / Weekly / Monthly / <1 per month), and the exact
    frequency × duration cutoffs for "increase faster."
18. **Drug–seizure-type cautions.** Confirm the tool should flag: carbamazepine /
    oxcarbazepine worsening **absence and myoclonic**; lamotrigine worsening
    **myoclonic**; ethosuximide for **absence only** (switch otherwise);
    levetiracetam caution with behavioral conditions (autism, depression,
    anxiety, aggression). Advisory note added for CBZ/OXC with absence or
    myoclonic. Those two stay `verified:false`; the caution is not on your sheet.
19. **Urgency thresholds.** Is **absence ≥10/day** genuinely urgent (or common at
    baseline in untreated childhood absence)? Is recurrent (non-prolonged) **GTC
    ≥1/week** urgent or emergent? Plus the emergent per-type cutoffs
    (e.g. ≥300 s, ≥40/day). Also: families can't reliably tell 20 s from 30 s -
    confirm it's acceptable to round boundary cases up to the safer (higher) tier.
20. **Breakthrough decision-table cells.** Confirm the category in each cell of
    the non-urgent / urgent / emergent tables (urgency × phase × side-effect
    level), and the verbatim clinician step lists.
21. **"Not sure" handling.** Confirm the case-by-case approach (not one blanket
    rule): "not sure" on dose goal with no seizures/side effects → no change;
    "not sure" with ongoing seizures + a recent dose/adherence issue → needs more
    info; "not sure" on whether the medicine helps → the more cautious option;
    "prefer not to say" on a rash follow-up → skip that one check only.
22. **Preference / adjustment prompts** - should each map to "defer to the
    family" (need more info) or to a concrete recommendation?
23. **Table vs. preference-prompt precedence.** For a medium-impact breakthrough
    case, should the family-preference / "is it improving?" questions still be
    asked on top of the table's action, or do the newer tables replace them?
24. **Family "want a different medicine" preference** - should it override the
    seizure-free action, or not (current: does not)?
25. **Follow-up timing.** At the hold / lower / switch levels, follow-up is
    currently 4 weeks across all phases - confirm. And: when an at-goal child's
    "how long on this dose" is blank, default to **Steady** (4-week follow-up) or
    **Holding** (2-week)?

## Survey content (clinical accuracy)

26. **Provoking-factors question** - add one (illness/fever, sleep, stress,
    menses)? Currently provocation is derived only from the adherence question.
27. **Medicine list + brand labels** shown to families; the **3-seizure-type
    cap**; the duration/frequency buckets; and the most-recent-seizure time
    buckets (<2 weeks / 2–4 weeks / >4 weeks). Confirm.
28. **New seizure type "acting off / not responding without shaking or
    stiffening"** (focal impaired awareness) - confirm the wording and mapping to
    the focal class.
29. **Triggers question** (unusual lack of sleep / missed dose in prior 2 days /
    fever or illness / other) and the "was a trigger present before **all** the
    breakthrough seizures?" follow-up. Confirm the list, and whether "present for
    all" should change the recommendation (currently advisory; adherence-first
    for a missed-dose trigger).
30. **Medication-preference ranking.** The survey captures *direction* (increase
    / add / switch, 1st vs 2nd + how strongly), not specific drug names. Confirm
    direction-level is enough, or whether you want drug-level ranking (which needs
    your pre-specified per-drug option list).

## Validation cases we still need from you

31. **Checked example cases for the newer logic** - seizure-free side-effect
    levels L0–L5, ADD-a-medication, whole-body danger, and the
    preference/adjustment branches - reviewed the same way as the original 63.
32. **Full inputs for the cases you already flagged** - Lily Tran, Maya Alvarez,
    Emma, Sofia Mendez. You gave the expected outcome; we need each case's
    medicine, dose, side effects, and seizure detail to add them to the test set.
    (Note: Lily Tran is a two-medicine stop + defer, which the tool doesn't model
    as a single path yet - flag if it should.)

## From the 2026-07-09 review (folded in before that review file was retired)

33. **Valproate teratogenicity in adolescent females.** Add a `safety_note` when the drug
    is valproate and the patient is female and ≈≥10–11 y (major teratogenic /
    neurodevelopmental risk; weight gain / PCOS), prompting the physician to reconsider or
    counsel. Currently absent.
34. **Zonisamide / topiramate class effects.** Zonisamide is a sulfonamide - ask about sulfa
    allergy (cross-reactivity) and tie it into the rash/SJS logic. Both zonisamide and
    topiramate can cause oligohidrosis / hyperthermia in children and metabolic acidosis -
    surface as monitoring notes?
35. **Topiramate ≤11 kg band looks too high.** `final_doses_titration_v6.xlsx` lists ≤11 kg
    maintenance 150–250 mg/day (~13–22 mg/kg/day) vs your stated 9 mg/kg/day ceiling - likely
    a transcription / band-labeling error; verify that row.
36. **Levetiracetam maximum contradiction.** The titration sheet gives effective max
    60 mg/kg/day; your case notes / item 6 say 80 mg/kg/day. Reconcile to one number.
37. **Ethosuximide adjunctive use.** Rule R3 switches ethosuximide on any non-absence seizure,
    which assumes monotherapy; ethosuximide is often used adjunctively (absence + GTC).
    Confirm the monotherapy assumption, or the switch may be wrong for a correct add-on.

## From the 2026-07-16 review-case reconciliation (cases B3/B4/B5/F1)

38. **Breakthrough + tolerable (Level-2) side effects while titrating no longer auto-slows
    (cases B4, B5).** The engine now lets the breakthrough URGENCY tier drive the plan even
    for legacy/6-level side-effect inputs: non-urgent → continue on schedule; urgent → quicken
    (end +1 higher); emergent → quicken (end +2 higher) - matching your v2 tables. The old
    behavior (any medium side effect while titrating → "slow the uptitration" + a 1-week
    adjustment family question) is **retired for breakthrough** cases. Confirm you're happy
    dropping that 1-week adjustment prompt for breakthrough (it remains for the seizure-free
    path). DRAFT.
39. **F1 - IMPLEMENTED 2026-07-16 (PI-confirmed).** A breakthrough reported WITH a frequency but
    WITHOUT a duration now assumes the middle tier (urgent) and follows the phase-appropriate urgent
    row (titrating: quicken to every 3 days, end +1 higher; if at max, no change) instead of
    deferring. A bare "yes" with neither frequency nor duration still defers (escalating on zero
    info would be unsafe). Because the engine is deterministic, this same policy updated 7 gold
    cases that had the same shape (A4->escalate, A5->add [at max], A6->escalate, A7->no_change [at
    max], A8->no_change, C4->escalate, J3->no_change); labels flipped from `insufficient_data`,
    marked `inferred`. **PLEASE SPOT-CHECK** those 7 outcomes (esp. the at-max ones: A5 add, and
    A7/A8/J3 "no change") - they follow your urgent table's at-max fallbacks but weren't individually
    on your checklist. Synthetic set now 63/63, including J2 (#14).
40. **FIXED on the engineering side - but it exposed two routing questions that DO need you.**
    The frequency-format bug (the urgency-tier classifier read `frequency_bucket`/`count_4wk`
    but not a per-episode `frequency`+`frequency_unit`, so a focal seizure entered as "5 per
    week" was tiered non-urgent) is **fixed and re-verified**. The classifier now applies your
    thresholds (item 19) to all three formats, and focal 5/wk tiers **urgent** - matching your
    threshold sheet (focal ≥1/week) and your review case B2.
    **Two routing-gate inconsistencies the fix revealed - please confirm the intent:**
    - (a) An **urgent** breakthrough with a *tolerable* side effect while still titrating
      resolves to **slow the titration** through the older rule path but to **escalate**
      through your 6-level urgent table. Which should win?
    - (b) An **urgent** focal seizure with **no** side effect resolves to **continue**, while
      the same seizure with a **small** side effect **escalates** - because the tier only
      reaches your tables through the side-effect-level gate. Should an urgent tier escalate
      on its own, regardless of side effects?
41. **Second-medicine / switch family preference (Q12a) - RULES RECEIVED, NOW BUILT (DRAFT).**
    Your workbook `kristen/Switching to a different medication.xlsx` supplied the rules. They are
    now encoded in `data/medication_selection_rules.json`. File is
    `verified:true` from the workbook copy; (a)–(e) are still open. On a **non-urgent** ADD- or
    SWITCH recommendation the family now sees a ranked **top-3** with your Sheet-1 info tables and
    marks each **1st / 2nd / 3rd choice or avoid**; that preference reaches you in the review view
    as a DRAFT. **Your clinical sign-off is needed on:**
    - (a) the Sheet-2 favor/avoid rules and the Sheet-1 base ranking exactly as encoded
      (the engine reproduces all 50 of your `Simulated Data` cases; 2 cases correctly yield <3
      options and are left blank for you);
    - (b) the **kidney-stones** question is on the survey and the rule fires.
      Confirm the rule;
    - (c) the **adherence → "poor adherence"** mapping: currently only a *stopped* medicine counts
      (a "sometimes misses" answer does **not** avoid lamotrigine/oxcarbazepine) - confirm the
      threshold;
    - (d) **policy:** is it acceptable to show families the ranked options *before* your review
      (framed as preference-elicitation, "your care team decides"), given the human-in-the-loop
      rule? Also confirm the ancestry-question wording shown when it wasn't already asked.
    - (e) the tool now treats the child's **current medication as already-tried**, so a switch/add
      never suggests the drug they are already on, and the treatment-naive "avoid brivaracetam"
      combination rule no longer fires for an already-treated child. Confirm this is the intended
      reading of "never tried any antiseizure medication."
    Engineering/UI details are in the engineering notes.
42. **Table transcription fixes reconciled to your v2 xlsx (confirm).** (a) urgent / still-
    titrating / Level-3 now lists "add a second medication" among the dosing-switch options
    (was missing; your sibling holding/steady cells already had it). (b) emergent / still-
    titrating / Level-3 no longer tags pace="faster" - your L3 cell says "end at 2 increases
    higher" with **no** "quicken to every 3 days" (unlike L0–L2). Confirm the pacing intent for
    emergent + "medium-a-lot" side effects.
43. **Superseded by #52.** TID while still titrating, any medicine, per your
    2026-10-25 answer.
44. **A1 & D1 family-message wording - RESOLVED (optional confirm).** Your edited family-message
    text WAS in the review sheet after all (message column), recovered for A1 and D1 and being
    folded into the family-message template. A1 → "you can continue the current dose with no
    changes" (drug name, the "care team may suggest" hedge, the do-not-change line, and the
    "your care team" sign-off all removed). D1 → same rewrite, adherence tips as a bulleted list.
    No action needed unless you want these to become the standard family-message style - then we
    apply the same edits everywhere.
45. **SAFETY - D1 "shorten the AI summary" touches a safety warning.** The summary you marked for
    trimming is the **physician-facing** clinical summary (not the family message, which is
    already short and does not contain these lines). Of the four lines you listed to remove, three
    are fine to cut (the "Do NOT switch to…", "Why this is suggested", and adherence-note lines).
    The fourth - "⚠ oxcarbazepine: consider HLA-B*1502 screening…" - is a **safety flag**, and our
    guardrail is that safety flags always surface to the physician and are never suppressed.
    **Resolved per your direction (please confirm in writing):** on a no-change continuation the
    HLA-B*1502 screening advisory is now dropped from the rendered clinician summary (it is not
    actionable when nothing is being started/switched). It is RETAINED in the structured
    safety_notes output for audit and still renders on escalate/switch/add recommendations (e.g.
    switching TO oxcarbazepine). Because this is a documented exception to the "never suppress a
    safety flag" guardrail, we'd like your written OK on file.
46. **B3 - "end 2 dose increases higher" needs the patient's planned end dose.** Your B3 note says
    to ask the patient their end dose on the current titration schedule, then end 2 increases
    higher. Right now the engine only PRINTS "end 2 dose increases higher" as guidance text - it
    does not capture the planned end dose or compute the target dose. To do it properly we need a
    NEW survey question (the child's current titration end/goal dose); please confirm we should add
    that question (and bless its wording), after which the engine can compute end-dose + 2 ladder
    steps. Same applies to the urgent "+1 higher" plans.

---

## 2026-07-28 - Family-message templates wired + cross-checked against `kristen/` kb-edits

Templates are generated by `engine_core.outputs.build_patient_message` (structured
decision → template; doses filled from your ladder). Every open question below was then
cross-checked against your authoritative markup
`kristen/Family_Message_Templates_for_Review  - kb edits.docx` and
`kristen/dose_to_tablet_conversion_scenarios.xlsx`. Status of each:

47. **Templates wired + your kb-edits applied - please confirm final wording.** Live: A1, A2,
    B1, B2, B3, B5, C, HOLD (watch-and-wait), E1, F, G, H + Labs/Follow-up add-ons. Selection:
    NO_CHANGE→A1 if still titrating else A2; ESCALATE→B3 if faster, else B2 if "two increases",
    else B1; SLOWER_UPTITRATION→C; HOLD→B5 if "weeks 1-2 stay…" else watch-and-wait; LOWER→E1;
    SWITCH→F; ADD→G; INSUFFICIENT_DATA + family question→H. Applied from your markup:
    - **C is now "slow the cadence"** (space increases to ~every 2 weeks instead of weekly,
      still climbing) - your edit moved C away from "stay at the current dose." The pure
      *stay/pause* case is now a separate **HOLD / watch-and-wait** message.
    - B1 opens "To help with seizures…"; A1 now says "no extra changes **to that plan**…".

48. **Doses in the message - CONFIRMED intended** ("the engine fills the doses from your
    ladder"). B1/B2/B3/B5 name the mg + per-dose; A1 names the goal (planned-end) dose. All
    numbers come only from your ladder / the family-reported current/planned-end dose; when
    no planned-end dose was reported the message holds off on the number (your §3).

49. **B4 / E2 / D - resolved by your kb-edits:**
    - **B4 half-step - dropped.** Your note: *"I think we only use 1 and 2 step increases now."*
      Not wired (matches your note).
    - **E2 keep-this-week-then-lower - parked.** Your note: *"I'm not sure which scenario this
      fits with, so I left it for now."* Left unwired until you place it.
    - **D change-the-split - now surfaced as an OPTION** (uneven AM/PM, or three-times-a-day)
      whenever the engine's dosing_note suggests it, total held constant. The AM/PM/TID
      **amounts are not printed** (engine does not compute the split) - and per your H note the
      split is really a family *choice*; see #52.

50. **E1 lower-dose target stays bracketed** - your own E1 brackets `[lower dose]`, and the
    ladder only resolves upward, so the physician sets it. Confirmed acceptable.

51. **RESOLVED 2026-10-25.** Safety flag withholds the family message.

52. **RESOLVED 2026-10-25 (the family chooses) - built; wording for you to edit (see below).** **NEW from your kb-edits - H is a family-choice flow for breakthrough + Level-3 side
    effects.** You specified that for *any breakthrough seizure + Level 3 (medium, a lot) side
    effects*, the survey should offer five options (1 uneven, 2 TID, 3 watch-and-wait, 4 lower
    + add a second med, 5 switch) with a drop-down, and the family's pick drives the message
    (→ D / watch-and-wait / E+G / F). This needs (a) a **new survey question** (Aim 1) and
    (b) engine routing from the answer - not built. Today those L3 cases produce the
    watch-and-wait/hold message with the split shown as an option. Confirm the flow + bless the
    option wording.

53. **RESOLVED 2026-10-25 (taper from your template F, old medicine off at week 4) - built in the summary; one reading to confirm (see below).** **NEW - F (switch) is a cross-taper plan you're still finalizing.** Your kb-edits replace
    the generic switch note with a **week-by-week cross-taper table** (current ×0.75 → ×0.50 →
    ×0.25 while the new medicine climbs) and add *"I am working on a standard plan for this one,
    so we can talk more in person."* I kept the generic "we'll go over the plan" message for
    now; send the finalized taper rule and I'll wire the table.

54. **Tablet/liquid layer - now in the CLINICIAN SUMMARY (2026-09-30); family message still mg-only.**
    Your answers on the Flags tab of `dose_to_tablet_conversion_scenarios.xlsx` are encoded in
    `data/formulation_rules.json` (draft): -10%/+30% on the total dose, stay on tablets/liquid,
    the child's own tablet strength first, uneven AM/PM for every medicine (larger dose at night,
    within 30/70-40/60), half tablets only on a climb step, "skip to the next step" when a climb
    step has no tablet fit, +25% OK, lamotrigine rounds like the others, OK above the effective
    max but never above the absolute max, ER/DR never split, valproate liquid 50 mg/mL.
    **Please confirm one thing:** you answered "Bias up" for rounding, but your worked example
    (30 kg, levetiracetam 250 mg tablets, 1300 mg/day -> 500 mg morning + 750 mg night =
    1250 mg/day) rounds to the CLOSEST dose (bias-up would give 1500). The engine follows the
    example: closest dose, ties go up. Also confirm before the tablet amounts go into the
    family message (B templates).

55. **NEW - G could name the specific second medicine + starting dose.** Your kb-edits end G
    with *"I sent a prescription to your pharmacy to start [new medication] [per dose]
    [frequency]."* The engine has a medication-selection layer
    (`medication_selection_rules.json`) that is not yet fed into the family message, so G
    currently says "I'll go over which medicine and the doses with you." Confirm you want G to
    name the drug/dose once that layer is wired.

56. **Summary §2 - clinical seizure names (partial).** The per-type breakdown uses your
    clinical labels where you gave them (focal → "focal motor seizures", whole-body →
    "generalized tonic-clonic seizures"), with responsiveness in the header and
    "lasting…/occurring…" phrasing. Tokens you did **not** label (staring, jerks, stiffening,
    acting-off) keep the family's lay description - the engine does not assert an unverified
    classification (absence/myoclonic/etc.). Send clinical labels for those if you want them
    shown.

## 2026-09-30 clinician-summary wording (display fixes done; three need your call)

Done without changing any recommendation: mg/kg/day shown for every dose when weight is
known; the absolute max labelled "(fixed cap)" instead of converted to mg/kg (3000 mg/day
read as "250 mg/kg/day" for a 12 kg child); rounded tablet doses above the usual effective
max are flagged; "+N%" now says "vs the ladder step"; the liquid fallback is labelled when
the family gives tablets; "reported at goal but below the typical range" is flagged; the
headroom line no longer mentions switching.

57. **Timing between steps in the summary.** "Step 1 … step 2 …" doesn't say how long to stay
    on step 1 (1 week, every 3 days when quickening, 2 weeks for lamotrigine / topiramate /
    zonisamide). Show the interval from your ladder band, and the 3-day interval when the
    plan is "quicken titration"?
58. **Rash wording.** For a spreading/blistering rash the suggested step reads "Consider
    switching to a different medicine"; "stop the medicine now" is only in the reason line.
    Should the suggested step itself say "STOP now, then switch"? (The same summary also
    shows the family's own "overall manageable" side-effect rating next to the SJS flag.)
59. **"If max" text in the reason line.** The reason copies the whole table cell, e.g. "Make 2
    dose increases … if max before 2 increases: add a second medication", even when the
    child is far from the max. Drop the "if max" clause unless the child is near/at the max?

## 2026-10-06 tablet / liquid conversion (age rule, one bottle, Dr. Barbour's round-1 comments)

Built as DRAFT in `data/formulation_rules.json` (display only; no recommendation changes). Workbook for
review: `kristen/tablet_dosing_logic_refinement.xlsx` (sheets "Round 1 comments", "Questions for you").

60. **ANSWERED 2026-10-25 (capsules are never split; use what is available in practice) - override removed; waiting for your updated sheet.** **Are the half-tablet products really splittable?** You asked for 2 + 2.5 tablets then 2 + 3 for
    valproic acid 500 mg and zonisamide 50 mg, and this is built (`half_tablet_overrides`). Our product
    list has valproate 500 mg only as a **delayed-release capsule** (no 500 mg tablet) and zonisamide 50 mg
    only as a **capsule**; both are normally swallowed whole, and your earlier rule #9 says ER/DR is never
    split. Confirm the exact products, add a 500 mg valproate tablet if one exists, or tell us to remove
    the override. (Also: 2250 mg at 45 kg is 50 mg/kg/day, your note says 45.)
61. **RESOLVED 2026-10-25 (keep the family's current form; the age rule is only for simulated cases) - built.** **Age rule vs. the form the family is on.** Built: over 10 y = tablets, 10 y and under = liquid
    (age 10 = liquid "for now"), and **age wins** over what the family reports (with a note). Confirm, or
    keep the current form. Also what to show when the preferred form has no product (no oral liquid listed
    for a young child; no tablet fits an older child).
62. **RESOLVED 2026-10-25 (no standard strength; best fit, fewest tablets) - built.** **One bottle per climb.** Built: one tablet strength across both steps (child's own strength if it fits
    as many steps as the best one; otherwise the closest single strength). When no single strength fits the
    -10%/+30% range, should we switch strength, skip ahead, or show liquid? (Currently: liquid is shown as a
    fallback, flagged "needs attention".)
63. **RESOLVED 2026-10-25 (use the ladder's step increases below the therapeutic range) - already how the ladder works; no change.** **Starting dose -> therapeutic dose schedules (data needed).** The examples now only cover children whose
    current dose is already in the therapeutic window. Send the titration schedules from the initial starting
    dose to the minimum maintenance dose; until then the engine would still convert those steps if asked.
64. **RESOLVED 2026-10-25 (v9 oxcarbazepine revision confirmed as intended).** **Oxcarbazepine ladder revision (data needed).** You noted the step increases are too small; the
    oxcarbazepine rows in the workbook are marked provisional until you send revised values.
65. **ANSWERED 2026-10-25 (follow your initiation-rule .md) - waiting for that file.** **Smaller judgment calls** (current setting in brackets): widest morning/night split [30/70; 40/60?];
    max tablets per dose [4]; larger dose at night for three-times-daily drugs [yes]; lamotrigine 2 mg / 5 mg
    chewable-dispersible tablets usable [yes]; default valproate product [first plain tablet/capsule line].
66. **RESOLVED 2026-10-25 (no standard strength; tablet size may change when a dose needs it) - built.** **One tablet strength per medicine (study team, 2026-10-15).** Levetiracetam plans now use the
    family's current tablet when it fits every step, otherwise **500 mg tablets** - never another
    best-fit strength (previously 250, 500 or 750 mg depending on weight/dose). Set in
    `data/formulation_rules.json` -> `standard_tablet_strength`. Trade-off: a few steps that only a
    250 mg tablet could hit now show the liquid fallback instead. Confirm 500 mg, and whether other
    medicines should get a standard strength too.
67. **RESOLVED 2026-10-25 ("500 mg (1 tablet) twice a day"; no "don't go higher than") - built.** **Family message names the tablets (study team, 2026-10-15).** When every increase step converts
    to tablets, the family draft now says e.g. "give 2 tablets in the morning and 3 tablets at night
    (250 mg each, 1250 mg/day)", and "don't go higher than" uses the tablet total (inside the
    -10%/+30% window, never above the absolute max) instead of the ladder mg/day. Liquid / partial
    conversions keep the mg wording plus the "How much to give" line. Confirm the wording.
68. **RESOLVED 2026-10-25 (no - the preference questions stay removed) - no change.** **Survey changes from your 2026-10-15 list (built, DRAFT).** (a) Removed the increase / add /
    switch preference questions: Q12 "Do you have a preference about the medicine?", 12a ranking,
    12b strength, and the at-goal "continue increasing vs add another medication" question. The
    engine treats an unanswered Q12 as no strong preference, so rules R7 and R16 (family wants a
    slower increase) and R15 (family wants a different medicine) no longer fire from the survey; R12g still asks the family the increase-vs-add
    question in its draft message. (b) The medicine-options card now shows only when a switch/add is
    likely: at the goal dose with ongoing seizures, or side effects at Level 3 or higher. (c) New
    Level-3 question: wait vs uneven AM/PM vs three-times-a-day, showing only the options the engine
    suggests (uneven for the eligible medicines in `no_seizures_decision_table.json`; otherwise TID at
    steady state or with seizures). The answer goes to the clinician summary only, never into the
    decision. Wording is a draft for you to edit (#52). (d) Fixed: the "Your preference" card checked a
    value that no option had, so it never appeared; the 1-week adjustment question now shows for
    still-titrating + Level 2/3.
69. **RESOLVED 2026-10-25 (off at week 4; new medicine starts in week 1) - built in the summary.** **Switch: stopping the old medicine (v9 email vs template F table).** Her template F table
    (kb edits) tapers the old medicine x0.75 / x0.50 / x0.25 "and continue" while the new one reaches its
    goal by week 4; her 2026-10-23 email says decrease "until week 4 off". The new starting tabs take 3-14
    weeks to reach goal. NOT built: the switch summary shows only the new medicine's starting schedule.
    Questions in kristen/logic_tradeoffs_for_discussion.md section 14.
70. **CONFIRMED 2026-10-25 (all four v6->v9 changes intended) - data flags still open, see below.** **final_doses_titration_v9 loaded (2026-10-23).** data/dose_titration_ladder.json is now rebuilt from
    the sheet by data/_build_dose_ladder.py (verified to reproduce the v6 JSON exactly, apart from the two
    documented oxcarbazepine >=70 kg hand corrections); asm_rules.json rebuilt from it. The 10
    '<Drug> Initiation' tabs are in data/initiation_schedules.json (verbatim) and shown to the clinician
    for switch/add options; defers when no band, two bands, no tab (carbamazepine) or lamotrigine with
    valproate / an enzyme inducer. All six v6 data-quality flags are resolved in v9. Left
    false: zonisamide 2 to <5 and ≥5 to <16 (3.5 to 5 mg/kg/day), brivaracetam ≥50 kg
    (initial above minimum maintenance). Other v9 bands are `verified:true` pending the
    line check in #6. Open
    questions in kristen/logic_tradeoffs_for_discussion.md section 15. Also fixed: data-quality flags were
    matched by drug only and shown on every band of that drug; now band-specific.


## 2026-10-25 - answers (what I built, and what is still open)

Superseded by the 2026-10-26 note (then: 523 tests, κ 0.976, J2 only).

**Built**

- **#51 Safety flag -> no family message.** On any safety flag the engine now gives no family draft
  at all (no STOP / contact-the-clinic draft). The review page already shows "Family message
  withheld - handle per urgent protocol".
- **#69 Switching.** The doctor's summary now shows how to stop the old medicine under the new
  medicine's starting schedule: week 1 current dose, week 2 ×0.75, week 3 ×0.50, week 4 ×0.25, then
  off, with the new medicine starting in week 1. **Please confirm one reading:** you said "off at week
  4". I built it as ×0.25 *during* week 4 and stopped after week 4 (your template F table has ×0.25 in
  week 4). If you meant no medicine at all in week 4, I'll drop the ×0.25 week. The family message for
  a switch is unchanged ("we'll go over the plan").
- **#52 Level-3 side effects: the family chooses.** The survey uses your template H wording. It offers
  uneven dosing, three times a day, and watch and wait for any medicine, in any phase (three times a
  day is now offered while still increasing). It offers "lower the dose and add a second medication"
  only with breakthrough seizures, and the engine acts on it only where your table cell lists it
  (urgent / emergent L3). Their answer is now a structured field the tables read, not a free-text
  note. If they chose uneven or three times a day, the plan is split that way. A hold is re-split
  with the same total when whole tablets allow it; otherwise the team sets the amounts. If they
  didn't choose, the draft lists the cell's options for them.
- **Level 4 + urgent ongoing seizures -> lower the dose and add a second medicine,** straight from
  your table. The family draft now says both (lower + add), using your option-4 wording; the lower
  target stays "[lower dose - physician to confirm]". Behavioral side effects follow the same table
  as every other side effect (they already did; I added a test).
- **Decided dose only (rounding).** The family message and the doctor's summary show only the dose the
  family will actually give: the tablet plan, or the liquid rounded to a measurable volume. They no
  longer show the exact ladder value (e.g. 1112.14 mg/day becomes 1110 mg/day = 11.1 mL twice a day)
  or the "% vs the ladder" figure. The exact ladder value stays in the structured schedule for the
  audit trail. **To confirm:** I round liquid to the nearest **0.1 mL** per dose (still inside your
  -10%/+30% window, never above the max). Tell me if you'd prefer a different step (e.g. 0.5 mL for
  large volumes).
- **#67 Tablet wording.** Each step now reads like "levetiracetam: give 500 mg (1 tablet) twice a
  day", or "500 mg (2 tablets) in the morning and 750 mg (3 tablets) at night". Liquid reads "530 mg
  (5.3 mL) twice a day". The "Please don't go higher than…" line is gone everywhere.
- **Mixed liquid/tablet plans (Hazel).** Every step sentence names its own decided dose (the
  liquid step in mL, the tablet step as tablets), so the ladder dose (1217.83) never appears.
- **Ethosuximide 250 mg = capsule.** The wording comes from the product form now, so ethosuximide
  (and any other capsule) says "capsules".
- **#61 Tablets vs liquid.** The family's current form is kept. The age rule (over 10 = tablets) is
  used only when the form isn't known, i.e. simulated cases. The new medicine's starting schedule
  follows the same rule.
- **#62 / #66 Tablet strength.** No standard strength any more. For each step I pick the strength whose
  plan lands closest to the ladder dose. On a tie, the child's current tablet (or the one the previous
  step used) wins, then the fewest tablets. One strength per step; the strength can change from step to
  step when the dose needs it (e.g. 1 × 750 mg -> 2 × 500 mg twice a day), and the family message
  says so. Your initiation-rule .md may refine this ordering once I have it.
- **#60 Half units.** I removed the half-unit override for valproate 500 mg and zonisamide 50 mg.
  Capsules are never split.
- **Carbamazepine** is never offered as a new medication (it was already absent from the medicine
  options; a switch target of carbamazepine now says so instead of "no schedule yet").
- **Medicine-options questions** moved to a second page after the check-in. It appears only when the
  decision is switch or add, and families can skip it.
- **Already as you answered (no change):** breakthrough with no length -> assume urgent (F1);
  near the max, the table's "if max before…" clause decides; levetiracetam blank tablet bands
  (17.5-20 kg) say "use the liquid"; below the therapeutic range the ladder's step increases are used.

**Waiting on files from you**

- **Your initiation-rule .md** (tablet-choice examples, #65 settings: the -10%/+30% window on the goal
  step, max 4 tablets per dose, the 30/70 vs 40/60 split). It isn't in my folder yet; the current
  settings stay until I have it.
- **Your updated v9 sheet**, which separates tablets from capsules (#60) and fixes brivaracetam ≥50 kg,
  topiramate 11-12 kg, lamotrigine weight-vs-age / age 16, and the product lists (valproate plain
  tablets, lamotrigine 50/100 mg chewables, topiramate 15 mg). My copy is still the 2026-10-23 one.
  When it arrives I'll rebuild the ladder, starting schedules and asm_rules from it.

**Still need your call**

- **Zonisamide child step (v9 flag).** You wrote "the zonisamide steps look like 0.5 mg/kg/day". Do you
  mean the 3.5 -> 5 mg/kg/day jump should be broken into 0.5 mg/kg/day steps (3.5, 4, 4.5, 5), or
  that 1.5 is fine? I've left the sheet's values as they are until you say which.
- **Draft wording for you to edit (#52).** The Level-3 messages now in the draft:
  - *Uneven (chosen):* "To help with side effects, as you chose, let's change how [medicine] is given:
    give [AM dose] in the morning and [PM dose] at night - the same total each day."
  - *Three times a day (chosen):* "…give it three times a day instead of twice a day: [morning],
    [afternoon] and [night] - the same total each day."
  - *Not chosen yet:* "We have a few options to handle the side effects, in no particular order: (1) a
    smaller dose in the morning and a larger dose at night…; (2) giving the medicine in smaller doses
    three times a day…; (3) watching and waiting… Let us know which you would prefer."
  - *Lower + add:* "To help with side effects, let's lower the [medicine] dose to [lower dose] and add
    a second medicine alongside it. Sometimes two medications, at lower doses each, is a better fit for
    both seizures and side effects. Your team will go over which medicine and the exact doses with you."
  - *At goal (R12g, ongoing seizures + medium side effects):* the question still uses your 2026-06
    wording ("…continue increasing the dose? Or … adding another medication…"). A shorter draft for
    you to edit: "Your child is at the goal dose and still having some seizures, with side effects that
    are noticeable but manageable. We could keep increasing [medicine] a little, or add a second
    medicine that works in a different way. Which would you prefer?" I haven't wired it in yet.

## 2026-10-28 - uneven and three-times-a-day doses the family already gives

**Built (draft, for you to check)**

- **Survey (Q1a).** After "Times per day", the check-in now asks "Is it the same amount each time?".
  If the answer is no, the family enters one amount per dose (Morning / Night, Morning / Afternoon /
  Night, or four slots for 4 a day). The live mg box adds them up (e.g. 500 mg + 750 mg = 1250 mg
  total per day). A blank dose box is required on the page; if one still arrives empty, the total is
  "not computable" and the summary says so, as with a missing tablet strength.
- **Summary.** The current dose line now shows the split: "1250 mg/day (41.7 mg/kg/day) (given as
  500 mg morning, 750 mg night)".
- **Level-3 choice already in place.** If the child already takes the medicine three times a day and
  the family picks three times a day (or already gives uneven doses and picks uneven), there is
  nothing to change. I don't act on that pick. It goes to you as "clinician to discuss", the family
  draft keeps the watch-and-wait / hold message, and its option list leaves out what they already
  do. The survey also hides that option.
- **Wording.** "Three times a day instead of twice a day" now uses the child's real schedule (e.g.
  "instead of once a day").

**Need your call**

- **Keep the family's uneven split on increases?** When a family already gives uneven doses, I
  currently plan the next increase steps as uneven too (same 30/70-40/60 range, larger dose at
  night), and fall back to equal doses only when no uneven split of whole tablets fits. Setting:
  `uneven_dosing.keep_family_split_on_increase` in `data/formulation_rules.json`. Display only; the
  dose decision is unchanged. Should I keep this, or always go back to equal doses?
- **Uneven liquid.** For a liquid, the next steps are still equal doses (the liquid rounding only
  splits evenly). Is that fine, or should liquid steps also keep the family's split?
- **Fixed: the current medicine counts when a new one starts (add and switch).** Before, the
  starting-schedule check for lamotrigine ignored the child's current medicine, so a child on
  valproate saw the standard lamotrigine schedule when lamotrigine was suggested as an add or a
  switch. Now the current medicine counts (it stays on for an add and overlaps the 4-week taper on
  a switch), and the tool defers the starting doses to you. **To confirm:** on a switch from
  valproate, should lamotrigine wait until valproate is off (after week 4), or start in week 1 on
  the with-valproate regimen? Details and the rest of the add/start questions:
  `kristen/add_start_medication_logic.md`.
