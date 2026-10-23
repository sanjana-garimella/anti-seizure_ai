"""
normalize_duration_frequency.py
--------------------------------
ADVISORY free-text normalizer for seizure DURATION and FREQUENCY.

Maps caregiver phrasing ("a couple minutes", "30-ish seconds", "few times a
day", "once a week") onto the engine's canonical duration buckets and
frequency count/unit or buckets, so the clinician-review layer can offer to
fill fields that would otherwise be missing (the engine DEFERS - rules R8/R12 -
when duration/frequency are absent).

HARD RULES (clinical-safety.md):
  * Output is a SUGGESTION for clinician review; it is NEVER written into the
    IntervalSurvey or the decision engine.
  * Pure stdlib, deterministic (no ML, no network).
  * Ambiguity -> lower confidence or NO finding (defer), never a guess.
  * Ranges resolve to the UPPER bound (never under-triage duration).

Canonical vocab mirrors engine_core.snapshot:
  duration buckets: under_1min (<60s) | 1_3min (60-180s) | over_3min (180-300s)
                    | over_5min (>=300s)
  frequency: (count, unit in per_day|per_week|per_month) OR bucket
             daily | weekly | monthly | less_than_monthly
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# word-number map (lower-case), incl. vague quantifiers (conservative estimates)
_WORDNUM = {
    "a": 1, "an": 1, "one": 1, "once": 1, "two": 2, "twice": 2, "couple": 2,
    "three": 3, "thrice": 3, "few": 3, "several": 4, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "half": 0.5,
}
_SEC = r"(?:s|sec|secs|second|seconds)"
_MIN = r"(?:m|min|mins|minute|minutes)"
_NUM = r"(\d+(?:\.\d+)?)"
_WORDS = r"(a|an|one|two|couple|three|few|several|four|five|half)"


@dataclass
class DFFinding:
    field: str                 # "seizure_duration_bucket" | "seizure_frequency"
    suggested_value: str
    confidence: float
    evidence: str
    seconds_estimate: Optional[float] = None   # duration only
    count: Optional[float] = None              # frequency only
    unit: Optional[str] = None                 # frequency only
    note: str = "SUGGESTION for clinician review - not used by the decision engine."


def _seconds_to_bucket(sec: float) -> str:
    if sec < 60:
        return "under_1min"
    if sec < 180:
        return "1_3min"
    if sec < 300:
        return "over_3min"
    return "over_5min"


def _val(tok: str) -> Optional[float]:
    tok = tok.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return float(tok)
    return _WORDNUM.get(tok)


# ------------------------------ DURATION ------------------------------------
# ordered patterns: (regex, kind) — kind tells how to compute seconds
_DUR_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(rf"(?:for\s+)?(?:about|around|~|approximately)?\s*{_NUM}\s*(?:to|-|–|or)\s*{_NUM}\s*{_MIN}\b", re.I), "range_min"),
    (re.compile(rf"(?:for\s+)?(?:about|around|~|approximately)?\s*{_NUM}\s*(?:to|-|–|or)\s*{_NUM}\s*{_SEC}\b", re.I), "range_sec"),
    (re.compile(rf"(?:for\s+)?(?:about|around|~|approximately|roughly)?\s*{_NUM}\s*(?:-|\s)?(?:ish)?\s*{_MIN}\b", re.I), "min"),
    (re.compile(rf"(?:for\s+)?(?:about|around|~|approximately|roughly)?\s*{_NUM}\s*(?:-|\s)?(?:ish)?\s*{_SEC}\b", re.I), "sec"),
    (re.compile(rf"\bhalf\s+a\s+{_MIN}\b", re.I), "half_min"),
    (re.compile(rf"\b{_WORDS}\s+{_MIN}\b", re.I), "word_min"),
    (re.compile(rf"\b{_WORDS}\s+{_SEC}\b", re.I), "word_sec"),
    (re.compile(rf"\b(?:a\s+)?few\s+{_SEC}\b", re.I), "few_sec"),
]


def parse_duration(text: str) -> List[DFFinding]:
    if not text:
        return []
    out: List[DFFinding] = []
    seen_spans = []
    for pat, kind in _DUR_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if any(not (span[1] <= s or span[0] >= e) for s, e in seen_spans):
                continue  # overlaps an already-matched (higher-priority) phrase
            sec, conf = _duration_seconds(m, kind)
            if sec is None:
                continue
            seen_spans.append(span)
            out.append(DFFinding(
                field="seizure_duration_bucket",
                suggested_value=_seconds_to_bucket(sec),
                confidence=conf, evidence=m.group(0).strip(),
                seconds_estimate=sec))
    return out


def _duration_seconds(m, kind) -> Tuple[Optional[float], float]:
    try:
        if kind == "range_min":
            hi = _val(m.group(2)) or _val(m.group(1))
            return (hi * 60.0, 0.85) if hi else (None, 0)
        if kind == "range_sec":
            hi = _val(m.group(2)) or _val(m.group(1))
            return (hi, 0.85) if hi else (None, 0)
        if kind == "min":
            v = _val(m.group(1))
            return (v * 60.0, 0.9) if v is not None else (None, 0)
        if kind == "sec":
            v = _val(m.group(1))
            return (v, 0.9) if v is not None else (None, 0)
        if kind == "half_min":
            return (30.0, 0.8)
        if kind == "word_min":
            v = _val(m.group(1))
            return (v * 60.0, 0.6) if v is not None else (None, 0)
        if kind == "word_sec":
            v = _val(m.group(1))
            return (v, 0.6) if v is not None else (None, 0)
        if kind == "few_sec":
            return (5.0, 0.5)
    except (IndexError, TypeError):
        return (None, 0)
    return (None, 0)


# ------------------------------ FREQUENCY -----------------------------------
_PERIOD = {"day": "per_day", "daily": "per_day", "week": "per_week",
           "weekly": "per_week", "month": "per_month", "monthly": "per_month"}
_FREQ_PATTERNS = [
    # "3 times a day", "twice per week", "a couple times a day"
    re.compile(rf"\b(?:{_NUM}|{_WORDS})\s*(?:times|x)?\s*(?:a|per|/|every)\s*(day|week|month)\b", re.I),
    # "once/twice a day" (word count without 'times')
    re.compile(r"\b(once|twice|thrice)\s*(?:a|per|every)\s*(day|week|month)\b", re.I),
]
_BUCKET_WORDS = [
    (re.compile(r"\b(every ?day|daily|each day)\b", re.I), "daily"),
    (re.compile(r"\b(every ?week|weekly|each week)\b", re.I), "weekly"),
    (re.compile(r"\b(every ?month|monthly|each month)\b", re.I), "monthly"),
    (re.compile(r"\b(less than (?:once|1|one) a month|rarely|hardly ever)\b", re.I), "less_than_monthly"),
]


def parse_frequency(text: str) -> List[DFFinding]:
    if not text:
        return []
    out: List[DFFinding] = []
    seen = []

    def _add(span, f):
        if any(not (span[1] <= s or span[0] >= e) for s, e in seen):
            return
        seen.append(span)
        out.append(f)

    for pat in _FREQ_PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            # count token = first non-empty capture before the period (numeric
            # OR word); period = last capture.
            tokens = [x for x in g[:-1] if x]
            cnt = _val(tokens[0]) if tokens else None
            period = (g[-1] or "").lower()
            unit = _PERIOD.get(period)
            if cnt is None or unit is None:
                continue
            _add((m.start(), m.end()), DFFinding(
                field="seizure_frequency", suggested_value=f"{cnt:g} {unit}",
                confidence=0.85, evidence=m.group(0).strip(), count=cnt, unit=unit))
    for pat, bucket in _BUCKET_WORDS:
        for m in pat.finditer(text):
            _add((m.start(), m.end()), DFFinding(
                field="seizure_frequency", suggested_value=bucket,
                confidence=0.7, evidence=m.group(0).strip()))
    return out


def normalize(text: str) -> List[DFFinding]:
    """All duration + frequency findings from a free-text snippet (advisory)."""
    return parse_duration(text) + parse_frequency(text)
