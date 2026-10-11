import time

import pytest


def poll(client, job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


# ---------------- misc ----------------
def test_health_and_ui(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["cities"] == 5 and h["documents"] == 8 and h["llm_enabled"] is False
    page = client.get("/")
    assert page.status_code == 200 and "Travel Multi-Agent Assistant" in page.text


def test_openapi_available(client):
    assert client.get("/openapi.json").status_code == 200


# ---------------- city CRUD ----------------
def test_city_crud_over_http(client):
    r = client.post("/api/cities", json={"name": "Lisbon", "country": "Portugal", "tags": ["Beach"], "avg_daily_budget_usd": 90})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.get(f"/api/cities/{cid}").json()["tags"] == ["beach"]
    assert client.put(f"/api/cities/{cid}", json={"avg_daily_budget_usd": 95}).json()["avg_daily_budget_usd"] == 95
    assert any(c["id"] == cid for c in client.get("/api/cities", params={"country": "portugal"}).json())
    assert client.delete(f"/api/cities/{cid}").json()["deleted"]["name"] == "Lisbon"
    assert client.get(f"/api/cities/{cid}").status_code == 404


def test_duplicate_and_validation_errors(client):
    assert client.post("/api/cities", json={"name": "Goa", "country": "India"}).status_code == 409
    assert client.post("/api/cities", json={"name": ""}).status_code == 422
    assert client.post("/api/cities", json={"name": "X", "avg_daily_budget_usd": -3}).status_code == 422
    assert client.get("/api/cities", params={"limit": 0}).status_code == 422
    assert client.put("/api/cities/999", json={"region": "r"}).status_code == 404
    assert client.delete("/api/cities/999").status_code == 404


def test_new_city_over_http_sends_email(client):
    before = len(client.get("/api/emails").json())
    client.post("/api/cities", json={"name": "Oslo", "country": "Norway"})
    mails = client.get("/api/emails").json()
    assert len(mails) == before + 2 and "Oslo"          # two notify recipients in test settings
    assert "Oslo" in mails[0]["subject"] and mails[0]["status"] == "simulated"


def test_tags_endpoint(client):
    tags = {t["name"]: t["cities"] for t in client.get("/api/tags").json()}
    assert tags["food"] == 4


# ---------------- chat: sync, async, stateless, stateful ----------------
def test_sync_chat_stateless(client):
    r = client.post("/api/chat", json={"message": "Best time to visit Goa?"})
    body = r.json()
    assert r.status_code == 200 and body["agent"] == "travel_rag" and body["session_id"] is None
    assert body["sources"][0]["title"] == "Goa Travel Guide"


def test_chat_validation(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 422
    assert client.post("/api/chat", json={}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi", "session_id": "nope"}).status_code == 404


def test_sync_chat_stateful_flow(client):
    first = client.post("/api/chat", json={"message": "Tell me about Tokyo", "stateful": True}).json()
    sid = first["session_id"]
    assert sid and first["stateful"] is True
    second = client.post("/api/chat", json={"message": "What about its budget?", "session_id": sid}).json()
    assert second["data"]["city"] == "Tokyo" and "100-180" in second["reply"]
    hist = client.get(f"/api/sessions/{sid}").json()
    assert hist["last_city"] == "Tokyo" and len(hist["messages"]) == 4
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_stateless_followup_has_no_context(client):
    client.post("/api/chat", json={"message": "Tell me about Tokyo"})
    r = client.post("/api/chat", json={"message": "What about its budget?"}).json()
    assert r["data"]["city"] is None


def test_async_chat_job(client):
    r = client.post("/api/chat/async", json={"message": "Add city Lisbon in Portugal with tags beach"})
    assert r.status_code == 202
    job = poll(client, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["agent"] == "city_master"
    assert client.get("/api/cities", params={"q": "Lisbon"}).json()[0]["name"] == "Lisbon"


def test_async_many_jobs_concurrently(client):
    ids = [client.post("/api/chat/async", json={"message": f"Best time to visit {c}?"}).json()["job_id"]
           for c in ("Goa", "Paris", "Tokyo", "Jaipur", "Bali")]
    results = [poll(client, i) for i in ids]
    assert all(j["status"] == "done" for j in results)
    assert [j["result"]["data"]["city"] for j in results] == ["Goa", "Paris", "Tokyo", "Jaipur", "Bali"]


def test_async_unknown_session_and_job(client):
    assert client.post("/api/chat/async", json={"message": "hi", "session_id": "nope"}).status_code == 404
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_async_stateful(client):
    sid = client.post("/api/chat", json={"message": "Tell me about Paris", "stateful": True}).json()["session_id"]
    job = poll(client, client.post("/api/chat/async", json={"message": "what is its budget", "session_id": sid}).json()["job_id"])
    assert job["result"]["data"]["city"] == "Paris"


# ---------------- knowledge ----------------
def test_knowledge_api(client):
    r = client.post("/api/knowledge", json={"title": "Rome", "content": "Rome has the Colosseum and Vatican.", "city": "Rome"})
    assert r.status_code == 201
    assert client.post("/api/knowledge", json={"title": "Rome", "content": "duplicate title content"}).status_code == 409
    assert client.post("/api/knowledge", json={"title": "x", "content": "short"}).status_code == 422
    found = client.post("/api/knowledge/search", json={"query": "Colosseum", "city": "Rome"}).json()
    assert found["count"] == 1
    assert any(d["title"] == "Rome" for d in client.get("/api/knowledge").json())
    assert client.delete(f"/api/knowledge/{r.json()['id']}").status_code == 204
    assert client.delete("/api/knowledge/9999").status_code == 404


# ---------------- MCP-over-HTTP helper ----------------
def test_mcp_catalogue_and_call(client):
    names = {t["name"] for t in client.get("/api/mcp/tools").json()}
    assert "add_city" in names and "search_travel_knowledge" in names
    res = client.post("/api/mcp/call/list_cities", json={"country": "India"}).json()
    assert res["count"] == 2
    added = client.post("/api/mcp/call/add_city", json={"name": "Rome", "country": "Italy"}).json()
    assert added["ok"] and added["city"]["name"] == "Rome"            # tool arg called `name` works
    assert client.post("/api/mcp/call/nope", json={}).status_code == 404


# ---------------- admin ----------------
@pytest.mark.parametrize("path", ["/api/admin/audit", "/api/admin/reports/countries", "/api/admin/reports/tags",
                                  "/api/admin/reports/budget", "/api/admin/reports/activity",
                                  "/api/admin/reports/emails", "/api/admin/reports/similar/1",
                                  "/api/admin/reports/cities-with-tags?tags=food"])
def test_admin_requires_key(client, admin, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get(path, headers=admin).status_code == 200


def test_admin_reports_content(client, admin):
    rows = client.get("/api/admin/reports/countries", headers=admin).json()
    assert rows[0]["country"] == "India" and rows[0]["city_count"] == 2
    both = client.get("/api/admin/reports/cities-with-tags", params={"tags": "food,culture"}, headers=admin).json()
    assert [c["name"] for c in both] == ["Jaipur", "Paris", "Tokyo"]
    assert client.get("/api/admin/reports/similar/999", headers=admin).status_code == 404
    audit = client.get("/api/admin/audit", params={"action": "insert", "limit": 3}, headers=admin).json()
    assert len(audit) == 3 and {a["action"] for a in audit} == {"INSERT"}
