"""The recommender agent.

    Profile + query -> requirement analysis -> eligible set -> preference matching
                    -> policy validation -> answer

Two modes, same tools underneath (agent/tools.py):
  * llm  : an LLM gets the tools below and decides what to call. It has to finish by calling
           submit_recommendations; every course it submits is re-checked by Session.validate()
           before it reaches the student, so it can't recommend something the engine didn't clear.
           Works with Anthropic (Claude) or any OpenAI-compatible API - Gemini and Groq have free tiers.
  * rules: no API key -> agent/nlu.py parses the query and we call the same tools directly,
           explanations come from templates.

Put a key in .env (GEMINI_API_KEY / GROQ_API_KEY / ANTHROPIC_API_KEY, or LLM_PROVIDER + LLM_API_KEY),
or paste it in the dashboard sidebar.
"""
from __future__ import annotations

import json
import os

from agent import nlu
from agent.retrieval import PROPERTIES
from agent.tools import Session
from engine.profile import Profile

MAX_TURNS = 8

PROP_LABELS = {
    "no_midsem": "No midsem", "no_compre": "No compre", "no_attendance_requirement": "No attendance requirement",
    "lenient_makeup": "Lenient makeup", "project_based": "Project-based evaluation", "has_quiz": "Has quizzes",
    "no_quiz": "No quizzes", "has_lab": "Has a lab", "no_lab": "No lab", "open_book": "Open-book component",
    "has_assignment": "Has assignments",
}

SYSTEM_PROMPT = """You are the BITS Pilani academic course recommender for one student (First Semester 2026-27, Pilani campus).

How to work:
1. Academic validity comes first. Only recommend courses returned by find_courses (they already passed the
   deterministic eligibility checks: remaining requirements, prerequisites, prior preparation, 25-unit cap,
   timetable clashes). Never recommend a course from memory.
2. Translate the student's request into tool arguments: requirement categories (CDC / DEL / HUEL / OPEL),
   handout properties (`require`), time preferences, and topics. For topics, pass a few concrete syllabus
   words (e.g. for "AI" -> "artificial intelligence machine learning neural networks reinforcement learning").
   If the first search is thin, try again with other wording before concluding there is nothing.
3. Re-rank what comes back by how well it matches the student's interest; you may read course_details to judge.
4. Finish by calling submit_recommendations with your picks (usually 3-5), best first, each with a one-line reason.
   Then write the final answer.

Rules for the final answer:
- Keep it short. For each course: requirement it fills, why the student is eligible, the relevant course
  properties, and why it matches the request. Quote handout wording for policies when useful.
- If a requested property could not be verified from the supplied data, say so explicitly. If the handout says
  nothing about something (attendance, makeup ...), say "No specific information mentioned; contact the
  Instructor-in-Charge (name)".
- Prerequisites: if none are listed, say "No prerequisites required" only when the student asks about
  prerequisites; don't bring it up otherwise.
- Mention any notes from get_requirements that apply (older batch curriculum notice, CSP, unit cap).
- Don't invent rules, course properties or requirements that the tools didn't give you.
- If nothing fits, say what blocked it (e.g. unit cap, property not met, not offered) and suggest the closest options."""

