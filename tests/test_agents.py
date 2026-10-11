import pytest

from app.agents import Router, classify_city_intent, split_markers, split_name_country
from app.models import CityCreate


@pytest.mark.parametrize("msg,intent", [
    ("Add city Lisbon in Portugal", "add"),
    ("please add a new city called Rome", "add"),
    ("create city Oslo", "add"),
    ("delete city Goa", "delete"),
    ("Remove the city Paris from the database", "delete"),
    ("update city Goa budget to 90", "update"),
    ("list all cities", "list"),
    ("show me cities with beaches", "list"),
    ("how many cities do we have?", "count"),
    ("find city Tokyo", "find"),
    ("Do we have Tokyo in the database?", "find"),
    ("is Goa in the city master?", "find"),
    ("What is the best time to visit Goa?", None),
    ("Tell me about Paris", None),
    ("Show me the best city for food", None),
    ("How do I get a visa?", None),
])
def test_intent_classification(msg, intent):
    assert classify_city_intent(msg) == intent


def test_router_chooses_agent():
    r = Router()
    assert r.route("add city Rome").agent == "city_master"
    assert r.route("is Rome worth visiting?").agent == "travel_rag"


def test_parsing_helpers():
    assert split_markers("Goa in India with tags beach, party budget 80") == (
        "Goa in India", {"tags": "beach, party", "budget": "80"})
    assert split_name_country("new york in usa") == ("New York", "USA")
    assert split_name_country("Lisbon, Portugal") == ("Lisbon", "Portugal")


def chat(c, msg, **kw):
    return c.assistant.chat(msg, **kw)


def test_city_master_full_lifecycle(container):
    r = chat(container, "Add city Lisbon in Portugal with tags beach, food budget 90 season spring")
    assert r["agent"] == "city_master" and r["intent"] == "add"
    city = r["data"]["city"]
    assert (city["name"], city["country"], city["avg_daily_budget_usd"], city["tags"], city["best_season"]) == \
        ("Lisbon", "Portugal", 90, ["beach", "food"], "Spring")

    assert "Found" in chat(container, "find city lisbon")["reply"]
    r = chat(container, "update city Lisbon budget to 120")
    assert r["data"]["city"]["avg_daily_budget_usd"] == 120
    assert chat(container, "how many cities?")["data"]["total"] == 1
    assert chat(container, "show cities in portugal")["data"]["count"] == 1
    assert "Deleted" in chat(container, "delete city Lisbon")["reply"]
    assert chat(container, "how many cities?")["data"]["total"] == 0


def test_city_master_errors_are_friendly(container):
    chat(container, "add city Rome in Italy")
    assert "already exists" in chat(container, "add city Rome in Italy")["reply"]
    assert "not found" in chat(container, "delete city Atlantis")["reply"].lower()
    assert "No city named" in chat(container, "find city Atlantis")["reply"]
    assert "valid city name" in chat(container, "add city 123")["reply"]
    assert "what to change" in chat(container, "update city Rome")["reply"].lower()
    assert "must be a number" in chat(container, "update city Rome budget to lots")["reply"]


def test_ambiguous_city_asks_for_country(container):
    chat(container, "add city Paris in France")
    chat(container, "add city Paris in USA")
    assert "specify the country" in chat(container, "delete city Paris")["reply"]
    assert "Deleted" in chat(container, "delete city Paris in USA")["reply"]


def test_delete_by_id(container):
    chat(container, "add city Rome in Italy")
    assert "Deleted" in chat(container, "delete city 1")["reply"]


def test_rag_answers_with_sources(seeded):
    r = chat(seeded, "What is the best time to visit Goa?")
    assert r["agent"] == "travel_rag" and "November" in r["reply"]
    assert r["sources"] and r["sources"][0]["title"] == "Goa Travel Guide"
    assert r["data"]["city"] == "Goa" and r["data"]["city_in_master"] is True


def test_rag_unknown_topic_does_not_hallucinate(seeded):
    r = chat(seeded, "Explain quantum chromodynamics")
    assert "couldn't find" in r["reply"] and r["sources"] == []


def test_rag_city_without_documents(seeded):
    chat(seeded, "add city Oslo in Norway")
    r = chat(seeded, "best time to visit Oslo budget")
    assert r["data"]["city"] == "Oslo"
    assert "No Oslo-specific documents" in r["reply"] or "couldn't find" in r["reply"]


def test_rag_uses_llm_when_available(seeded):
    class FakeLLM:
        available = True
        def __init__(self): self.prompts = []
        def complete(self, system, user, max_tokens=500):
            self.prompts.append(user)
            return "LLM says: visit in winter."
    llm = FakeLLM()
    seeded.rag_agent.llm = llm
    r = chat(seeded, "best time to visit Goa")
    assert r["reply"] == "LLM says: visit in winter." and r["data"]["used_llm"] is True
    assert "November" in llm.prompts[0]                       # retrieved context was passed in


def test_rag_falls_back_when_llm_fails(seeded):
    class BrokenLLM:
        available = True
        def complete(self, *a, **k): return None
    seeded.rag_agent.llm = BrokenLLM()
    r = chat(seeded, "best time to visit Goa")
    assert "November" in r["reply"] and r["data"]["used_llm"] is False


def test_stateless_has_no_memory(seeded):
    r1 = chat(seeded, "best time to visit Goa")
    assert r1["session_id"] is None and r1["stateful"] is False
    r2 = chat(seeded, "What about its budget?")
    assert r2["data"]["city"] is None and r2["data"]["used_session_memory"] is False


def test_stateful_remembers_city(seeded):
    r1 = chat(seeded, "Tell me about Tokyo", stateful=True)
    sid = r1["session_id"]
    assert sid and r1["stateful"]
    r2 = chat(seeded, "What about its budget?", session_id=sid)
    assert r2["data"]["city"] == "Tokyo" and r2["data"]["used_session_memory"] is True
    assert "Tokyo" in r2["reply"] and "100-180" in r2["reply"]
    assert [m["role"] for m in seeded.sessions.history(sid)] == ["user", "assistant"] * 2


def test_sessions_are_isolated(seeded):
    a = chat(seeded, "Tell me about Goa", stateful=True)["session_id"]
    b = chat(seeded, "Tell me about Paris", stateful=True)["session_id"]
    assert chat(seeded, "what is its budget", session_id=a)["data"]["city"] == "Goa"
    assert chat(seeded, "what is its budget", session_id=b)["data"]["city"] == "Paris"


def test_unknown_session_raises(seeded):
    from app.agents import SessionNotFoundError
    with pytest.raises(SessionNotFoundError):
        chat(seeded, "hi", session_id="nope")


def test_deleting_city_clears_session_city(seeded):
    sid = chat(seeded, "Tell me about Goa", stateful=True)["session_id"]
    chat(seeded, "delete city Goa", session_id=sid)
    assert seeded.sessions.get(sid)["last_city"] is None
