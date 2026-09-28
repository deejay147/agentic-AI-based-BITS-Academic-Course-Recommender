"""Agent tests. The Claude path runs against a scripted fake client (no network here), which is
enough to check the tool loop and - more importantly - that validation throws out anything
the engine didn't clear."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent import nlu
from agent.agent import Recommender
from engine.profile import Profile

PROFILES = Path(__file__).parent / "profiles"


def load(name):
    return Profile.from_dict(json.loads((PROFILES / f"{name}.json").read_text()))


# ---------------------------------------------------------------- query parsing
@pytest.mark.parametrize("q,cats,req,topic", [
    ("Suggest DELs related to AI.", ["DEL"], [], "ai"),
    ("I want an OPEL with no attendance requirement.", ["OPEL"], ["no_attendance_requirement"], ""),
    ("Suggest courses with no midsem and a lenient makeup policy.", [], ["no_midsem", "lenient_makeup"], ""),
    ("I need a HUEL and prefer project-based evaluation.", ["HUEL"], ["project_based"], ""),
    ("Suggest an AI-related DEL with no midsem", ["DEL"], ["no_midsem"], "ai"),
])
def test_nlu_spec_examples(q, cats, req, topic):
    p = nlu.parse(q)
    assert p["intent"] == "recommend"
    assert p["categories"] == cats and p["require"] == req and p["topics"] == topic


def test_nlu_other_intents():
    assert nlu.parse("what are my remaining requirements?")["intent"] == "requirements"
    assert nlu.parse("can I take CS F407 and BITS F464 together")["intent"] == "plan"
    d = nlu.parse("prerequisites for cs f425")
    assert d["intent"] == "details" and d["codes"] == ["CS F425"] and d["asks_prerequisites"]
    t = nlu.parse("finance electives, no 8am classes and fridays free")
    assert t["no_8am"] and t["free_day"] == "F" and t["topics"] == "finance"


# ---------------------------------------------------------------- rules mode
def test_rules_ai_del():
    out = Recommender(load("cs_2nd_year"), api_key="").ask("Suggest DELs related to AI.")
    codes = [r["code"] for r in out["recommendations"]]
    assert out["mode"] == "rules" and "CS F407" in codes[:3]
    assert all("DEL" in r["can_count_as"] for r in out["recommendations"])


def test_rules_no_attendance_only_verified():
    out = Recommender(load("ece_3rd_year"), api_key="").ask("I want an OPEL with no attendance requirement.")
    for r in out["recommendations"]:
        assert r["properties"]["no_attendance_requirement"]["value"] is True
    assert out["could_not_verify"]      # courses whose handout is silent are reported, not recommended


def test_rules_prereq_only_when_asked():
    rec = Recommender(load("cs_2nd_year"), api_key="")
    assert "Prerequisites" in rec.ask("prerequisites of CS F425")["text"]
    assert "Prerequisites" not in rec.ask("tell me about CS F425")["text"]


def test_rules_unit_cap_explained():
    out = Recommender(load("dual_b3a7_3rd_year"), api_key="").ask("suggest an OPEL")
    assert not out["recommendations"] and "25-unit cap" in out["text"]


# ---------------------------------------------------------------- claude mode (fake client)
def _tool(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    """Plays back: search -> submit (one valid, one bogus pick) -> final text."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        n = len(self.calls)
        if n == 1:
            return NS(content=[_tool("t1", "find_courses", {"categories": ["DEL"],
                                                           "topics": "artificial intelligence machine learning"})])
        if n == 2:
            return NS(content=[_tool("t2", "submit_recommendations", {
                "categories": ["DEL"],
                "items": [{"code": "CS F407", "reason": "core AI course"},
                          {"code": "ME F212", "reason": "made up pick"}]})])
        return NS(content=[NS(type="text", text="CS F407 Artificial Intelligence fills a DEL ...")])


def test_claude_loop_and_validation():
    fake = FakeClient()
    out = Recommender(load("cs_2nd_year"), client=fake).ask("Suggest DELs related to AI.")
    assert out["mode"] == "llm" and out["provider"] == "anthropic"
    assert [r["code"] for r in out["recommendations"]] == ["CS F407"]
    assert out["rejected_by_validation"][0]["code"] == "ME F212"
    # tool results were fed back to the model
    last_user = fake.calls[-1]["messages"][-1]
    assert last_user["role"] == "user" and last_user["content"][0]["type"] == "tool_result"
    assert [t["tool"] for t in out["trace"]] == ["find_courses", "submit_recommendations"]


def test_claude_failure_falls_back_to_rules():
    class Broken:
        messages = property(lambda self: (_ for _ in ()).throw(ConnectionError("no network")))
    out = Recommender(load("cs_2nd_year"), client=Broken()).ask("Suggest DELs related to AI.")
    assert out["mode"] == "rules" and "LLM unavailable" in out["text"]


def test_rules_blocked_topic_explained():
    # finance courses exist but a CS 2nd year can't take them yet - the answer has to say why
    out = Recommender(load("cs_2nd_year"), api_key="").ask("I like finance and economics, any OPEL?")
    assert "not open to you this semester" in out["text"] and "3.15(b)(i)" in out["text"]


def test_nlu_compact():
    assert nlu.parse("plan CS F317 and GS F232 with no gaps")["compact"]


# ---------------------------------------------------------------- openai-compatible (gemini / groq) fake
def _call(id_, name, args):
    return NS(id=id_, type="function", function=NS(name=name, arguments=json.dumps(args)))


class FakeOpenAI:
    """chat.completions.create playback: search -> submit (valid + bogus) -> final text"""

    def __init__(self):
        self.calls = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        n = len(self.calls)
        if n == 1:
            msg = NS(content=None, tool_calls=[_call("c1", "find_courses", {"categories": ["DEL"], "topics": "deep learning"})])
        elif n == 2:
            msg = NS(content=None, tool_calls=[_call("c2", "submit_recommendations", {
                "items": [{"code": "CS F425", "reason": "deep learning"}, {"code": "PHA F311", "reason": "nope"}]})])
        else:
            msg = NS(content="CS F425 Deep Learning fills a DEL ...", tool_calls=None)
        return NS(choices=[NS(message=msg)])


def test_openai_compatible_loop(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    fake = FakeOpenAI()
    out = Recommender(load("cs_2nd_year"), client=fake, provider="gemini", api_key="x").ask("deep learning DEL?")
    assert out["mode"] == "llm" and out["provider"] == "gemini"
    assert [r["code"] for r in out["recommendations"]] == ["CS F425"]
    assert out["rejected_by_validation"][0]["code"] == "PHA F311"
    # system prompt goes first, tool results come back as role=tool messages
    msgs = fake.calls[-1]["messages"]
    assert msgs[0]["role"] == "system" and any(m["role"] == "tool" for m in msgs)
    assert fake.calls[0]["tools"][0]["type"] == "function"


def test_provider_resolution(monkeypatch):
    from agent.agent import resolve_llm
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    cfg = resolve_llm()
    assert cfg["provider"] == "gemini" and cfg["api_key"] == "g-key" and "generativelanguage" in cfg["base_url"]
    monkeypatch.delenv("GEMINI_API_KEY")
    assert resolve_llm()["api_key"] == ""     # nothing set -> rules mode
