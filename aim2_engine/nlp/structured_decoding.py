"""
nlp/structured_decoding.py
--------------------------
Schema-valid output enforcement for the point-A extractor (structurer.py).

If/when a GENERATIVE model replaces the lexicon baseline as a FreeTextStructurer,
its output must still be schema-valid: a known `field`, and for closed fields a
value from that field's allowed set (drug/test fields must name a known study
drug / test). This module:

  1. Derives the extraction SCHEMA from structurer._LEXICON — the single source
     of truth — so the constraint can never drift from the baseline.
  2. Emits an Outlines/XGrammar-style REGEX (and a GBNF sketch) that constrains
     a decoder to emit only schema-valid JSON findings. This is the artifact you
     hand to Outlines / XGrammar / llama.cpp grammars.
  3. Provides post-hoc validate()/coerce() so ANY extractor's output (even one
     you can't constrain at decode time) is checked before it reaches the
     clinician-review layer, dropping or flagging invalid items.
  4. SchemaConstrainedStructurer wraps any FreeTextStructurer and enforces the
     schema on its findings.

IMPORTANT CAVEAT (do NOT overclaim):
  Constrained decoding enforces FORMAT ONLY. It is NOT a safety control. A
  schema-valid finding can still be clinically wrong, and constrained decoding
  has a documented attack surface plus a reasoning "alignment tax". It exists so
  malformed output can't crash or silently corrupt the review layer — not to
  make the extractor trustworthy. As always, findings are ADVISORY and never
  enter engine_core/ (clinical-safety.md, spec §11).

Pure stdlib; the Outlines integration is optional and lazily imported.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional, Tuple

from .interfaces import FreeTextFinding
from .structurer import _LEXICON, _DRUG_NAMES, _TEST_PHRASES

# Fields whose value is an OPEN vocabulary (a specific name), constrained to a
# known list rather than a fixed enum.
_OPEN_FIELDS = {
    "drug_mention": [n.lower() for n in _DRUG_NAMES],
    "test_mention": [p.lower() for p in _TEST_PHRASES],
}


def _closed_values(field: str) -> List[str]:
    """Allowed enum values for a closed field, from the lexicon's suggested
    values (value=None marks an open field, handled separately)."""
    vals = []
    for _pat, val in _LEXICON.get(field, []):
        if val is not None and val not in vals:
            vals.append(val)
    return vals


@dataclass
class FindingSchema:
    """The closed-world schema for advisory findings."""
    fields: List[str]
    closed_values: Dict[str, List[str]]     # field -> allowed enum values
    open_values: Dict[str, List[str]]       # field -> allowed vocabulary

    @classmethod
    def from_lexicon(cls) -> "FindingSchema":
        fields = list(_LEXICON.keys())
        closed = {f: _closed_values(f) for f in fields if f not in _OPEN_FIELDS}
        return cls(fields=fields, closed_values=closed, open_values=dict(_OPEN_FIELDS))

    # ---- membership -------------------------------------------------------
    def allowed_values(self, field: str) -> List[str]:
        if field in self.open_values:
            return self.open_values[field]
        return self.closed_values.get(field, [])

    def is_valid(self, field: str, value: str) -> bool:
        if field not in self.fields:
            return False
        return value.lower() in [v.lower() for v in self.allowed_values(field)]

    # ---- constraint artifacts --------------------------------------------
    def value_regex(self, field: str) -> str:
        vals = sorted(self.allowed_values(field), key=len, reverse=True)
        return "(?:" + "|".join(re.escape(v) for v in vals) + ")"

    def finding_object_regex(self) -> str:
        """Regex matching ONE JSON finding object, where value is constrained to
        the field it is paired with (a per-field alternation)."""
        pairs = []
        for f in self.fields:
            pairs.append(
                r'\{"field":\s*"' + re.escape(f) + r'",\s*"value":\s*"'
                + self.value_regex(f) + r'"\}'
            )
        return "(?:" + "|".join(pairs) + ")"

    def array_regex(self) -> str:
        """Regex for a JSON array of 0+ schema-valid findings — the string you
        pass to Outlines' regex processor / XGrammar."""
        obj = self.finding_object_regex()
        return r"\[\s*(?:" + obj + r"(?:\s*,\s*" + obj + r")*)?\s*\]"

    def gbnf_sketch(self) -> str:
        """A GBNF grammar sketch (llama.cpp / XGrammar) — one rule per field."""
        lines = ["root ::= \"[\" (finding (\",\" finding)*)? \"]\"",
                 "finding ::= " + " | ".join(f"f_{f}" for f in self.fields)]
        for f in self.fields:
            alts = " | ".join('"' + v.replace('"', '\\"') + '"'
                              for v in self.allowed_values(f))
            lines.append(f'f_{f} ::= "{{\\"field\\":\\"{f}\\",\\"value\\":" '
                         f'( {alts} ) "}}"')
        return "\n".join(lines)

    # ---- optional Outlines hook ------------------------------------------
    def outlines_processor(self, tokenizer):
        """Build an Outlines regex logits-processor if `outlines` is installed;
        else raise a clear error. FORMAT-only guard (see module caveat)."""
        try:
            from outlines.processors import RegexLogitsProcessor  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "outlines not installed; use array_regex()/gbnf_sketch() with "
                "your decoder, or validate() post-hoc. (%s)" % e)
        return RegexLogitsProcessor(self.array_regex(), tokenizer)


