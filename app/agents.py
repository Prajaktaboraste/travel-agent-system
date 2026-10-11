"""Agents.

  Router            -> decides which agent should handle a message
  CityMasterAgent   -> Agent 1: add / search / list / update / delete cities (via MCP tool layer)
  TravelRagAgent    -> Agent 2: answers travel questions from the knowledge base (RAG)
  TravelAssistant   -> orchestrator; supports stateless and stateful (session memory) chat
(The third agent, EmailAgent, lives in email_agent.py and is driven by the new-city trigger.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMClient
from .services import CityService, SessionService
from .tools import ToolRegistry

# ------------------------------------------------------------------ helpers
MARKER_RE = re.compile(
    r"\b(with\s+tags?|tagged(?:\s+as)?|tags?|best\s+season|season|budget|country|region|description|desc)\b\s*[:=]?\s*",
    re.I,
)
LEADING_FILLER = re.compile(r"^(?:please|kindly|can you|could you|would you)\s+", re.I)
BAD_NAME_START = re.compile(r"^(?:for|to|in|with|that|which|where|is|are|the best|best|good)\b", re.I)


@dataclass
class AgentResult:
    agent: str
    intent: str
    reply: str
    data: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    city: str | None = None            # city the answer was about (stored as session memory)


def _pretty(value: str, upper_short: bool = False) -> str:
    value = " ".join(value.split()).strip(" .,;")
    if value and value == value.lower():
        value = value.upper() if (upper_short and len(value) <= 3) else value.title()
    return value


def split_markers(text: str) -> tuple[str, dict[str, str]]:
    """'Goa in India with tags beach, party budget 80' -> ('Goa in India', {'tags': 'beach, party', 'budget': '80'})"""
    matches = list(MARKER_RE.finditer(text))
    if not matches:
        return text.strip(), {}
    head = text[: matches[0].start()].strip()
    head = re.sub(r"\s+(?:with|and|having)$", "", head, flags=re.I)
    fields: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        key = m.group(1).lower()
        key = ("tags" if "tag" in key else "best_season" if "season" in key
               else "description" if key.startswith("desc") else key)
        value = re.sub(r"^(?:to|as|is|of)\s+", "", text[m.end():end].strip(), flags=re.I)
        value = re.sub(r"\s+(?:and|with)$", "", value, flags=re.I).strip(" ,;")
        if value:
            fields[key] = value
    return head, fields


def split_name_country(head: str) -> tuple[str, str]:
    head = re.sub(r"^(?:the\s+)?(?:city\s+)?(?:called\s+|named\s+)?", "", head.strip(), flags=re.I)
    head = re.sub(r"\s+(?:city|from the (?:database|city master|master table|table)|to the (?:database|city master|master table|table)|(?:in|into|from) the (?:database|city master|master table|table))$",
                  "", head, flags=re.I).strip()
    parts = re.split(r"\s+in\s+|\s*,\s*", head, maxsplit=1, flags=re.I)
    name = _pretty(parts[0])
    country = _pretty(parts[1], upper_short=True) if len(parts) > 1 else ""
    return name, country


def parse_tags(value: str) -> list[str]:
    return [t.strip().lower() for t in re.split(r",|&|\band\b|;", value) if t.strip()]


def parse_number(value: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", value.replace(",", ""))
    return float(m.group(0)) if m else None


# ------------------------------------------------------------------ router
@dataclass
class Route:
    agent: str
    intent: str
    reason: str


_V_ADD = r"(?:add|create|insert|register|save)"
_V_DEL = r"(?:delete|remove|drop)"
_V_UPD = r"(?:update|edit|change|modify|set)"
_V_FIND = r"(?:find|search(?:\s+for)?|look\s*up|get|check|show|display|fetch)"
_V_LIST = r"(?:list|show|display|give|get|fetch|search(?:\s+for)?|find)"


def classify_city_intent(message: str) -> str | None:
    """Rule-based intent detection for City Master operations. None -> not a city operation."""
    t = LEADING_FILLER.sub("", message.strip())
    t = LEADING_FILLER.sub("", t)  # "please can you ..."
    if re.search(r"\bhow many cities\b|\bcount(?: of)? (?:the )?cities\b|\bnumber of cities\b", t, re.I):
        return "count"
    if re.match(rf"^{_V_ADD}\b.*\b(?:city|cities)\b", t, re.I):
        return "add"
    if re.match(rf"^{_V_DEL}\b.*\b(?:city|cities)\b", t, re.I):
        return "delete"
    if re.match(rf"^{_V_UPD}\b.*\b(?:city|cities)\b", t, re.I):
        return "update"
    if re.match(r"^(?:do we have|is there|have we got)\b.*\b(?:database|city master|master|city table|table)\b", t, re.I) \
            or re.match(r"^is\s+.+\s+in\s+(?:the\s+)?(?:database|city master|master table|city table)\b", t, re.I):
        return "find"
    if re.match(rf"^{_V_LIST}\b.*\bcities\b", t, re.I) or re.match(r"^(?:all|list)\s+cities\b", t, re.I):
        return "list"
    m = re.match(rf"^{_V_FIND}\s+(?:the\s+)?city\s+(?:called\s+|named\s+)?(?P<rest>.+)$", t, re.I)
    if m and not BAD_NAME_START.match(m.group("rest").strip()):
        return "find"
    return None


class Router:
    def route(self, message: str) -> Route:
        intent = classify_city_intent(message)
        if intent:
            return Route("city_master", intent, f"Detected a City Master operation ('{intent}')")
        return Route("travel_rag", "question", "No city-record operation detected, treating it as a travel question")


# ------------------------------------------------------------------ Agent 1
class CityMasterAgent:
    name = "city_master"

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    def handle(self, message: str, intent: str, context: dict[str, Any] | None = None) -> AgentResult:
        handler = getattr(self, f"_do_{intent}", None)
        if handler is None:
            return AgentResult(self.name, intent, "Sorry, I can add, find, list, update, count or delete cities.")
        t = LEADING_FILLER.sub("", LEADING_FILLER.sub("", message.strip()))
        return handler(t)

    # --- helpers
    @staticmethod
    def _fmt(c: dict[str, Any]) -> str:
        bits = [f"#{c['id']} {c['name']}" + (f", {c['country']}" if c["country"] else "")]
        if c["best_season"]:
            bits.append(f"best season: {c['best_season']}")
        if c["avg_daily_budget_usd"] is not None:
            bits.append(f"~${c['avg_daily_budget_usd']:g}/day")
        if c["tags"]:
            bits.append("tags: " + ", ".join(c["tags"]))
        return " | ".join(bits)

    def _resolve(self, head: str) -> tuple[dict[str, Any] | None, AgentResult | None, str]:
        """Resolve 'Goa', 'Goa in India' or '5' to a city record or an error result."""
        head = re.sub(r"^(?:the\s+)?(?:city\s+)?(?:with\s+)?(?:id\s*#?\s*)?", "", head.strip(), flags=re.I)
        if re.fullmatch(r"#?\d+", head.strip()):
            res = self.tools.get_city(city_id=int(head.strip().lstrip("#")))
            name = head
        else:
            name, country = split_name_country(head)
            if not name:
                return None, AgentResult(self.name, "error", "Please tell me which city you mean."), ""
            res = self.tools.get_city(name=name, country=country or None)
        if not res["ok"]:
            return None, AgentResult(self.name, "error", res["error"], data=res), name
        return res["city"], None, name

    # --- intents
    def _do_add(self, t: str) -> AgentResult:
        m = re.match(rf"^{_V_ADD}\s+(?:a\s+|the\s+)?(?:new\s+)?(?:city\s+)?(?P<rest>.+)$", t, re.I)
        head, fields = split_markers(m.group("rest") if m else t)
        name, country = split_name_country(head)
        if "country" in fields:
            country = _pretty(fields["country"], upper_short=True)
        if not name or len(name) > 100 or not re.fullmatch(r"[^\W\d_][\w .'\-]*", name, re.U):
            return AgentResult(self.name, "error",
                               "I couldn't find a valid city name. Try: 'Add city Lisbon in Portugal with tags beach, food budget 90'.")
        res = self.tools.add_city(
            name=name, country=country, region=_pretty(fields.get("region", "")),
            description=fields.get("description", ""), best_season=_pretty(fields.get("best_season", "")),
            avg_daily_budget_usd=parse_number(fields["budget"]) if "budget" in fields else None,
            tags=parse_tags(fields["tags"]) if "tags" in fields else [],
        )
        if not res["ok"]:
            return AgentResult(self.name, "error", f"Could not add the city: {res['error']}", data=res)
        c = res["city"]
        return AgentResult(self.name, "add", f"Added city {self._fmt(c)}. The Email Agent has been notified.",
                           data=res, city=c["name"])

    def _do_find(self, t: str) -> AgentResult:
        m = (re.match(rf"^{_V_FIND}\s+(?:the\s+)?city\s+(?:called\s+|named\s+)?(?P<rest>.+)$", t, re.I)
             or re.match(r"^(?:do we have|is there|have we got)\s+(?:a\s+|an\s+)?(?:city\s+)?(?P<rest>.+?)\s+(?:in|on)\s+(?:the\s+)?(?:database|city master|master table|city table|table|master)\b.*$", t, re.I)
             or re.match(r"^is\s+(?P<rest>.+?)\s+in\s+(?:the\s+)?(?:database|city master|master table|city table)\b.*$", t, re.I))
        head = (m.group("rest") if m else t).strip(" ?.!")
        city, err, name = self._resolve(head)
        if err:
            return err if err.data and err.data.get("code") != "not_found" else AgentResult(
                self.name, "find", f"No city named '{name}' is in the City Master yet.", data=err.data, city=None)
        return AgentResult(self.name, "find", f"Found {self._fmt(city)}", data={"ok": True, "city": city}, city=city["name"])

    def _do_list(self, t: str) -> AgentResult:
        _, fields = split_markers(t)
        country = None
        m = re.search(r"\bcities\s+(?:in|of|from)\s+(?P<c>[A-Za-z .'\-]+?)(?:\s+(?:with|tagged|tags?)\b|$)", t, re.I)
        if m:
            country = _pretty(m.group("c"), upper_short=True)
        tag = parse_tags(fields["tags"])[0] if "tags" in fields and parse_tags(fields["tags"]) else None
        if tag is None:
            m2 = re.search(r"\b(?:with|tagged|that (?:have|has)|having)\s+(?:the\s+)?(?:tags?\s+)?(?P<t>[a-z][a-z ]*?)(?:\s+tags?)?\s*[?.!]?$",
                           t, re.I)
            if m2:
                tag = m2.group("t").strip().lower()
        res = self.tools.list_cities(country=country, tag=tag)
        if res["count"] == 0:
            return AgentResult(self.name, "list", "No cities match.", data=res)
        lines = "\n".join(f"- {self._fmt(c)}" for c in res["cities"])
        return AgentResult(self.name, "list", f"{res['count']} cit{'y' if res['count'] == 1 else 'ies'}:\n{lines}", data=res)

    def _do_count(self, t: str) -> AgentResult:
        res = self.tools.list_cities(limit=1)
        return AgentResult(self.name, "count", f"There are {res['total']} cities in the City Master.", data={"total": res["total"]})

    def _do_delete(self, t: str) -> AgentResult:
        m = re.match(rf"^{_V_DEL}\s+(?P<rest>.+)$", t, re.I)
        head = re.sub(r"\s+from\s+(?:the\s+)?(?:database|city master|master table|city table|table)\b.*$", "",
                      (m.group("rest") if m else t), flags=re.I)
        city, err, _ = self._resolve(head.strip(" ?.!"))
        if err:
            return err
        res = self.tools.delete_city(city["id"])
        if not res["ok"]:
            return AgentResult(self.name, "error", res["error"], data=res)
        return AgentResult(self.name, "delete", f"Deleted city {self._fmt(city)}.", data=res, city=None)

    def _do_update(self, t: str) -> AgentResult:
        m = re.match(rf"^{_V_UPD}\s+(?:the\s+)?(?:city\s+)?(?P<rest>.+)$", t, re.I)
        head, fields = split_markers(m.group("rest") if m else t)
        if not fields:
            return AgentResult(self.name, "error",
                               "Tell me what to change, e.g. 'Update city Goa budget to 90' or 'Update city Goa season to winter'.")
        city, err, _ = self._resolve(head.strip(" ?.!"))
        if err:
            return err
        kwargs: dict[str, Any] = {}
        if "budget" in fields:
            n = parse_number(fields["budget"])
            if n is None:
                return AgentResult(self.name, "error", "The budget must be a number.")
            kwargs["avg_daily_budget_usd"] = n
        for key in ("region", "description"):
            if key in fields:
                kwargs[key] = fields[key]
        if "best_season" in fields:
            kwargs["best_season"] = _pretty(fields["best_season"])
        if "country" in fields:
            kwargs["country"] = _pretty(fields["country"], upper_short=True)
        if "tags" in fields:
            kwargs["tags"] = parse_tags(fields["tags"])
        res = self.tools.update_city(city["id"], **kwargs)
        if not res["ok"]:
            return AgentResult(self.name, "error", f"Could not update: {res['error']}", data=res)
        return AgentResult(self.name, "update", f"Updated {self._fmt(res['city'])}", data=res, city=res["city"]["name"])


# ------------------------------------------------------------------ Agent 2
PRONOUN_RE = re.compile(r"\b(it|there|that city|this city|the city|its)\b", re.I)
RAG_SYSTEM = (
    "You are a careful travel assistant. Answer the question using ONLY the provided context passages. "
    "If the context does not contain the answer, say you don't know. Be concise (max 6 sentences)."
)


class TravelRagAgent:
    name = "travel_rag"

    def __init__(self, tools: ToolRegistry, cities: CityService, llm: LLMClient | None = None):
        self.tools, self.cities, self.llm = tools, cities, llm

    def detect_city(self, message: str) -> str | None:
        names = sorted(set(self.cities.names()) | {d["city"] for d in self.tools.kb.all_documents() if d["city"]},
                       key=len, reverse=True)
        for n in names:
            if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", message, re.I):
                return n
        return None

    def handle(self, message: str, intent: str = "question", context: dict[str, Any] | None = None) -> AgentResult:
        context = context or {}
        city = self.detect_city(message)
        used_memory = False
        if not city and context.get("last_city") and PRONOUN_RE.search(message):
            city, used_memory = context["last_city"], True       # stateful follow-up ("what about its budget?")
        query = f"{message} {city}" if used_memory and city else message
        res = self.tools.search_travel_knowledge(query, city=city, top_k=4)
        hits = res["results"]
        in_master = None
        if city:
            in_master = bool(self.cities.find(city))
        sources = [{"title": h["title"], "city": h["city"], "score": h["score"],
                    "snippet": h["text"][:200]} for h in hits]
        data = {"city": city, "used_session_memory": used_memory, "city_in_master": in_master,
                "retrieved": len(hits), "used_llm": False}
        if not hits:
            reply = ("I couldn't find anything about that in the travel knowledge base."
                     + (f" '{city}' has no documents yet - you can add one with the knowledge API." if city else ""))
            return AgentResult(self.name, intent, reply, data=data, sources=[], city=city)
        specific = [h for h in hits if city and h["city"] and h["city"].lower() == city.lower()]
        note = f"(No {city}-specific documents found, here is general guidance.)\n" if city and not specific else ""
        answer = None
        if self.llm and self.llm.available:
            ctx = "\n\n".join(f"[{i + 1}] {h['title']}: {h['text']}" for i, h in enumerate(hits))
            answer = self.llm.complete(RAG_SYSTEM, f"Context:\n{ctx}\n\nQuestion: {message}")
            data["used_llm"] = answer is not None
        if answer is None:
            body = "\n".join(f"- {h['text'].strip().splitlines()[-1] if len(h['text']) > 600 else h['text'].strip()}"
                             for h in hits[:3])
            answer = f"{note}Here is what I found{(' about ' + city) if city else ''}:\n{body}"
        elif note:
            answer = note + answer
        return AgentResult(self.name, intent, answer, data=data, sources=sources, city=city)


# ------------------------------------------------------------------ orchestrator
class SessionNotFoundError(Exception):
    pass


class TravelAssistant:
    def __init__(self, router: Router, city_agent: CityMasterAgent, rag_agent: TravelRagAgent,
                 sessions: SessionService):
        self.router, self.city_agent, self.rag_agent, self.sessions = router, city_agent, rag_agent, sessions

    def chat(self, message: str, session_id: str | None = None, stateful: bool = False) -> dict[str, Any]:
        use_session = stateful or session_id is not None
        context: dict[str, Any] = {}
        sid: str | None = None
        if use_session:
            if session_id:
                session = self.sessions.get(session_id)
                if not session:
                    raise SessionNotFoundError(session_id)
                sid = session_id
                context["last_city"] = session["last_city"]
            else:
                sid = self.sessions.create()
        route = self.router.route(message)
        agent = self.city_agent if route.agent == "city_master" else self.rag_agent
        result = agent.handle(message, route.intent, context)
        if sid:
            self.sessions.append(sid, "user", message)
            self.sessions.append(sid, "assistant", result.reply, agent=result.agent)
            if result.city:
                self.sessions.set_last_city(sid, result.city)
            elif result.intent == "delete":
                self.sessions.set_last_city(sid, None)
        return {"agent": result.agent, "intent": result.intent, "reply": result.reply,
                "route_reason": route.reason, "session_id": sid, "stateful": use_session,
                "sources": result.sources, "data": result.data}
