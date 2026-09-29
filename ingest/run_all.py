"""Rebuild everything in data/processed from the PDFs in data/raw.

    python -m ingest.run_all

Takes ~3-4 min for the PDFs (mostly pdfplumber on the bulletin), plus a few minutes of embedding
training on CPU at the end.
"""
import time

from ingest import build_db, bulletin_courses, bulletin_programmes, handouts, regulations_rules, timetable

STEPS = [
    ("timetable + equivalents", timetable.main),
    ("bulletin programmes, HUEL pool, minors", lambda: (bulletin_programmes.build(), bulletin_programmes.build_minors())),
    ("bulletin course descriptions", bulletin_courses.build),
    ("handouts", handouts.build),
    ("regulation rules", regulations_rules.build),
    ("sqlite db + validation", build_db.build),
    # trains the LSA model, rebuilds the bge-small course embedding index, re-runs the retrieval evaluation
    ("embedding training (agent/train_embeddings.py)",
     lambda: __import__("agent.train_embeddings", fromlist=["main"]).main([])),
]


if __name__ == "__main__":
    for name, fn in STEPS:
        t = time.time()
        print(f"-> {name} ...", flush=True)
        fn()
        print(f"   done in {time.time() - t:.0f}s")
    print("all good, see data/processed/validation_report.md")
