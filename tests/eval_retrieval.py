"""How good is interest matching? A small offline evaluation.

Ground truth comes from the Bulletin itself, not from me: each of the 23 minors is a topic ("Finance",
"Robotics and Automation", ...) with a list of courses the departments put under it. The minor's name
is the query, and its offered courses are the relevant answers. Every method ranks ALL offered courses
(no eligibility filter), and we report:

    P@5 / P@10   how many of the top 5 / 10 are relevant
    R@10         how much of the minor shows up in the top 10
    MRR          1 / rank of the first relevant course

The structure boost the app uses on top (courses of a matching minor / department) is left out here
on purpose - it would just be reading the answers.

    python -m tests.eval_retrieval          (writes docs/retrieval_eval.md)
"""
from __future__ import annotations

from pathlib import Path

from agent.retrieval import expand, get_index
from agent.semantic import get_semantic
from engine.catalog import get_catalog

OUT = Path(__file__).resolve().parent.parent / "docs" / "retrieval_eval.md"


def _emb(q, codes):
    from agent.embeddings import get_embeddings
    e = get_embeddings()
    raw = e.raw_similarity(q)
    return {c: float(raw[e.row[c]]) if c in e.row else 0.0 for c in codes}


def _final(q, codes):
    """the app's course score without the Bulletin-group boost (that would be reading the answers)"""
    from agent.retrieval import TopicMatch
    tm = TopicMatch(q)
    tm.anchors = []
    return {c: tm.score(c)["score"] for c in codes}


def queries(cat):
    out = []
    for name, m in sorted(cat.minors_by_name.items()):
        codes = {r["code"] for g in list(m["core"].values()) + list(m["electives"].values()) for r in g}
        rel = {c for c in cat.offerings if any(cat.same(c, x) for x in codes)}
        if len(rel) >= 3:
            out.append((name.replace("Minor in ", ""), rel))
    return out


def rank(scores: dict) -> list[str]:
    return [c for c, s in sorted(scores.items(), key=lambda t: (-t[1], t[0])) if s > 0]


def metrics(ranked, rel):
    p5 = sum(c in rel for c in ranked[:5]) / 5
    p10 = sum(c in rel for c in ranked[:10]) / 10
    r10 = sum(c in rel for c in ranked[:10]) / len(rel)
    mrr = next((1 / (i + 1) for i, c in enumerate(ranked) if c in rel), 0.0)
    return p5, p10, r10, mrr


def hybrid_scores(q, codes, idx, sem, w_kw=0.4):
    """what the app uses: w * BM25 (scaled to the best match) + (1 - w) * semantic similarity"""
    qt = expand(q)
    kw = {c: idx.score(c, qt)[0] for c in codes}
    top = max(kw.values()) or 1.0
    sim = sem.similarity(q, codes) if sem else {c: 0.0 for c in codes}
    return {c: w_kw * kw[c] / top + (1 - w_kw) * max(sim[c], 0.0) for c in codes}


# everyday wording -> the Bulletin group a student would expect (none of these use the group's own name)
PARAPHRASES = [
    ("investing and stock markets", "Minor in Finance"), ("finance", "Minor in Finance"),
    ("biotech", "BIO department"), ("genetic engineering and cell biology", "BIO department"),
    ("robots and automation", "Minor in Robotics and Automation"), ("movies and journalism", "Minor in Film and Media"),
    ("starting a company", "Minor in Entrepreneurship"), ("startups", "Minor in Entrepreneurship"),
    ("machine learning and big data", "Minor in Data Science"), ("chip design and semiconductors",
    "Minor in Semiconductor Devices and Technology"), ("quantum computing", "Minor in Quantum Information and Technologies"),
    ("aircraft and flight", "Minor in Aeronautics"), ("novels and poetry", "Minor in English Studies"),
    ("government and policy making", "Minor in Public Policy"), ("logistics and supply chains",
    "Minor in Supply Chain Analytics"), ("nanotechnology", "Minor in Nanoscience and Nanobiotechnology"),
    ("drinking water and sanitation", "Minor in Water and Sanitation"), ("music", "MUSIC department"),
    ("business management", "Minor in Management"), ("medical devices and implants", "Minor in Biomedical Engineering"),
]


