"""The tool layer. These functions are the single interface both agents use
AND the ones exposed to external AI clients by the MCP server (app/mcp_server.py).

Every tool returns a JSON-friendly dict: {"ok": True, ...} or {"ok": False, "error": "..."}.
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError

from .models import CityCreate, CityUpdate
from .rag import Retriever
from .services import (AmbiguousCityError, CityError, CityNotFoundError, CityService, DuplicateCityError,
                       KnowledgeError, KnowledgeService)


def _err(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ValidationError):
        msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return {"ok": False, "error": msg, "code": "validation_error"}
    code = {DuplicateCityError: "duplicate", CityNotFoundError: "not_found", AmbiguousCityError: "ambiguous"}.get(
        type(exc), "error")
    return {"ok": False, "error": str(exc), "code": code}


class ToolRegistry:
    def __init__(self, cities: CityService, kb: KnowledgeService, retriever: Retriever):
        self.cities, self.kb, self.retriever = cities, kb, retriever

    # ---------------- city master tools ----------------
    def add_city(self, name: str, country: str = "", region: str = "", description: str = "",
                 best_season: str = "", avg_daily_budget_usd: float | None = None,
                 tags: list[str] | None = None) -> dict[str, Any]:
        """Add a new city to the City Master table. A new-city trigger notifies the Email Agent."""
        try:
            data = CityCreate(name=name, country=country, region=region, description=description,
                              best_season=best_season, avg_daily_budget_usd=avg_daily_budget_usd, tags=tags or [])
            return {"ok": True, "city": self.cities.add(data)}
        except (CityError, ValidationError) as exc:
            return _err(exc)

    def get_city(self, name: str | None = None, city_id: int | None = None,
                 country: str | None = None) -> dict[str, Any]:
        """Look up one city by id, or by name (optionally with country)."""
        try:
            if city_id is not None:
                return {"ok": True, "city": self.cities.get(city_id)}
            if not name:
                return {"ok": False, "error": "Provide a city name or id", "code": "validation_error"}
            return {"ok": True, "city": self.cities.resolve(name, country)}
        except CityError as exc:
            return _err(exc)

    def list_cities(self, country: str | None = None, tag: str | None = None, search: str | None = None,
                    limit: int = 100) -> dict[str, Any]:
        """List cities, optionally filtered by country, tag or name substring."""
        rows = self.cities.list(country=country, tag=tag, q=search, limit=limit)
        return {"ok": True, "count": len(rows), "total": self.cities.count(), "cities": rows}

    def update_city(self, city_id: int, name: str | None = None, country: str | None = None,
                    region: str | None = None, description: str | None = None, best_season: str | None = None,
                    avg_daily_budget_usd: float | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        """Update fields of an existing city (only the fields you pass are changed)."""
        fields = {k: v for k, v in dict(name=name, country=country, region=region, description=description,
                                        best_season=best_season, avg_daily_budget_usd=avg_daily_budget_usd,
                                        tags=tags).items() if v is not None}
        try:
            return {"ok": True, "city": self.cities.update(city_id, CityUpdate(**fields))}
        except (CityError, ValidationError) as exc:
            return _err(exc)

    def delete_city(self, city_id: int) -> dict[str, Any]:
        """Delete a city by id."""
        try:
            return {"ok": True, "deleted": self.cities.delete(city_id)}
        except CityError as exc:
            return _err(exc)

    # ---------------- knowledge / RAG tools ----------------
    def search_travel_knowledge(self, query: str, city: str | None = None, top_k: int = 4) -> dict[str, Any]:
        """Search the travel knowledge base (RAG retrieval) and return the best matching passages."""
        hits = self.retriever.search(query, city=city, top_k=max(1, min(top_k, 20)))
        return {"ok": True, "count": len(hits), "results": hits}

    def add_travel_document(self, title: str, content: str, city: str | None = None) -> dict[str, Any]:
        """Add a document to the travel knowledge base so the RAG agent can answer from it."""
        try:
            return {"ok": True, "document": self.kb.add(title, content, city, source="tool")}
        except KnowledgeError as exc:
            return {"ok": False, "error": str(exc), "code": "error"}

    # ---------------- registry ----------------
    def tool_functions(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            "add_city": self.add_city,
            "get_city": self.get_city,
            "list_cities": self.list_cities,
            "update_city": self.update_city,
            "delete_city": self.delete_city,
            "search_travel_knowledge": self.search_travel_knowledge,
            "add_travel_document": self.add_travel_document,
        }

    def call(self, tool: str, /, **kwargs: Any) -> dict[str, Any]:
        """Invoke a tool by name. `tool` is positional-only so tools may have a `name` argument."""
        fn = self.tool_functions().get(tool)
        if fn is None:
            return {"ok": False, "error": f"Unknown tool '{tool}'", "code": "unknown_tool"}
        try:
            return fn(**kwargs)
        except TypeError as exc:
            return {"ok": False, "error": f"Bad arguments for {tool}: {exc}", "code": "validation_error"}