TOOLS = [
    {"name": "get_requirements",
     "description": "The student's remaining requirements (GIR, CDC, DEL, HUEL, OPEL, minor), graduation checklist, "
                    "units already registered this semester and any notes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "find_courses",
     "description": "Search courses the student is ELIGIBLE for this semester. Filters by requirement category, "
                    "handout/timetable properties and time preferences; ranks by topic match. Returns cards with "
                    "category, eligibility notes, property evidence and source refs, plus a list of courses whose "
                    "requested property could not be verified.",
     "input_schema": {"type": "object", "properties": {
         "categories": {"type": "array", "items": {"type": "string", "enum": ["CDC", "GIR", "DEL", "HUEL", "OPEL"]}},
         "topics": {"type": "string", "description": "space separated syllabus keywords"},
         "require": {"type": "array", "items": {"type": "string", "enum": PROPERTIES}},
         "no_8am": {"type": "boolean"},
         "free_day": {"type": "string", "enum": ["M", "T", "W", "Th", "F", "S"]},
         "include_projects": {"type": "boolean", "description": "include study/lab/design project courses"},
         "limit": {"type": "integer", "default": 10}}}},
    {"name": "course_details",
     "description": "Everything about one course: description, prerequisites, handout (evaluation, makeup, attendance), "
                    "timetable sections and exams, and whether this student is eligible (with reasons).",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "check_plan",
     "description": "Check a set of courses the student wants to take together: files each as CDC/DEL/HUEL/OPEL, "
                    "picks clash-free sections, checks the 25 unit cap.",
     "input_schema": {"type": "object", "properties": {
         "codes": {"type": "array", "items": {"type": "string"}},
         "no_8am": {"type": "boolean"}, "free_day": {"type": "string"},
         "compact": {"type": "boolean", "description": "pick sections with the fewest idle hours between classes"}},
         "required": ["codes"]}},
    {"name": "submit_recommendations",
     "description": "Submit the final picks. Each is re-validated against the eligibility engine and the requested "
                    "category/properties; invalid ones are rejected and you'll be told why.",
     "input_schema": {"type": "object", "properties": {
         "items": {"type": "array", "items": {"type": "object", "properties": {
             "code": {"type": "string"}, "reason": {"type": "string"}}, "required": ["code", "reason"]}},
         "categories": {"type": "array", "items": {"type": "string"}},
         "require": {"type": "array", "items": {"type": "string"}}}, "required": ["items"]}},
]


