"""The deterministic tools the agent works with. Same functions back the Claude tool calls
and the no-API-key fallback, so both paths give the same (validated) answers.

Everything returned here is plain JSON-able dicts, with source references attached.
"""
from __future__ import annotations

from engine import eligibility as el
from engine import planner
from engine.catalog import Catalog, get_catalog
from engine.profile import Profile
from engine.schedule import check_course, busy_from_registered, pick_offering
from collections import Counter

from agent.retrieval import PROPERTIES, TopicMatch, expand, get_index, handout_summary, prop, readable

DAYS = ["M", "T", "W", "Th", "F", "S"]


def _hours_to_avoid(no_8am: bool = False, free_day: str | None = None) -> set:
    s = set()
    if no_8am:
        s |= {(d, 1) for d in DAYS}              # hour slot 1 = 8-8:50 AM
    if free_day:
        s |= {(free_day, h) for h in range(1, 13)}
    return s


class Session:
    """One student's context: profile + cached eligibility."""

    def __init__(self, profile: Profile, cat: Catalog | None = None):
        self.cat = cat or get_catalog()
        self.profile = profile
        self.ev = el.evaluate(profile, self.cat)
        self.state = self.ev["state"]
        self.eligible = {x["code"]: x for x in self.ev["eligible"]}
        self.ineligible = {x["code"]: x for x in self.ev["ineligible"]}

    # ------------------------------------------------------------------ requirement summary
    def get_requirements(self) -> dict:
        st = self.state
        progs = []
        for p in st["programmes"]:
            progs.append({
                "programme": f"{p['name']} ({p['id']})",
                "cdc_remaining": [" / ".join(g) for g in p["cdc"].get("remaining", [])],
                "cdc_in_progress": p["cdc"]["in_progress"],
                "del_required_courses": p["del"]["required_courses"],
                "del_done": p["del"]["done"], "del_in_progress": p["del"]["in_progress"],
                "del_remaining_courses": p["del"].get("courses_remaining"),
                "del_note": p["del"]["note"],
            })
        return {
            "student": {"id": self.profile.id_no, "semester": self.profile.semester_label,
                        "programmes": self.profile.programmes, "stream": self.profile.stream,
                        "minor": self.profile.minor},
            "notes": st["notes"],
            "gir_remaining": [" / ".join(g) for g in st["gir"].get("remaining", [])],
            "programmes": progs,
            "huel": {"required_courses": st["huel"]["required_courses"], "done": st["huel"]["done"],
                     "in_progress": st["huel"]["in_progress"], "remaining_courses": st["huel"].get("courses_remaining")},
            "opel": {"required_courses": st["opel"]["required_courses"], "done": st["opel"]["done"],
                     "in_progress": st["opel"]["in_progress"], "remaining_courses": st["opel"].get("courses_remaining"),
                     "note": st["opel"]["note"]},
            "minor": st.get("minor"),
            "registered_units": self.ev["registered_units"],
            "units_left_this_semester": max(0, 25 - self.ev["registered_units"]),
            "eligible_count": len(self.eligible),
            "graduation": st["graduation"],
            "not_cleared": st["not_cleared"],
        }

    # ------------------------------------------------------------------ search
    def can_count_as(self, x: dict) -> list[str]:
        cat = x["category"]
        if cat in ("CDC", "GIR"):
            return [cat]
        if cat == "DEL":
            return ["DEL", "OPEL"]
        if cat == "HUEL":
            return ["HUEL", "OPEL"]
        return ["OPEL"]

    def find_courses(self, categories: list[str] | None = None, topics: str | None = None,
                     require: list[str] | None = None, no_8am: bool = False, free_day: str | None = None,
                     include_projects: bool = False, limit: int = 8) -> dict:
        """Eligible courses, filtered by requirement category, handout properties and time prefs,
        ranked by topic match (or the profile's interests if no topic is given)."""
        cats = [c.upper() for c in (categories or [])]
        require = [r for r in (require or []) if r in PROPERTIES]
        topic_text = topics or self.profile.interests or ""
        tm = TopicMatch(topic_text) if expand(topic_text) else None
        idx = get_index()
        avoid = _hours_to_avoid(no_8am, free_day)
        busy = busy_from_registered(self.cat, self.profile.current, self.profile.batch, self.profile.current_sections) if avoid else None

        matched, unverified, excluded = [], [], 0
        for code, x in self.eligible.items():
            counts = self.can_count_as(x)
            if cats and not set(cats) & set(counts):
                continue
            if x["is_project"] and not include_projects and "project_based" not in require:
                continue
            props, ok, unknown = {}, True, False
            for r in require:
                p = prop(self.cat, code, r)
                props[r] = p
                if p["value"] is False:
                    ok = False
                elif p["value"] is None:
                    unknown = True
            if not ok:
                excluded += 1
                continue
            sections = x["sections"]
            if avoid:
                fit = check_course(pick_offering(self.cat, code, self.profile.batch), busy, avoid)
                if not fit["ok"]:
                    excluded += 1
                    continue
                sections = fit["sections"]
            m = tm.score(code) if tm else {"score": 0.0, "hits": [], "sim": 0.0, "anchor": None, "kw": 0.0}
            if topics and not tm.relevant(m):
                continue
            rec = self._card(x, counts, props, m["hits"], m["score"], sections)
            rec["similarity"] = round(m["sim"], 2)
            rec["anchor"] = m["anchor"]
            (unverified if unknown else matched).append(rec)

        key = lambda r: (-r["match_score"], r["code"])
        matched.sort(key=key)
        unverified.sort(key=key)
        # cross-listed courses (EEE F313 / INSTR F313 ...) are one course - keep the first
        def dedupe(rows):
            seen, out = set(), []
            for r in rows:
                h = self.cat.handout(r["code"])
                # same equivalence group, or literally the same handout pdf (ECON F412 / FIN F313)
                k = (h or {}).get("file") or self.cat.canon(r["code"])
                if k not in seen:
                    seen.add(k)
                    out.append(r)
            return out
        matched, unverified = dedupe(matched), dedupe(unverified)
        # drop the long tail of weak topic matches (a single stray word deep in a lecture plan)
        if topics and matched:
            top = matched[0]["match_score"]
            matched = [r for r in matched if r["match_score"] >= 0.45 * top]
        related = {"terms": [], "results": []}
        weak = False
        if topics and matched and tm:
            # direct matches far weaker than the best match anywhere in the timetable only brush the topic
            best_any = max((tm.score(c)["score"] for c in idx.docs), default=0)
            weak = matched[0]["match_score"] < 0.35 * best_any
        if topics and (len(matched) < 3 or weak):
            related = self.related_courses(topics, cats, require, no_8am, free_day,
                                           exclude={r["code"] for r in matched}, limit=max(3, limit - len(matched)))
        return {
            "filters": {"categories": cats, "topics": topics, "require": require, "no_8am": no_8am,
                        "free_day": free_day},
            "results": matched[:limit],
            "related": related,
            "total_matches": len(matched),
            "could_not_verify": [{"code": r["code"], "title": r["title"],
                                  "why": {k: v["evidence"] for k, v in r["properties"].items() if v["value"] is None}}
                                 for r in unverified[:5]],
            "excluded_by_property_or_time": excluded,
        }

    def related_courses(self, topics: str, categories=None, require=None, no_8am=False, free_day=None,
                        exclude=(), limit=4) -> dict:
        """'Nearby' courses when few eligible ones match the topic directly: the student's eligible
        courses that the semantic model (trained on the catalogue) puts closest to the topic, even
        without a shared keyword. Returned with the words the model links to the topic, so the answer
        can say why."""
        from agent.semantic import get_semantic
        sem = get_semantic()
        if not sem or not expand(topics or ""):
            return {"terms": [], "results": []}
        pool = self.find_courses(categories=categories, topics=None, require=require, no_8am=no_8am,
                                 free_day=free_day, limit=500)["results"]
        sims = sem.similarity(topics, [r["code"] for r in pool])
        scored = sorted(((sims[r["code"]], r) for r in pool if r["code"] not in exclude and sims[r["code"]] >= 0.15),
                        key=lambda t: -t[0])
        out = [{**r, "match_score": round(sc, 2), "similarity": round(sc, 2), "match_terms": [], "related": True,
                "related_to": topics} for sc, r in scored[:limit]]
        get_index()     # makes sure readable() knows the surface forms
        return {"terms": sem.neighbours(topics, 8), "results": out}

    def _card(self, x, counts, props, hits, score, sections):
        c = self.cat.courses.get(x["code"]) or {}
        return {
            "code": x["code"], "title": x["title"], "units": x["units"],
            "fills": x["category"], "can_count_as": counts, "why_category": x["why"],
            "eligibility": ["all checks passed (requirements, prerequisites, prior preparation, units, timetable)"]
                           + [f"note: {ch['note']} ({ch['clause']})" for ch in x["checks"] if ch["note"]],
            "properties": props,
            "match_terms": hits, "match_score": round(score, 2),
            "ic": (x["ic"] or "").title() or None,
            "sections": sections,
            "midsem": x["midsem"][0] and f"{x['midsem'][0]} {x['midsem'][1]}",
            "compre": x["compre"][0] and f"{x['compre'][0]} {x['compre'][1]}",
            "is_higher_degree": x["is_higher_degree"],
            "sources": {"timetable": x["timetable_source"], "bulletin": c.get("source"),
                        "handout": (self.cat.handout(x["code"]) or {}).get("file")},
        }

    def blocked_matches(self, topics: str, categories=None, limit=3) -> list[dict]:
        """Courses that match the topic but the student can't take this semester, with the rule that
        blocks each (full cards, marked locked). Makes 'nothing found' answers useful."""
        if not expand(topics or ""):
            return []
        tm = TopicMatch(topics)
        out = []
        for code, x in self.ineligible.items():
            if categories and x["category"] not in [c.upper() for c in categories] and \
                    not ({"OPEL"} & set(c.upper() for c in categories)):
                continue
            m = tm.score(code)
            if not tm.relevant(m):
                continue
            card = self._card(x, self.can_count_as(x), {}, m["hits"], m["score"], x["sections"])
            reasons = [f"{c['note']} ({c['clause']})" for c in x["checks"] if not c["ok"]]
            card.update({"locked": True, "blocked_by": reasons, "eligibility": reasons, "score": round(m["score"], 2),
                         "similarity": round(m["sim"], 2), "anchor": m["anchor"]})
            out.append(card)
        out.sort(key=lambda r: -r["score"])
        seen, uniq = set(), []
        for r in out:   # cross-listed duplicates (ECON F412 / FIN F313 share a handout)
            k = (self.cat.handout(r["code"]) or {}).get("file") or self.cat.canon(r["code"])
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        if uniq:
            uniq = [r for r in uniq if r["score"] >= 0.45 * uniq[0]["score"]]
        return uniq[:limit]

    def near_misses(self, categories=None, topics=None, require=None, limit=3) -> list[dict]:
        """When nothing satisfies every requested property: the best eligible courses ranked by how many
        of the requested properties they DO satisfy, with the evidence for each."""
        base = self.find_courses(categories=categories, topics=topics, require=[], limit=40)["results"]
        scored = []
        for r in base:
            props = {name: prop(self.cat, r["code"], name) for name in (require or [])}
            ok = sum(1 for v in props.values() if v["value"] is True)
            if ok == 0:
                continue
            scored.append((ok, r["match_score"], {**r, "properties": props}))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        if not scored:
            # nothing satisfies even one property - still show what the best matches actually have
            return [{**r, "properties": {n: prop(self.cat, r["code"], n) for n in (require or [])}}
                    for r in base[:limit]]
        return [t[2] for t in scored[:limit]]

    # ------------------------------------------------------------------ details
    def course_details(self, code: str) -> dict:
        from ingest.common import norm_code
        code = norm_code(code) or code
        cat = self.cat
        c = cat.courses.get(code) or {}
        if code in self.eligible:
            status = {"eligible": True, "fills": self.eligible[code]["category"],
                      "notes": [ch["note"] for ch in self.eligible[code]["checks"] if ch["note"]]}
        elif code in self.ineligible:
            status = {"eligible": False, "fills": self.ineligible[code]["category"],
                      "reasons": [f"{ch['note']} ({ch['clause']})" for ch in self.ineligible[code]["checks"]
                                  if not ch["ok"]]}
        elif cat.canon(code) in set(self.state["cleared"]):
            status = {"eligible": False, "reasons": ["already cleared"]}
        elif cat.canon(code) in set(self.state["registered"]):
            status = {"eligible": False, "reasons": ["already registered this semester"]}
        elif code not in cat.offerings:
            status = {"eligible": False, "reasons": ["not offered in the First Semester 2026-27 timetable"]}
        else:
            status = {"eligible": False, "reasons": ["practice school / thesis type course"]}
        if c.get("prerequisite_codes") or c.get("prerequisite_text"):
            pre = {"stated": True, "text": c.get("prerequisite_text"), "codes": c.get("prerequisite_codes")}
        else:
            pre = {"stated": False, "text": "No prerequisites required (none listed in the supplied bulletin)."}
        bg = (cat.handout(code) or {}).get("recommended_background")
        if bg:
            pre["handout_recommends"] = bg   # advice, not an enforced prerequisite
        off = pick_offering(cat, code, self.profile.batch) if code in cat.offerings else None
        return {
            "code": code, "title": cat.title(code), "units": cat.units(code),
            "description": (c.get("description") or "")[:800] or None,
            "prerequisites": pre, "status_for_student": status,
            "handout": handout_summary(cat, code),
            "timetable": None if not off else {
                "ic": off["ic"], "midsem": [off["midsem_date"], off["midsem_session"]],
                "compre": [off["compre_date"], off["compre_session"]],
                "sections": [{"section": s["section"], "type": s["type"], "slots": s["slots"],
                              "instructors": s["instructors"], "room": s["room"]} for s in off["sections"]],
                "source": off["source"]},
            "bulletin_source": c.get("source"),
        }

    def check_plan(self, codes: list[str], no_8am: bool = False, free_day: str | None = None,
                   compact: bool = False, schedule_registered: bool = False, allowed: dict | None = None,
                   include_ineligible: bool = False, options: int = 0) -> dict:
        out = planner.plan(self.profile, self.cat, codes, _hours_to_avoid(no_8am, free_day) or None, compact,
                           schedule_registered, allowed, include_ineligible, options)
        out.pop("requirements_after", None)
        return out

    # ------------------------------------------------------------------ policy validation
    def validate(self, code: str, require: list[str] | None = None, categories: list[str] | None = None) -> dict:
        """Final gate before anything is shown as a recommendation."""
        from ingest.common import norm_code
        code = norm_code(code) or code
        if code not in self.eligible:
            d = self.course_details(code)
            return {"code": code, "valid": False, "reasons": d["status_for_student"].get("reasons", ["not eligible"])}
        x = self.eligible[code]
        problems = []
        if categories and not set(c.upper() for c in categories) & set(self.can_count_as(x)):
            problems.append(f"doesn't count as {'/'.join(categories)} for this student (fills {x['category']})")
        for r in require or []:
            if r in PROPERTIES and prop(self.cat, code, r)["value"] is False:
                problems.append(f"handout/timetable contradicts '{r}'")
        return {"code": code, "valid": not problems, "reasons": problems}
