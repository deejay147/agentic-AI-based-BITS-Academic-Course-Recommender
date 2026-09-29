"""Contextual sentence embeddings for courses (meaning + context, not just shared words).

Model: BAAI/bge-small-en-v1.5, a small (33M parameter, 384-d) retrieval model run through fastembed /
ONNX Runtime - no PyTorch, no GPU. It reads a whole phrase in context, so "investing in stocks" lands
next to Security Analysis and Portfolio Management even though they share no word.

Only the supplied dataset is embedded: each course's title + Bulletin description + handout lecture
plan. The course vectors are computed once (`python -m agent.embeddings`, also in ingest.run_all) and
saved in data/processed/embeddings.npz; at run time only the student's query is embedded (the model is
downloaded once, ~67 MB, into the local cache).

If fastembed isn't installed or the model can't be loaded (offline first run), get_embeddings() returns
None and matching falls back to the LSA model in agent/semantic.py - nothing breaks.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from engine.catalog import Catalog, get_catalog

MODEL = "BAAI/bge-small-en-v1.5"
PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "embeddings.npz"


def course_text(cat: Catalog, code: str) -> str:
    c = cat.courses.get(code) or {}
    h = cat.handout(code) or {}
    title = cat.title(code) or c.get("title") or code
    parts = [title + "."]
    if c.get("description"):
        parts.append(c["description"])
    if h.get("topics_text"):
        parts.append("Topics: " + h["topics_text"][:1200])
    return " ".join(parts)


def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL)


def build(cat: Catalog | None = None) -> dict:
    cat = cat or get_catalog()
    codes = sorted(set(cat.courses) | set(cat.offerings))
    vecs = np.array(list(_model().embed([course_text(cat, c) for c in codes], batch_size=64)), dtype=np.float32)
    vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
    np.savez_compressed(PATH, codes=np.array(codes), vecs=vecs.astype(np.float16), model=np.array(MODEL))
    return {"courses": len(codes), "dims": int(vecs.shape[1]), "model": MODEL}


class Embeddings:
    def __init__(self, path: Path = PATH):
        z = np.load(path, allow_pickle=False)
        self.codes = [str(c) for c in z["codes"]]
        self.row = {c: i for i, c in enumerate(self.codes)}
        self.V = z["vecs"].astype(np.float32)
        self.model = _model()          # raises if fastembed / the model isn't available

    @lru_cache(maxsize=256)
    def _q(self, text: str) -> tuple:
        v = np.array(list(self.model.query_embed([text]))[0], dtype=np.float32)
        return tuple(v / (np.linalg.norm(v) or 1.0))

    def embed(self, text: str) -> np.ndarray:
        return np.array(self._q(text), dtype=np.float32)

    def raw_similarity(self, text: str) -> np.ndarray:
        """cosine of the query with every course (same order as self.codes)"""
        return self.V @ self.embed(text)

    def similarity(self, text: str, codes: list[str]) -> dict[str, float]:
        """calibrated per query: bge cosines sit in a narrow band (~0.4-0.75), so rescale so the best
        course in the catalogue is 1 and the 90th percentile is 0 - only the clearly-closest courses score"""
        raw = self.raw_similarity(text)
        lo, hi = float(np.percentile(raw, 90)), float(raw.max())
        span = (hi - lo) or 1.0
        out = {}
        for c in codes:
            i = self.row.get(c)
            out[c] = float(np.clip((raw[i] - lo) / span, 0, 1)) if i is not None else 0.0
        return out


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings | None:
    try:
        return Embeddings()
    except Exception:      # no npz, no fastembed, no model download -> LSA fallback
        return None


if __name__ == "__main__":
    print(build())
