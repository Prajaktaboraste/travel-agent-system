import asyncio
import json

import pytest

from app.mcp_server import build_server

EXPECTED = {"add_city", "get_city", "list_cities", "update_city", "delete_city",
            "search_travel_knowledge", "add_travel_document"}


def payload(result):
    """Normalise MCP SDK v1/v2 call results into a dict."""
    content = getattr(result, "content", None)
    if content is None:                      # v1 may return (content, structured) or a list
        content = result[0] if isinstance(result, tuple) else result
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured
    return json.loads(content[0].text)


def call(server, name, args):
    return payload(asyncio.run(server.call_tool(name, args)))


def test_registry_direct_calls(container):
    t = container.tools
    r = t.add_city("Lisbon", country="Portugal", tags=["Beach"], avg_daily_budget_usd=90)
    assert r["ok"] and r["city"]["tags"] == ["beach"]
    assert t.add_city("Lisbon", country="Portugal")["code"] == "duplicate"
    assert t.add_city("")["code"] == "validation_error"
    assert t.add_city("X", avg_daily_budget_usd=-1)["code"] == "validation_error"
    assert t.get_city(name="lisbon")["city"]["id"] == 1
    assert t.get_city(city_id=1)["ok"] and t.get_city(city_id=5)["code"] == "not_found"
    assert t.get_city()["code"] == "validation_error"
    assert t.list_cities(tag="beach")["count"] == 1
    assert t.update_city(1, region="Lisboa")["city"]["region"] == "Lisboa"
    assert t.update_city(9, region="x")["code"] == "not_found"
    assert t.delete_city(1)["ok"] and t.delete_city(1)["code"] == "not_found"


def test_call_dispatch_and_bad_args(container):
    assert container.tools.call("nope")["code"] == "unknown_tool"
    assert container.tools.call("add_city", bogus=1)["code"] == "validation_error"
    assert container.tools.call("add_city", name="Rome")["ok"]


def test_knowledge_tools(container):
    t = container.tools
    assert t.add_travel_document("Rome", "Rome is known for the Colosseum and the Vatican museums.", "Rome")["ok"]
    assert t.add_travel_document("rome", "duplicate title with different case ......")["ok"] is False
    res = t.search_travel_knowledge("Colosseum", city="Rome")
    assert res["count"] == 1 and res["results"][0]["title"] == "Rome"


def test_mcp_server_lists_all_tools(container):
    server = build_server(container.tools)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == EXPECTED
    schema = {t.name: t for t in tools}["add_city"]
    props = getattr(schema, "input_schema", None) or getattr(schema, "inputSchema")
    assert "name" in props["properties"] and props["required"] == ["name"]


def test_mcp_server_tool_calls_hit_the_database_and_email_agent(container):
    server = build_server(container.tools)
    res = call(server, "add_city", {"name": "Rome", "country": "Italy", "tags": ["history"]})
    assert res["ok"] and res["city"]["name"] == "Rome"
    assert container.cities.count() == 1
    assert len(container.email_agent.list_emails()) == 2         # MCP path also triggers the Email Agent
    assert call(server, "list_cities", {"tag": "history"})["count"] == 1
    assert call(server, "delete_city", {"city_id": 1})["ok"]
    assert call(server, "delete_city", {"city_id": 1})["code"] == "not_found"


def test_mcp_server_search_tool(seeded):
    server = build_server(seeded.tools)
    res = call(server, "search_travel_knowledge", {"query": "Schengen visa", "top_k": 2})
    assert res["count"] >= 1 and res["results"][0]["title"] == "Visa and Travel Documents"
