#!/usr/bin/env python3
"""
verify_ladder.py  -  machine verification of the dose-titration LADDER + thresholds
===================================================================================

verify_asm_rules.py checks the age/weight dose BANDS. This companion checks the
files where the explicit dose NUMBERS live:

  * data/dose_titration_ladder.json  - the per-drug/band rungs transcribed
    VERBATIM from Dr. Barbour's final_doses_titration_v6.xlsx. This is where a
    mis-transcribed or non-monotone rung would actually change a dose, so it is
    the highest-value table to prove.
  * data/titration_thresholds.json   - duration ranks / frequency buckets /
    switch-timing thresholds; we check their internal ordering is consistent.

PROPERTIES (ladder)
  L. RUNG MONOTONICITY - the full climb
        initial -> pre_maintenance_increments -> up_increments -> high_increments
     is strictly increasing (a ladder you can actually climb; no flat/backward
     rung), and each tier list is itself strictly increasing.
  M. TIER / RANGE CONSISTENCY - initial <= min_maintenance; pre-maintenance
     rungs < min_maintenance; up rungs within [min_maintenance, effective_max];
     high rungs > effective_max; and maintenance_range "lo-hi" agrees with
     min_maintenance (=lo) and effective_max (<=hi). Caps are positive.
  X. CROSS-TABLE - the ladder's max_absolute_mg_per_day agrees with the
     absolute_max_mg_per_day in asm_rules.json for the same drug (INFO if the
     two clinician tables disagree - a transcription check, not a contradiction).

PROPERTIES (thresholds)
  T. ORDERING CONSISTENCY - duration ranks lie in the documented 1..3 scale, the
     "continue if short" rank < the "escalate" rank < the whole-body danger
     rank, and "worse -> switch" fires no later than "unchanged -> switch".

SAFETY / SCOPE (clinical-safety.md, NON_NEGOTIABLE_RULES.md)
  Build-time only; no engine_core import at runtime; reads JSON offline; invents
  no clinical values; advisory output for PI review (DRAFT/unverified). Findings
  reuse the Finding type + severities from verify_asm_rules.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from verification.verify_asm_rules import Finding  # noqa: E402

EPS = 1e-9
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "..", "data")


def _val(x: Any) -> Optional[float]:
    """Pull a numeric value from a {'value': n} rung or a bare number."""
    if isinstance(x, dict):
        v = x.get("value")
        return float(v) if v is not None else None
    if isinstance(x, (int, float)):
        return float(x)
    return None


def _vals(lst: Any) -> List[float]:
    out = []
    for x in lst or []:
        v = _val(x)
        if v is not None:
            out.append(v)
    return out


def _parse_range(s: Any) -> Optional[Tuple[float, float]]:
    """Parse 'lo-hi ...' (any dash) -> (lo, hi). Strips comma thousands
    separators ('800-1,200') so the high bound isn't truncated to '1'."""
    if not isinstance(s, str):
        return None
    t = s.replace("–", "-").replace("—", "-").replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*-\s*(\d+\.?\d*)", t)
    return (float(m.group(1)), float(m.group(2))) if m else None


