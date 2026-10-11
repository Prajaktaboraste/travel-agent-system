from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


def _clean_tags(tags: list[str] | None) -> list[str]:
    seen: list[str] = []
    for t in tags or []:
        t = " ".join(str(t).strip().lower().split())
        if t and t not in seen:
            seen.append(t)
    return seen


class CityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country: str = Field(default="", max_length=100)
    region: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    best_season: str = Field(default="", max_length=100)
    avg_daily_budget_usd: float | None = Field(default=None, ge=0, le=100000)
    tags: list[str] = Field(default_factory=list)

    @field_validator("name", "country", "region", "best_season")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return _clean_tags(v)


class CityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    best_season: str | None = Field(default=None, max_length=100)
    avg_daily_budget_usd: float | None = Field(default=None, ge=0, le=100000)
    tags: list[str] | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_tags(v)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    stateful: bool = False

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


class ChatResponse(BaseModel):
    agent: str
    intent: str
    reply: str
    route_reason: str
    session_id: str | None = None
    stateful: bool = False
    sources: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] | None = None


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=50000)
    city: str | None = Field(default=None, max_length=100)


class KnowledgeSearch(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    city: str | None = None
    top_k: int = Field(default=4, ge=1, le=20)
