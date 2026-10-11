"""Starts `python -m app.mcp_server` as a real subprocess and talks MCP to it over stdio."""
import asyncio
import json
import os
import sqlite3
import sys

import pytest

from app.config import BASE_DIR

mcp = pytest.importorskip("mcp")


def _text(result):
    content = getattr(result, "content", None) or []
    return json.loads(content[0].text)


async def _session_flow(db_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "DB_PATH": db_path, "SEED_DEMO": "0", "LOAD_KNOWLEDGE": "1",
           "NOTIFY_EMAILS": "boss@example.com", "SMTP_HOST": ""}
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], env=env, cwd=str(BASE_DIR))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            added = _text(await session.call_tool("add_city", {"name": "Lisbon", "country": "Portugal", "tags": ["beach"]}))
            dup = _text(await session.call_tool("add_city", {"name": "Lisbon", "country": "Portugal"}))
            found = _text(await session.call_tool("search_travel_knowledge", {"query": "best time to visit Goa", "city": "Goa"}))
            return names, added, dup, found


def test_real_mcp_server_over_stdio(tmp_path):
    db = str(tmp_path / "mcp.db")
    names, added, dup, found = asyncio.run(asyncio.wait_for(_session_flow(db), timeout=60))
    assert {"add_city", "list_cities", "search_travel_knowledge"} <= names
    assert added["ok"] and added["city"]["name"] == "Lisbon"
    assert dup["ok"] is False and dup["code"] == "duplicate"
    assert found["count"] >= 1 and found["results"][0]["city"] == "Goa"
    with sqlite3.connect(db) as conn:          # the server really wrote to the database + sent the notification
        assert conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0] == 1
        assert conn.execute("SELECT to_addr FROM email_outbox").fetchall() == [("boss@example.com",)]
