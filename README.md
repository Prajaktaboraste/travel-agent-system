# Travel Multi-Agent System

An agentic AI travel project built to meet the manager's requirements: multiple agents, a City Master table, Retrieval-Augmented Generation (RAG), Model Context Protocol (MCP), email notifications for new cities, stateful and stateless chat, synchronous and asynchronous APIs, and complex admin database queries.

## Features and Architecture

| Requirement | Implementation |
|---|---|
| Multiple AI agents | `CityMasterAgent` and `TravelRagAgent` in `app/agents.py`, selected by the Router |
| City Master database | `cities`, `tags`, and `city_tags` tables in `app/database.py`, supporting many-to-many relationships |
| Email notification trigger | SQL trigger `trg_city_after_insert` records city events, which the Email Agent processes |
| RAG | `app/rag.py` uses BM25 retrieval over the `kb_documents` table |
| MCP | `app/mcp_server.py` exposes tools from `app/tools.py` to MCP clients |
| Stateful and stateless chat | `POST /api/chat` supports stateless requests and session-based conversation memory |
| Synchronous and asynchronous APIs | `POST /api/chat` for synchronous requests; `POST /api/chat/async` and `GET /api/jobs/{id}` for asynchronous jobs |
| Admin database reports | `audit_log` and `app/reports.py` provide complex SQL reports using CTEs, window functions, relational division, and Jaccard similarity |

## Architecture Overview

```text
User
 |
 v
Router
 |-------------------------|
 v                         v
City Master Agent      Travel RAG Agent
 |                         |
 |----------+--------------|
            v
         Tool Layer
            |
      SQLite Database
            |
   +--------+---------+
   |                  |
City tables       Knowledge base
   |
Insert new city
   |
SQL trigger
   |
City events
   |
Email Agent
   |
Email outbox / SMTP
```

## Technology Stack

- Python 3.10+
- SQLite
- FastAPI
- BM25 information retrieval
- MCP SDK
- Optional Anthropic API integration
- Pytest
- HTML-based web interface

## Installation and Setup

### 1. Navigate to the project directory

```bash
cd travel-agent-system
```

### 2. Create a virtual environment

**Windows (Command Prompt):**

```bat
python -m venv .venv
.venv\Scripts\activate
```

**Linux or macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` if you need to customize the configuration. Review the available settings before enabling email delivery or optional LLM features.

### 5. Run the application

```bash
python run.py
```

The application is expected to be available at:

- Web interface: `http://127.0.0.1:8000`
- API documentation: `http://127.0.0.1:8000/docs`

On first startup, the application initializes `data/travel.db`, loads sample travel documents, and creates demo cities, according to the project configuration.

## Running Tests

Run the test suite with:

```bash
python -m pytest -q
```

The project documentation reports 110 tests. The actual test count and results should be verified by running the command locally.

## API Examples

### Add a city

```bash
curl -X POST http://localhost:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"Add city Lisbon in Portugal with tags beach, food budget 90\"}"
```

### Retrieve email notifications

```bash
curl http://localhost:8000/api/emails
```

### Ask a travel question using RAG

```bash
curl -X POST http://localhost:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"Best time to visit Goa?\"}"
```

### Start a stateful conversation

```bash
curl -X POST http://localhost:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"Tell me about Tokyo\",\"stateful\":true}"
```

Use the returned `session_id` for subsequent requests:

```bash
curl -X POST http://localhost:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"What about its budget?\",\"session_id\":\"YOUR_SESSION_ID\"}"
```

### Start an asynchronous request

```bash
curl -X POST http://localhost:8000/api/chat/async ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"Best time to visit Jaipur\"}"
```

Use the returned job ID to check the result:

```bash
curl http://localhost:8000/api/jobs/YOUR_JOB_ID
```

### Access admin reports

```bash
curl -H "X-Admin-Key: YOUR_ADMIN_KEY" ^
  http://localhost:8000/api/admin/reports/countries
```

Replace `YOUR_ADMIN_KEY` with the configured admin key.

## Supported City Operations

The City Master Agent supports commands such as:

- Add a city with its country, tags, budget, or season.
- Find a city by name.
- Check whether a city exists in the database.
- List cities by country.
- Search for cities with particular tags.
- Update a city's budget.
- Delete a city.
- Count the cities in the database.

Examples:

```text
Add city Lisbon in Portugal with tags beach, food budget 90
Find city Tokyo
List cities in India
Show cities with beaches
Update city Goa budget to 100
How many cities?
```

Other travel-related questions are routed to the Travel RAG Agent.

## Admin Reports

Admin endpoints use the `X-Admin-Key` header. Available reports documented by the project include:

- `/api/admin/audit`
- `/api/admin/reports/countries`
- `/api/admin/reports/tags`
- `/api/admin/reports/budget`
- `/api/admin/reports/activity`
- `/api/admin/reports/emails`
- `/api/admin/reports/cities-with-tags?tags=food,culture`
- `/api/admin/reports/similar/{city_id}`

Change the default development admin key before deploying the application.

## MCP Server Setup

Start the MCP server using:

```bash
python -m app.mcp_server
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "travel-city-master": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/travel-agent-system"
    }
  }
}
```

Replace `/path/to/travel-agent-system` with the absolute path to your project directory.

Available MCP tools include:

- `add_city`
- `get_city`
- `list_cities`
- `update_city`
- `delete_city`
- `search_travel_knowledge`
- `add_travel_document`

Adding a city through MCP also invokes the configured database trigger.

## Email Agent

When SMTP settings are not configured, notification emails are recorded in the `email_outbox` table with a simulated status.

To enable real email delivery, configure the appropriate `SMTP_*` environment variables in `.env`.

Failed deliveries can be recorded and retried using the application's email retry endpoint, if configured.

## Optional LLM Integration

Set `ANTHROPIC_API_KEY` in `.env` to enable optional LLM-generated answers based on retrieved travel passages.

Without a working API configuration, the application can return retrieved passages directly, according to its fallback implementation.

## Known Limitations

- City-command parsing and routing use rule-based logic.
- Travel knowledge retrieval uses lexical BM25 matching rather than semantic embeddings.
- Sample travel documents provide general guidance; verify current prices, visa requirements, and travel restrictions independently.
- Asynchronous jobs are held in memory and may be lost when the application restarts.
- SQLite is intended for this project's single-node database setup.
- Live LLM behavior and external email delivery depend on their respective configurations.

## Project Structure

```text
travel-agent-system/
├── app/
│   ├── agents.py
│   ├── tools.py
│   ├── mcp_server.py
│   ├── rag.py
│   ├── email_agent.py
│   ├── services.py
│   ├── reports.py
│   ├── database.py
│   ├── main.py
│   └── static/
├── data/
│   └── knowledge/
├── tests/
├── .env.example
├── .gitignore
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── run.py
└── README.md
```

## Security Notes

- Never commit `.env` files containing API keys, SMTP credentials, or other secrets.
- Use a strong admin API key outside development.
- Verify authentication and authorization settings before exposing admin endpoints publicly.

## License

Add the appropriate license information if this project is to be distributed publicly.
