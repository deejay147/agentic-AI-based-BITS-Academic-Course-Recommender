"""Rule-based query understanding - used when there's no API key (and as a sanity check).

Turns 'Suggest an AI related DEL with no midsem and lenient makeup' into
    {intent: recommend, categories: [DEL], topics: 'ai', require: [no_midsem, lenient_makeup]}

Deliberately simple: keyword patterns for the things the task lists (category, handout
properties, time preferences) and whatever words are left over are the topic.
"""
from __future__ import annotations

import re

from ingest.common import norm_code

CATEGORY_PATTERNS = [
    ("DEL", r"\bdels?\b|discipline\s+electives?|departmental\s+electives?"),
    ("OPEL", r"\bopels?\b|open\s+electives?"),
    ("HUEL", r"\bhuels?\b|humanities|hum\s+electives?"),
    ("CDC", r"\bcdcs?\b|\bcore\b|compulsory\s+courses?"),
]
PROPERTY_PATTERNS = [
    ("no_midsem", r"no\s+mid[\s-]?sems?|without\s+(a\s+)?mid[\s-]?sems?|mid[\s-]?sem\s*free|skip\s+mid[\s-]?sem"),
    ("no_compre", r"no\s+compre|without\s+(a\s+)?compre|no\s+(final|end[\s-]?sem)"),
    ("no_attendance_requirement", r"(no|without|zero|not?\s+\w+\s+)\s*attendance|attendance\s+(not\s+)?(required|mandatory|compulsory)\s*(\?|$)?|no\s+attendance"),
    ("lenient_makeup", r"lenient\s+make[\s-]?ups?|easy\s+make[\s-]?ups?|make[\s-]?ups?\s+(policy\s+)?(is\s+)?(lenient|easy|flexible|relaxed)|flexible\s+make[\s-]?up"),
    ("project_based", r"project[\s-]?based|projects?|term\s+paper"),
    ("no_quiz", r"no\s+quiz(zes)?|without\s+quiz"),
    ("no_lab", r"no\s+labs?|without\s+(a\s+)?labs?|no\s+practicals?"),
    ("has_lab", r"(with|has|having)\s+(a\s+)?labs?|hands[\s-]?on\s+lab"),
    ("open_book", r"open[\s-]?book"),
]
DAYS = {"monday": "M", "tuesday": "T", "wednesday": "W", "thursday": "Th", "friday": "F", "saturday": "S"}
FILLER = r"\b(suggest|recommend|find|show|give|list|want|need|prefer|looking|some|any|good|best|courses?|electives?|" \
         r"related|to|for|with|and|a|an|the|me|i|in|on|about|that|which|has|have|having|is|are|of|evaluation|" \
         r"policy|requirement|requirements|this|semester|please|can|could|you|also|like|interested|based|" \
         r"no|without|lenient|easy|makeup|make-up|make|up|midsem|mid-sem|mid|sem|attendance|compre|free|" \
         r"classes|class|am|8|8am|keep|my|day|days|off|what|take|together|prerequisites?|prereqs?|of|do|does|i|need)\b"


def parse(query: str) -> dict:
    q = query.strip()
    low = q.lower()
    out = {"intent": "recommend", "categories": [], "require": [], "topics": "", "codes": [],
           "no_8am": False, "free_day": None}

    codes = []
    for d, n in re.findall(r"\b([A-Za-z]{2,5})\s*[-_ ]?\s*([A-Za-z]\s?\d{3}[A-Za-z]?)\b", q):
        c = norm_code(f"{d} {n.replace(' ', '')}")
        if c and c not in codes:
            codes.append(c)
    out["codes"] = codes

    if codes:
        if len(codes) > 1 or re.search(r"\b(plan|take\s+together|all\s+of|clash|schedule|timetable)\b", low):
            out["intent"] = "plan"
        else:
            out["intent"] = "details"
    if re.search(r"pre[\s-]?req", low):
        out["intent"] = "details" if codes else out["intent"]
        out["asks_prerequisites"] = True

    for cat, pat in CATEGORY_PATTERNS:
        if re.search(pat, low):
            out["categories"].append(cat)
    for name, pat in PROPERTY_PATTERNS:
        if re.search(pat, low):
            out["require"].append(name)
    # 'what's left / remaining requirements' - but not 'no attendance requirement'
    if not codes and not out["require"] and not out["categories"] and \
            re.search(r"\b(remaining|left|still\s+need|requirements?|graduat\w*|how\s+many)\b", low):
        out["intent"] = "requirements"
    if "has_lab" in out["require"] and "no_lab" in out["require"]:
        out["require"].remove("has_lab")
    if re.search(r"no\s+8\s*(am)?|no\s+early|after\s+9|nothing\s+at\s+8", low):
        out["no_8am"] = True
    m = re.search(r"(monday|tuesday|wednesday|thursday|friday|saturday)s?\s+(free|off)|free\s+(on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday)", low)
    if m:
        out["free_day"] = DAYS[m.group(1) or m.group(4)]

    # topic = what's left after removing everything we understood
    t = low
    for _, pat in CATEGORY_PATTERNS + PROPERTY_PATTERNS:
        t = re.sub(pat, " ", t)
    for c in codes:
        t = t.replace(c.lower(), " ")
    t = re.sub(r"(monday|tuesday|wednesday|thursday|friday|saturday)s?", " ", t)
    t = re.sub(FILLER, " ", t)
    t = re.sub(r"[^a-z0-9+# ]", " ", t)
    out["topics"] = re.sub(r"\s+", " ", t).strip()
    return out
