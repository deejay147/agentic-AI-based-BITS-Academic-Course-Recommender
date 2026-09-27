"""Small shared stuff for the ingest scripts - paths, course code cleanup, json io."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
PROCESSED.mkdir(parents=True, exist_ok=True)

# Source documents (file names inside data/raw)
TIMETABLE_PDF = RAW / "timetable.pdf"
BULLETIN_PDF = RAW / "bulletin.pdf"
REGULATIONS_PDF = RAW / "regulations.pdf"
HANDOUT_DIR = RAW / "handouts"

_CODE_RE = re.compile(r"^\s*([A-Z]{2,5})\s*[-_ ]?\s*([A-Z])\s*(\d{3})([A-Z]?)\s*$")


def norm_code(raw: str) -> str | None:
    """Normalise a course code to 'DEPT X123' form (e.g. 'cs_f407' -> 'CS F407').

    Returns None if the string does not look like a course code.
    """
    if raw is None:
        return None
    s = raw.strip().upper().replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    m = _CODE_RE.match(s)
    if not m:
        return None
    dept, level, num, suffix = m.groups()
    return f"{dept} {level}{num}{suffix}"


def dump_json(obj, name: str) -> Path:
    path = PROCESSED / name
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    return path


def load_json(name: str):
    return json.loads((PROCESSED / name).read_text())
