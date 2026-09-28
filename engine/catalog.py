"""Read-only view over data/processed/academic.db, loaded once into plain dicts.

Everything the engine needs is small enough to just keep in memory (few MB),
so no ORM, no per-request queries. `get_catalog()` caches it.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "academic.db"

PROJECT_RE = re.compile(r" [A-Z](266|366|367|376|377|491)$")
# practice school / thesis / dissertation style courses - not something we "recommend"
NON_RECOMMENDABLE_RE = re.compile(r"^BITS (F221|F231|F241|F412|F413|G639|G560)|T$|^BITS E\d|^BITS C7")


def _rows(db, sql, *args):
    cur = db.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class Catalog:
    def __init__(self, db_path=DB_PATH):
        db = sqlite3.connect(db_path)
        self.courses = {r["code"]: r for r in _rows(db, "SELECT * FROM courses")}
        for c in self.courses.values():
            c["prerequisite_codes"] = json.loads(c["prerequisite_codes"] or "[]")
            c["source"] = json.loads(c["source"] or "null")

        self._canon = {r["code"]: r["canonical"] for r in _rows(db, "SELECT * FROM equivalents")}

        # timetable: code -> list of offering rows (usually 1, 2 when there's a 2026-only duplicate)
        self.offerings = defaultdict(list)
        secs = defaultdict(list)
        for s in _rows(db, "SELECT * FROM sections"):
            s["instructors"] = json.loads(s["instructors"])
            s["slots"] = json.loads(s["slots"])
            secs[(s["comcod"], s["code"])].append(s)
        for o in _rows(db, "SELECT * FROM offerings"):
            o["source"] = json.loads(o["source"])
            o["sections"] = secs.get((o["comcod"], o["code"]), [])
            self.offerings[o["code"]].append(o)

        # handouts, looked up by any of their codes
        self.handouts = {}
        hrows = {h["id"]: h for h in _rows(db, "SELECT * FROM handouts")}
        for h in hrows.values():
            h["components"] = json.loads(h["components"] or "[]")
        for r in _rows(db, "SELECT * FROM handout_codes"):
            self.handouts.setdefault(r["code"], hrows[r["handout_id"]])

        self.programmes = {p["id"]: p for p in _rows(db, "SELECT * FROM programmes")}
        for p in self.programmes.values():
            p["notes"] = json.loads(p["notes"])
            p["verification"] = json.loads(p["verification"])
            p["source"] = json.loads(p["source"])
            p["chart_positions"] = json.loads(p["chart_positions"] or "{}")
            p.update({"cdc_groups": [], "del_codes": {}, "gir_codes": [], "compulsory_del": []})
        groups = defaultdict(list)
        for r in _rows(db, "SELECT * FROM programme_courses"):
            groups[(r["programme_id"], r["category"], r["group_id"])].append(r)
        for (pid, cat, _), members in groups.items():
            p = self.programmes[pid]
            if cat == "CDC":
                p["cdc_groups"].append(members)
            elif cat == "DEL":
                for m in members:
                    p["del_codes"][m["code"]] = m
                    if m["compulsory"]:
                        p["compulsory_del"].append(m["code"])
            else:
                p["gir_codes"].append(members[0]["code"])

        meta = {r["key"]: r["value"] for r in _rows(db, "SELECT * FROM meta")}
        self.gir_structure = json.loads(meta["gir_structure"])
        self.gir_alternatives = json.loads(meta["gir_alternatives"])
        self.minor_rules = json.loads(meta["minor_rules"])
        self.huel_rule = meta["huel_rule"]
        self.huel = {r["code"]: r for r in _rows(db, "SELECT * FROM huel_pool")}

        self.minors = {}
        for m in _rows(db, "SELECT * FROM minors"):
            m["source"] = json.loads(m["source"])
            m["core"], m["electives"] = defaultdict(list), defaultdict(list)
            self.minors[m["id"]] = m
        for r in _rows(db, "SELECT * FROM minor_courses"):
            m = self.minors[r["minor_id"]]
            (m["core"] if r["category"] == "core" else m["electives"])[r["group_id"]].append(r)
        self.minors_by_name = {m["name"]: m for m in self.minors.values()}

        self.rules = {r["id"]: r for r in _rows(db, "SELECT * FROM rules")}
        for r in self.rules.values():
            r["params"] = json.loads(r["params"] or "null")

        # every code that is a CDC/DEL of *some* programme -> which programmes. needed for reg 3.15(b)(i)
        self.discipline_owner = defaultdict(set)
        for pid, p in self.programmes.items():
            for g in p["cdc_groups"]:
                for m in g:
                    self.discipline_owner[m["code"]].add(pid)
            for c in p["del_codes"]:
                self.discipline_owner[c].add(pid)
        db.close()

    # ------------------------------------------------------------------ helpers
    def canon(self, code: str) -> str:
        """Collapse equivalent / old codes to one canonical code (timetable section IX)."""
        return self._canon.get(code, code)

    def same(self, a: str, b: str) -> bool:
        return self.canon(a) == self.canon(b)

    def title(self, code: str) -> str | None:
        c = self.courses.get(code)
        if c and c["title"]:
            return c["title"]
        offs = self.offerings.get(code)
        return offs[0]["title"].title() if offs else None

    def units(self, code: str) -> int | None:
        c = self.courses.get(code)
        if c and c["units"]:
            return c["units"]
        for o in self.offerings.get(code, []):
            if (o["units"] or "").isdigit() and not o["only_2026_admits"]:
                return int(o["units"])
        return None

    def handout(self, code: str):
        if code in self.handouts:
            return self.handouts[code]
        # fall back to an equivalent code's handout (e.g. cross-listed EEE/INSTR)
        for other, cn in self._canon.items():
            if cn == self.canon(code) and other in self.handouts:
                return self.handouts[other]
        return None

    def programme_depts(self, pid: str) -> set[str]:
        """Department prefixes that make up a programme's 'own discipline' (from its CDCs)."""
        counts = defaultdict(int)
        for g in self.programmes[pid]["cdc_groups"]:
            for m in g:
                counts[m["code"].split()[0]] += 1
        return {d for d, n in counts.items() if n >= 2}


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()
