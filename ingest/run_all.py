"""Rebuild everything in data/processed from the PDFs in data/raw.

    python -m ingest.run_all

Takes ~3-4 min, most of it is pdfplumber chewing through the bulletin.
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
    ("semantic course model (agent/semantic.py)", lambda: __import__("agent.semantic", fromlist=["build"]).build()),
    ("course embeddings, bge-small (agent/embeddings.py; skipped if fastembed isn't installed)",
     lambda: _try_embeddings()),
]


def _try_embeddings():
    try:
        from agent import embeddings
        print("  ", embeddings.build())
    except ImportError:
        print("   fastembed not installed - keeping the committed data/processed/embeddings.npz")

if __name__ == "__main__":
    for name, fn in STEPS:
        t = time.time()
        print(f"-> {name} ...", flush=True)
        fn()
        print(f"   done in {time.time() - t:.0f}s")
    print("all good, see data/processed/validation_report.md")
