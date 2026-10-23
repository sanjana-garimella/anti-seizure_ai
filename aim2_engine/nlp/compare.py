"""
nlp/compare.py
--------------
Multi-model comparison harness for FreeTextStructurer implementations.

Runs Lexicon, BioBERT, and ClinicalBERT on the same free-text inputs and
produces a structured ComparisonReport with per-model findings, agreement
metrics, and per-field diffs.

Usage (standalone):
    from nlp.compare import StructurerComparator
    from nlp.structurer import LexiconStructurer
    from nlp.transformer_structurer import BioBERTStructurer, ClinicalBERTStructurer

    cmp = StructurerComparator()
    report = cmp.compare({"Q13": "she had a seizure and her rash is spreading"})
    print(report.summary())

The comparator is ADVISORY: it produces a report for human review.
No comparison output feeds the decision engine.

Epilepsy-domain terminology sourced from the Epilepsy Foundation
(https://www.epilepsy.com/what-is-epilepsy/seizure-types), covering
ILAE 2017 seizure classification, focal/generalized onset types,
and common parent-reported descriptors.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .interfaces import FreeTextFinding
from .structurer import LexiconStructurer


# ---------------------------------------------------------------------------
# Domain vocabulary from Epilepsy Foundation / ILAE 2017
# ---------------------------------------------------------------------------
# Used to annotate comparison output with clinical context and to help
# readers interpret model agreement/disagreement.

SEIZURE_VOCAB = {
    # Focal onset
    "focal_aware":        ["focal aware", "simple partial", "aware"],
    "focal_impaired":     ["focal impaired", "complex partial", "dyscognitive",
                           "staring", "staring spell", "blank stare"],
    "focal_motor":        ["focal motor", "clonic", "tonic", "myoclonic", "spasm",
                           "jerking", "twitching", "automatism", "chewing", "lip smacking"],
    "focal_nonmotor":     ["aura", "déjà vu", "deja vu", "sensory", "autonomic",
                           "emotional", "cognitive focal"],
    # Generalized onset
    "tonic_clonic":       ["tonic-clonic", "tonic clonic", "grand mal",
                           "convulsion", "convulsing", "shaking all over",
                           "full body shaking", "fell and shook"],
    "absence":            ["absence", "petit mal", "staring", "zoning out",
                           "blank", "unresponsive briefly"],
    "myoclonic":          ["myoclonic", "myclonic", "muscle jerk", "sudden jerk",
                           "morning jerk"],
    "atonic":             ["atonic", "drop attack", "drop seizure", "drop spell",
                           "head drop", "suddenly fell"],
    "tonic":              ["tonic", "stiffening", "body stiff", "rigid"],
    "clonic":             ["clonic", "rhythmic jerking", "jerking"],
    # Post-ictal
    "postictal":          ["after the seizure", "postictal", "post-ictal",
                           "tired after", "confused after", "sleeping after",
                           "wouldn't wake", "hard to rouse"],
    # Status
    "prolonged":          ["didn't stop", "lasted more than", "more than 5 min",
                           "called 911", "went to ER", "emergency room",
                           "prolonged seizure"],
}

# Common ASM side effects (parent-reported language)
SIDE_EFFECT_VOCAB = {
    "sleepiness":        ["tired", "sleepy", "drowsy", "fatigue", "lethargic",
                          "hard to wake", "sleeping a lot", "sluggish"],
    "behavior":          ["irritable", "aggressive", "mood", "angry", "emotional",
                          "acting out", "behavior problems", "tantrums", "hyper",
                          "rage", "crying", "mean"],
    "cognitive":         ["memory", "word finding", "brain fog", "slow", "confused",
                          "can't concentrate", "school problems", "focus"],
    "dizziness":         ["dizzy", "unsteady", "balance", "wobbly", "falling",
                          "clumsy", "coordination"],
    "gi":                ["nausea", "vomit", "stomach", "appetite", "eating less",
                          "threw up", "belly ache", "diarrhea"],
    "rash":              ["rash", "hives", "skin", "blister", "blistering",
                          "sores", "peeling", "itchy skin"],
    "weight":            ["weight gain", "weight loss", "eating more", "eating less",
                          "gained weight", "lost weight"],
    "hair":              ["hair loss", "hair thinning", "losing hair"],
    "tremor":            ["shaking hands", "tremor", "trembling"],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelResult:
    model_name: str
    findings: List[FreeTextFinding]
    duration_ms: float
    available: bool = True
    error: Optional[str] = None


@dataclass
class FieldAgreement:
    field: str
    models_agreeing: List[str]    # models that found this field
    models_missing: List[str]     # models that did NOT find this field
    values_by_model: Dict[str, str]
    agreement_level: str          # "full" | "partial" | "none"

    @classmethod
    def compute(cls, field: str,
                results: Dict[str, ModelResult]) -> "FieldAgreement":
        values: Dict[str, str] = {}
        for model_name, r in results.items():
            if r.available:
                match = next((f for f in r.findings if f.field == field), None)
                if match:
                    values[model_name] = match.suggested_value
        all_models = [n for n, r in results.items() if r.available]
        agreeing = list(values.keys())
        missing = [m for m in all_models if m not in values]
        if len(agreeing) == len(all_models) and len(set(values.values())) == 1:
            level = "full"
        elif agreeing:
            level = "partial"
        else:
            level = "none"
        return cls(
            field=field,
            models_agreeing=agreeing,
            models_missing=missing,
            values_by_model=values,
            agreement_level=level,
        )


@dataclass
class ComparisonReport:
    """Full comparison output across all models for a set of text inputs."""
    inputs: Dict[str, str]               # {source_question: text}
    results: Dict[str, ModelResult]      # {model_name: ModelResult}
    field_agreements: List[FieldAgreement]
    unique_to_model: Dict[str, List[FreeTextFinding]]   # findings only one model sees

    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = ["=== StructurerComparator Report ==="]
        for q, txt in self.inputs.items():
            lines.append(f"\nInput [{q}]: {txt!r}")

        lines.append("\n--- Per-model findings ---")
        for model_name, r in self.results.items():
            status = "OK" if r.available else f"UNAVAILABLE ({r.error})"
            lines.append(f"\n  [{model_name}]  {status}  ({r.duration_ms:.0f} ms)")
            if r.available:
                if not r.findings:
                    lines.append("    (no findings)")
                for f in r.findings:
                    lines.append(f"    {f.field} = {f.suggested_value!r}"
                                 f"  conf={f.confidence:.2f}"
                                 f"  evidence={f.evidence!r}")

        lines.append("\n--- Field agreement ---")
        for fa in self.field_agreements:
            lines.append(f"  {fa.field}: [{fa.agreement_level.upper()}]"
                         f"  agreeing={fa.models_agreeing}"
                         f"  missing={fa.models_missing}"
                         f"  values={fa.values_by_model}")

        if self.unique_to_model:
            lines.append("\n--- Model-unique findings (others missed) ---")
            for model_name, findings in self.unique_to_model.items():
                for f in findings:
                    lines.append(f"  [{model_name}] ONLY → {f.field}={f.suggested_value!r}"
                                 f"  evidence={f.evidence!r}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "inputs": self.inputs,
            "results": {
                name: {
                    "model_name": r.model_name,
                    "available": r.available,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                    "findings": [
                        {
                            "field": f.field,
                            "suggested_value": f.suggested_value,
                            "confidence": f.confidence,
                            "evidence": f.evidence,
                            "source_question": f.source_question,
                        }
                        for f in r.findings
                    ],
                }
                for name, r in self.results.items()
            },
            "field_agreements": [
                {
                    "field": fa.field,
                    "agreement_level": fa.agreement_level,
                    "models_agreeing": fa.models_agreeing,
                    "models_missing": fa.models_missing,
                    "values_by_model": fa.values_by_model,
                }
                for fa in self.field_agreements
            ],
            "unique_to_model": {
                name: [
                    {"field": f.field, "suggested_value": f.suggested_value,
                     "evidence": f.evidence}
                    for f in findings
                ]
                for name, findings in self.unique_to_model.items()
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------

class StructurerComparator:
    """
    Runs multiple FreeTextStructurer implementations on the same inputs
    and produces a ComparisonReport.

    By default wires up Lexicon + BioBERT + ClinicalBERT. Pass your own
    structurers dict to override:
        cmp = StructurerComparator(structurers={"lexicon": LexiconStructurer()})
    """

    def __init__(
        self,
        structurers: Optional[Dict[str, object]] = None,
        include_biobert: bool = True,
        include_clinical_bert: bool = True,
    ):
        if structurers is not None:
            self._structurers = structurers
        else:
            self._structurers: Dict[str, object] = {
                "lexicon": LexiconStructurer(),
            }
            if include_biobert:
                try:
                    from .transformer_structurer import BioBERTStructurer
                    self._structurers["biobert"] = BioBERTStructurer(lazy=True)
                except Exception as exc:             # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning(
                        "BioBERTStructurer unavailable: %s", exc)
            if include_clinical_bert:
                try:
                    from .transformer_structurer import ClinicalBERTStructurer
                    self._structurers["clinical_bert"] = ClinicalBERTStructurer(lazy=True)
                except Exception as exc:             # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning(
                        "ClinicalBERTStructurer unavailable: %s", exc)

    # ------------------------------------------------------------------

    def compare(self, answers: Dict[str, str]) -> ComparisonReport:
        """
        Run all structurers on `answers` and return a ComparisonReport.

        answers: {question_id: free_text_string}
        """
        results: Dict[str, ModelResult] = {}

        for name, structurer in self._structurers.items():
            t0 = time.perf_counter()
            try:
                # Check availability for transformer models
                available = True
                if hasattr(structurer, "is_available"):
                    available = structurer.is_available()
                if available:
                    findings = structurer.structure_many(answers)   # type: ignore[attr-defined]
                else:
                    findings = []
                error = None if available else getattr(structurer, "_load_error", "unavailable")
            except Exception as exc:                               # noqa: BLE001
                findings = []
                available = False
                error = str(exc)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            results[name] = ModelResult(
                model_name=getattr(structurer, "model_name", name),
                findings=findings,
                duration_ms=elapsed_ms,
                available=available,
                error=error,
            )

        return self._build_report(answers, results)

    def compare_text(self, text: str,
                     source_question: str = "free_text") -> ComparisonReport:
        """Convenience wrapper for a single string."""
        return self.compare({source_question: text})

    # ------------------------------------------------------------------

    def _build_report(self, inputs: Dict[str, str],
                      results: Dict[str, ModelResult]) -> ComparisonReport:
        # Collect all fields seen across all models
        all_fields: Set[str] = set()
        for r in results.values():
            for f in r.findings:
                all_fields.add(f.field)

        field_agreements = [
            FieldAgreement.compute(fld, results)
            for fld in sorted(all_fields)
        ]

        # Findings unique to exactly one model (only meaningful with 2+ models)
        unique_to_model: Dict[str, List[FreeTextFinding]] = {}
        available_models = [n for n, r in results.items() if r.available]
        if len(available_models) >= 2:
            for name, r in results.items():
                if not r.available:
                    continue
                other_fields: Set[Tuple[str, str]] = set()
                for other_name, other_r in results.items():
                    if other_name != name and other_r.available:
                        for f in other_r.findings:
                            other_fields.add((f.field, f.suggested_value))
                unique = [
                    f for f in r.findings
                    if (f.field, f.suggested_value) not in other_fields
                ]
                if unique:
                    unique_to_model[name] = unique

        return ComparisonReport(
            inputs=inputs,
            results=results,
            field_agreements=field_agreements,
            unique_to_model=unique_to_model,
        )