def topic_coverage():
    """does a plain-English query find the right Bulletin group, and how much of that group comes back?"""
    from agent.retrieval import TopicMatch, topic_groups
    cat = get_catalog()
    groups = {g["label"]: g for g in topic_groups()}
    rows = []
    for q, want in PARAPHRASES:
        tm = TopicMatch(q)
        got = [g["label"] for g in tm.groups]
        rel = {c for c in cat.offerings if cat.canon(c) in groups[want]["codes"]}
        shown = {c for c in cat.offerings if tm.relevant(tm.score(c))}
        if not rel:
            continue            # nothing of that group is offered this semester (e.g. MUSIC)
        cov = len(shown & rel) / len(rel)
        rows.append((q, want, want in got, cov, got))
    return rows


def main():
    cat = get_catalog()
    idx = get_index()
    sem = get_semantic()
    codes = sorted(cat.offerings)
    qs = queries(cat)
    methods = {
        "Keyword (BM25)": lambda q: {c: idx.score(c, expand(q))[0] for c in codes},
        "Semantic (LSA)": lambda q: sem.similarity(q, codes),
        "Keyword + LSA (0.4 / 0.6)": lambda q: hybrid_scores(q, codes, idx, sem, 0.4),
        "Embedding (bge-small)": lambda q: _emb(q, codes),
        "Keyword + LSA + embedding (0.25 / 0.15 / 0.6, used)": lambda q: _final(q, codes),
    }
    rows, per_q = [], {}
    for name, f in methods.items():
        ms = []
        for q, rel in qs:
            m = metrics(rank(f(q)), rel)
            ms.append(m)
            per_q.setdefault(q, {})[name] = m
        avg = [sum(x[i] for x in ms) / len(ms) for i in range(4)]
        rows.append((name, *avg))
    lines = ["# Interest matching: offline evaluation", "",
             f"Generated by `python -m tests.eval_retrieval`. {len(qs)} topics (the Bulletin's minors with at least "
             "3 offered courses); query = the minor's name, relevant = its offered courses; every method ranks all "
             f"{len(codes)} offered courses.", "",
             "| Method | P@5 | P@10 | R@10 | MRR |", "|---|---|---|---|---|"]
    lines += [f"| {n} | {a:.2f} | {b:.2f} | {c:.2f} | {d:.2f} |" for n, a, b, c, d in rows]
    lines += ["", "Per topic (P@5, keyword → used):", ""]
    for q, _ in qs:
        a = per_q[q]["Keyword (BM25)"][0]
        b = per_q[q]["Keyword + LSA + embedding (0.25 / 0.15 / 0.6, used)"][0]
        lines.append(f"- {q}: {a:.1f} → {b:.1f}")
    cov = topic_coverage()
    hit = sum(r[2] for r in cov) / len(cov)
    avg = sum(r[3] for r in cov) / len(cov)
    lines += ["", "## Plain-English topics", "",
              f"{len(cov)} queries in everyday words (not the group's name). *Group found* = the expected Bulletin group "
              "(minor / department) is among the matched groups. *Coverage* = share of that group's offered courses "
              "that come back (the app shows them all: the ones you can take, and the ones you can't yet with the reason).",
              "", f"**Group found: {hit:.0%} · average coverage: {avg:.0%}**", "",
              "| Query | Expected group | Found | Coverage | Matched groups |", "|---|---|---|---|---|"]
    lines += [f"| {q} | {w} | {'✅' if ok else '❌'} | {c:.0%} | {', '.join(g)[:90]} |" for q, w, ok, c, g in cov]
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:4 + len(rows) + 3]))


if __name__ == "__main__":
    main()