# Which LLM to use. Anthropic's own API, or anything that speaks the OpenAI chat-completions + tools format
# (Gemini and Groq both have free tiers and an OpenAI-compatible endpoint). Model names change over time -
# LLM_MODEL overrides the default, check the provider's model list if a default stops working.
PROVIDERS = {
    "anthropic": {"key_env": "ANTHROPIC_API_KEY", "model": "claude-sonnet-5", "base_url": None},
    "gemini": {"key_env": "GEMINI_API_KEY", "model": "gemini-3.8-flash",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "groq": {"key_env": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1"},
    "openai": {"key_env": "OPENAI_API_KEY", "model": None, "base_url": None},   # any OpenAI-compatible server
}


def resolve_llm(provider=None, api_key=None, model=None, base_url=None) -> dict:
    """Figure out provider / key / model from arguments, then env vars. No key -> rules mode."""
    provider = (provider or os.getenv("LLM_PROVIDER") or "").lower() or None
    if provider is None:   # auto-detect from whichever key is set
        provider = next((p for p, c in PROVIDERS.items() if os.getenv(c["key_env"])), "anthropic")
    cfg = PROVIDERS.get(provider, PROVIDERS["openai"])
    key = api_key if api_key is not None else (os.getenv("LLM_API_KEY") or os.getenv(cfg["key_env"]) or "")
    mdl = model or os.getenv("LLM_MODEL") or (os.getenv("ANTHROPIC_MODEL") if provider == "anthropic" else None) \
        or cfg["model"]
    return {"provider": provider, "api_key": key, "model": mdl,
            "base_url": base_url or os.getenv("LLM_BASE_URL") or cfg["base_url"]}


def _openai_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in TOOLS]


class Recommender:
    def __init__(self, profile: Profile, api_key: str | None = None, model: str | None = None, client=None,
                 provider: str | None = None, base_url: str | None = None):
        self.session = Session(profile)
        cfg = resolve_llm(provider, api_key, model, base_url)
        if client is not None and provider is None:
            # injected client: an OpenAI-style one has .chat, otherwise treat it as Anthropic-style
            try:
                is_openai = hasattr(client, "chat")
            except Exception:
                is_openai = False
            cfg["provider"] = "openai" if is_openai else "anthropic"
        self.provider, self.api_key, self.model, self.base_url = cfg["provider"], cfg["api_key"], cfg["model"], cfg["base_url"]
        self._client = client     # injectable, the tests pass fake ones

    @property
    def mode(self) -> str:
        return "llm" if (self._client or self.api_key) else "rules"

    @property
    def label(self) -> str:
        return f"{self.provider} ({self.model})" if self.mode == "llm" else "rule-based (no API key)"

    def ask(self, query: str, history: list[dict] | None = None) -> dict:
        if self.mode == "llm":
            try:
                return self._ask_llm(query, history or [])
            except Exception as e:  # network / auth / quota problems -> still answer, just without the LLM
                out = self._ask_rules(query)
                out["text"] = f"_(LLM unavailable - {type(e).__name__}: {str(e)[:120]} - answered with the " \
                              f"rule-based parser.)_\n\n" + out["text"]
                return out
        return self._ask_rules(query)

    # ------------------------------------------------------------------ tool dispatch
    def _call_tool(self, name: str, args: dict, ctx: dict):
        s = self.session
        if name == "get_requirements":
            return s.get_requirements()
        if name == "find_courses":
            res = s.find_courses(**{k: v for k, v in args.items() if k in (
                "categories", "topics", "require", "no_8am", "free_day", "include_projects", "limit")})
            for r in res["results"]:
                ctx["cards"][r["code"]] = r
            ctx["unverified"].extend(res["could_not_verify"])
            ctx["last_filters"] = res["filters"]
            return res
        if name == "course_details":
            return s.course_details(args["code"])
        if name == "check_plan":
            return s.check_plan(args["codes"], args.get("no_8am", False), args.get("free_day"), args.get("compact", False))
        if name == "submit_recommendations":
            accepted, rejected = [], []
            for it in args.get("items", []):
                v = s.validate(it["code"], args.get("require"), args.get("categories"))
                if v["valid"]:
                    accepted.append({"code": v["code"], "reason": it.get("reason", "")})
                else:
                    rejected.append(v)
            ctx["accepted"] = accepted
            ctx["rejected"] = rejected
            return {"accepted": [a["code"] for a in accepted], "rejected": rejected}
        return {"error": f"unknown tool {name}"}

    # ------------------------------------------------------------------ llm mode
    def _client_or_new(self):
        if self._client is None:
            if self.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            else:
                from openai import OpenAI
                if not self.model:
                    raise ValueError("set LLM_MODEL for this provider")
                self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _first_message(self, query):
        s = self.session
        blurb = json.dumps({
            "id": s.profile.id_no, "semester": s.profile.semester_label, "programmes": s.profile.programmes,
            "stream": s.profile.stream, "minor": s.profile.minor, "interests": s.profile.interests,
            "registered_now": s.profile.current,
        })
        return f"Student profile: {blurb}\n\nQuestion: {query}"

    def _ask_llm(self, query: str, history: list[dict]) -> dict:
        client = self._client_or_new()
        ctx = {"cards": {}, "unverified": [], "accepted": None, "rejected": [], "trace": [], "last_filters": None}
        hist = [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
        if self.provider == "anthropic":
            text = self._loop_anthropic(client, hist, query, ctx)
        else:
            text = self._loop_openai(client, hist, query, ctx)
        recs = []
        for a in ctx["accepted"] or []:
            card = ctx["cards"].get(a["code"]) or self._card_for(a["code"])
            if card:
                recs.append({**card, "agent_reason": a["reason"]})
        return {"mode": "llm", "provider": self.provider, "model": self.model, "text": text.strip(),
                "recommendations": recs, "rejected_by_validation": ctx["rejected"],
                "could_not_verify": self._dedupe(ctx["unverified"]), "trace": ctx["trace"]}

    def _run_tool(self, name, args, ctx) -> str:
        out = self._call_tool(name, args, ctx)
        ctx["trace"].append({"tool": name, "input": args})
        return json.dumps(out, default=str)[:30000]

    def _loop_anthropic(self, client, hist, query, ctx) -> str:
        messages = hist + [{"role": "user", "content": self._first_message(query)}]
        text = ""
        for _ in range(MAX_TURNS):
            resp = client.messages.create(model=self.model, max_tokens=1500, system=SYSTEM_PROMPT,
                                          tools=TOOLS, messages=messages)
            blocks = resp.content
            text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")
            uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if not uses:
                break
            messages.append({"role": "assistant", "content": [self._block_to_dict(b) for b in blocks]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": u.id, "content": self._run_tool(u.name, dict(u.input or {}), ctx)}
                for u in uses]})
        return text

    def _loop_openai(self, client, hist, query, ctx) -> str:
        # same loop, OpenAI chat-completions flavour (Gemini / Groq / any compatible server)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + hist + \
                   [{"role": "user", "content": self._first_message(query)}]
        text = ""
        for _ in range(MAX_TURNS):
            resp = client.chat.completions.create(model=self.model, messages=messages, tools=_openai_tools(),
                                                  tool_choice="auto")
            msg = resp.choices[0].message
            text = msg.content or ""
            calls = msg.tool_calls or []
            if not calls:
                break
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls]})
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                messages.append({"role": "tool", "tool_call_id": c.id, "content": self._run_tool(c.function.name, args, ctx)})
        return text

    @staticmethod
    def _block_to_dict(b):
        if getattr(b, "type", "") == "text":
            return {"type": "text", "text": b.text}
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}

    def _card_for(self, code):
        s = self.session
        if code not in s.eligible:
            return None
        x = s.eligible[code]
        return s._card(x, s.can_count_as(x), {}, [], 0.0, x["sections"])

    @staticmethod
    def _dedupe(items):
        seen, out = set(), []
        for i in items:
            if i["code"] not in seen:
                seen.add(i["code"])
                out.append(i)
        return out[:6]

    # ------------------------------------------------------------------ rules mode
    def _ask_rules(self, query: str) -> dict:
        s = self.session
        p = nlu.parse(query)
        notes = s.state["notes"]
        head = "".join(f"> {n}\n\n" for n in notes)

        if p["intent"] == "requirements":
            return {"mode": "rules", "parsed": p, "text": head + format_requirements(s.get_requirements()),
                    "recommendations": [], "could_not_verify": []}

        if p["intent"] == "details":
            d = s.course_details(p["codes"][0])
            return {"mode": "rules", "parsed": p, "text": head + format_details(d, p.get("asks_prerequisites")),
                    "recommendations": [], "could_not_verify": []}

        if p["intent"] == "plan":
            out = s.check_plan(p["codes"], p["no_8am"], p["free_day"], p.get("compact", False))
            return {"mode": "rules", "parsed": p, "text": head + format_plan(out), "recommendations": [],
                    "could_not_verify": [], "plan": out}

        res = s.find_courses(categories=p["categories"], topics=p["topics"] or None, require=p["require"],
                             no_8am=p["no_8am"], free_day=p["free_day"], limit=5)
        recs = res["results"]
        rel = res.get("related") or {"terms": [], "results": []}
        if rel["results"]:
            # fewer / weaker direct matches: add 'nearby' courses, marked as related (see Session.related_courses)
            room = max(0, 6 - len(recs))
            res = {**res, "results": recs + rel["results"][:room]}
            recs = res["results"]
        for r in recs:
            # show the category the student asked for, if the course can be filed that way (reg 2.05)
            asked = [c for c in p["categories"] if c in r["can_count_as"]]
            r["shown_as"] = asked[0] if asked else r["fills"]
        text = head + format_recommendations(res, p, s)
        near, shown_blocked, blocked_first = [], [], False
        if p["topics"]:
            blocked = s.blocked_matches(p["topics"], p["categories"])
            best = max((r["match_score"] for r in recs if not r.get("related")), default=0)
            if blocked and (len(recs) < 3 or blocked[0]["score"] > 1.5 * best):
                shown_blocked = blocked
                blocked_first = blocked[0]["score"] > 1.5 * best
                if recs and blocked_first:
                    # the real matches are blocked - say that first, the list below only touches the topic
                    text = head + f"> **Heads up:** the courses that best match '{p['topics']}' aren't open to you " \
                                  f"this semester (shown with the reason). The ones you can take only partly cover it.\n\n" \
                        + text[len(head):]
                text += "\n\n**Matches your topic, but not open to you this semester:**\n\n" + "\n".join(
                    f"- {b['code']} - {b['title']}: {'; '.join(b['blocked_by'])}" for b in blocked)
        if not recs and len(p["require"]) >= 1:
            near = s.near_misses(p["categories"], p["topics"] or None, p["require"])
            if near:
                text += "\n\n**Closest options** (meet some of what you asked, not all):\n\n" + \
                        "\n".join(_short_card(r) for r in near)
        return {"mode": "rules", "parsed": p, "text": text, "recommendations": recs, "near_misses": near,
                "could_not_verify": res["could_not_verify"], "blocked": shown_blocked, "blocked_first": blocked_first}


