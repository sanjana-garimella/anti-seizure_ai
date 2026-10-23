"""
baseline_nb.py
--------------
A fast, interpretable, LOCAL text-classification floor for the free-text NLP
layer (pure stdlib) - the baseline any transformer (ClinicalBERT / HUMADEX)
must beat before it is worth the dependency and opacity.

  * TfidfVectorizer  - tokenize + tf-idf (sublinear tf, smoothed idf, L2 norm).
  * MultinomialNB    - multinomial Naive Bayes with Laplace smoothing.
  * TextClassifier   - vectorizer + NB pipeline; predict / predict_proba /
                       top_features(label) for interpretability (why a label?).

No ML libraries, no network; deterministic. Interpretable because you can read
the exact tokens driving each class - important for clinical trust and audit.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return _TOKEN.findall((text or "").lower())


class TfidfVectorizer:
    def __init__(self, min_df: int = 1):
        self.min_df = min_df
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}

    def fit(self, docs: Sequence[str]) -> "TfidfVectorizer":
        df = Counter()
        toksets = [set(tokenize(d)) for d in docs]
        for ts in toksets:
            df.update(ts)
        n = len(docs)
        self.vocab = {t: i for i, t in enumerate(sorted(t for t, c in df.items() if c >= self.min_df))}
        self.idf = {t: math.log((1 + n) / (1 + df[t])) + 1.0 for t in self.vocab}
        return self

    def transform(self, doc: str) -> Dict[int, float]:
        counts = Counter(tokenize(doc))
        vec: Dict[int, float] = {}
        for t, c in counts.items():
            if t in self.vocab:
                tf = 1.0 + math.log(c)                      # sublinear tf
                vec[self.vocab[t]] = tf * self.idf[t]
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {i: v / norm for i, v in vec.items()}


class MultinomialNB:
    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.classes: List[str] = []
        self.log_prior: Dict[str, float] = {}
        self.log_prob: Dict[str, Dict[int, float]] = {}
        self._n_features = 0

    def fit(self, X: Sequence[Dict[int, float]], y: Sequence[str], n_features: int) -> "MultinomialNB":
        self._n_features = n_features
        self.classes = sorted(set(y))
        cls_feat = {c: defaultdict(float) for c in self.classes}
        cls_tot = {c: 0.0 for c in self.classes}
        cls_docs = Counter(y)
        for xi, yi in zip(X, y):
            for f, v in xi.items():
                cls_feat[yi][f] += v
                cls_tot[yi] += v
        n = len(y)
        for c in self.classes:
            self.log_prior[c] = math.log(cls_docs[c] / n)
            denom = cls_tot[c] + self.alpha * n_features
            self.log_prob[c] = {}
            for f in range(n_features):
                self.log_prob[c][f] = math.log((cls_feat[c].get(f, 0.0) + self.alpha) / denom)
        return self

    def _scores(self, x: Dict[int, float]) -> Dict[str, float]:
        s = {}
        for c in self.classes:
            lp = self.log_prior[c]
            for f, v in x.items():
                if f in self.log_prob[c]:
                    lp += v * self.log_prob[c][f]
            s[c] = lp
        return s

    def predict_proba(self, x: Dict[int, float]) -> Dict[str, float]:
        s = self._scores(x)
        m = max(s.values())
        exp = {c: math.exp(v - m) for c, v in s.items()}
        z = sum(exp.values()) or 1.0
        return {c: v / z for c, v in exp.items()}

    def predict(self, x: Dict[int, float]) -> str:
        return max(self._scores(x).items(), key=lambda kv: kv[1])[0]


class TextClassifier:
    """Vectorizer + NB pipeline for one field (e.g. side_effect present/type)."""
    def __init__(self, min_df: int = 1, alpha: float = 1.0):
        self.vec = TfidfVectorizer(min_df=min_df)
        self.nb = MultinomialNB(alpha=alpha)
        self._inv: Dict[int, str] = {}

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "TextClassifier":
        self.vec.fit(texts)
        X = [self.vec.transform(t) for t in texts]
        self.nb.fit(X, labels, len(self.vec.vocab))
        self._inv = {i: t for t, i in self.vec.vocab.items()}
        return self

    def predict(self, text: str) -> str:
        return self.nb.predict(self.vec.transform(text))

    def predict_proba(self, text: str) -> Dict[str, float]:
        return self.nb.predict_proba(self.vec.transform(text))

    def top_features(self, label: str, k: int = 10) -> List[str]:
        """Tokens most indicative of `label` vs the mean of other classes -
        the interpretable 'why'."""
        if label not in self.nb.log_prob:
            return []
        others = [c for c in self.nb.classes if c != label]
        scored = []
        for f, t in self._inv.items():
            base = self.nb.log_prob[label].get(f, 0.0)
            comp = sum(self.nb.log_prob[c].get(f, 0.0) for c in others) / (len(others) or 1)
            scored.append((base - comp, t))
        scored.sort(reverse=True)
        return [t for _, t in scored[:k]]