# ---------------------------------------------------------------------------
# Ladder checks
# ---------------------------------------------------------------------------
def check_ladder(path: str, asm_rules_path: Optional[str] = None) -> List[Finding]:
    with open(path) as fh:
        d = json.load(fh)
    findings: List[Finding] = []
    asm_abs = _asm_absolute_caps(asm_rules_path) if asm_rules_path else {}

    for drug, bands in (d.get("bands_by_drug") or {}).items():
        cap_seen = None
        for i, b in enumerate(bands):
            label = b.get("band_label", f"band {i}")
            init = _val(b.get("initial_dose"))
            minm = _val(b.get("min_maintenance_dose"))
            effmax = _val(b.get("effective_max"))
            pre = _vals(b.get("pre_maintenance_increments"))
            up = _vals(b.get("up_increments"))
            high = _vals(b.get("high_increments_often_not_tolerated"))

            # ---- L: each tier strictly increasing ----
            for tier_name, tier in (("pre", pre), ("up", up), ("high", high)):
                for a, c in zip(tier, tier[1:]):
                    if c <= a + EPS:
                        findings.append(Finding("L", "FAIL", drug,
                            f"'{label}' {tier_name}_increments not strictly "
                            f"increasing: {a} -> {c}", {"band": i}))

            # ---- L: full climb monotone (rung tiers only; initial_dose is the
            # first rung and is checked separately so it isn't double-counted) --
            climb = pre + up + high
            for a, c in zip(climb, climb[1:]):
                if c <= a + EPS:
                    findings.append(Finding("L", "FAIL", drug,
                        f"'{label}' rung climb not strictly increasing: {a} -> {c}",
                        {"band": i, "climb": climb}))
                    break
            if init is not None and climb and init > climb[0] + EPS:
                findings.append(Finding("L", "WARN", drug,
                    f"'{label}' initial dose {init} is above the first rung "
                    f"{climb[0]}", {"band": i}))

            # ---- M: tier/range consistency ----
            # initial > min_maintenance is VALID for start-at-goal fixed-mg bands
            # (e.g. brivaracetam >=50 kg), so it's informational, not a failure.
            if init is not None and minm is not None and init > minm + EPS:
                findings.append(Finding("M", "INFO", drug,
                    f"'{label}' initial {init} > min_maintenance {minm} "
                    f"(starts at/above goal)", {"band": i}))
            if minm is not None and pre and max(pre) > minm + EPS:
                findings.append(Finding("M", "WARN", drug,
                    f"'{label}' a pre-maintenance rung {max(pre)} >= "
                    f"min_maintenance {minm}", {"band": i}))
            if effmax is not None and up and max(up) > effmax + EPS:
                findings.append(Finding("M", "WARN", drug,
                    f"'{label}' an up-increment {max(up)} exceeds effective_max "
                    f"{effmax}", {"band": i}))
            if effmax is not None and high and min(high) <= effmax + EPS:
                findings.append(Finding("M", "WARN", drug,
                    f"'{label}' a high-increment {min(high)} is <= effective_max "
                    f"{effmax}", {"band": i}))
            rng = _parse_range(b.get("maintenance_range"))
            if rng:
                lo, hi = rng
                if minm is not None and abs(minm - lo) > EPS:
                    findings.append(Finding("M", "WARN", drug,
                        f"'{label}' min_maintenance {minm} != maintenance_range "
                        f"low {lo}", {"band": i}))
                if effmax is not None and effmax > hi + EPS:
                    findings.append(Finding("M", "FAIL", drug,
                        f"'{label}' effective_max {effmax} > maintenance_range "
                        f"high {hi}", {"band": i}))
            for capname in ("initial_dose_cap_mg_per_day", "max_absolute_mg_per_day"):
                cap = b.get(capname)
                if cap is not None and cap <= 0:
                    findings.append(Finding("M", "FAIL", drug,
                        f"'{label}' {capname} {cap} not positive", {"band": i}))

            # ---- X: cross-table absolute cap vs asm_rules ----
            lad_abs = b.get("max_absolute_mg_per_day")
            if lad_abs is not None and drug in asm_abs and asm_abs[drug] is not None:
                if abs(lad_abs - asm_abs[drug]) > EPS:
                    findings.append(Finding("X", "INFO", drug,
                        f"'{label}' ladder max_absolute {lad_abs} != asm_rules "
                        f"absolute_max {asm_abs[drug]} (clinician tables disagree "
                        f"- transcription check)", {"band": i}))
    return findings


def _asm_absolute_caps(path: str) -> Dict[str, Optional[float]]:
    """Smallest absolute_max_mg_per_day per drug in asm_rules.json (for X-check)."""
    out: Dict[str, Optional[float]] = {}
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return out
    for name, dd in raw.items():
        if name.startswith("_") or not isinstance(dd, dict):
            continue
        caps = [bb.get("absolute_max_mg_per_day") for bb in dd.get("dose_bands", [])
                if bb.get("absolute_max_mg_per_day") is not None]
        out[name.strip().lower()] = min(caps) if caps else None
    return out