# ---------------------------------------------------------------------- templates (rules mode)

def _prop_line(name, pr):
    label = PROP_LABELS.get(name, name)
    if pr["value"] is True:
        mark = "yes"
    elif pr["value"] is False:
        mark = "no"
    else:
        mark = "could not be verified"
    ev = (pr.get("evidence") or "").strip()
    return f"{label}: {mark}" + (f" - \"{ev[:160]}\"" if ev else "")


def _short_card(r) -> str:
    props = "; ".join(_prop_line(n, pr) for n, pr in r["properties"].items())
    return f"- **{r['code']} - {r['title']}** ({r['fills']}): {props}"


def format_recommendations(res, parsed, s) -> str:
    f = res["filters"]
    want = []
    if f["categories"]:
        want.append("/".join(f["categories"]))
    if f["topics"]:
        want.append(f"about '{f['topics']}'")
    want += [PROP_LABELS[r].lower() for r in f["require"]]
    if f["no_8am"]:
        want.append("no 8 AM classes")
    if f["free_day"]:
        want.append(f"{f['free_day']} free")
    lines = [f"**Looking for:** {', '.join(want) or 'anything that fits your requirements'}", ""]
    rel = res.get("related") or {}
    n_direct = sum(1 for r in res["results"] if not r.get("related"))
    if rel.get("results") and any(r.get("related") for r in res["results"]):
        lines.append(("Nothing open to you mentions it directly, so here" if not n_direct else
                      "Fewer direct matches than I'd like, so I've also added") +
                     f" related courses (🔭), found through words typical of '{f['topics']}' courses: "
                     f"{', '.join(rel['terms'][:6])}.")
        lines.append("")
    if not res["results"]:
        units_left = 25 - s.ev["registered_units"]
        lines.append("No eligible course matches all of that.")
        if units_left < 3:
            lines.append(f"You're registered for {s.ev['registered_units']} units already, so only "
                         f"{units_left} more fit under the 25-unit cap (reg 1.01).")
        if res["excluded_by_property_or_time"]:
            lines.append(f"{res['excluded_by_property_or_time']} eligible courses were ruled out because the "
                         f"handout/timetable contradicts a requested property or time preference.")
    for i, r in enumerate(res["results"], 1):
        lines.append(f"**{i}. {r['code']} - {r['title']}** ({r['units']} units)" + (" 🔭 related" if r.get("related") else ""))
        asked = [c for c in f["categories"] if c in r["can_count_as"]]
        if asked and asked[0] != r["fills"]:
            fills = f"{asked[0]} - you asked for {asked[0]}; it's also in your {r['fills']} pool, so it can be filed either way (reg 2.05)"
        elif len(r["can_count_as"]) > 1:
            fills = f"{r['fills']} - {r['why_category']} (could also count as " + \
                "/".join(c for c in r["can_count_as"] if c != r["fills"]) + ")"
        else:
            fills = f"{r['fills']} - {r['why_category']}"
        lines.append(f"- Requirement: {fills}")
        lines.append(f"- Eligibility: {'; '.join(r['eligibility'])}")
        for name, pr in r["properties"].items():
            lines.append(f"- {_prop_line(name, pr)}")
        extra = []
        if r["midsem"]:
            extra.append(f"midsem {r['midsem']}")
        if r["compre"]:
            extra.append(f"compre {r['compre']}")
        if r["sections"]:
            extra.append("sections " + ", ".join(f"{v}" for v in r["sections"].values()))
        if r["ic"]:
            extra.append(f"IC {r['ic']}")
        if extra:
            lines.append(f"- Timetable: {'; '.join(extra)}")
        if r["match_terms"]:
            src = "your request" if f["topics"] else "your interests"
            lines.append(f"- Why it matches {src}: syllabus/description mentions "
                         f"{', '.join(dict.fromkeys(r['match_terms'][:6]))}")
        lines.append("")
    if res["could_not_verify"]:
        lines.append("**Could not verify** the requested property for: " +
                     "; ".join(f"{c['code']} ({' '.join(c['why'].values())[:120]})" for c in res["could_not_verify"][:3]))
    return "\n".join(lines).strip()


