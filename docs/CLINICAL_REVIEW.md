# Clinical Review - Aim 2 ASM Titration Assistant

*Reviewer perspective: a senior pediatric epileptologist who manages childhood
epilepsy in clinic daily. This is a DESIGN review of a physician-in-the-loop
decision-support tool - it does not override the PI's (Dr. Barbour's) clinical
authority, and every value in the engine remains DRAFT until she signs off.*

All of the decision content reviewed here is **Kristen's feedback** (the 5 Excel
sheets + comments). Below I mark what is **clinically sound and already
addressed (✅)** versus what is **ambiguous / unverified (⚠️ → moved to
`NEEDS_PI_SIGNOFF.md`)**.

> **Engineering status added 2026-08-04.** This review was written 2026-06-25; the
> ⚠️ items below now carry a dated note recording what the code does today. The
> clinical concerns themselves still stand until Dr. Barbour rules on them -
> `../aim2_engine/NEEDS_PI_SIGNOFF.md` is the live checklist.

## What is clinically sound (✅ addressed)

- **Rule-based, physician-approves-everything, draft-tagged, safety flags surface.**
  This is the right architecture for pediatric epilepsy decision support - no
  black-box dosing, every recommendation traceable. ✅
- **Adherence-first on a missed-dose-provoked breakthrough.** Correct - you treat
  the missed dose, you don't chase the maintenance dose. ✅
- **Seizure-free + big side effects → lower the dose; "not a fit" → switch.**
  Matches how I'd manage a well-controlled child who is over-medicated. ✅
- **Side-effect "adjustment period" question (does it ease as the body adjusts?)**
  reflects real practice - early post-titration side effects often settle. ✅
- **VPA lab panel (CBC, CMP, LFTs, ammonia, level).** Appropriate, including
  ammonia for the valproate-encephalopathy question. ✅
- **Lamotrigine rash → SJS pathway** handled as a distinct urgent safety route. ✅
- **Weight (kg) and age captured.** Essential - pediatric ASM dosing is weight-
  based; you cannot titrate sensibly without them. ✅
- **Precise per-type urgency thresholds** (20/30/90/180/300 s + frequencies) now
  evaluated per seizure type, erring toward the higher tier at boundaries. ✅
- **New "acting off / not responding" type** correctly modeled as focal impaired
  awareness. ✅

## Clinical concerns - ambiguous or unverified (⚠️ → sign-off)

1. **⚠️ TOP SAFETY ISSUE - prolonged seizures and status epilepticus.** A
   convulsive seizure ≥5 minutes is **status epilepticus**: the response is
   *rescue medication now and call emergency services*, not "switch medication;
   schedule an urgent visit/phone call" (which is how the emergent table's L5 reads).
   The engine treats **whole-body ≥3 min** as an emergency (good), but a **focal**
   or **acting-off** seizure ≥5 min (focal/non-convulsive status) currently flows
   through the emergent *table* to an "urgent call," which is too weak. Any seizure
   ≥5 min should hit the acute-emergency pathway (911 / the child's rescue plan)
   and that must take precedence over the table. **Must verify/fix before use.**
   *(2026-08-04: unchanged in code - an `over_5min` duration bucket exists and
   feeds the emergent line, but the ≥5-min-overrides-the-table rule is not
   implemented. Open as `NEEDS_PI_SIGNOFF.md` #4.)*

2. **⚠️ No rescue-medication / seizure action plan anywhere.** Every child with
   breakthrough or prolonged seizures should have a rescue plan (intranasal
   midazolam, rectal diazepam, etc.). The tool manages maintenance titration only.
   Confirm whether rescue therapy is intentionally out of Aim 2 scope; if so, the
   family-facing copy for prolonged seizures should still point to their existing
   rescue plan / emergency number.

3. **⚠️ Drug–seizure-type aggravation.** Confirm the engine flags agents that can
   *worsen* the reported type: carbamazepine/oxcarbazepine (and sometimes
   gabapentin) can aggravate **absence and myoclonic** seizures; lamotrigine can
   worsen **myoclonic** (Dravet especially). With the new absence/"acting-off"
   types, please confirm these aggravation flags fire.
   *(2026-10-26: CBZ/OXC with absence or myoclonic adds an advisory note.
   Category and dose are unchanged. Those two drugs stay `verified:false`.
   LTG myoclonic was already in her notes.)*

4. **⚠️ "Absence ≥10/day = urgent."** At presentation, untreated childhood absence
   epilepsy commonly has many absences per day - flagging every such check-in as
   "urgent" may over-trigger. Confirm this is intended (vs. expected-at-baseline).

5. **⚠️ "GTC ≥1/week = emergent."** A weekly generalized tonic-clonic is serious
   and warrants prompt escalation, but routing it to the *emergent* path (urgent
   visit/call, withhold family message) at every check-in may be operationally
   heavy. Confirm urgent vs emergent for recurrent (not prolonged) GTCs.

6. ~~**⚠️ Dose targets are not computed.**~~ **RESOLVED (2026-07 → verified
   2026-08-04).** "End at +1/+2 increase," "make 1–2 increases" now resolve to
   actual mg. `data/dose_titration_ladder.json` holds Dr. Barbour's exact per-band
   rungs; `engine_core/dose_ladder.resolve_plan()` returns the next rungs up
   (`next_up[].target_mg_per_day`), and `engine_core/outputs` turns those into the
   rendered schedule with `target_total_mg_per_day` **and** `per_dose_mg` per step,
   her ceiling enforced.
   The family message names the same numbers (never any other number). Two
   remaining pieces: the "½ dose increase" half-step was **dropped** at her
   direction (#49). Three ladder flags and the line check are still open (#6). *(The "+1/+2 higher" plans also need her to bless the new
   planned-end-dose survey question - #46; the field `Q10f_planned_end_mg_per_day`
   is already wired.)*

7. **⚠️ Breakthrough tables apply only at side-effect level ≥2.** The most common
   breakthrough scenario (seizures, *no* side effects → increase the dose) uses the
   legacy rules, not the new table's L0/L1 columns. Confirm the legacy output
   matches the table intent so behavior is consistent.
   *(2026-10-26: level 0 or 1 enters the breakthrough table when duration is
   captured. #40(a) and #40(b) are still open.)*

8. **⚠️ Source-file sanity check.** The "emergent" decision sheet's row labels read
   "Breakthrough Seizure (urgent)" (likely a copy-paste), and the emergent
   thresholds sheet repeats "D4 ≥1 per day" for rows E and F. I implemented by file
   name + column intent; please confirm the emergent file is the emergent table.

9. **⚠️ Boundary precision.** Families cannot reliably distinguish 20 vs 30 seconds.
   The tool rounds toward the safer (higher) tier at boundaries - confirm that's
   acceptable, and consider collapsing the 20–30 s distinction if it proves noisy.

## Bottom line

The framework and most of Kristen's decision content are clinically sensible and
faithfully implemented. **The one item I would not deploy without resolving is #1
(status-epilepticus / ≥5-minute escalation).** The rest are confirmations and
refinements, now itemized in `NEEDS_PI_SIGNOFF.md`.