# ---------------------------------------------------------------------------
# Threshold checks
# ---------------------------------------------------------------------------
def check_thresholds(path: str) -> List[Finding]:
    with open(path) as fh:
        d = json.load(fh)
    f: List[Finding] = []
    drug = "(thresholds)"

    def rank(section, key):
        return (d.get(section) or {}).get(key)

    danger = rank("whole_body_danger", "min_duration_rank")
    esc_a = rank("escalate_faster", "rule_a_min_duration_rank")
    esc_b = rank("escalate_faster", "rule_b_min_duration_rank")
    cont_all = rank("continue_ok", "all_short_max_duration_rank")
    cont_lt = rank("continue_ok", "still_titrating_max_duration_rank_lt")

    for name, r in [("whole_body danger", danger), ("escalate_a", esc_a),
                    ("escalate_b", esc_b), ("continue all_short", cont_all)]:
        if r is not None and not (1 <= r <= 3):
            f.append(Finding("T", "FAIL", drug,
                f"{name} duration rank {r} outside documented 1..3 scale"))

    if cont_all is not None and esc_a is not None and cont_all >= esc_a:
        f.append(Finding("T", "WARN", drug,
            f"continue-if-short rank {cont_all} >= escalate rank {esc_a} "
            f"(short seizures would also trigger 'escalate faster')"))
    if esc_a is not None and danger is not None and esc_a > danger:
        f.append(Finding("T", "WARN", drug,
            f"escalate rank {esc_a} > whole-body danger rank {danger}"))
    if cont_all is not None and cont_lt is not None and cont_all >= cont_lt + 1:
        f.append(Finding("T", "WARN", drug,
            f"continue_ok all_short rank {cont_all} inconsistent with "
            f"still_titrating_max_rank_lt {cont_lt}"))

    unchanged = rank("switch_when_not_helpful", "unchanged_min_weeks")
    worse = rank("switch_when_not_helpful", "worse_min_weeks")
    if unchanged is not None and worse is not None and worse > unchanged:
        f.append(Finding("T", "FAIL", drug,
            f"'worse -> switch' at {worse}wk fires LATER than 'unchanged -> "
            f"switch' at {unchanged}wk (worse should switch no later)"))

    fu = d.get("followup") or {}
    for k, v in fu.items():
        if isinstance(v, (int, float)) and v <= 0:
            f.append(Finding("T", "FAIL", drug, f"followup.{k} {v} not positive"))
    return f


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
PROP_NAME = {"L": "rung monotonicity", "M": "tier/range consistency",
             "X": "cross-table", "T": "threshold ordering"}


def verify_all(ladder_path: str, thresholds_path: str,
               asm_rules_path: Optional[str]) -> List[Finding]:
    findings: List[Finding] = []
    if os.path.isfile(ladder_path):
        findings += check_ladder(ladder_path, asm_rules_path)
    if os.path.isfile(thresholds_path):
        findings += check_thresholds(thresholds_path)
    return findings


def summarize(findings: List[Finding]) -> dict:
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    findings = sorted(findings, key=lambda x: (order[x.severity], x.prop, x.drug))
    return {
        "artifact": "ladder_thresholds_verification_proof",
        "status": "DRAFT - unverified (advisory; requires PI review)",
        "counts": {s: sum(1 for x in findings if x.severity == s)
                   for s in ("FAIL", "WARN", "INFO")},
        "hard_properties_proved": not any(x.is_fail() for x in findings),
        "findings": [{"property": x.prop, "severity": x.severity, "drug": x.drug,
                      "message": x.message, "witness": x.witness} for x in findings],
    }


def print_report(summary: dict) -> None:
    print("=" * 74)
    print("  DOSE LADDER + THRESHOLDS  -  MACHINE VERIFICATION  (DRAFT, advisory)")
    print("=" * 74)
    c = summary["counts"]
    print(f"  result : {c['FAIL']} FAIL   {c['WARN']} WARN   {c['INFO']} INFO")
    print("-" * 74)
    for x in summary["findings"]:
        tag = {"FAIL": "[FAIL]", "WARN": "[warn]", "INFO": "[info]"}[x["severity"]]
        print(f"{tag} ({x['property']} {PROP_NAME.get(x['property'], x['property'])}) "
              f"{x['drug']}: {x['message']}")
    print("-" * 74)
    print("  PROVED: no ladder/threshold contradictions." if summary["hard_properties_proved"]
          else "  VIOLATION(S) FOUND - PI review required.")
    print("=" * 74)


def main() -> int:
    p = argparse.ArgumentParser(description="Verify dose ladder + thresholds (advisory).")
    p.add_argument("--ladder", default=os.path.join(_DATA, "dose_titration_ladder.json"))
    p.add_argument("--thresholds", default=os.path.join(_DATA, "titration_thresholds.json"))
    p.add_argument("--asm-rules", default=os.path.join(_DATA, "asm_rules.json"))
    p.add_argument("--json", metavar="OUT")
    args = p.parse_args()
    findings = verify_all(args.ladder, args.thresholds, args.asm_rules)
    summary = summarize(findings)
    print_report(summary)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"  proof artifact -> {args.json}")
    return 0 if summary["hard_properties_proved"] else 1


if __name__ == "__main__":
    sys.exit(main())
