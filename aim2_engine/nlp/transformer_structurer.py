"""
nlp/transformer_structurer.py
------------------------------
Point A upgrade — BioBERT / ClinicalBERT NER implementations of FreeTextStructurer.

Drop-in replacement for LexiconStructurer: same FreeTextFinding output,
same ADVISORY-only contract (findings are NEVER fed into the decision engine).

Two pre-configured profiles:
  * BioBERTStructurer      — default model: alvaroalon2/biobert_diseases_ner
                             (BioBERT base fine-tuned on BC5CDR diseases + chemicals)
  * ClinicalBERTStructurer — default model: samrawal/bert-base-uncased_clinical-ner
                             (Bio_ClinicalBERT fine-tuned on i2b2-2010 clinical NER)

Both degrade gracefully when `transformers` / `torch` are not installed:
they log a warning and return an empty finding list rather than crashing.

Custom models can be passed at construction:
    s = TransformerStructurer(model_name="your/model", entity_map={...})

Entity-type → field mapping is configured per model profile (see _PROFILES).

HARD BOUNDARY: findings produced here are ADVISORY. They are attached to the
clinician review layer only. They are NEVER written into IntervalSurvey or any
path the decision engine consumes. (spec §11 / GUARDRAILS.md)
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .interfaces import FreeTextFinding
from .negation import is_negated

log = logging.getLogger(__name__)

# Generic words the "treatment"/"chemical" entity types over-fire on — these
# are not drug NAMES, just words about medicating in general. Confirmed by
# validation/nlp_synthetic_eval.py (ClinicalBERT drug_mention precision was
# 0.29 before this filter — it was tagging "dose" and "medicine" themselves).
_GENERIC_DRUG_TERMS = {
    "dose", "doses", "dosage", "medicine", "medicines", "medication",
    "medications", "med", "meds", "pill", "pills", "tablet", "tablets",
    "drug", "drugs", "treatment",
}

# Splits a sentence into clauses on commas/semicolons/"and" so the NER
# pipeline gets a separate shot at each item in a symptom list (e.g.
# "nauseous, irritable, and unsteady"). The synthetic eval showed both
# transformer models only surfacing ONE entity out of three when several
# symptoms were listed in a single run over the whole sentence. Returns
# (clause_text, char_offset_into_original_text) pairs so entity offsets can
# still be mapped back for negation scoping.
_CLAUSE_SPLIT_RE = re.compile(r",|;|\band\b", re.I)


def _split_clauses(text: str):
    clauses = []
    pos = 0
    for m in _CLAUSE_SPLIT_RE.finditer(text):
        clause = text[pos:m.start()]
        if clause.strip():
            clauses.append((clause, pos))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        clauses.append((tail, pos))
    return clauses or [(text, 0)]

# ---------------------------------------------------------------------------
# Model profiles
# ---------------------------------------------------------------------------
# entity_map: transformer entity_group → (our field, suggested_value_or_None)
# When suggested_value is None the entity span text is used directly.
# Additional keyword refinement maps a broad entity to a more specific field.
# ---------------------------------------------------------------------------

@dataclass
class ModelProfile:
    model_name: str
    # entity_group (lowercased) → field name in FreeTextFinding
    entity_map: Dict[str, str]
    # regex patterns used to refine broad entity hits (field → pattern → value)
    refinement: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    # scalar to multiply raw model confidence (calibration; 1.0 = no change)
    confidence_scale: float = 1.0
    description: str = ""


# BioBERT fine-tuned on BC5CDR: entity_group = DISEASE | CHEMICAL
_BIOBERT_PROFILE = ModelProfile(
    model_name="alvaroalon2/biobert_diseases_ner",
    entity_map={
        "disease":  "side_effect",     # refined below if seizure keyword
        "chemical": "drug_mention",    # advisory only — not a decision field
    },
    refinement={
        "side_effect": [
            (r"seizure|convuls|staring|jerk|twitch|shaking|spell|epilep|"
             r"went\s+stiff|stiffened|was\s+not\s+responding|not\s+responding|unresponsive",
             "seizure_mention"),
            # See nlp/structurer.py for why bare "sleep"/"eating" were
            # narrowed - both matched reassuring "...normally" phrasing.
            # Caregiver phrasings match the lexicon (eyes open, room spins,
            # snaps at, stomach turns, hitting, red patches, went stiff).
            (r"tired|drowsy|fatigue|letharg|"
             r"sleep(?!ing\s+(?:normally|well|fine|great|ok|okay))|"
             r"barely\s+keep\s+\w+\s+eyes?\s+open|"
             r"can(?:not|'t)\s+keep\s+\w+\s+eyes?\s+open|"
             r"hard\s+to\s+stay\s+awake|nodding\s+off|keeps?\s+falling\s+asleep",
             "sleepiness"),
            (r"rash|hives|skin|blister|red\s+patches|open\s+sores",        "rash"),
            (r"dizz|unsteady|balance|wobbl|room\s+spins|head\s+spins|"
             r"spinning|light-?headed|vertigo",                            "dizziness"),
            (r"irritab|mood|angry|anger|cranky|emotional|snaps?\s+at|"
             r"cries\s+more|short-tempered|lashing\s+out",                 "irritability"),
            (r"nause|vomit|appetite|throw\w*\s*up|off.{0,15}food|"
             r"not\s+eating|trouble\s+eating|won'?t\s+eat|stomach\s+turns|"
             r"queasy|upset\s+stomach|"
             r"leaves\s+most\s+of\s+(?:his\s+|her\s+|the\s+)?plate",       "nausea"),
            (r"behav|aggress|hyper|acting out|\bhitting\b|yelling\s+at|"
             r"\bbiting\b|\bkicking\b",                                    "behavior"),
        ],
    },
    confidence_scale=0.9,
    description="BioBERT base fine-tuned on BC5CDR chemical/disease NER",
)

# ClinicalBERT (Bio_ClinicalBERT base) fine-tuned on i2b2-2010:
# entity_group = problem | treatment | test
_CLINICALBERT_PROFILE = ModelProfile(
    model_name="samrawal/bert-base-uncased_clinical-ner",
    entity_map={
        "problem":   "side_effect",    # clinical problems / symptoms
        "treatment": "drug_mention",   # treatments (advisory)
        "test":      "test_mention",   # tests (advisory)
    },
    refinement={
        "side_effect": [
            (r"seizure|convuls|staring|jerk|twitch|shaking|spell|epilep|"
             r"went\s+stiff|stiffened|was\s+not\s+responding|not\s+responding|unresponsive",
             "seizure_mention"),
            # See nlp/structurer.py. Same caregiver phrasings as the lexicon.
            (r"tired|drowsy|fatigue|letharg|"
             r"sleep(?!ing\s+(?:normally|well|fine|great|ok|okay))|"
             r"barely\s+keep\s+\w+\s+eyes?\s+open|"
             r"can(?:not|'t)\s+keep\s+\w+\s+eyes?\s+open|"
             r"hard\s+to\s+stay\s+awake|nodding\s+off|keeps?\s+falling\s+asleep",
             "sleepiness"),
            (r"rash|hives|skin|blister|red\s+patches|open\s+sores",        "rash"),
            (r"dizz|unsteady|balance|wobbl|room\s+spins|head\s+spins|"
             r"spinning|light-?headed|vertigo",                            "dizziness"),
            (r"irritab|mood|angry|anger|cranky|emotional|snaps?\s+at|"
             r"cries\s+more|short-tempered|lashing\s+out",                 "irritability"),
            (r"nause|vomit|appetite|throw\w*\s*up|off.{0,15}food|"
             r"not\s+eating|trouble\s+eating|won'?t\s+eat|stomach\s+turns|"
             r"queasy|upset\s+stomach|"
             r"leaves\s+most\s+of\s+(?:his\s+|her\s+|the\s+)?plate",       "nausea"),
            (r"behav|aggress|hyper|acting out|\bhitting\b|yelling\s+at|"
             r"\bbiting\b|\bkicking\b",                                    "behavior"),
        ],
    },
    confidence_scale=0.85,
    description="Bio_ClinicalBERT fine-tuned on i2b2-2010 clinical NER",
)

# BioLORD-2023 — biomedical sentence transformer trained on concept pairs and
# clinical ontologies. Not an NER model: used here for semantic similarity
# matching between free-text spans and known side-effect / seizure-type labels.
# Approach: embed the input text and each known field value, return a
# FreeTextFinding for any pair whose cosine similarity exceeds _BIOLORD_THRESH.
# Reference: FremyCompany/BioLORD-2023 (huggingface.co)
# Architecture credit: Chen 2024 · drug_safety_and_recommendation (github.com)
_BIOLORD_PROFILE = ModelProfile(
    model_name="FremyCompany/BioLORD-2023",
    # entity_map is unused for BioLORD (similarity-based, not token-classif.)
    entity_map={},
    confidence_scale=1.0,
    description="BioLORD-2023 semantic similarity over biomedical concept labels",
)

PROFILES: Dict[str, ModelProfile] = {
    "biobert":       _BIOBERT_PROFILE,
    "clinical_bert": _CLINICALBERT_PROFILE,
    "biolord":       _BIOLORD_PROFILE,
}


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------

class TransformerStructurer:
    """
    Generic transformer-based FreeTextStructurer.

    Parameters
    ----------
    profile_name : "biobert" | "clinical_bert" — pre-configured profile key.
        Ignored if model_name is supplied directly.
    model_name   : override the model. Requires entity_map too.
    entity_map   : override entity → field mapping.
    device       : -1 = CPU (default), 0+ = GPU index.
    lazy         : if True (default), load model on first call to .structure().
    """

    def __init__(
        self,
        profile_name: str = "biobert",
        *,
        model_name: Optional[str] = None,
        entity_map: Optional[Dict[str, str]] = None,
        device: int = -1,
        lazy: bool = True,
    ):
        if model_name:
            # Custom model — build a minimal profile
            self._profile = ModelProfile(
                model_name=model_name,
                entity_map=entity_map or {},
            )
        else:
            if profile_name not in PROFILES:
                raise ValueError(f"Unknown profile '{profile_name}'. "
                                 f"Choose from: {list(PROFILES)}")
            self._profile = PROFILES[profile_name]
            if entity_map:
                self._profile = ModelProfile(
                    model_name=self._profile.model_name,
                    entity_map=entity_map,
                    refinement=self._profile.refinement,
                    confidence_scale=self._profile.confidence_scale,
                    description=self._profile.description,
                )

        self._device = device
        self._pipeline = None
        self._available: Optional[bool] = None
        self._load_error: Optional[str] = None

        # Pre-compile refinement patterns
        self._compiled_refinement: Dict[str, List[Tuple[re.Pattern, str]]] = {
            f: [(re.compile(pat, re.I), val) for pat, val in rules]
            for f, rules in self._profile.refinement.items()
        }

        if not lazy:
            self._ensure_loaded()

    # ------------------------------------------------------------------
    # Public interface (FreeTextStructurer protocol)
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._profile.model_name

    @property
    def description(self) -> str:
        return self._profile.description

    def structure(self, text: str,
                  source_question: Optional[str] = None) -> List[FreeTextFinding]:
        """Extract findings from a single free-text string."""
        if not text or not text.strip():
            return []
        if not self._ensure_loaded():
            return []
        return self._run(text, source_question)

    def structure_many(self, answers: Dict[str, str]) -> List[FreeTextFinding]:
        """Extract findings from a dict of {question_id: free_text}."""
        out: List[FreeTextFinding] = []
        for q, txt in (answers or {}).items():
            if isinstance(txt, str):
                out.extend(self.structure(txt, source_question=q))
        return out

    def is_available(self) -> bool:
        """Return True if transformers + model are loadable (lazy check)."""
        return self._ensure_loaded()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from transformers import pipeline  # type: ignore
            log.info("Loading NER model '%s' (this may take a moment on first run)…",
                     self._profile.model_name)
            t0 = time.perf_counter()
            self._pipeline = pipeline(
                "ner",
                model=self._profile.model_name,
                aggregation_strategy="simple",
                device=self._device,
            )
            elapsed = time.perf_counter() - t0
            log.info("Model loaded in %.1f s", elapsed)
            self._available = True
        except ImportError:
            self._load_error = (
                "transformers / torch not installed. "
                "Run: pip install transformers torch --break-system-packages"
            )
            log.warning("TransformerStructurer unavailable: %s", self._load_error)
            self._available = False
        except Exception as exc:                       # noqa: BLE001
            self._load_error = str(exc)
            log.warning("Could not load model '%s': %s",
                        self._profile.model_name, exc)
            self._available = False
        return self._available

    def _run(self, text: str,
             source_question: Optional[str]) -> List[FreeTextFinding]:
        findings: List[FreeTextFinding] = []
        seen: set = set()

        # Run per-clause (not once over the whole sentence): the synthetic
        # eval showed the pipeline often surfaces only ONE entity when several
        # symptoms are listed together ("nauseous, irritable, and unsteady").
        # This also fixes refinement against the CLAUSE instead of the full
        # text, so a keyword for a different symptom elsewhere in the
        # sentence can no longer get attributed to this entity's span.
        for clause, offset in _split_clauses(text):
            entities = self._pipeline(clause)          # type: ignore[misc]
            for ent in entities:
                raw_group = (ent.get("entity_group") or ent.get("entity") or "").lower()
                span = ent.get("word") or ""
                raw_score = float(ent.get("score", 0.0))

                if span.strip().startswith("##"):
                    # Orphaned WordPiece continuation fragment - the
                    # aggregation_strategy="simple" merge failed to join it
                    # with the preceding token (happens on domain-specific
                    # pharma words the tokenizer splits into many pieces,
                    # e.g. "levetiracetam" -> lev + ##eti + ##tam, where the
                    # model doesn't tag every piece the same way). Never a
                    # real standalone concept. Found via real-scale PubMed
                    # eval (validation/nlp_pubmed_eval.py) - would otherwise
                    # surface as a nonsense "##tam" suggestion.
                    continue

                field_name = self._profile.entity_map.get(raw_group)
                if not field_name:
                    continue  # entity type not in our mapping

                final_field = field_name
                final_value = span.strip()
                if field_name in self._compiled_refinement:
                    matched_refinement = False
                    for pat, refined_value in self._compiled_refinement[field_name]:
                        # CLAUSE only, not span: a context-dependent pattern
                        # (e.g. the sleep(?!ing normally|well|...) negative
                        # lookahead) can't see disambiguating words that got
                        # truncated out of a short NER span ("sleeping" alone,
                        # no trailing "normally") - checking span would let it
                        # match on truncated text even though the full clause
                        # correctly wouldn't. The clause always contains
                        # whatever the span contains plus its real context,
                        # so it's the only check needed. Found via
                        # validation/nlp_confusion_matrix.py (HUMADEX still
                        # hallucinating "sleepiness" from "sleeping normally"
                        # after the lookahead fix, because span-only matching
                        # bypassed it).
                        # Negation is judged at the matched word, not at the NER
                        # span start. A span often starts at 0 (or includes the
                        # cue, as in "No rash"), and is_negated treats offset 0
                        # as not negated, so the denied word was kept.
                        m = pat.search(clause)
                        if not m or is_negated(text, offset + m.start()):
                            continue
                        final_value = refined_value
                        matched_refinement = True
                        if refined_value == "seizure_mention":
                            final_field = "seizure_mention"
                        break
                    if not matched_refinement:
                        # The model tagged this span as a broad symptom/sign
                        # entity, but it doesn't match any canonical category
                        # we know how to surface, or every match is negated.
                        # Rather than pass an arbitrary raw word through as a
                        # "side_effect" suggestion (e.g. "stable"/"happy"/"flag"
                        # from plain reassurance text - found via
                        # validation/nlp_confusion_matrix.py on HUMADEX/d4data),
                        # drop it. Doesn't affect drug_mention/test_mention,
                        # which have no refinement entry and keep their raw
                        # span as intended.
                        continue

                if final_field == "drug_mention" and span.strip().lower() in _GENERIC_DRUG_TERMS:
                    continue  # generic word ("dose", "medicine"...), not an actual drug name
                # A drug or test NAME is kept even in "no adverse effects with
                # lacosamide". Symptom negation is handled at the match above.

                confidence = min(raw_score * self._profile.confidence_scale, 1.0)
                dedup_key = (final_field, final_value)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                findings.append(FreeTextFinding(
                    field=final_field,
                    suggested_value=final_value,
                    confidence=round(confidence, 3),
                    evidence=span.strip(),
                    source_question=source_question,
                    note=(
                        f"SUGGESTION for clinician review - not used by the decision engine. "
                        f"[model: {self._profile.model_name}]"
                    ),
                ))

        return findings


# ---------------------------------------------------------------------------
# Generic adapter — try ANY HuggingFace token-classification checkpoint
# ---------------------------------------------------------------------------
# Reuse the BioBERT side-effect refinement list (identical to ClinicalBERT's)
# rather than authoring it a third time.
_SIDE_EFFECT_REFINEMENT = [
    (re.compile(pat, re.I), val) for pat, val in _BIOBERT_PROFILE.refinement["side_effect"]
]

# Third-party models spell their entity labels differently (Maccrobat's
# "Sign_symptom"/"Disease_disorder"/"Medication"/"Diagnostic_procedure" vs.
# i2b2-style "problem"/"treatment"/"test" vs. others). Rather than hand-author
# an exact ModelProfile.entity_map per model — which risks a wrong guess at
# the exact label spelling — this matches by SUBSTRING, so it tolerates
# naming differences across model authors. Less precise than an exact map;
# spot-check a new model's output before trusting it.
_FUZZY_RULES = (
    (("symptom", "sign", "disease", "disorder", "problem", "finding"), "side_effect"),
    (("medication", "drug", "chemical", "treatment", "therapeutic"), "drug_mention"),
    (("test", "diagnostic", "lab"), "test_mention"),
)


def _fuzzy_field(raw_group: str) -> Optional[str]:
    g = raw_group.lower()
    for keywords, mapped_field in _FUZZY_RULES:
        if any(k in g for k in keywords):
            return mapped_field
    return None


class GenericNERAdapter:
    """Wraps ANY HuggingFace token-classification checkpoint as a
    FreeTextStructurer, to quickly try additional pretrained models against
    the same synthetic eval without hand-writing a ModelProfile for each one.
    Applies the same fixes validated on BioBERT/ClinicalBERT: per-clause NER
    (multi-symptom recall), sentence-scope negation suppression, and the
    generic-drug-word stoplist. Field mapping is heuristic/substring-based
    (_fuzzy_field) since third-party label spelling isn't guaranteed.

    Same ADVISORY-only contract as TransformerStructurer: never feeds
    engine_core.
    """

    def __init__(self, model_name: str, device: int = -1, lazy: bool = True,
                 aggregation_strategy: str = "max"):
        self.model_name = model_name
        self.description = f"generic NER adapter over {model_name}"
        self._device = device
        self._aggregation_strategy = aggregation_strategy
        self._pipeline = None
        self._available: Optional[bool] = None
        self._load_error: Optional[str] = None
        if not lazy:
            self._ensure_loaded()

    def is_available(self) -> bool:
        return self._ensure_loaded()

    def _ensure_loaded(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from transformers import pipeline  # type: ignore
            self._pipeline = pipeline(
                "ner", model=self.model_name,
                aggregation_strategy=self._aggregation_strategy,
                device=self._device,
            )
            self._available = True
        except ImportError:
            self._load_error = "transformers / torch not installed."
            log.warning("GenericNERAdapter unavailable: %s", self._load_error)
            self._available = False
        except Exception as exc:                       # noqa: BLE001
            self._load_error = str(exc)
            log.warning("Could not load model '%s': %s", self.model_name, exc)
            self._available = False
        return self._available

    def structure(self, text: str, source_question: Optional[str] = None) -> List[FreeTextFinding]:
        if not text or not text.strip():
            return []
        if not self._ensure_loaded():
            return []
        findings: List[FreeTextFinding] = []
        seen: set = set()
        for clause, offset in _split_clauses(text):
            try:
                entities = self._pipeline(clause)       # type: ignore[misc]
            except Exception as exc:                    # noqa: BLE001
                log.warning("GenericNERAdapter inference failed on %r: %s", clause, exc)
                continue
            for ent in entities:
                raw_group = ent.get("entity_group") or ent.get("entity") or ""
                field_name = _fuzzy_field(raw_group)
                if not field_name:
                    continue
                span = (ent.get("word") or "").strip()
                raw_score = float(ent.get("score", 0.0))

                if span.startswith("##"):
                    # Orphaned WordPiece continuation fragment - see the
                    # matching comment in TransformerStructurer._run above.
                    continue

                final_field, final_value = field_name, span
                if field_name == "side_effect":
                    matched_refinement = False
                    for pat, refined_value in _SIDE_EFFECT_REFINEMENT:
                        # CLAUSE only, not the NER span. Negation uses the
                        # matched word's offset: a span that starts at 0
                        # ("No rash") used to skip is_negated entirely.
                        m = pat.search(clause)
                        if not m or is_negated(text, offset + m.start()):
                            continue
                        final_value = refined_value
                        matched_refinement = True
                        if refined_value == "seizure_mention":
                            final_field = "seizure_mention"
                        break
                    if not matched_refinement:
                        # See the matching comment in TransformerStructurer._run
                        # above - drop uncanonicalized or negated side_effect
                        # spans instead of surfacing an arbitrary word.
                        continue

                if final_field == "drug_mention" and span.lower() in _GENERIC_DRUG_TERMS:
                    continue

                key = (final_field, final_value)
                if key in seen:
                    continue
                seen.add(key)

                findings.append(FreeTextFinding(
                    field=final_field,
                    suggested_value=final_value,
                    confidence=round(min(raw_score, 1.0), 3),
                    evidence=span,
                    source_question=source_question,
                    note=(
                        "SUGGESTION for clinician review - not used by the decision engine. "
                        f"[model: {self.model_name}]"
                    ),
                ))
        return findings

    def structure_many(self, answers: Dict[str, str]) -> List[FreeTextFinding]:
        out: List[FreeTextFinding] = []
        for q, txt in (answers or {}).items():
            if isinstance(txt, str):
                out.extend(self.structure(txt, source_question=q))
        return out


# ---------------------------------------------------------------------------
# Named subclasses (convenience aliases, pre-wired to profiles)
# ---------------------------------------------------------------------------

class BioLORDStructurer:
    """
    Semantic similarity structurer using BioLORD-2023 (sentence-transformers).

    Instead of token classification, embeds the free text and each known
    clinical label, then returns FreeTextFinding for any label whose cosine
    similarity exceeds `threshold`.

    Requires: pip install sentence-transformers --break-system-packages
    Falls back to [] if sentence_transformers not installed.

    Architecture credit: Chen 2024 · drug_safety_and_recommendation
    (FremyCompany/BioLORD-2023 — trained on biomedical concept pairs and
    clinical ontologies; outperforms general-purpose transformers on
    biomedical semantic similarity tasks.)
    """

    _LABELS: Dict[str, List[str]] = {
        "seizure_mention":    ["seizure", "convulsion", "tonic-clonic", "absence",
                               "myoclonic", "atonic", "focal seizure", "staring spell",
                               "drop attack", "epileptic episode"],
        "sleepiness":         ["drowsiness", "fatigue", "lethargy", "sedation",
                               "excessive sleepiness"],
        "rash":               ["skin rash", "hives", "urticaria", "blistering rash",
                               "Stevens-Johnson syndrome", "skin reaction"],
        "dizziness":          ["dizziness", "vertigo", "unsteadiness", "ataxia"],
        "irritability":       ["irritability", "agitation", "aggression", "mood disorder",
                               "behavioral change"],
        "nausea":             ["nausea", "vomiting", "gastrointestinal upset"],
        "missed_dose_reason": ["forgot medication", "ran out of medication",
                               "child refuses medication", "cost of medication"],
        "dose_change":        ["stopped medication", "dose increased", "dose decreased",
                               "switched medication"],
    }

    model_name = "FremyCompany/BioLORD-2023"
    description = "BioLORD-2023 semantic similarity over biomedical concept labels"

    def __init__(self, threshold: float = 0.45, device: int = -1, lazy: bool = True):
        # NOTE (finding D9): threshold=0.45 is an UNVALIDATED default cosine
        # cutoff. It has NOT been tuned via a precision/recall sweep on any
        # labeled set for this app's labels, and it directly trades false
        # positives against false negatives. Before this structurer is used for
        # anything but exploration, run a P/R sweep over candidate thresholds
        # on a clinician-labeled set and justify the chosen value. Exposed as a
        # constructor arg precisely so it can be swept, not hardcoded in logic.
        self._threshold = threshold
        self._device = device
        self._model = None
        self._available: Optional[bool] = None
        self._load_error: Optional[str] = None
        if not lazy:
            self._ensure_loaded()

    def is_available(self) -> bool:
        return self._ensure_loaded()

    def structure(self, text: str,
                  source_question: Optional[str] = None) -> List[FreeTextFinding]:
        if not text or not text.strip():
            return []
        if not self._ensure_loaded():
            return []
        return self._run(text, source_question)

    def structure_many(self, answers: Dict[str, str]) -> List[FreeTextFinding]:
        out: List[FreeTextFinding] = []
        for q, txt in (answers or {}).items():
            if isinstance(txt, str):
                out.extend(self.structure(txt, source_question=q))
        return out

    def _ensure_loaded(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            log.info("Loading BioLORD model '%s'…", self.model_name)
            self._model = SentenceTransformer(self.model_name, device=(
                "cpu" if self._device < 0 else f"cuda:{self._device}"))
            self._available = True
        except ImportError:
            self._load_error = (
                "sentence-transformers not installed. "
                "Run: pip install sentence-transformers --break-system-packages"
            )
            log.warning("BioLORDStructurer unavailable: %s", self._load_error)
            self._available = False
        except Exception as exc:                   # noqa: BLE001
            self._load_error = str(exc)
            log.warning("BioLORDStructurer: could not load model: %s", exc)
            self._available = False
        return self._available

    def _run(self, text: str,
             source_question: Optional[str]) -> List[FreeTextFinding]:
        import numpy as np                          # type: ignore

        # Collect all (field, label) pairs and embed everything in one batch
        pairs: List[Tuple[str, str]] = []
        for field, labels in self._LABELS.items():
            for label in labels:
                pairs.append((field, label))

        all_texts = [text] + [lbl for _, lbl in pairs]
        embeddings = self._model.encode(all_texts,   # type: ignore[union-attr]
                                        normalize_embeddings=True)
        text_emb = embeddings[0]
        label_embs = embeddings[1:]

        findings: List[FreeTextFinding] = []
        seen: set = set()
        for i, (field, label) in enumerate(pairs):
            sim = float(np.dot(text_emb, label_embs[i]))
            if sim >= self._threshold and field not in seen:
                seen.add(field)
                findings.append(FreeTextFinding(
                    field=field,
                    suggested_value=label,
                    confidence=round(min(sim, 1.0), 3),
                    evidence=label,
                    source_question=source_question,
                    note=(
                        "SUGGESTION for clinician review - not used by the decision engine. "
                        f"[model: {self.model_name}, similarity={sim:.3f}]"
                    ),
                ))
        return findings


class BioBERTStructurer(TransformerStructurer):
    """BioBERT fine-tuned on BC5CDR disease/chemical NER."""
    def __init__(self, *, device: int = -1, lazy: bool = True,
                 model_name: Optional[str] = None):
        super().__init__(
            profile_name="biobert",
            model_name=model_name,
            device=device,
            lazy=lazy,
        )


class ClinicalBERTStructurer(TransformerStructurer):
    """Bio_ClinicalBERT fine-tuned on i2b2-2010 clinical NER."""
    def __init__(self, *, device: int = -1, lazy: bool = True,
                 model_name: Optional[str] = None):
        super().__init__(
            profile_name="clinical_bert",
            model_name=model_name,
            device=device,
            lazy=lazy,
        )
