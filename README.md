# Travel Multi-Agent System

An agentic-AI travel project built from the manager's brief: **at least two agents**, a **City Master table**,
**RAG + MCP**, an **email agent triggered by new cities**, **stateful and stateless** chat,
**synchronous and asynchronous APIs**, and a **complex-query admin database**.

## How the manager's notes map to the code

| Requirement (from the notes) | Where it lives |
|---|---|
| ≥ 2 agents (business case: travelling) | `CityMasterAgent` (Agent 1) and `TravelRagAgent` (Agent 2) in `app/agents.py`, chosen by `Router` |
| City table: master, city name, id, N→N | `cities`, `tags`, `city_tags` (many-to-many) in `app/database.py` |
| "New city trigger" → email agent | SQL `TRIGGER trg_city_after_insert` writes a `city_events` row → `EmailAgent` (`app/email_agent.py`) mails `NOTIFY_EMAILS` |
| RAG | `app/rag.py` – BM25 retrieval over the `kb_documents` table (optional LLM answer in `app/llm.py`) |
| MCP | `app/mcp_server.py` exposes the tool layer in `app/tools.py` to any MCP client; both agents use the same tools |
| Stateful & stateless | `POST /api/chat` with no session = stateless; `stateful:true` / `session_id` = memory in `sessions` tables |
| API: synchronous & asynchronous | `POST /api/chat` (sync) · `POST /api/chat/async` → `GET /api/jobs/{id}` (async job) |
| Complex database + queries, admin table | `audit_log` (admin table, filled by triggers) + `app/reports.py` (CTEs, window functions, relational division, Jaccard self-join) |

```
User ──► Router ──► Agent 1: City Master ──┐
                └─► Agent 2: Travel RAG ───┤
                                           ▼
                           Tool layer  (also served as an MCP server)
                                           ▼
                SQLite: cities/tags/city_tags · kb_documents · sessions · audit_log · email_outbox
                    │
                    └─ INSERT INTO cities ─► trigger ─► city_events ─► Email Agent ─► email_outbox / SMTP
```

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                                          # http://127.0.0.1:8000  (UI)  ·  /docs (Swagger)
```
On first start it creates `data/travel.db`, loads the 8 sample travel documents in `data/knowledge/` and 5 demo cities.
Everything works offline; no API keys are needed. Copy `.env.example` to `.env` to change settings.

### Run the tests
```bash
python -m pytest -q          # 110 tests
```

### Try it
```bash
curl -X POST localhost:8000/api/chat -H 'content-type: application/json' \
     -d '{"message":"Add city Lisbon in Portugal with tags beach, food budget 90"}'     # Agent 1 + email trigger
curl localhost:8000/api/emails                                                           # the notification
curl -X POST localhost:8000/api/chat -H 'content-type: application/json' \
     -d '{"message":"Best time to visit Goa?"}'                                          # Agent 2 (RAG) with sources
curl -X POST localhost:8000/api/chat -H 'content-type: application/json' \
     -d '{"message":"Tell me about Tokyo","stateful":true}'                              # returns a session_id
curl -X POST localhost:8000/api/chat -H 'content-type: application/json' \
     -d '{"message":"What about its budget?","session_id":"<id>"}'                        # remembers Tokyo
curl -X POST localhost:8000/api/chat/async -H 'content-type: application/json' \
     -d '{"message":"best time to visit Jaipur"}'                                        # -> job_id
curl localhost:8000/api/jobs/<job_id>
curl -H 'X-Admin-Key: dev-admin-key' localhost:8000/api/admin/reports/countries         # complex query
```

Example messages the agents understand: `add city X in Y with tags a, b budget 80 season winter` ·
`find city X` · `do we have X in the database?` · `list cities in India` · `show cities with beaches` ·
`update city X budget to 100` · `delete city X` · `how many cities?`. Anything else is a travel question for the RAG agent.

Admin reports (header `X-Admin-Key`, default `dev-admin-key` – **change `ADMIN_API_KEY`**):
`/api/admin/audit`, `/reports/countries`, `/reports/tags`, `/reports/budget`, `/reports/activity`, `/reports/emails`,
`/reports/cities-with-tags?tags=food,culture`, `/reports/similar/{city_id}`.

## MCP server

```bash
python -m app.mcp_server        # stdio transport
```
Claude Desktop / any MCP client config:
```json
{ "mcpServers": { "travel-city-master": {
    "command": "python", "args": ["-m", "app.mcp_server"], "cwd": "/path/to/travel-agent-system" } } }
```
Tools: `add_city`, `get_city`, `list_cities`, `update_city`, `delete_city`, `search_travel_knowledge`, `add_travel_document`.
Adding a city through MCP also fires the email trigger. Works with `mcp` SDK v1 and v2.

## Email agent
Without `SMTP_HOST` emails are recorded in `email_outbox` with status `simulated`. Set `SMTP_*` in `.env` to send real mail;
delivery failures are stored as `failed` and can be retried with `POST /api/emails/retry`. A failing mail never blocks city creation.

## Optional LLM
Set `ANTHROPIC_API_KEY` to have the RAG agent write fluent answers from the retrieved passages. Without it (or if the call fails)
it returns the retrieved passages directly. The LLM path is covered by tests with a fake client; it has **not** been run against the live API here.

## Known limits
* Routing and city-command parsing are rule-based (predictable, offline). Unusual phrasing falls through to the RAG agent; use the REST/MCP tools for exact control.
* Retrieval is lexical (BM25), not semantic embeddings. The sample documents are brief general guides – verify prices, visas and dates before relying on them.
* Async jobs are kept in memory (lost on restart) and run in a thread pool; SQLite is the single-node database.

## Layout
```
app/        agents.py  tools.py  mcp_server.py  rag.py  email_agent.py  services.py  reports.py  database.py  main.py ...
data/knowledge/   sample travel documents (title:/city: header, then text)
tests/      unit, API, MCP (in-process and real stdio) and end-to-end tests
```