# ---------------------------------------------------------------------------
# Post-hoc validation / coercion for any extractor output
# ---------------------------------------------------------------------------
@dataclass
class ValidationReport:
    kept: List[FreeTextFinding] = dc_field(default_factory=list)
    dropped: List[Tuple[FreeTextFinding, str]] = dc_field(default_factory=list)

    @property
    def n_dropped(self) -> int:
        return len(self.dropped)


def validate(findings: List[FreeTextFinding],
             schema: Optional[FindingSchema] = None) -> ValidationReport:
    """Keep only schema-valid findings; report the rest with a reason. Nothing is
    silently mutated — dropped items are returned for logging/human review."""
    sch = schema or FindingSchema.from_lexicon()
    rep = ValidationReport()
    for f in findings:
        if f.field not in sch.fields:
            rep.dropped.append((f, f"unknown field '{f.field}'"))
        elif not sch.is_valid(f.field, str(f.suggested_value)):
            rep.dropped.append((f, f"value '{f.suggested_value}' not allowed for "
                                   f"field '{f.field}'"))
        else:
            rep.kept.append(f)
    return rep


def parse_and_validate(raw_json: str,
                       schema: Optional[FindingSchema] = None
                       ) -> ValidationReport:
    """Parse a model's raw JSON array string and validate it. Malformed JSON ->
    everything dropped (never a partial/garbage finding into the review layer)."""
    sch = schema or FindingSchema.from_lexicon()
    try:
        data = json.loads(raw_json)
        assert isinstance(data, list)
    except Exception as e:
        rep = ValidationReport()
        rep.dropped.append((FreeTextFinding("<parse>", raw_json[:40], 0.0, ""),
                            f"invalid JSON array: {e}"))
        return rep
    findings = []
    for d in data:
        if isinstance(d, dict) and "field" in d and ("value" in d or "suggested_value" in d):
            findings.append(FreeTextFinding(
                field=str(d.get("field")),
                suggested_value=str(d.get("value", d.get("suggested_value"))),
                confidence=float(d.get("confidence", 0.5)),
                evidence=str(d.get("evidence", "")),
            ))
        else:
            findings.append(FreeTextFinding("<malformed>", json.dumps(d)[:40], 0.0, ""))
    return validate(findings, sch)


# ---------------------------------------------------------------------------
# Wrapper: enforce the schema on any FreeTextStructurer's output
# ---------------------------------------------------------------------------
@dataclass
class SchemaConstrainedStructurer:
    """Wrap any FreeTextStructurer; drop schema-invalid findings before they
    reach the review layer. Preserves the advisory-only contract."""
    inner: object
    schema: FindingSchema = dc_field(default_factory=FindingSchema.from_lexicon)
    last_report: Optional[ValidationReport] = None

    def structure(self, text: str, source_question: Optional[str] = None
                  ) -> List[FreeTextFinding]:
        raw = self.inner.structure(text, source_question)
        self.last_report = validate(raw, self.schema)
        return self.last_report.kept


def _demo() -> None:
    sch = FindingSchema.from_lexicon()
    print("Extraction schema (from structurer._LEXICON):")
    for f in sch.fields:
        vals = sch.allowed_values(f)
        show = vals if f not in _OPEN_FIELDS else f"{len(vals)} known names"
        print(f"  {f:16} : {show}")
    print("\nOutlines/XGrammar array regex (truncated):")
    print("  " + sch.array_regex()[:160] + " ...")
    print("\nValidation of a mixed model output:")
    model_json = json.dumps([
        {"field": "side_effect", "value": "rash"},         # valid
        {"field": "side_effect", "value": "spontaneous_combustion"},  # invalid value
        {"field": "made_up_field", "value": "x"},          # invalid field
        {"field": "drug_mention", "value": "keppra"},      # valid (open vocab)
    ])
    rep = parse_and_validate(model_json, sch)
    for f in rep.kept:
        print(f"  KEEP  {f.field}={f.suggested_value}")
    for f, why in rep.dropped:
        print(f"  DROP  {f.field}={f.suggested_value}  ({why})")


if __name__ == "__main__":
    _demo()
