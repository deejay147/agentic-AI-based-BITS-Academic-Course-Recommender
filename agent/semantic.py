"""A small semantic model of the course catalogue, learned from the supplied documents.

Keyword search (BM25) only sees the exact words: 'finance' matched Copywriting because both mention
'market'. This model learns which words go together from the ~2,100 course descriptions + handout
lecture plans themselves (latent semantic analysis):

    1. every course -> a TF-IDF vector over the catalogue vocabulary (title counted 3x)
    2. SVD keeps the ~160 strongest directions of that matrix; words that keep showing up in the same
       kind of course (finance, investment, portfolio, valuation, derivatives) end up close together
    3. a query is folded into the same space, and courses are ranked by cosine similarity

So a course can match 'finance' without the word 'finance' in it, and a course that mentions 'market'
once in an advertising syllabus doesn't look like a finance course. No download, no GPU, deterministic;
trained by `python -m agent.semantic` (also part of `python -m ingest.run_all`), saved to
data/processed/semantic.npz and loaded by the app.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np

from agent.retrieval import expand, readable, tokens
from engine.catalog import Catalog, get_catalog

PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "semantic.npz"
K = 100            # latent dimensions (tuned on tests/eval_retrieval.py: 60 / 100 / 160 / 250 tried)
MAX_VOCAB = 12000


def _doc_text(cat: Catalog, code: str) -> str:
    c = cat.courses.get(code) or {}
    h = cat.handout(code) or {}
    title = cat.title(code) or c.get("title") or ""
    return " ".join([title] * 3 + [c.get("description") or "", h.get("topics_text") or ""])


def build(cat: Catalog | None = None, k: int = K) -> dict:
    cat = cat or get_catalog()
    codes = sorted(set(cat.courses) | set(cat.offerings))
    docs = [Counter(tokens(_doc_text(cat, c))) for c in codes]
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(d.keys())
    # words in at least 2 courses (else they can't link anything) and at most 30% of them (else noise)
    # instructor names appear in handouts and would link courses by who teaches them, not what they are
    from agent.retrieval import stem
    names = set()
    for offs in cat.offerings.values():
        for o in offs:
            people = [o.get("ic") or ""] + [i for s_ in o["sections"] for i in (s_.get("instructors") or [])]
            for person in people:
                names |= {stem(w) for w in re.findall(r"[a-z]+", person.lower()) if len(w) > 2}
    vocab = [t for t, m in df.most_common() if 2 <= m <= 0.3 * n
             and not any(part in names for part in t.split("_"))][:MAX_VOCAB]
    col = {t: i for i, t in enumerate(vocab)}
    idf = np.array([math.log(n / df[t]) for t in vocab], dtype=np.float32)
    X = np.zeros((n, len(vocab)), dtype=np.float32)
    for i, d in enumerate(docs):
        for t, f in d.items():
            j = col.get(t)
            if j is not None:
                X[i, j] = (1 + math.log(f)) * idf[j]
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    # SVD through the small n x n Gram matrix (n ~ 2k): X = U S V^T
    evals, evecs = np.linalg.eigh(X @ X.T)
    top = np.argsort(evals)[::-1][:k]
    S = np.sqrt(np.maximum(evals[top], 1e-9)).astype(np.float32)
    U = evecs[:, top].astype(np.float32)
    V = (X.T @ U) / S                       # term -> latent (the fold-in basis)
    D = U * S                               # document vectors  (= X @ V)
    D /= np.maximum(np.linalg.norm(D, axis=1, keepdims=True), 1e-9)
    np.savez_compressed(PATH, codes=np.array(codes), vocab=np.array(vocab), idf=idf,
                        V=V.astype(np.float16), D=D.astype(np.float16))
    return {"courses": n, "vocab": len(vocab), "dims": int(k)}


class Semantic:
    def __init__(self, path: Path = PATH):
        z = np.load(path, allow_pickle=False)
        self.codes = [str(c) for c in z["codes"]]
        self.row = {c: i for i, c in enumerate(self.codes)}
        self.vocab = [str(t) for t in z["vocab"]]
        self.col = {t: i for i, t in enumerate(self.vocab)}
        self.idf = z["idf"].astype(np.float32)
        self.V = z["V"].astype(np.float32)
        self.D = z["D"].astype(np.float32)
        self._Vn = self.V / np.maximum(np.linalg.norm(self.V, axis=1, keepdims=True), 1e-9)

    def embed(self, query: str) -> np.ndarray | None:
        """query -> unit vector in the latent space (expanded with the synonym table first)"""
        q = np.zeros(len(self.vocab), dtype=np.float32)
        for t, f in Counter(expand(query)).items():
            j = self.col.get(t)
            if j is not None:
                q[j] = (1 + math.log(f)) * self.idf[j]
        if not q.any():
            return None
        v = q @ self.V
        nrm = np.linalg.norm(v)
        return v / nrm if nrm > 0 else None

    def similarity(self, query: str, codes: list[str]) -> dict[str, float]:
        v = self.embed(query)
        if v is None:
            return {c: 0.0 for c in codes}
        out = {}
        for c in codes:
            i = self.row.get(c)
            out[c] = float(self.D[i] @ v) if i is not None else 0.0
        return out

    def neighbours(self, query: str, n: int = 8) -> list[str]:
        """the words the model learned are closest to the query - shown as 'why these courses'"""
        v = self.embed(query)
        if v is None:
            return []
        own = set(expand(query))
        sims = self._Vn @ v
        out = []
        for j in np.argsort(-sims):
            t = self.vocab[j]
            if t in own or "_" in t or len(t) < 4:
                continue
            out.append(readable(t))
            if len(out) >= n:
                break
        return out


@lru_cache(maxsize=1)
def get_semantic() -> Semantic | None:
    try:
        return Semantic()
    except (FileNotFoundError, OSError, KeyError):
        return None          # model not built -> keyword search only


if __name__ == "__main__":
    print(build())