def format_requirements(r) -> str:
    L = [f"**{r['student']['semester']}** - registered for {r['registered_units']} units "
         f"({r['units_left_this_semester']} more allowed this semester)", ""]
    if r["gir_remaining"]:
        L.append("- General institutional courses left: " + ", ".join(r["gir_remaining"]))
    for p in r["programmes"]:
        L.append(f"- **{p['programme']}** core left: {', '.join(p['cdc_remaining']) or 'none'}"
                 + (f" (in progress: {', '.join(p['cdc_in_progress'])})" if p["cdc_in_progress"] else ""))
        L.append(f"- DEL: {len(p['del_done'])} done, {len(p['del_in_progress'])} in progress, "
                 f"{p['del_remaining_courses']} of {p['del_required_courses']} left")
    h, o = r["huel"], r["opel"]
    L.append(f"- HUEL: {h['remaining_courses']} of {h['required_courses']} left")
    L.append(f"- OPEL: {o['remaining_courses']} of {o['required_courses']} left" if o["required_courses"]
             else f"- OPEL: {o['note']}")
    if r["minor"]:
        m = r["minor"]
        L.append(f"- {m['name']}: {m['courses_counted']} of {m['required_courses']} courses; core left: "
                 + (", ".join("/".join(g) for g in m["core_remaining"]) or "none"))
    if r["not_cleared"]:
        L.append(f"- Not cleared (NC/W/...): {', '.join(r['not_cleared'])}")
    return "\n".join(L)


