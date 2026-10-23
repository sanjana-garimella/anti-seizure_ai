"""
validation/fetch_asm_pubmed_cases.py
--------------------------------------
Pulls real, externally-authored PubMed abstracts restricted to the 11 study
ASMs (levetiracetam, lamotrigine, oxcarbazepine, valproate, clobazam,
topiramate, zonisamide, brivaracetam, ethosuximide, lacosamide,
carbamazepine), AND mentioning seizures/epilepsy AND adverse
effects/dose-adjustment/titration/discontinuation language. Caches the
results locally as JSON for validation/nlp_pubmed_eval.py to run the point-A
structurers against.

WHY A SEPARATE LOCAL SCRIPT: pulling hundreds-to-thousands of abstracts
through a chat-mediated article-search tool doesn't work - individual tool
calls cap out well under what's needed (confirmed: even a single batch of 50
abstracts exceeded the per-call output limit). This talks directly to NCBI's
public E-utilities API instead, which scales to the requested 1,000-10,000
range with simple pagination, no per-call size ceiling, and no PyPI
dependency (stdlib only: urllib, xml.etree.ElementTree, json).

WHAT THIS DATA CAN AND CAN'T TELL YOU (read before running nlp_pubmed_eval.py):
  - It IS real, externally-authored, ASM-specific medical-literature text -
    not anything written by this assistant, and not the general/mixed-drug
    ADE-Corpus-V2 sample used earlier (see nlp_external_eval.py) - this is
    narrowly your actual 11 drugs.
  - It is NOT annotated with character-level gold spans (PubMed abstracts
    aren't pre-labeled like ADE-Corpus-V2 was). So nlp_pubmed_eval.py can
    only report RECALL-style proxies (did a structurer find the ASM that's
    definitely mentioned; did it find ANY side-effect/seizure finding) - it
    cannot compute true precision, because there's no labeled negative class
    to measure false positives against. Treat it as a large-scale real-text
    smoke test / bug-hunting corpus, not a clinical validation metric.
  - It is still formal medical-literature language (case reports, cohort
    studies, pharmacokinetic trials), not caregiver speech. Same caveat as
    every other "real text" eval in this directory.

NCBI ETIQUETTE: without an API key, NCBI asks for <=3 requests/second; this
script sleeps between calls accordingly. Set NCBI_API_KEY env var (free,
https://www.ncbi.nlm.nih.gov/account/) to raise that to 10/sec and speed this
up significantly for the larger --n values.

DECISION-PATH NOTE: advisory/diagnostic only. Does not touch engine_core.

Run:
  cd aim2_engine
  python3 validation/fetch_asm_pubmed_cases.py --n 2000
  python3 validation/fetch_asm_pubmed_cases.py --n 10000   # slower - budget ~10-20 min without an API key
  NCBI_API_KEY=xxxxx python3 validation/fetch_asm_pubmed_cases.py --n 10000   # faster
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ASM_NAMES = [
    "levetiracetam", "lamotrigine", "oxcarbazepine", "valproate", "valproic acid",
    "clobazam", "topiramate", "zonisamide", "brivaracetam", "ethosuximide",
    "lacosamide", "carbamazepine",
]

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "aim2_engine_nlp_eval"
CONTACT_EMAIL = "research-tool@localhost"   # NCBI asks tools to self-identify; no real PII needed


def _api_key_params():
    key = os.environ.get("NCBI_API_KEY")
    return {"api_key": key} if key else {}


def _sleep_for_rate_limit():
    time.sleep(0.11 if os.environ.get("NCBI_API_KEY") else 0.35)


def _get(url, params, retries=3):
    qs = urllib.parse.urlencode(params)
    full = f"{url}?{qs}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=30) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            if attempt == retries - 1:
                raise
            print(f"    (retrying after error: {exc})")
            time.sleep(2 * (attempt + 1))


def esearch(term: str, retmax: int) -> list:
    params = {
        "db": "pubmed", "term": term, "retmax": retmax, "retmode": "json",
        "tool": TOOL_NAME, "email": CONTACT_EMAIL, **_api_key_params(),
    }
    raw = _get(f"{EUTILS}/esearch.fcgi", params)
    data = json.loads(raw)
    return data.get("esearchresult", {}).get("idlist", [])


def _abstract_text(article_el) -> str:
    parts = []
    for ab in article_el.findall(".//Abstract/AbstractText"):
        label = ab.get("Label")
        text = (ab.text or "").strip()
        if not text:
            continue
        parts.append(f"{label}: {text}" if label else text)
    return "\n\n".join(parts)


def efetch_batch(pmids: list) -> list:
    params = {
        "db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml",
        "tool": TOOL_NAME, "email": CONTACT_EMAIL, **_api_key_params(),
    }
    raw = _get(f"{EUTILS}/efetch.fcgi", params)
    root = ET.fromstring(raw)
    out = []
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        title_el = article.find(".//ArticleTitle")
        pmid = pmid_el.text if pmid_el is not None else None
        title = (title_el.text or "") if title_el is not None else ""
        abstract = _abstract_text(article)
        if not abstract:
            continue
        text = f"{title}\n\n{abstract}" if title else abstract
        matched = sorted({n for n in ASM_NAMES if n.lower() in text.lower()})
        out.append({"pmid": pmid, "title": title, "abstract": abstract,
                    "text": text, "matched_asms": matched})
    return out


def build_query() -> str:
    drug_clause = " OR ".join(f'"{n}"[tiab]' for n in ASM_NAMES)
    return (
        f"({drug_clause}) AND "
        f'("seizure"[tiab] OR "epilepsy"[tiab] OR "epileptic"[tiab]) AND '
        f'("adverse effect"[tiab] OR "adverse reaction"[tiab] OR "side effect"[tiab] OR '
        f'"dose adjustment"[tiab] OR "dosage adjustment"[tiab] OR "titration"[tiab] OR '
        f'"discontinuation"[tiab] OR "switch"[tiab] OR "tolerability"[tiab])'
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=2000, help="target number of abstracts")
    ap.add_argument("--batch-size", type=int, default=200, help="EFetch batch size")
    ap.add_argument("--out", default=None, help="output JSON path "
                     "(default: validation/data/asm_pubmed_cases.json)")
    args = ap.parse_args()

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "asm_pubmed_cases.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    term = build_query()
    print(f"  ESearch query (PubMed):\n    {term}\n")
    print(f"  Searching for up to {args.n} PMIDs...")
    pmids = esearch(term, args.n)
    print(f"  Found {len(pmids)} PMIDs.")
    if not pmids:
        print("  Nothing found - check network access / query.")
        sys.exit(1)

    cases = []
    seen_pmids = set()
    n_batches = (len(pmids) + args.batch_size - 1) // args.batch_size
    for i in range(0, len(pmids), args.batch_size):
        batch = pmids[i:i + args.batch_size]
        batch_num = i // args.batch_size + 1
        print(f"  EFetch batch {batch_num}/{n_batches} ({len(batch)} PMIDs)...")
        try:
            results = efetch_batch(batch)
        except Exception as exc:
            print(f"    ! batch failed: {exc} - skipping")
            _sleep_for_rate_limit()
            continue
        for r in results:
            if r["pmid"] and r["pmid"] not in seen_pmids:
                seen_pmids.add(r["pmid"])
                cases.append(r)
        _sleep_for_rate_limit()

    with open(out_path, "w") as f:
        json.dump({"query": term, "n_requested": args.n, "n_fetched": len(cases),
                    "cases": cases}, f, indent=1)

    print(f"\n  Saved {len(cases)} cases (with non-empty abstracts) to {out_path}")
    matched_counts = {}
    for c in cases:
        for n in c["matched_asms"]:
            matched_counts[n] = matched_counts.get(n, 0) + 1
    print(f"  Per-drug mention counts: {matched_counts}")


if __name__ == "__main__":
    main()
