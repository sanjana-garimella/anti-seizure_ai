"""
titration_config.py
--------------------
Loads the clinician-supplied titration thresholds (data/titration_thresholds.json,
falling back to the .template.json shape). These are the numeric thresholds from
Dr. Barbour's review (seizure frequency/duration -> increase faster; time-on-med
-> switch when not helpful; follow-up cadence; lamotrigine small-rash workup).

HARD RULE (spec §11.1): NO clinical numbers are defined in this module. If the
file is absent or a field is null, the corresponding rule simply DOES NOT FIRE
(the engine defers), rather than guessing a threshold.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DEFAULT = os.path.join(_DATA, "titration_thresholds.json")
_TEMPLATE = os.path.join(_DATA, "titration_thresholds.template.json")
_NO_SEIZURES_TABLE = os.path.join(_DATA, "no_seizures_decision_table.json")
_BREAKTHROUGH_TABLES = os.path.join(_DATA, "breakthrough_decision_tables.json")
_BREAKTHROUGH_URGENCY = os.path.join(_DATA, "breakthrough_urgency_thresholds.json")


def _clean(d: Any) -> Any:
    """Drop documentation keys (starting with '_') recursively."""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if not k.startswith("_")}
    return d


@dataclass
class Thresholds:
    """Typed, null-tolerant access to the titration thresholds."""
    raw: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    source: str = ""

    def section(self, name: str) -> Dict[str, Any]:
        v = self.raw.get(name)
        return v if isinstance(v, dict) else {}

    def get(self, section: str, key: str) -> Optional[Any]:
        return self.section(section).get(key)

    @property
    def faster_cadence_interval_days(self) -> Optional[int]:
        return self.raw.get("faster_cadence_interval_days")


def load_thresholds(path: Optional[str] = None) -> Thresholds:
    p = path or (_DEFAULT if os.path.isfile(_DEFAULT) else _TEMPLATE)
    if not os.path.isfile(p):
        return Thresholds()
    try:
        with open(p) as fh:
            raw = _clean(json.load(fh))
    except (json.JSONDecodeError, OSError):
        # A corrupt/half-written file must DEGRADE (defer), not crash the engine:
        # same empty result as the missing-file case above.
        return Thresholds()
    return Thresholds(raw=raw, verified=bool(raw.get("verified", False)),
                      source=raw.get("source", ""))


# Module-level singleton (lazy) so callers can ignore plumbing.
_CACHE: Optional[Thresholds] = None


def default_thresholds() -> Thresholds:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_thresholds()
    return _CACHE


def load_no_seizures_table(path: Optional[str] = None) -> Dict[str, Any]:
    """The seizure-free 6-level action grid (Barbour 2026-06-25), or {} if the
    file is absent - in which case the table-driven branch simply does not fire
    and the engine falls back to its legacy 3-level seizure-free logic.

    Same HARD RULE as the thresholds: no clinical values are defined here; the
    file is the single (clinician-sourced, verified:false) source of truth.
    """
    p = path or _NO_SEIZURES_TABLE
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as fh:
            return _clean(json.load(fh))
    except (json.JSONDecodeError, OSError):
        # Malformed file -> same empty result as missing-file: the table-driven
        # branch simply does not fire (engine defers to legacy logic).
        return {}


_NS_CACHE: Optional[Dict[str, Any]] = None


def default_no_seizures_table() -> Dict[str, Any]:
    global _NS_CACHE
    if _NS_CACHE is None:
        _NS_CACHE = load_no_seizures_table()
    return _NS_CACHE


def load_breakthrough_tables(path: Optional[str] = None) -> Dict[str, Any]:
    """The breakthrough decision tables (non_urgent/urgent/emergent x phase x
    side-effect level), Barbour 2026-06-25. {} if absent -> the table-driven
    breakthrough branch does not fire and the engine uses its legacy rules."""
    p = path or _BREAKTHROUGH_TABLES
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as fh:
            return _clean(json.load(fh))
    except (json.JSONDecodeError, OSError):
        return {}


_BT_CACHE: Optional[Dict[str, Any]] = None


def default_breakthrough_tables() -> Dict[str, Any]:
    global _BT_CACHE
    if _BT_CACHE is None:
        _BT_CACHE = load_breakthrough_tables()
    return _BT_CACHE


def load_breakthrough_urgency(path: Optional[str] = None) -> Dict[str, Any]:
    """Per-seizure-type urgency thresholds (duration seconds + frequency), Barbour
    2026-06-25. {} if absent -> the engine falls back to its coarse tier mapping."""
    p = path or _BREAKTHROUGH_URGENCY
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as fh:
            return _clean(json.load(fh))
    except (json.JSONDecodeError, OSError):
        return {}


_BTU_CACHE: Optional[Dict[str, Any]] = None


def default_breakthrough_urgency() -> Dict[str, Any]:
    global _BTU_CACHE
    if _BTU_CACHE is None:
        _BTU_CACHE = load_breakthrough_urgency()
    return _BTU_CACHE


def reset_caches() -> None:
    """Clear the module-global config caches so the next default_* call reloads
    from disk. The documented PI workflow is to edit data/*.json; without this
    the process would keep serving the values loaded at first access until a
    restart. The clinic app (and tests) can call this after a data edit.
    Default behavior is unchanged (caches still lazy-load on first access)."""
    global _CACHE, _NS_CACHE, _BT_CACHE, _BTU_CACHE
    _CACHE = None
    _NS_CACHE = None
    _BT_CACHE = None
    _BTU_CACHE = None
    # Also clear the dose-ladder cache (data/dose_titration_ladder.json) so a
    # single reset reloads every clinician-edited data file. Imported lazily to
    # avoid a circular import at module load.
    try:
        from . import dose_ladder
        dose_ladder.reset_cache()
    except Exception:
        pass
