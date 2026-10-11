"""The manager's workflow end to end, through the public HTTP API only."""


def test_full_business_workflow(client, admin):
    # 1. Router -> Agent 1 (City Master) adds a city through the MCP tool layer
    r = client.post("/api/chat", json={"message": "Add city Lisbon in Portugal with tags beach, food budget 90"}).json()
    assert (r["agent"], r["intent"]) == ("city_master", "add")
    city_id = r["data"]["city"]["id"]

    # 2. New-city trigger -> Email Agent produced the notification(s)
    mails = client.get("/api/emails").json()
    assert mails and "Lisbon" in mails[0]["subject"]

    # 3. Router -> Agent 2 (Travel RAG): no document for Lisbon yet, so it must not invent an answer
    r = client.post("/api/chat", json={"message": "What is the best time to visit Lisbon?"}).json()
    assert r["agent"] == "travel_rag" and r["data"]["city"] == "Lisbon" and r["data"]["city_in_master"] is True
    assert not [s for s in r["sources"] if s["city"] == "Lisbon"]
    assert "No Lisbon-specific documents" in r["reply"] or "couldn't find" in r["reply"]

    # 4. Knowledge is ingested, RAG now answers from it (and cites it)
    client.post("/api/knowledge", json={"title": "Lisbon Guide", "city": "Lisbon",
                                        "content": "Lisbon is best visited in spring and early autumn. Tram 28 is a classic ride."})
    r = client.post("/api/chat", json={"message": "best time to visit Lisbon"}).json()
    assert "spring" in r["reply"] and r["sources"][0]["title"] == "Lisbon Guide"

    # 5. Stateful follow-up resolves "it"
    first = client.post("/api/chat", json={"message": "Tell me about Lisbon", "stateful": True}).json()
    follow = client.post("/api/chat", json={"message": "Which tram should I ride there?", "session_id": first["session_id"]}).json()
    assert follow["data"]["city"] == "Lisbon" and "Tram 28" in follow["reply"]

    # 6. Admin complex queries see the new city
    cities = client.get("/api/admin/reports/countries", headers=admin).json()
    assert any(c["country"] == "Portugal" for c in cities)
    similar = client.get(f"/api/admin/reports/similar/{city_id}", headers=admin).json()
    assert "Goa" in [s["name"] for s in similar]                 # shares beach + food

    # 7. Delete -> audit trail keeps the history
    assert "Deleted" in client.post("/api/chat", json={"message": "delete city Lisbon"}).json()["reply"]
    audit = client.get("/api/admin/audit", headers=admin, params={"limit": 2}).json()
    assert audit[0]["action"] == "DELETE" and "Lisbon" in audit[0]["details"]
    assert client.get(f"/api/cities/{city_id}").status_code == 404
