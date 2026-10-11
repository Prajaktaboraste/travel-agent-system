"""FastAPI application - REST API (sync + async), admin reports and the web UI."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from .agents import SessionNotFoundError
from .config import Settings
from .container import Container, build_container
from .models import (ChatRequest, ChatResponse, CityCreate, CityUpdate, KnowledgeCreate, KnowledgeSearch)
from .services import (AmbiguousCityError, CityNotFoundError, DuplicateCityError, KnowledgeError)

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    c: Container = build_container(settings)
    app = FastAPI(title="Travel Multi-Agent System", version="1.0.0",
                  description="City Master agent + Travel RAG agent + Email agent, exposed via REST and MCP.")
    app.state.container = c

    # ---------------- errors ----------------
    @app.exception_handler(CityNotFoundError)
    async def _nf(_: Request, exc: CityNotFoundError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(DuplicateCityError)
    async def _dup(_: Request, exc: DuplicateCityError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(AmbiguousCityError)
    async def _amb(_: Request, exc: AmbiguousCityError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KnowledgeError)
    async def _kb(_: Request, exc: KnowledgeError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(SessionNotFoundError)
    async def _sess(_: Request, exc: SessionNotFoundError):
        return JSONResponse({"detail": "Unknown session_id"}, status_code=404)

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if not x_admin_key or not secrets.compare_digest(x_admin_key, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="Missing or invalid X-Admin-Key header")

    # ---------------- misc ----------------
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health():
        return {"status": "ok", "cities": c.cities.count(), "documents": c.kb.count(),
                "llm_enabled": c.llm.available, "smtp_enabled": bool(settings.smtp_host)}

    # ---------------- chat: synchronous + asynchronous ----------------
    @app.post("/api/chat", response_model=ChatResponse, tags=["chat"],
              summary="Synchronous chat (stateless unless session_id/stateful is given)")
    async def chat(req: ChatRequest):
        return await run_in_threadpool(c.assistant.chat, req.message, req.session_id, req.stateful)

    @app.post("/api/chat/async", status_code=202, tags=["chat"],
              summary="Asynchronous chat: returns a job id, poll /api/jobs/{id}")
    async def chat_async(req: ChatRequest):
        if req.session_id and not c.sessions.get(req.session_id):
            raise SessionNotFoundError(req.session_id)
        job_id = c.jobs.submit(c.assistant.chat, req.message, req.session_id, req.stateful)
        return {"job_id": job_id, "status": "queued", "poll_url": f"/api/jobs/{job_id}"}

    @app.get("/api/jobs/{job_id}", tags=["chat"])
    async def job_status(job_id: str):
        job = c.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job id")
        return job

    @app.get("/api/sessions/{session_id}", tags=["chat"])
    async def session_history(session_id: str):
        session = c.sessions.get(session_id)
        if not session:
            raise SessionNotFoundError(session_id)
        return {**session, "messages": c.sessions.history(session_id)}

    @app.delete("/api/sessions/{session_id}", status_code=204, tags=["chat"])
    async def session_delete(session_id: str):
        if not c.sessions.delete(session_id):
            raise SessionNotFoundError(session_id)

    # ---------------- City Master CRUD ----------------
    @app.post("/api/cities", status_code=201, tags=["cities"])
    async def create_city(city: CityCreate):
        return await run_in_threadpool(c.cities.add, city)

    @app.get("/api/cities", tags=["cities"])
    async def list_cities(country: str | None = None, tag: str | None = None, q: str | None = None,
                          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        return await run_in_threadpool(c.cities.list, country, tag, q, limit, offset)

    @app.get("/api/cities/{city_id}", tags=["cities"])
    async def get_city(city_id: int):
        return await run_in_threadpool(c.cities.get, city_id)

    @app.put("/api/cities/{city_id}", tags=["cities"])
    async def update_city(city_id: int, patch: CityUpdate):
        return await run_in_threadpool(c.cities.update, city_id, patch)

    @app.delete("/api/cities/{city_id}", tags=["cities"])
    async def delete_city(city_id: int):
        return {"deleted": await run_in_threadpool(c.cities.delete, city_id)}

    @app.get("/api/tags", tags=["cities"])
    async def tags():
        return await run_in_threadpool(c.cities.list_tags)

    # ---------------- knowledge base (RAG) ----------------
    @app.post("/api/knowledge", status_code=201, tags=["knowledge"])
    async def add_document(doc: KnowledgeCreate):
        return await run_in_threadpool(c.kb.add, doc.title, doc.content, doc.city, "api")

    @app.get("/api/knowledge", tags=["knowledge"])
    async def list_documents():
        return await run_in_threadpool(c.kb.list)

    @app.delete("/api/knowledge/{doc_id}", status_code=204, tags=["knowledge"])
    async def delete_document(doc_id: int):
        if not await run_in_threadpool(c.kb.delete, doc_id):
            raise HTTPException(404, "Document not found")

    @app.post("/api/knowledge/search", tags=["knowledge"])
    async def search_knowledge(q: KnowledgeSearch):
        return await run_in_threadpool(c.tools.search_travel_knowledge, q.query, q.city, q.top_k)

    # ---------------- email agent ----------------
    @app.get("/api/emails", tags=["email"])
    async def emails(limit: int = Query(50, ge=1, le=500)):
        return await run_in_threadpool(c.email_agent.list_emails, limit)

    @app.post("/api/emails/retry", tags=["email"])
    async def retry_emails():
        return await run_in_threadpool(c.email_agent.retry_failed)

    # ---------------- MCP tool catalogue ----------------
    @app.get("/api/mcp/tools", tags=["mcp"])
    async def mcp_tools():
        return [{"name": n, "description": (f.__doc__ or "").strip()} for n, f in c.tools.tool_functions().items()]

    @app.post("/api/mcp/call/{tool}", tags=["mcp"])
    async def mcp_call(tool: str, arguments: dict[str, Any] | None = None):
        result = await run_in_threadpool(lambda: c.tools.call(tool, **(arguments or {})))
        if not result["ok"] and result.get("code") == "unknown_tool":
            raise HTTPException(404, result["error"])
        return result

    # ---------------- admin (complex queries) ----------------
    admin = [Depends(require_admin)]

    @app.get("/api/admin/audit", dependencies=admin, tags=["admin"])
    async def audit(limit: int = Query(50, ge=1, le=500), action: str | None = None):
        return await run_in_threadpool(c.reports.audit_log, limit, action)

    @app.get("/api/admin/reports/countries", dependencies=admin, tags=["admin"])
    async def r_countries():
        return await run_in_threadpool(c.reports.country_summary)

    @app.get("/api/admin/reports/tags", dependencies=admin, tags=["admin"])
    async def r_tags():
        return await run_in_threadpool(c.reports.tag_popularity)

    @app.get("/api/admin/reports/budget", dependencies=admin, tags=["admin"])
    async def r_budget():
        return await run_in_threadpool(c.reports.budget_buckets)

    @app.get("/api/admin/reports/activity", dependencies=admin, tags=["admin"])
    async def r_activity():
        return await run_in_threadpool(c.reports.activity_by_day)

    @app.get("/api/admin/reports/emails", dependencies=admin, tags=["admin"])
    async def r_emails():
        return await run_in_threadpool(c.reports.email_stats)

    @app.get("/api/admin/reports/cities-with-tags", dependencies=admin, tags=["admin"])
    async def r_all_tags(tags: str = Query(..., description="comma separated, city must have ALL")):
        return await run_in_threadpool(c.reports.cities_with_all_tags, tags.split(","))

    @app.get("/api/admin/reports/similar/{city_id}", dependencies=admin, tags=["admin"])
    async def r_similar(city_id: int, limit: int = Query(5, ge=1, le=50)):
        await run_in_threadpool(c.cities.get, city_id)           # 404 if unknown
        return await run_in_threadpool(c.reports.similar_cities, city_id, limit)

    return app


def get_app() -> FastAPI:  # for `uvicorn app.main:get_app --factory`
    return create_app()
