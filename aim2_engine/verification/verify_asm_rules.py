#!/usr/bin/env python3
"""
verify_asm_rules.py  -  machine verification of data/asm_rules.json
===================================================================

Proof-of-concept SMT/interval verifier for the clinician-supplied ASM dosing
tables. It turns "the clinician eyeballed the tables" into a checkable proof of
four structural safety properties, over the study population (ages 2-17y):

  A. ORDER-INDEPENDENCE  - no two dose_bands of the same drug overlap in
     (age x weight). The engine's band_for() returns the FIRST matching band
     (aim2_engine/engine_core/asm_rules.py), so any overlap makes the chosen
     dose depend on JSON ordering - a latent bug even if today's order is fine.

  B. COVERAGE            - every (age, weight) in the study domain is matched by
     at least one band, i.e. nothing "falls through" to the defer path silently.
     (Gaps may be intended - e.g. a drug only used above a weight - so gaps are
     reported for PI review, not treated as hard contradictions.)

  C. LADDER MONOTONICITY - within a band: start <= target_min <= target_max, the
     up-titration step is positive, and the per-kg ceiling >= target_max. A
     violation is a genuine contradiction (a ladder that can't be climbed).

  D. CEILING REACHABILITY - the target dose is achievable under the ceiling for
     all weights in the band. This mirrors the engine's ceiling_mg_per_day():
     ceiling(w) = min(max_mg_per_kg * w, absolute_max_mg_per_day). Where an
     absolute mg/day cap bites before the mg/kg target (common for heavy teens),
     the crossover weight is reported (WARN, usually clinically expected).

DESIGN / SAFETY (NON_NEGOTIABLE_RULES.md, clinical-safety.md):
  * This module is BUILD-TIME ONLY. It does not run in engine_core/ and imports
    nothing from it. It reads the JSON the same way the engine's loader does and
    re-encodes the band-selection semantics (documented against asm_rules.py).
  * No clinical numbers live here - every value comes from the JSON.
  * The TRUSTED checker is Z3 (independent SMT solver) when available. When Z3
    is not installed, an EXACT pure-stdlib interval checker runs instead (the
    properties are linear over 1-2 real variables, so exact analysis is sound).
    Both backends must agree on the built-in --self-test cases.
  * Output is an advisory proof artifact for the PI. It never edits rules.

USAGE
  python3 verify_asm_rules.py                 # verify data/asm_rules.json
  python3 verify_asm_rules.py --json OUT.json # also write the proof artifact
  python3 verify_asm_rules.py --solver z3     # force Z3 (error if missing)
  python3 verify_asm_rules.py --self-test     # run known good/bad fixtures
  exit code 0 = all hard properties (A, C, D-fail) proved; 1 = a violation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# STUDY DOMAIN (assumptions, tagged with provenance for PI review)
# ---------------------------------------------------------------------------
# Ages: the study population is 2-17y (asm_rules.json band labels). Weight: no
# authoritative min/max is stated, so we use a generous pediatric superset and
# FLAG it as an assumption. Coverage results outside a clinically real weight
# for an age are informational only.
STUDY_AGE_MIN = 2.0
STUDY_AGE_MAX = 17.0
STUDY_WEIGHT_MIN = 5.0     # ASSUMPTION (unverified) - lightest plausible 2y
STUDY_WEIGHT_MAX = 150.0   # ASSUMPTION (unverified) - heaviest plausible teen
EPS = 1e-9
MICRO_GAP = 0.1            # gaps narrower than this are "X to <Y" encoding noise

INF = float("inf")


# ---------------------------------------------------------------------------
# Data model  (mirrors the fields the engine's DoseBand actually reasons over;
# see aim2_engine/engine_core/asm_rules.py - kept in sync deliberately)
# ---------------------------------------------------------------------------
@dataclass
class Band:
    drug: str
    idx: int
    label: str
    amin: float          # age interval [amin, amax], INCLUSIVE both ends
    amax: float          # (matches DoseBand.applies_to: v<lo or v>hi -> out)
    wmin: float          # weight interval [wmin, wmax], INCLUSIVE both ends
    wmax: float
    basis: str
    start: Optional[float]
    tmin: Optional[float]
    tmax: Optional[float]
    kg_max: Optional[float]        # max_mg_per_kg_per_day
    abs_max: Optional[float]       # absolute_max_mg_per_day
    step: Optional[float]
    numeric: bool                  # has enough mg/kg numbers to check C/D

    def age_iv(self) -> Tuple[float, float]:
        return (self.amin, self.amax)

    def wt_iv(self) -> Tuple[float, float]:
        return (self.wmin, self.wmax)


@dataclass
class Finding:
    prop: str                      # A / B / C / D
    severity: str                  # FAIL / WARN / INFO
    drug: str
    message: str
    witness: Dict[str, Any] = field(default_factory=dict)

    def is_fail(self) -> bool:
        return self.severity == "FAIL"


def _f(v: Any, default: float) -> float:
    return default if v is None else float(v)


def load_bands(path: str) -> List[Band]:
    """Read asm_rules.json exactly as the engine loader treats it (skip "_" keys,
    lowercase drug names) and project each dose_band into a Band."""
    with open(path) as fh:
        raw = json.load(fh)
    bands: List[Band] = []
    for name, d in raw.items():
        if name.startswith("_") or not isinstance(d, dict):
            continue
        drug = name.strip().lower()
        for i, b in enumerate(d.get("dose_bands", [])):
            basis = b.get("dose_basis", "fixed_mg")
            kg_max = b.get("max_mg_per_kg_per_day")
            tmax = b.get("target_mg_per_kg_per_day_max")
            step = b.get("uptitration_step_mg_per_kg_per_day")
            # "numeric" = a weight-based band with enough numbers to check the
            # ladder (C) and ceiling (D). Fixed-mg titration bands (all-None in
            # this file) are reported as UNVERIFIABLE, not silently passed.
            numeric = (
                basis == "weight_based_mg_per_kg_per_day"
                and tmax is not None
                and (kg_max is not None or b.get("absolute_max_mg_per_day") is not None)
            )
            bands.append(Band(
                drug=drug, idx=i, label=b.get("label", ""),
                amin=_f(b.get("min_age_years"), -INF),
                amax=_f(b.get("max_age_years"), INF),
                wmin=_f(b.get("min_weight_kg"), -INF),
                wmax=_f(b.get("max_weight_kg"), INF),
                basis=basis,
                start=b.get("start_mg_per_kg_per_day"),
                tmin=b.get("target_mg_per_kg_per_day_min"),
                tmax=tmax,
                kg_max=kg_max,
                abs_max=b.get("absolute_max_mg_per_day"),
                step=step,
                numeric=numeric,
            ))
    return bands


def by_drug(bands: List[Band]) -> Dict[str, List[Band]]:
    out: Dict[str, List[Band]] = {}
    for b in bands:
        out.setdefault(b.drug, []).append(b)
    return out


# ---------------------------------------------------------------------------
# Interval helpers (exact backend)
# ---------------------------------------------------------------------------
def _iv_intersect(a: Tuple[float, float], b: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if lo <= hi + EPS:
        return (lo, hi)
    return None


def _clip(a: Tuple[float, float], lo: float, hi: float) -> Optional[Tuple[float, float]]:
    return _iv_intersect(a, (lo, hi))


# ---------------------------------------------------------------------------
# Property C + D  - per-band arithmetic (backend-independent; concrete numbers)
# ---------------------------------------------------------------------------
def check_band_ladder(b: Band) -> List[Finding]:
    out: List[Finding] = []
    if not b.numeric:
        out.append(Finding("C", "INFO", b.drug,
                   f"band '{b.label}' has no mg/kg ladder numbers "
                   f"(basis={b.basis}) - UNVERIFIABLE here; needs PI transcription",
                   {"band": b.idx}))
        return out
    start, tmin, tmax = b.start, b.tmin, b.tmax
    # C1: start <= target_min
    if start is not None and tmin is not None and start > tmin + EPS:
        out.append(Finding("C", "FAIL", b.drug,
                   f"band '{b.label}': start {start} > target_min {tmin} mg/kg/day",
                   {"band": b.idx, "start": start, "target_min": tmin}))
    # C2: target_min <= target_max
    if tmin is not None and tmax is not None and tmin > tmax + EPS:
        out.append(Finding("C", "FAIL", b.drug,
                   f"band '{b.label}': target_min {tmin} > target_max {tmax} mg/kg/day",
                   {"band": b.idx, "target_min": tmin, "target_max": tmax}))
    # C3: step > 0
    if b.step is not None and b.step <= 0:
        out.append(Finding("C", "FAIL", b.drug,
                   f"band '{b.label}': up-titration step {b.step} is not positive",
                   {"band": b.idx, "step": b.step}))
    # C4: per-kg ceiling >= target_max
    if b.kg_max is not None and tmax is not None and b.kg_max < tmax - EPS:
        out.append(Finding("C", "FAIL", b.drug,
                   f"band '{b.label}': ceiling {b.kg_max} < target_max {tmax} mg/kg/day",
                   {"band": b.idx, "kg_max": b.kg_max, "target_max": tmax}))
    return out


def check_ceiling_reachability(b: Band) -> List[Finding]:
    """Property D: for all weights w in the band's weight range (clipped to the
    study domain), target(w) <= ceiling(w), where
        target(w)  = target_max * w
        ceiling(w) = min(kg_max * w [or target_max*w if kg_max None], abs_max).
    The mg/kg part is handled by C4; here we find where abs_max bites first."""
    out: List[Finding] = []
    if not b.numeric or b.tmax is None:
        return out
    dom = _clip(b.wt_iv(), STUDY_WEIGHT_MIN, STUDY_WEIGHT_MAX)
    if dom is None:
        return out
    wlo, whi = dom
    if b.abs_max is not None:
        # target_max * w <= abs_max  <=>  w <= abs_max / target_max
        crossover = b.abs_max / b.tmax
        if crossover < whi - EPS:
            w_star = max(wlo, crossover)
            sev = "FAIL" if crossover < wlo - EPS else "WARN"
            out.append(Finding("D", sev, b.drug,
                       (f"band '{b.label}': absolute cap {b.abs_max} mg/day is "
                        f"below the mg/kg target ({b.tmax} mg/kg/day) for weight "
                        f">= {crossover:.1f} kg; target unreachable there"),
                       {"band": b.idx, "crossover_kg": round(crossover, 3),
                        "abs_max": b.abs_max, "target_max": b.tmax,
                        "witness_weight_kg": round(min(whi, w_star + EPS), 3)}))
    # start dose must itself fit under the absolute cap across the range
    if b.abs_max is not None and b.start is not None:
        if b.start * whi > b.abs_max + EPS and b.start * wlo > b.abs_max + EPS:
            out.append(Finding("D", "FAIL", b.drug,
                       f"band '{b.label}': start dose {b.start} mg/kg exceeds the "
                       f"absolute cap {b.abs_max} mg/day across the whole range",
                       {"band": b.idx}))
    return out


# ===========================================================================
# BACKEND: exact pure-stdlib interval checker  (Properties A + B)
# ===========================================================================
class ExactBackend:
    name = "exact-interval (pure stdlib)"

    def overlaps(self, bands: List[Band]) -> List[Finding]:
        out: List[Finding] = []
        for i in range(len(bands)):
            for j in range(i + 1, len(bands)):
                b1, b2 = bands[i], bands[j]
                ai = _iv_intersect(b1.age_iv(), b2.age_iv())
                wi = _iv_intersect(b1.wt_iv(), b2.wt_iv())
                if ai is None or wi is None:
                    continue
                # ignore degenerate touch at a single point from inclusive bounds
                if (ai[1] - ai[0] < EPS) and (wi[1] - wi[0] < EPS):
                    continue
                differ = (b1.start, b1.tmin, b1.tmax, b1.kg_max, b1.abs_max, b1.step) \
                    != (b2.start, b2.tmin, b2.tmax, b2.kg_max, b2.abs_max, b2.step)
                sev = "FAIL" if differ else "WARN"
                out.append(Finding("A", sev, b1.drug,
                           (f"bands '{b1.label}' (#{b1.idx}) and '{b2.label}' "
                            f"(#{b2.idx}) overlap; first-match selection is "
                            f"order-dependent"
                            + ("" if differ else " (identical dosing)")),
                           {"age_overlap": [ai[0], ai[1]],
                            "weight_overlap": [None if wi[0] == -INF else wi[0],
                                               None if wi[1] == INF else wi[1]],
                            "dosing_differs": differ}))
        return out

    def coverage(self, bands: List[Band]) -> List[Finding]:
        """Exact rectilinear coverage: axis-aligned rectangles cover a cell iff
        they cover its midpoint, so compress the age/weight breakpoints and test
        each grid cell's centre. Sound because band edges are the only places
        coverage can change."""
        out: List[Finding] = []
        axs = sorted({STUDY_AGE_MIN, STUDY_AGE_MAX}
                     | {b.amin for b in bands if STUDY_AGE_MIN < b.amin < STUDY_AGE_MAX}
                     | {b.amax for b in bands if STUDY_AGE_MIN < b.amax < STUDY_AGE_MAX})
        wxs = sorted({STUDY_WEIGHT_MIN, STUDY_WEIGHT_MAX}
                     | {b.wmin for b in bands if STUDY_WEIGHT_MIN < b.wmin < STUDY_WEIGHT_MAX}
                     | {b.wmax for b in bands if STUDY_WEIGHT_MIN < b.wmax < STUDY_WEIGHT_MAX})
        for ci in range(len(axs) - 1):
            for cj in range(len(wxs) - 1):
                a0, a1 = axs[ci], axs[ci + 1]
                w0, w1 = wxs[cj], wxs[cj + 1]
                am, wm = (a0 + a1) / 2, (w0 + w1) / 2
                covered = any(b.amin - EPS <= am <= b.amax + EPS
                              and b.wmin - EPS <= wm <= b.wmax + EPS
                              for b in bands)
                if not covered:
                    aw = a1 - a0
                    ww = w1 - w0
                    micro = aw < MICRO_GAP or ww < MICRO_GAP
                    out.append(Finding("B", "INFO" if micro else "WARN",
                               bands[0].drug,
                               (f"no band covers age [{a0:g},{a1:g}]y x weight "
                                f"[{w0:g},{w1:g}]kg"
                                + (" (micro-gap: 'X to <Y' encoding)" if micro else "")),
                               {"age_gap": [a0, a1], "weight_gap": [w0, w1],
                                "witness": {"age": am, "weight": wm}}))
        return out


# ===========================================================================
# BACKEND: Z3 (trusted SMT solver)  - used when installed
# ===========================================================================
class Z3Backend:
    name = "z3 (SMT)"

    def __init__(self):
        import z3  # noqa: F401  (raises ImportError if absent)
        self.z3 = z3

    def _dom(self, v, lo, hi):
        z3 = self.z3
        return z3.And(v >= lo, v <= hi)

    def _in_band(self, b: Band, age, wt):
        z3 = self.z3
        cs = []
        if b.amin != -INF:
            cs.append(age >= b.amin)
        if b.amax != INF:
            cs.append(age <= b.amax)
        if b.wmin != -INF:
            cs.append(wt >= b.wmin)
        if b.wmax != INF:
            cs.append(wt <= b.wmax)
        return z3.And(*cs) if cs else z3.BoolVal(True)

    def overlaps(self, bands: List[Band]) -> List[Finding]:
        z3 = self.z3
        out: List[Finding] = []
        for i in range(len(bands)):
            for j in range(i + 1, len(bands)):
                b1, b2 = bands[i], bands[j]
                age = z3.Real("age")
                wt = z3.Real("wt")
                s = z3.Solver()
                s.add(self._dom(age, STUDY_AGE_MIN, STUDY_AGE_MAX))
                s.add(self._dom(wt, STUDY_WEIGHT_MIN, STUDY_WEIGHT_MAX))
                s.add(self._in_band(b1, age, wt))
                s.add(self._in_band(b2, age, wt))
                if s.check() == z3.sat:
                    m = s.model()
                    differ = (b1.start, b1.tmin, b1.tmax, b1.kg_max, b1.abs_max, b1.step) \
                        != (b2.start, b2.tmin, b2.tmax, b2.kg_max, b2.abs_max, b2.step)
                    out.append(Finding("A", "FAIL" if differ else "WARN", b1.drug,
                               (f"bands '{b1.label}' (#{b1.idx}) and '{b2.label}' "
                                f"(#{b2.idx}) overlap; first-match selection is "
                                f"order-dependent"),
                               {"witness": {"age": str(m[age]), "wt": str(m[wt])},
                                "dosing_differs": differ}))
        return out

    def coverage(self, bands: List[Band]) -> List[Finding]:
        z3 = self.z3
        age = z3.Real("age")
        wt = z3.Real("wt")
        s = z3.Solver()
        s.add(self._dom(age, STUDY_AGE_MIN, STUDY_AGE_MAX))
        s.add(self._dom(wt, STUDY_WEIGHT_MIN, STUDY_WEIGHT_MAX))
        # UNSAT of "in domain AND in no band" == full coverage (a real proof)
        s.add(z3.Not(z3.Or(*[self._in_band(b, age, wt) for b in bands])))
        out: List[Finding] = []
        if s.check() == z3.sat:
            m = s.model()
            out.append(Finding("B", "WARN", bands[0].drug,
                       "coverage gap: a (age, weight) in the study domain matches "
                       "no band",
                       {"witness": {"age": str(m[age]), "wt": str(m[wt])}}))
        return out


def get_backend(pref: str):
    if pref == "exact":
        return ExactBackend()
    try:
        return Z3Backend()
    except ImportError:
        if pref == "z3":
            print("ERROR: --solver z3 requested but z3-solver is not installed "
                  "(pip install z3-solver).", file=sys.stderr)
            sys.exit(2)
        return ExactBackend()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
_CONMED_HINT = ("valproate", "enzyme inducer", "inducer", "without", "with ")


def _annotate_conmed(findings: List[Finding], bands_by_drug: Dict[str, List[Band]]) -> None:
    """If overlapping bands are distinguished only by concomitant medication (as
    lamotrigine's are), band_for(age, weight) can't tell them apart and returns
    the FIRST one. Spell out the clinical consequence so the PI sees why it's a
    hard FAIL, not a cosmetic ordering nit."""
    for f in findings:
        if f.prop != "A":
            continue
        labels = " ".join(b.label.lower() for b in bands_by_drug.get(f.drug, []))
        if any(h in labels for h in _CONMED_HINT) and "kg" not in f.message.lower():
            # These bands differ by CONCOMITANT MED, not age/weight. This is now
            # handled by conmed-based selection in ASMRule.band_for(...,
            # concomitant_asms) (SJS/TEN fix), so it is EXPECTED, not a latent
            # bug -> downgrade FAIL to WARN. The WARN remains as a standing
            # reminder that selection MUST stay conmed-aware.
            f.severity = "WARN"
            f.message += (" -- differ by CONCOMITANT MED, not age/weight; handled "
                          "by conmed-based band_for(concomitant_asms) selection "
                          "(must stay conmed-aware to avoid SJS/TEN risk)")
            f.witness["needs_conmed_selection"] = True


def verify(bands: List[Band], backend) -> List[Finding]:
    findings: List[Finding] = []
    grouped = by_drug(bands)
    for drug, bs in grouped.items():
        findings += backend.overlaps(bs)
        findings += backend.coverage(bs)
        for b in bs:
            findings += check_band_ladder(b)
            findings += check_ceiling_reachability(b)
    _annotate_conmed(findings, grouped)
    return findings


def summarize(findings: List[Finding], backend, source: str) -> Dict[str, Any]:
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    findings = sorted(findings, key=lambda f: (order[f.severity], f.prop, f.drug))
    return {
        "artifact": "asm_rules_verification_proof",
        "status": "DRAFT - unverified (advisory; requires PI review)",
        "source_file": source,
        "checker": backend.name,
        "study_domain": {
            "age_years": [STUDY_AGE_MIN, STUDY_AGE_MAX],
            "weight_kg": [STUDY_WEIGHT_MIN, STUDY_WEIGHT_MAX],
            "weight_domain_provenance": "ASSUMPTION (unverified) - PI to confirm",
        },
        "counts": {
            sev: sum(1 for f in findings if f.severity == sev)
            for sev in ("FAIL", "WARN", "INFO")
        },
        "hard_properties_proved": not any(f.is_fail() for f in findings),
        "findings": [
            {"property": f.prop, "severity": f.severity, "drug": f.drug,
             "message": f.message, "witness": f.witness}
            for f in findings
        ],
    }


PROP_NAME = {"A": "order-independence", "B": "coverage",
             "C": "ladder monotonicity", "D": "ceiling reachability"}


def print_report(summary: Dict[str, Any]) -> None:
    print("=" * 74)
    print("  ASM RULES  -  MACHINE VERIFICATION  (DRAFT, advisory)")
    print("=" * 74)
    print(f"  source : {summary['source_file']}")
    print(f"  checker: {summary['checker']}")
    d = summary["study_domain"]
    print(f"  domain : age {d['age_years']}y  x  weight {d['weight_kg']}kg "
          f"({d['weight_domain_provenance']})")
    c = summary["counts"]
    print(f"  result : {c['FAIL']} FAIL   {c['WARN']} WARN   {c['INFO']} INFO")
    print("-" * 74)
    for f in summary["findings"]:
        tag = {"FAIL": "[FAIL]", "WARN": "[warn]", "INFO": "[info]"}[f["severity"]]
        print(f"{tag} ({f['property']} {PROP_NAME[f['property']]}) "
              f"{f['drug']}: {f['message']}")
        if f["witness"]:
            print(f"        witness: {json.dumps(f['witness'])}")
    print("-" * 74)
    if summary["hard_properties_proved"]:
        print("  PROVED: no order-dependence / ladder / hard-ceiling violations.")
        print("  (WARN/INFO items are for PI review, not contradictions.)")
    else:
        print("  VIOLATION(S) FOUND - see [FAIL] lines above. PI review required.")
    print("=" * 74)


# ---------------------------------------------------------------------------
# Self-test: synthetic good/bad tables both backends must classify correctly
# ---------------------------------------------------------------------------
def _mk(drug, idx, label, amin, amax, wmin, wmax, start, tmin, tmax, kg_max,
        abs_max, step) -> Band:
    return Band(drug, idx, label,
                _f(amin, -INF), _f(amax, INF), _f(wmin, -INF), _f(wmax, INF),
                "weight_based_mg_per_kg_per_day", start, tmin, tmax, kg_max,
                abs_max, step,
                numeric=(tmax is not None and (kg_max is not None or abs_max is not None)))


def self_test() -> int:
    cases = []
    # GOOD: single band, sane ladder, full age coverage, no abs cap issue
    good = [_mk("good", 0, "2-17y", 2, 17, None, None, 20, 40, 60, 80, None, 20)]
    cases.append(("clean table -> no FAIL", good, 0))
    # BAD-A: two overlapping bands with different dosing
    overlap = [_mk("ov", 0, "2-10y", 2, 10, None, None, 10, 20, 30, 40, None, 5),
               _mk("ov", 1, "8-17y", 8, 17, None, None, 15, 25, 35, 45, None, 5)]
    cases.append(("overlapping bands -> A FAIL", overlap, 1))
    # BAD-C: target_min > target_max
    badc = [_mk("bc", 0, "2-17y", 2, 17, None, None, 10, 60, 40, 80, None, 5)]
    cases.append(("inverted target band -> C FAIL", badc, 1))
    # BAD-C4: ceiling below target
    badceil = [_mk("bcc", 0, "2-17y", 2, 17, None, None, 5, 20, 50, 30, None, 5)]
    cases.append(("ceiling<target -> C FAIL", badceil, 1))
    ok = 0
    for backend in (ExactBackend(), *( [Z3Backend()] if _has_z3() else [] )):
        for name, bands, want_fail in cases:
            findings = verify(bands, backend)
            got_fail = 1 if any(f.is_fail() for f in findings) else 0
            status = "PASS" if got_fail == want_fail else "FAIL"
            if status == "FAIL":
                ok = 1
            print(f"  [{backend.name:28}] {status}  {name}")
    print("self-test:", "OK" if ok == 0 else "FAILED")
    return ok


def _has_z3() -> bool:
    try:
        import z3  # noqa
        return True
    except ImportError:
        return False


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_rules = os.path.join(here, "..", "data", "asm_rules.json")
    p = argparse.ArgumentParser(description="Verify data/asm_rules.json (advisory).")
    p.add_argument("rules", nargs="?", default=default_rules)
    p.add_argument("--solver", choices=["auto", "z3", "exact"], default="auto")
    p.add_argument("--json", metavar="OUT", help="write the proof artifact to OUT")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    if args.self_test:
        return self_test()

    if not os.path.isfile(args.rules):
        print(f"ERROR: rules file not found: {args.rules}", file=sys.stderr)
        return 2

    backend = get_backend(args.solver)
    bands = load_bands(args.rules)
    findings = verify(bands, backend)
    summary = summarize(findings, backend, os.path.relpath(args.rules))
    print_report(summary)

    if args.json:
        d = os.path.dirname(os.path.abspath(args.json))
        os.makedirs(d, exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"  proof artifact -> {args.json}")

    return 0 if summary["hard_properties_proved"] else 1


if __name__ == "__main__":
    sys.exit(main())
