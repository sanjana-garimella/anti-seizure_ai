"""Engine version - tagged so validation runs are reproducible (spec §9).

0.4.0 - Barbour 2026-10-25 answers: no family message on any safety flag (#51);
        Level-3 family choice (uneven / TID / wait / lower+add) read from each
        decision-table cell, for any medicine (#52); old-medicine taper on a switch
        (#69); family message + summary show only the decided dose (tablets, or
        liquid rounded to 0.1 mL), mg-first wording, no "don't go higher than"
        (#67); tablet strength = best fit per step, no standard strength (#62/#66);
        keep the family's current form, age rule only when unknown (#61); capsules
        never split (#60); carbamazepine never started as new; medicine-options
        questions moved to a second page shown only for switch/add. (Was 0.3.0.)
0.3.0 - Barbour 2026-06 UPDATE: re-cut seizure-duration buckets (<1min / 1-3min /
        >3min); whole-body >3-min DANGER rule; <1-min-while-titrating = fine;
        frequency RANGE buckets (daily/weekly/monthly/<monthly); new ADD_MEDICATION
        category with the add-vs-switch logic; at-goal/titrating x medium/big-impact
        branches with PI-supplied preference + 1-week adjustment prompts; new
        'acting confused' side effect + 'Other' free-text box; per-seizure
        'unresponsive' capture. (Was 0.2.0.)
0.2.0 - Barbour 2026-06 review: seizure duration/frequency rules, weight-based
        dosing + clinician ceilings, drug-appropriateness (ethosuximide indication,
        lamotrigine/levetiracetam contraindications, do-not-switch targets), SJS
        rash handling, helpfulness/time-on-med switch timing, follow-up scheduling,
        adherence tips, recommendation reason. (Was 0.1.0.)
"""

ENGINE_VERSION = "0.4.0"
