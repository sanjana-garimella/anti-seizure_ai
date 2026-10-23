# Data Contract (Aim 2 Engine)

The integration seam between the Aim 1 survey platform and the Aim 2 engine.
Build an `IntervalSnapshot`, call `run_engine`, consume a `Recommendation`.

## Input: IntervalSnapshot

```
IntervalSnapshot
  patient: PatientContext       external_id, age_years, weight_kg, sex,
                                epilepsy_etiology, seizure_types[], site
  medication: MedicationContext medication (generic), dose_amount, dose_unit,
                                formulation, frequency_per_day, concentration,
                                goal_dose, is_goal_reached, dose_changed_or_stopped
  survey: IntervalSurvey        (built from §7 answers - table below)
  contingency: PhysicianContingency  can_increase_dose, can_add_medication,
                                can_switch_to, notes   (from physician intake)
  snapshot_id, taken_at
```

`MedicationContext.total_daily_dose` is derived from the family's reported dose
only (amount × frequency, ×concentration for liquids); it echoes the report and
introduces no clinical value.

## §7 interval answers → IntervalSurvey

Pass a dict keyed by question code to `survey_from_answers()` /
`snapshot_from_interval()`. Values are the structured option **values** below.

| Code | Meaning | Accepted values |
|---|---|---|
| `Q2` | adherence | `almost_never` \| `sometimes` \| `stopped` |
| `Q2a` | missed-dose reasons | list of strings |
| `Q3` | any seizures | `no` \| `yes` |
| `Q4` | seizure types | list of strings |
| `Q5` | seizure count | integer |
| `Q5_unit` | count unit | `per_day` \| `per_week` \| `per_month` |
| `Q5b` | most recent seizure | date string (exact date; see NEEDS_PI_SIGNOFF) |
| `Q6` | trend vs last visit | `fewer` \| `about_same` \| `more` \| `new_type` |
| `Q7` | on meds 2 days prior | `true` \| `false` |
| `Q9` | side effects present | list of strings |
| `Q9a` | rash severity | `small` \| `large` \| `blisters` |
| `Q10a` | dose/goal status | `still_increasing` \| `at_goal` \| `not_sure` |
| `Q10b_impact` / `Q10c_impact` | side-effect impact scale | **Current (Barbour 6-level):** `no_impact` \| `small` \| `medium_tolerable` \| `medium_alot` \| `big` \| `not_a_fit`. **Legacy, still accepted:** `medium_big` \| `big_wants_change`. The 6-level tokens are the ones that route through her decision tables (`categorize.IMPACT_TO_SEVERITY` / `IMPACT_TO_LEVEL`) - emit those. |
| `Q10c_time` | time at current dose | free text |
| `Q11` | overall change | `better` \| `about_same` \| `worse` \| `not_sure` |
| `Q12` | family preference | `no_preference` \| `change_dose` \| `different_medicine` \| `other` |
| `Q13` | free text | string (passed through; read by optional NLP layer, not the decision) |
| `urgent_safety` | page-1 emergency items already raised | list of strings |

### Additional keys the mapper reads

| Code | Meaning | Accepted values |
|---|---|---|
| `Q2b` (alias `stopped_reason`) | why doses were missed/stopped | free text |
| `Q9_other` (alias `side_effects_other`) | side effect not on the list | free text |
| `Q10d_dose_limited_by_se` | side effects capped the dose | bool-ish |
| `Q10e_min_goal_unreachable_se` | can't reach the minimum goal dose because of side effects | bool-ish |
| `Q10f_planned_end_mg_per_day` | the child's **planned end / goal** total mg/day on the current schedule | number (drives the "+1 / +2 increases" plans and the goal dose named in the family message) |
| `Q14_viral_illness` | recent viral illness | bool-ish |
| `Q_sjs_risk_comeds` | on a known SJS-risk co-medication | bool-ish / list |
| `seizure_duration` | single overall duration when no per-episode detail was captured | duration bucket (below) |
| `seizure_triggers` / `seizure_triggers_all` | provoking factors; whether they applied to every episode | list of strings / bool-ish |
| `med_pref_rank` / `med_pref_strength` | the family's ranking of the offered switch/add options and how strongly they hold it | list of drug keys / string |
| `seizure_episodes` | per-episode detail (below) | list of dicts |

### `seizure_episodes[]` - per-episode schema

| Key | Meaning | Accepted values |
|---|---|---|
| `look` | seizure type(s) | list of `whole_body` \| `focal` \| `brief_staring` \| `sudden_jerks` \| `acting_off` \| `other` |
| `duration` | how long it lasted | fine buckets `under_20s` \| `20_30s` \| `30_90s` \| `90s_3min` \| `3_5min` \| `over_5min`; legacy minute buckets `under_1min` \| `1_3min` \| `over_3min`; or `not_sure` |
| `frequency` + `frequency_unit` | explicit count form | number + `per_day` \| `per_week` \| `per_month` |
| `frequency_bucket` | range form | `daily` \| `weekly` \| `monthly` \| `less_than_monthly` |
| `count_4wk` | exact count over the last 4 weeks | number |
| `last` | most recent occurrence | date string |
| `unresponsive` | child was unresponsive during it | bool-ish |