def format_details(d, asks_prereq=False) -> str:
    L = [f"**{d['code']} - {d['title']}** ({d['units']} units)"]
    st = d["status_for_student"]
    if st["eligible"]:
        L.append(f"- You can take it this semester; it fills **{st['fills']}**."
                 + (f" Notes: {'; '.join(st['notes'])}" if st.get("notes") else ""))
    else:
        L.append(f"- Not available to you this semester: {'; '.join(st['reasons'])}")
    pre = d["prerequisites"]
    if asks_prereq or pre["stated"]:
        L.append(f"- Prerequisites: {pre['text']}")
        if pre.get("handout_recommends"):
            L.append(f"- The handout recommends this background (advice, not a registration rule): "
                     f"{pre['handout_recommends']}")
    h = d["handout"]
    if h["available"]:
        if h["evaluation"]:
            L.append("- Evaluation: " + ", ".join(f"{c['name']} {c['weight']:g}%" for c in h["evaluation"]))
        L.append(f"- Makeup: {h['makeup']['text'][:250]}")
        L.append(f"- Attendance: {h['attendance']['text'][:250]}")
    else:
        L.append(f"- {h['message']}")
    if d["timetable"]:
        t = d["timetable"]
        L.append(f"- Midsem {t['midsem'][0] or 'not scheduled'} {t['midsem'][1] or ''}; compre {t['compre'][0] or 'n/a'}")
    return "\n".join(L)


def format_plan(out) -> str:
    L = []
    for p in out["picks"]:
        secs = ", ".join(p["sections"].values()) or "-"
        L.append(f"- **{p['code']}** {p['title']} ({p['units']}u) -> filed as **{p['filed_as']}**, sections {secs}")
    for r in out["rejected"]:
        L.append(f"- ~~{r['code']}~~ not allowed: {'; '.join(r['reasons'])}")
    if out.get("gap_hours") is not None:
        L.append(f"- Compact pick: {out['gap_hours']} idle hours across {out['days_used']} days")
    L.append(f"\nTotal this semester: {out['total_units']} units. " +
             ("Clash-free." if out["clash_free"] else "Clashes: " + "; ".join(out["clash_problems"])))
    for w in out["warnings"]:
        L.append(f"- Note: {w}")
    return "\n".join(L)
