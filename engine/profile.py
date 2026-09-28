"""Student profile + BITS ID parsing.

ID format (bulletin III-55): 2025A7PS0147P
    2025 -> admission year (batch)
    A7   -> first degree code (A7 = B.E. Computer Science)
    PS   -> Practice School stream (TS = thesis, RM/UB/.. = collaborative). For a dual
            degree this slot holds the second degree code instead, e.g. 2024B3A70123P
    0147 -> serial
    P    -> campus (P Pilani, G Goa, H Hyderabad, D Dubai)

The only timetable we have is First Semester 2026-27, so year of study = 2026 - batch + 1
and the semester being planned is always the first one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TIMETABLE_ACAD_YEAR = 2026        # First Semester 2026-27
TIMETABLE_SEMESTER = 1
CURRICULUM_BATCH = 2025           # the supplied bulletin is the 2025-26 one

ID_RE = re.compile(r"^(\d{4})([A-D][0-9A-Z])([A-Z][0-9A-Z])(\d{3,4})([PGHD])$")
CAMPUS = {"P": "Pilani", "G": "Goa", "H": "Hyderabad", "D": "Dubai"}

# grades that mean the course is cleared (reg 4.11: any letter grade, or a non-letter grade)
CLEARED_GRADES = {"A", "A-", "B", "B-", "C", "C-", "D", "E", "GOOD", "POOR", "CLR", "S", "SATISFACTORY",
                  "EXCELLENT", "FAIR", "ABOVE AVERAGE", "AVERAGE", "BELOW AVERAGE"}
# reports that are NOT a grade (reg 4.12): incomplete, grade awaited, withdrawn, reg cancelled, not cleared...
NOT_CLEARED = {"NC", "W", "I", "GA", "RC", "RRA", "DP", "TGA", "AC"}


# the slot after the first degree code: stream, or the second degree for dual degree students
STREAMS = {"PS": "PS", "TS": "TS", "CS": "CSP", "RM": "RMIT", "UB": "UB", "IS": "ISU", "RP": "RPI"}


def parse_id(id_no: str, known_programmes: set[str]) -> dict:
    s = (id_no or "").strip().upper().replace(" ", "")
    m = ID_RE.match(s)
    if not m:
        raise ValueError(f"'{id_no}' doesn't look like a BITS ID (e.g. 2025A7PS0147P)")
    batch, p1, p2, _, campus = m.groups()
    progs, stream = [p1], STREAMS.get(p2)
    if stream is None and (p2 in known_programmes or p2 == "C2"):
        progs.append(p2)          # dual degree, second code sits where PS/TS normally is
    return {"batch": int(batch), "programmes": progs, "stream": stream, "campus": CAMPUS[campus], "raw": s}


@dataclass
class CourseRecord:
    code: str
    grade: str | None = None      # optional; no grade given = assume cleared

    @property
    def cleared(self) -> bool:
        if not self.grade:
            return True
        g = self.grade.strip().upper()
        return g not in NOT_CLEARED


@dataclass
class Profile:
    id_no: str | None = None
    batch: int = CURRICULUM_BATCH
    programmes: list[str] = field(default_factory=list)   # 1 code, or 2 for dual degree
    campus: str = "Pilani"
    completed: list[CourseRecord] = field(default_factory=list)
    current: list[str] = field(default_factory=list)       # registered this semester
    minor: str | None = None
    interests: str = ""
    cgpa: float | None = None
    name: str | None = None
    stream: str | None = None                               # PS / TS / CSP (2+2 CentraleSupelec) ...

    @property
    def year(self) -> int:
        return TIMETABLE_ACAD_YEAR - self.batch + 1

    @property
    def semester_label(self) -> str:
        return f"Year {self.year}, Semester {TIMETABLE_SEMESTER} (First Semester 2026-27)"

    @property
    def curriculum_note(self) -> str | None:
        if self.batch == CURRICULUM_BATCH:
            return None
        return (f"Programme rules come from the supplied Bulletin, which is for the 2025-26 curriculum. "
                f"Your batch ({self.batch}) may follow a different structure; results are shown using the "
                f"2025-26 rules.")

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        comp = [CourseRecord(c["code"], c.get("grade")) if isinstance(c, dict) else CourseRecord(c)
                for c in d.get("completed", [])]
        return cls(id_no=d.get("id_no"), batch=int(d.get("batch", CURRICULUM_BATCH)),
                   programmes=list(d.get("programmes", [])), campus=d.get("campus", "Pilani"),
                   completed=comp, current=list(d.get("current", [])), minor=d.get("minor"),
                   interests=d.get("interests", ""), cgpa=d.get("cgpa"), name=d.get("name"),
                   stream=d.get("stream"))

    def to_dict(self) -> dict:
        return {"id_no": self.id_no, "name": self.name, "batch": self.batch, "programmes": self.programmes,
                "campus": self.campus, "completed": [{"code": c.code, "grade": c.grade} for c in self.completed],
                "current": self.current, "minor": self.minor, "interests": self.interests, "cgpa": self.cgpa,
                "stream": self.stream}
