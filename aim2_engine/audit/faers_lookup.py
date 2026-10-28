"""
audit/faers_lookup.py
----------------------
Lightweight openFDA FAERS adverse-event lookup for the 11 in-scope ASMs.

Fetches real-world adverse event counts from the openFDA drug/event API and
returns a structured summary suitable for the doctor's NLP audit view.

Every value returned here is a DRAFT for clinician verification — never used
by the decision engine. Source: openFDA (open.fda.gov); free, no key required
for ≤240 requests/min. Note: openFDA FAERS data updates may be suspended
(see NON_NEGOTIABLE_RULES.md).

Architecture credit: Chen 2024 · drug_safety_and_recommendation
(etl/load_faers_to_mongo.py, db/mongo_queries.py — FAERS evidence store
and audit pattern using openFDA drug/event API.)
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.parse
from typing import Dict, List, Optional, Any

log = logging.getLogger(__name__)

_BASE = "https://api.fda.gov/drug/event.json"


def _open_https(req, timeout):
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("refusing non-https URL")
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310

# Map our internal drug names to openFDA brand/generic search terms
_DRUG_SEARCH_TERMS: Dict[str, str] = {
    "levetiracetam":  "levetiracetam",
    "brivaracetam":   "brivaracetam",
    "clobazam":       "clobazam",
    "valproic_acid":  "valproic+acid",
    "lacosamide":     "lacosamide",
    "oxcarbazepine":  "oxcarbazepine",
    "carbamazepine":  "carbamazepine",
    "zonisamide":     "zonisamide",
    "lamotrigine":    "lamotrigine",
    "ethosuximide":   "ethosuximide",
    "topiramate":     "topiramate",
}


def get_faers_reaction_summary(
    drug_name: str,
    limit: int = 10,
    timeout: int = 6,
) -> Optional[Dict[str, Any]]:
    """
    Return the top adverse reactions reported in FAERS for `drug_name`.

    Returns a dict:
      {
        "drug":       str,
        "total":      int,          # total reports matched
        "top_reactions": [
          {"reaction": str, "count": int}, ...
        ],
        "source": "openFDA FAERS — draft for clinician verification",
      }
    or None on any failure (network, rate limit, drug not found).
    """
    term = _DRUG_SEARCH_TERMS.get(drug_name.lower().replace(" ", "_"),
                                   drug_name)
    params = urllib.parse.urlencode({
        "search": f"patient.drug.medicinalproduct:{term}",
        "count":  "patient.reaction.reactionmeddrapt.exact",
        "limit":  limit,
    })
    url = f"{_BASE}?{params}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "aim2-engine/0.2 (research; no-PHI)"})
        with _open_https(req, timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        total   = data.get("meta", {}).get("results", {}).get("total", 0)
        return {
            "drug": drug_name,
            "total": total,
            "top_reactions": [
                {"reaction": r["term"], "count": r["count"]}
                for r in results
            ],
            "source": "openFDA FAERS — draft for clinician verification",
        }
    except Exception as exc:                    # noqa: BLE001
        log.debug("FAERS lookup failed for '%s': %s", drug_name, exc)
        return None


def get_faers_serious_outcomes(
    drug_name: str,
    limit: int = 5,
    timeout: int = 6,
) -> Optional[Dict[str, Any]]:
    """
    Return serious outcome counts (hospitalization, disability, death, etc.)
    for `drug_name` from openFDA FAERS.
    """
    term = _DRUG_SEARCH_TERMS.get(drug_name.lower().replace(" ", "_"),
                                   drug_name)
    params = urllib.parse.urlencode({
        "search": (f"patient.drug.medicinalproduct:{term}"
                   "+AND+serious:1"),
        "count":  "seriousnesshospitalization",
        "limit":  limit,
    })
    url = f"{_BASE}?{params}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "aim2-engine/0.2 (research; no-PHI)"})
        with _open_https(req, timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "drug":    drug_name,
            "serious": data.get("results", []),
            "total":   data.get("meta", {}).get("results", {}).get("total", 0),
            "source":  "openFDA FAERS — draft for clinician verification",
        }
    except Exception as exc:                    # noqa: BLE001
        log.debug("FAERS serious outcomes lookup failed for '%s': %s", drug_name, exc)
        return None
