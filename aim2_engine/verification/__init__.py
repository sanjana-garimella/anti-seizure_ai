"""
verification/
-------------
BUILD-TIME machine verification of the clinician-supplied rule tables. These
tools NEVER run inside the decision path (engine_core/) and NEVER import from
it at runtime. They read data/*.json offline and prove structural safety
properties, emitting an auditable proof artifact for PI review.

Per clinical-safety.md + NON_NEGOTIABLE_RULES.md:
  * No clinical values are defined here (everything is read from the JSON).
  * Findings are ADVISORY: a proof failure flags a table for the PI; it does
    not silently edit rules.
  * The trusted checker is Z3 (an independent SMT solver) when installed; a
    pure-stdlib exact interval checker is the fallback so the suite runs in the
    no-dependency project environment.
"""