**Frequency format - resolved.** The urgency classifier accepts **all three**
forms and prefers them in this order: `count_4wk` → explicit
`frequency` + `frequency_unit` → `frequency_bucket`. (It previously read only
`count_4wk`/`frequency_bucket`, which under-triaged an explicit count such as
focal "5 per week"; fixed in `categorize._episode_freqs`.) Aim 1 may emit
whichever form the survey produces.

Unknown/missing keys fall back to safe defaults (seizure-free, good adherence,
not-sure goal). The mapping lives in `snapshot.py` and is the only place to edit
if the survey wording changes.

**Open integration decision (not a PI clinical ask):** the medication-selection
seizure-type input reads the per-visit `Q4` ("what did the recent seizures look
like"), not the stored diagnosis (`PatientContext.seizure_types[]`). Whether the
host system should feed the profile diagnosis instead of / in addition to the
per-visit report is an Aim 1 integration call - the engine consumes whichever
field the contract supplies.

## Output: Recommendation

```
Recommendation.to_dict() -> {
  category:            escalate | slower_uptitration | switch_medication |
                       add_medication | lower_dose | hold |
                       no_change | insufficient_data
  titration_schedule:  null | {status:"unavailable", note} |
                       {status:"ok", current_total_mg_per_day, ceiling_mg_per_day,
                        interval_weeks_between_steps, steps:[...], note}
  clinical_summary:    str (EHR-ready; see "Clinical summary format" below)
  draft_patient_message: str | null
                       On a safety flag the engine currently returns a short
                       PI-supplied STOP/contact-the-clinic DRAFT, not null.
                       (Withhold-vs-draft is open - NEEDS_PI_SIGNOFF.md #51 -
                       so a consumer must handle null as well.)
  safety_concern_flag: bool
  rationale:           [ {rule, description, inputs}, ... ]
  requires_physician_review: true
  switch_target:       str | null
  switch_family_note:  str | null     (annotation only; never changes category)
  urgent_flags:        [str]
  engine_version:      str
  pace:                faster | standard | slower | null
  followup_weeks:      int | null
  safety_notes:        [str]          (advisory; never suppressed)
  switch_exclusions:   [str]          (do-not-switch-to)
  reason_summary:      str | null     one-line "why" for the physician
  family_prompt:       str | null     PI-supplied wording
  lab_orders:          [str]
  dosing_note:         str | null
  guidance_steps:      str | null     verbatim clinician steps
  urgency_tier:        non_urgent | urgent | emergent | null
                       The tier of THIS breakthrough, NOT "the tier the firing
                       rule tagged": taken from the decision when its path sets
                       one, else from the classifier. Which rule fired is in
                       `rationale`. (Before 2026-08-05 this was decision-only, so
                       emergent cases reported null.)
  sources:             {...} | null   provenance: rules fired + every source used
  seizure_activity:    bool           (triage signal -> Flag)
  wants_med_change:    bool           (triage signal -> Flag)
  dose_exceeds_max:    bool           reported dose above the recommended max
}
```

Titration numbers appear **only** when clinician ASM dosing data is supplied for
the patient's band; otherwise `status:"unavailable"` and no numbers are produced.

## Clinical summary format

`clinical_summary` is plain text with a stable grammar (`engine_core.outputs._Note`),
so a consumer can lay it out without guessing. Section titles and their order come
from `outputs.SUMMARY_SECTIONS`; a section with nothing to say is omitted and the
numbering closes up.

```
BETWEEN-VISIT SUMMARY                      <- line 1, fixed
Patient <id> · <age> · <weight> · Medication: <med>

1. MEDICATION AND DOSE                     <- "<n>. <TITLE>", n is sequential
  - Current dose: 500 mg/day (16.7 mg/kg/day)      <- "  - " bullet, label: value
      Headroom: still below the usual effective max <- "      " detail
  - ...
2. SEIZURES        3. SIDE EFFECTS      4. FAMILY REPORT
5. RECOMMENDATION
  - Why this is suggested:
      - <reason>                           <- "      - " sub-bullet
6. SAFETY
  ! <safety line>                          <- "  ! " alert lead
7. CHECKS                                  <- post-engine checks (api.py)

<note-level footnotes, e.g. the DRAFT dosing-provenance tag>

No recommendation is final until the reviewing physician approves it.   <- last line
```

Rules a consumer can rely on: exactly two body levels (bullet, detail); safety lines
carry the `!` lead; the physician-review notice is always the final line; the
`urgency_tier` is written hyphenated and upper case (`NON-URGENT`, not `NON_URGENT`).

## Persistence (clinic_store.py)

The web app stores each check-in so it can be re-run and triaged without re-deriving
anything from the note text:

| Table.column | Why it exists |
|---|---|
| `submissions.med_context_json` | The **resolved** MedicationContext the engine ran on (medication, dose_amount, dose_unit, frequency_per_day, concentration). The family confirms their real dose/strength in Q1a and those fields arrive on the raw POST form only, so without this a re-run silently falls back to the doctor's intake dose. Makes a submission losslessly re-runnable (`tools/regen_notes.py`). |
| `recommendations.urgency_tier` | Queue classification (emergent / urgent / non-urgent) without parsing the note. |
| `recommendations.dose_exceeds_max` | Queue chip for a reported dose above the recommended maximum. |
| `recommendations.reason_summary` | The one-line "why" shown on the queue card. |

All four are added by `init_db()`'s ALTER migrations, so an older database picks them
up on next start; rows written before a column existed read as `null`.
