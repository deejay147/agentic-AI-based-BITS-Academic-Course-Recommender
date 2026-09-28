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
learning field fields area areas domain domains stuff thing things kind type types sort based oriented
something anything subject subjects interested interest interests chap chapter chapters sect slide slides""".split())
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
    "biotech": ["biotechnology", "biology", "biological", "molecular", "genetics", "cell", "microbiology",
                "biochemistry", "genomics", "protein", "enzyme", "bioinformatics"],
    "biotechnology": ["biotechnology", "biology", "molecular", "genetics", "cell", "microbiology", "biochemistry",
                      "genomics", "protein", "enzyme", "bioinformatics"],
    "biology": ["biology", "biological", "cell", "genetics", "molecular", "ecology", "physiology"],
    "genetics": ["genetics", "gene", "genome", "dna", "molecular", "heredity"],
    "health": ["health", "healthcare", "medical", "medicine", "clinical", "disease", "hospital", "public_health"],
    "healthcare": ["health", "healthcare", "medical", "medicine", "clinical", "disease", "hospital", "biomedical"],
    "medicine": ["medicine", "medical", "clinical", "disease", "drug", "physiology", "pharmacology"],
    "medical": ["medical", "medicine", "clinical", "biomedical", "disease", "health"],
    "neuroscience": ["neuroscience", "neural", "brain", "cognitive", "neuron"],
    "space": ["space", "astronomy", "astrophysics", "cosmology", "satellite", "orbital", "planetary", "rocket"],
    "astronomy": ["astronomy", "astrophysics", "cosmology", "stars", "galaxies", "telescope", "planetary"],
    "physics": ["physics", "quantum", "mechanics", "electromagnetic", "optics", "thermodynamics"],
    "quantum": ["quantum", "quantum_mechanics", "quantum_computing", "qubit"],
    "chemistry": ["chemistry", "chemical", "organic", "inorganic", "reaction", "spectroscopy"],
    "maths": ["mathematics", "mathematical", "algebra", "calculus", "probability", "statistics"],
    "math": ["mathematics", "mathematical", "algebra", "calculus", "probability", "statistics"],
    "statistics": ["statistics", "statistical", "probability", "regression", "inference", "sampling"],
    "environment": ["environment", "environmental", "ecology", "climate", "pollution", "sustainability"],
    "climate": ["climate", "environment", "environmental", "carbon", "sustainability", "emissions"],
    "sustainability": ["sustainability", "sustainable", "environment", "renewable", "climate", "green"],
    "marketing": ["marketing", "consumer", "brand", "advertising", "market_research", "sales"],
    "startup": ["entrepreneurship", "venture", "startup", "innovation", "business_plan"],
    "startups": ["entrepreneurship", "venture", "startup", "innovation", "business_plan"],
    "entrepreneurship": ["entrepreneurship", "venture", "startup", "innovation", "business_plan"],
    "law": ["law", "legal", "intellectual_property", "patent", "rights", "constitution"],
    "design": ["design", "product_design", "creativity", "prototyping", "user"],
    "history": ["history", "historical", "civilization", "colonial", "modern_india"],
    "sociology": ["sociology", "society", "social", "culture", "anthropology"],
    "literature": ["literature", "literary", "novel", "poetry", "fiction", "drama"],
    "writing": ["writing", "creative_writing", "composition", "rhetoric", "communication"],
    "language": ["language", "linguistics", "grammar", "french", "german", "japanese"],
    "music": ["music", "musical", "raga", "rhythm", "composition"],
    "art": ["art", "arts", "painting", "aesthetics", "visual"],
    "blockchain": ["blockchain", "cryptocurrency", "distributed_ledger", "bitcoin", "smart_contracts"],
    "cloud": ["cloud", "cloud_computing", "distributed", "virtualization", "datacenter"],
    "games": ["game", "games", "gaming", "game_theory", "graphics"],
    "gaming": ["game", "games", "gaming", "graphics", "animation"],
    "materials": ["materials", "material", "composites", "polymers", "metallurgy", "nanomaterials"],
    "nano": ["nanotechnology", "nanomaterials", "nano", "nanoscale"],
    "drones": ["drone", "uav", "aerial", "flight", "aircraft", "control"],
    "aeronautics": ["aeronautics", "aerospace", "aircraft", "flight", "aerodynamics", "propulsion", "gas_dynamics"],
    "aerospace": ["aerospace", "aircraft", "flight", "aerodynamics", "propulsion", "space"],
    "automobile": ["automobile", "automotive", "vehicle", "engine", "ic_engines"],
    "cars": ["automobile", "automotive", "vehicle", "engine"],
}


_SUFFIXES = ("abilities", "ability", "ibility", "ations", "ation", "ments", "ment", "ings", "ing", "ities", "ity",
             "ical", "ics", "ic", "ies", "ive", "ness", "able", "ible", "al", "es", "s")


def stem(w: str) -> str:
    """tiny suffix stripper so 'sustainability' / 'sustainable' / 'sustain' meet. not linguistics,
    just enough for syllabus words; applied the same way to documents and queries"""
    if len(w) <= 4 or w in ("ai", "ml", "iot", "vlsi", "cfd"):
        return w
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokens(text: str) -> list[str]:
    """unigrams (minus stopwords) + bigrams of adjacent words, e.g. machine_learning (all stemmed)"""
    raw = re.findall(r"[a-z][a-z0-9+#]*", (text or "").lower())
    words = [stem(w) for w in raw]
    # remember a readable form for each stem so 'why it matches' can show real words
    for st_, w in zip(words, raw):
        SURFACE.setdefault(st_, w)
    for i in range(len(words) - 1):
        SURFACE.setdefault(f"{words[i]}_{words[i+1]}", f"{raw[i]} {raw[i+1]}")
    uni = [t for t in words if t not in _STOP_STEMS and len(t) > 1]
    bi = [f"{a}_{b}" for a, b in zip(words, words[1:]) if a not in _STOP_STEMS or b not in _STOP_STEMS]
    return uni + bi


_STOP_STEMS = {stem(w) for w in STOP}
SURFACE: dict[str, str] = {}


def readable(term: str) -> str:
    return SURFACE.get(term, term.replace("_", " "))


def _stem_term(t: str) -> str:
    return "_".join(stem(x) for x in t.split("_"))


def expand(query: str) -> list[str]:
    out = []
    raw = [w for w in re.findall(r"[a-z][a-z0-9+#]*", (query or "").lower())]
    for t in tokens(query):
        out.append(t)
    for w in raw:                      # synonym table is keyed on the plain word
        out.extend(_stem_term(x) for x in SYNONYMS.get(w, []))
    # spelled-out forms map back to the short keys too ('artificial intelligence' -> ai list)
    joined = " ".join(re.findall(r"[a-z]+", (query or "").lower()))
    for full, key in (("artificial intelligence", "ai"), ("machine learning", "ml"), ("deep learning", "dl"),
                      ("data science", "data"), ("natural language", "nlp"), ("computer vision", "cv"),
                      ("fluid dynamics", "cfd"), ("internet of things", "iot"), ("operations research", "optimization")):
        if full in joined:
            out.extend(_stem_term(x) for x in SYNONYMS[key])
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
            hits.append(readable(t))
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


# ---------------------------------------------------------------------------- topic matching (hybrid)

# what each department prefix is about - lets 'finance' find the FIN courses even when a course text is thin
DEPT_TOPICS = {
    "FIN": "finance financial", "ECON": "economics economic finance", "MGTS": "management business",
    "BIO": "biology biological biotech biotechnology genetics life", "PHA": "pharmacy pharmaceutical pharmacology drug",
    "CHEM": "chemistry chemical", "PHY": "physics", "MATH": "mathematics maths math", "CS": "computer computing software programming",
    "EEE": "electrical electronics", "ECE": "electronics communication", "INSTR": "instrumentation", "ME": "mechanical",
    "CE": "civil construction", "CHE": "chemical process", "MF": "manufacturing production", "HSS": "humanities",
    "GS": "media communication writing", "ENVS": "environment environmental sustainability", "SNS": "nanoscience nano",
    "AN": "aeronautics aerospace flight aircraft", "MST": "materials", "DE": "design",
}
_GENERIC = {"minor", "in", "and", "of", "science", "sciences", "engineering", "technology", "technologies", "studies",
            "introduction", "the", "for", "devices", "information", "applied"}


def _stems(text: str) -> set:
    return {stem(w) for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in _GENERIC and len(w) > 2}


@lru_cache(maxsize=1)
def _anchors():
    """topic anchors from the Bulletin's own structure: each minor (name -> its courses) and each
    department prefix (topic words -> its courses). Used to boost, never to filter."""
    cat = get_catalog()
    out = []
    for name, m in cat.minors_by_name.items():
        codes = {cat.canon(r["code"]) for g in list(m["core"].values()) + list(m["electives"].values()) for r in g}
        out.append((name, _stems(name.replace("Minor in", "")), codes, 0.25))
    for dept, words in DEPT_TOPICS.items():
        codes = {cat.canon(c) for c in set(cat.courses) | set(cat.offerings) if c.split()[0] == dept}
        out.append((f"{dept} department", _stems(words), codes, 0.15))
    return out


class TopicMatch:
    """Scores courses against one topic: 0.4 x keyword (BM25, scaled to the best course in the timetable)
    + 0.6 x semantic similarity (agent/semantic.py), + a boost for courses of a minor / department whose
    name matches the topic. Weights picked on tests/eval_retrieval.py."""

    W_KW = 0.4

    def __init__(self, text: str):
        from agent.semantic import get_semantic
        self.text = text or ""
        self.q = expand(self.text)
        idx = get_index()
        self.idx = idx
        self.kw = {c: idx.score(c, self.q) for c in idx.docs} if self.q else {}
        self.kmax = max((s for s, _ in self.kw.values()), default=0) or 1.0
        sem = get_semantic()
        self.sim = sem.similarity(self.text, list(idx.docs)) if (sem and self.q) else {}
        qs = _stems(self.text)
        cat = get_catalog()
        self.cat = cat
        self.anchors = [(label, codes, boost) for label, words, codes, boost in _anchors() if qs & words]

    def score(self, code: str) -> dict:
        s, hits = self.kw.get(code, (0.0, []))
        sim = max(self.sim.get(code, 0.0), 0.0)
        total = self.W_KW * s / self.kmax + (1 - self.W_KW) * sim
        why = None
        cn = self.cat.canon(code)
        for label, codes, boost in self.anchors:
            if cn in codes:
                total += boost
                why = label
                break
        return {"score": total, "kw": s, "hits": hits, "sim": sim, "anchor": why}

    def relevant(self, m: dict) -> bool:
        """a real match: some keyword hit or clear semantic similarity, or it's in a matching minor/department"""
        return m["anchor"] is not None or (m["score"] >= 0.12 and (m["kw"] > 0 or m["sim"] >= 0.18))
