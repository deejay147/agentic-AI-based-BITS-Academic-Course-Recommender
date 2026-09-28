"""Course search over topics + handout properties.

Two parts:
  - a small BM25 index over (title + bulletin description + handout topics/lecture plan)
    for interest matching. No model download, deterministic. When an API key is set the
    agent layer lets Claude expand the student's interests and re-rank, so matching can still
    be semantic - this is just the recall step.
  - handout property checks (no midsem, attendance, makeup, project based, ...). Each check
    returns yes / no / unknown plus the evidence, never a guess.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from engine.catalog import Catalog, get_catalog

STOP = set("""a an the and or of in on for to with without from by at as is are be this that these those
course courses i me my want need like prefer suggest recommend some any which what please give show find
related about into also can could would should do does have has semester elective electives study studies
introduction intro basic basics fundamentals principles
outcome outcomes objective objectives lecture lectures chapter ch tb text book books reference references clo
students student able understand understanding week weeks topic topics module modules unit units hours
learning""".split())
# 'learning' alone is useless (every handout has 'Course Learning Outcomes'), but it survives inside
# bigrams like machine_learning / deep_learning, which is what we actually want to match

# a few common interest words expanded to what shows up in syllabi. only used for recall;
# with an API key Claude does the real expansion
SYNONYMS = {
    "ai": ["artificial_intelligence", "machine_learning", "deep_learning", "neural_networks", "neural_network",
           "reinforcement_learning", "intelligent", "agents"],
    "ml": ["machine_learning", "neural_networks", "classification", "regression", "supervised"],
    "dl": ["deep_learning", "neural_networks", "convolutional"],
    "data": ["data", "analytics", "mining", "statistics", "data_science"],
    "finance": ["finance", "financial", "investment", "portfolio", "banking", "markets"],
    "robotics": ["robot", "robotics", "kinematics", "automation", "control"],
    "bio": ["biology", "biological", "molecular", "cell", "genetics"],
    "security": ["security", "cryptography", "network", "attacks"],
    "nlp": ["natural_language", "language_processing", "text"],
    "cv": ["computer_vision", "image_processing", "image"],
    "cfd": ["computational_fluid", "fluid_dynamics", "fluid", "turbulence"],
    "vlsi": ["vlsi", "cmos", "integrated_circuits", "digital_design"],
    "iot": ["internet_of", "sensors", "embedded", "wireless"],
    "web": ["web", "internet", "html", "javascript", "server", "client"],
    "networks": ["network", "networks", "networking", "protocols", "tcp"],
    "economics": ["economics", "economic", "microeconomics", "macroeconomics", "markets"],
    "management": ["management", "managerial", "organisational", "organizational", "strategy", "business"],
    "business": ["business", "management", "marketing", "entrepreneurship", "strategy"],
    "psychology": ["psychology", "psychological", "behaviour", "behavior", "cognitive"],
    "philosophy": ["philosophy", "philosophical", "ethics", "logic"],
    "film": ["film", "cinema", "media", "video"],
    "media": ["media", "journalism", "communication", "advertising"],
    "energy": ["energy", "renewable", "solar", "power"],
    "optimization": ["optimization", "optimisation", "linear_programming", "operations_research"],
    "embedded": ["embedded", "microcontroller", "microprocessor", "real_time"],
    "pharmacology": ["pharmacology", "drug", "drugs", "pharmacokinetics", "therapeutic"],
    "security": ["security", "cryptography", "secure", "attacks", "network_security"],
}


def tokens(text: str) -> list[str]:
    """unigrams (minus stopwords) + bigrams of adjacent words, e.g. machine_learning"""
    words = re.findall(r"[a-z][a-z0-9+#]*", (text or "").lower())
    uni = [t for t in words if t not in STOP and len(t) > 1]
    bi = [f"{a}_{b}" for a, b in zip(words, words[1:]) if a not in STOP or b not in STOP]
    return uni + bi


def expand(query: str) -> list[str]:
    out = []
    for t in tokens(query):
        out.append(t)
        out.extend(SYNONYMS.get(t, []))
    # spelled-out forms map back to the short keys too ('artificial intelligence' -> ai list)
    joined = " ".join(re.findall(r"[a-z]+", (query or "").lower()))
    for full, key in (("artificial intelligence", "ai"), ("machine learning", "ml"), ("deep learning", "dl"),
                      ("data science", "data"), ("natural language", "nlp"), ("computer vision", "cv"),
                      ("fluid dynamics", "cfd"), ("internet of things", "iot"), ("operations research", "optimization")):
        if full in joined:
            out.extend(SYNONYMS[key])
    return out


class CourseIndex:
    """BM25 over one document per course code."""

    def __init__(self, cat: Catalog):
        self.docs = {}
        for code in cat.offerings:
            c = cat.courses.get(code) or {}
            h = cat.handout(code)
            title = cat.title(code) or ""
            # title counted 3x - a word in the title matters more than one in week 9 of the plan
            text = " ".join([title] * 3 + [c.get("description") or "", (h or {}).get("topics_text") or ""])
            self.docs[code] = Counter(tokens(text))
        self.N = len(self.docs)
        self.avgdl = sum(sum(d.values()) for d in self.docs.values()) / max(1, self.N)
        df = Counter()
        for d in self.docs.values():
            df.update(d.keys())
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def score(self, code: str, q: list[str], k1=1.2, b=0.75) -> tuple[float, list[str]]:
        d = self.docs.get(code)
        if not d:
            return 0.0, []
        dl = sum(d.values())
        s, hits = 0.0, []
        for t in set(q):
            f = d.get(t, 0)
            if not f:
                continue
            hits.append(t)
            s += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avgdl))
        return s, hits


@lru_cache(maxsize=1)
def get_index() -> CourseIndex:
    return CourseIndex(get_catalog())


# ---------------------------------------------------------------------------- handout properties

NO_INFO = "No specific information mentioned in the handout; contact the Instructor-in-Charge{ic}."
NO_HANDOUT = "No handout was supplied for this course; contact the Instructor-in-Charge{ic}."

STRICT_MAKEUP = r"hospital|medical\s+certificate|certificate|proof|extremely|serious|only\s+in\s+(case\s+of\s+)?genuine|strictly|documentary|exceptional|very\s+strong|strong\s+reason|not\s+(given\s+)?as\s+a\s+routine"
NO_MAKEUP_PART = r"no\s+(separate\s+)?make[\s-]*ups?|make[\s-]*ups?\s+(\w+\s+)?(will|shall)\s+not\s+be|not\s+be\s+(given|granted|allowed)"


def _ic(cat: Catalog, code: str) -> str:
    offs = cat.offerings.get(code)
    ic = offs[0].get("ic") if offs else None
    return f" ({ic.title()})" if ic else ""


def prop(cat: Catalog, code: str, name: str) -> dict:
    """value: True / False / None(unknown). evidence: quote or reason. source: where it came from."""
    h = cat.handout(code)
    offs = cat.offerings.get(code) or [{}]
    off = offs[0]
    ic = _ic(cat, code)
    src = {"doc": h["file"]} if h else None

    if name == "no_midsem":
        slot = off.get("midsem_date")
        if slot:
            return {"value": False, "evidence": f"Timetable lists a midsem on {slot} ({off.get('midsem_session')}).",
                    "source": off.get("source")}
        if h and h["eval_confidence"] == "evaluation_section":
            if h["has_midsem"]:
                return {"value": False, "evidence": "Handout evaluation scheme has a mid-semester component.",
                        "source": src}
            return {"value": True, "evidence": "No midsem slot in the timetable and no mid-semester component in "
                                               "the handout's evaluation scheme.", "source": src}
        return {"value": True, "evidence": "No midsem slot in the timetable (no handout to confirm).",
                "source": off.get("source")}

    if name == "no_compre":
        if off.get("compre_date"):
            return {"value": False, "evidence": f"Timetable lists a compre on {off['compre_date']}.",
                    "source": off.get("source")}
        if h and h["eval_confidence"] == "evaluation_section" and not h["has_compre"]:
            return {"value": True, "evidence": "No compre slot in the timetable and none in the handout.", "source": src}
        return {"value": None, "evidence": NO_INFO.format(ic=ic) if h else NO_HANDOUT.format(ic=ic), "source": src}

    if not h:
        return {"value": None, "evidence": NO_HANDOUT.format(ic=ic), "source": None}

    if name == "no_attendance_requirement":
        st = h["attendance_status"]
        if st == "not_required":
            return {"value": True, "evidence": h["attendance_evidence"], "source": src}
        if st in ("required", "expected"):
            return {"value": False, "evidence": h["attendance_evidence"], "source": src}
        if st == "mentioned":
            return {"value": None, "evidence": "Handout mentions attendance but doesn't say whether it is "
                                               f"required: \"{h['attendance_evidence'][:200]}\"", "source": src}
        return {"value": None, "evidence": NO_INFO.format(ic=ic), "source": src}

    if name == "lenient_makeup":
        st, ev = h["makeup_status"], h["makeup_evidence"] or ""
        if st in ("available", "restricted") and (h.get("makeup_note") or re.search(NO_MAKEUP_PART, ev, re.I)):
            # 'no make-up for the lab / quizzes' - partly no make-up, not what anyone means by lenient
            return {"value": False, "evidence": ev, "source": src, "level": "no make-up for some components"}
        if st == "available":
            return {"value": True, "evidence": ev, "source": src, "level": "available"}
        if st == "restricted":
            strict = bool(re.search(STRICT_MAKEUP, ev, re.I))
            return {"value": not strict, "evidence": ev, "source": src,
                    "level": "strict conditions" if strict else "with prior permission / genuine reason"}
        if st == "none":
            return {"value": False, "evidence": ev, "source": src, "level": "no make-up"}
        return {"value": None, "evidence": NO_INFO.format(ic=ic), "source": src}

    flags = {"project_based": "has_project", "has_quiz": "has_quiz", "has_lab": "has_lab",
             "open_book": "open_book", "has_assignment": "has_assignment"}
    if name in flags or name in ("no_quiz", "no_lab"):
        key = flags.get(name) or {"no_quiz": "has_quiz", "no_lab": "has_lab"}[name]
        if h["eval_confidence"] != "evaluation_section":
            return {"value": None, "evidence": NO_INFO.format(ic=ic), "source": src}
        v = bool(h[key])
        if name.startswith("no_"):
            v = not v
        ev = "Evaluation: " + ", ".join(f"{c['name']} {c['weight']:g}%" for c in h["components"]) \
            if h["components_reliable"] else (h["eval_evidence"] or "")[:300]
        out = {"value": v, "evidence": ev, "source": src}
        if name == "project_based" and h["project_weight"]:
            out["weight"] = h["project_weight"]
        return out

    raise ValueError(f"unknown property {name}")


PROPERTIES = ["no_midsem", "no_compre", "no_attendance_requirement", "lenient_makeup", "project_based",
              "has_quiz", "no_quiz", "has_lab", "no_lab", "open_book", "has_assignment"]


def handout_summary(cat: Catalog, code: str) -> dict:
    """Everything the UI/agent might want to show about a course's handout."""
    h = cat.handout(code)
    ic = _ic(cat, code)
    if not h:
        return {"available": False, "message": NO_HANDOUT.format(ic=ic)}
    return {
        "available": True, "file": h["file"], "instructor_in_charge": h["instructor_in_charge"],
        "evaluation": h["components"] if h["components_reliable"] else None,
        "evaluation_text": None if h["components_reliable"] else (h["eval_evidence"] or "")[:500],
        "midsem": h["has_midsem"], "compre": h["has_compre"], "quiz": h["has_quiz"], "project": h["has_project"],
        "lab": h["has_lab"], "open_book": h["open_book"],
        "makeup": {"status": h["makeup_status"], "text": h["makeup_evidence"] or NO_INFO.format(ic=ic)},
        "attendance": {"status": h["attendance_status"], "text": h["attendance_evidence"] or NO_INFO.format(ic=ic)},
    }
