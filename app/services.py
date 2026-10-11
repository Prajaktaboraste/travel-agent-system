"""Business logic on top of SQLite: cities (master data), knowledge base, sessions."""
from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable

from .database import Database
from .models import CityCreate, CityUpdate

log = logging.getLogger(__name__)


class CityError(Exception):
    """Base class for city-master errors."""


class DuplicateCityError(CityError):
    pass


class CityNotFoundError(CityError):
    pass


class AmbiguousCityError(CityError):
    pass


class KnowledgeError(Exception):
    pass


def _tag_variants(tag: str) -> list[str]:
    """'beaches' -> ['beaches', 'beache', 'beach'] so plural searches still match singular tags."""
    out = [tag]
    if len(tag) > 3 and tag.endswith("s") and not tag.endswith("ss"):
        out.append(tag[:-1])
        if tag.endswith("es"):
            out.append(tag[:-2])
    return out


class CityService:
    def __init__(self, db: Database):
        self.db = db
        # callbacks fired after a city has been committed, e.g. the Email Agent
        self.on_city_added: list[Callable[[dict[str, Any]], None]] = []

    # ---------- helpers ----------
    @staticmethod
    def _tags_for(conn: sqlite3.Connection, city_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT t.name FROM tags t JOIN city_tags ct ON ct.tag_id = t.id "
            "WHERE ct.city_id = ? ORDER BY t.name",
            (city_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = self._tags_for(conn, d["id"])
        return d

    @staticmethod
    def _set_tags(conn: sqlite3.Connection, city_id: int, tags: list[str]) -> None:
        conn.execute("DELETE FROM city_tags WHERE city_id = ?", (city_id,))
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()["id"]
            conn.execute("INSERT OR IGNORE INTO city_tags (city_id, tag_id) VALUES (?, ?)", (city_id, tag_id))

    # ---------- create ----------
    def add(self, data: CityCreate, notify: bool = True) -> dict[str, Any]:
        with self.db.session() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO cities (name, country, region, description, best_season, avg_daily_budget_usd) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (data.name, data.country, data.region, data.description, data.best_season,
                     data.avg_daily_budget_usd),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateCityError(
                    f"City '{data.name}' already exists" + (f" in {data.country}" if data.country else "")
                ) from exc
            city_id = cur.lastrowid
            self._set_tags(conn, city_id, data.tags)
            city = self._to_dict(conn, conn.execute("SELECT * FROM cities WHERE id = ?", (city_id,)).fetchone())
        if notify:
            for callback in self.on_city_added:
                try:
                    callback(city)
                except Exception:  # an email problem must never break city creation
                    log.exception("on_city_added callback failed")
        return city

    # ---------- read ----------
    def get(self, city_id: int) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM cities WHERE id = ?", (city_id,)).fetchone()
            if not row:
                raise CityNotFoundError(f"City with id {city_id} not found")
            return self._to_dict(conn, row)

    def find(self, name: str, country: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM cities WHERE name = ?", [name.strip()]
        if country:
            sql += " AND country = ?"
            params.append(country.strip())
        with self.db.session() as conn:
            return [self._to_dict(conn, r) for r in conn.execute(sql + " ORDER BY country", params)]

    def resolve(self, name: str, country: str | None = None) -> dict[str, Any]:
        matches = self.find(name, country)
        if not matches:
            raise CityNotFoundError(f"City '{name}' not found")
        if len(matches) > 1:
            countries = ", ".join(m["country"] or "unknown" for m in matches)
            raise AmbiguousCityError(f"Several cities named '{name}' exist ({countries}); please specify the country")
        return matches[0]

    def list(self, country: str | None = None, tag: str | None = None, q: str | None = None,
             limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        variants = _tag_variants(tag.strip().lower()) if tag else []
        tag_a, tag_b, tag_c = (variants + [None, None, None])[:3] if variants else (None, None, None)
        tag_b, tag_c = tag_b or tag_a, tag_c or tag_a
        like = f"%{q.strip()}%" if q else None
        sql = (
            "SELECT c.* FROM cities c WHERE (:country IS NULL OR c.country = :country) "
            "AND (:like IS NULL OR c.name LIKE :like) "
            "AND (:tag IS NULL OR EXISTS (SELECT 1 FROM city_tags ct JOIN tags t ON t.id = ct.tag_id "
            "     WHERE ct.city_id = c.id AND t.name IN (:tag, :tag2, :tag3))) "
            "ORDER BY c.name, c.country LIMIT :limit OFFSET :offset"
        )
        params = {"country": country.strip() if country else None, "like": like, "tag": tag_a, "tag2": tag_b, "tag3": tag_c,
                  "limit": max(1, min(limit, 500)), "offset": max(0, offset)}
        with self.db.session() as conn:
            return [self._to_dict(conn, r) for r in conn.execute(sql, params)]

    def count(self) -> int:
        with self.db.session() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM cities").fetchone()["n"]

    def names(self) -> list[str]:
        with self.db.session() as conn:
            return [r["name"] for r in conn.execute("SELECT DISTINCT name FROM cities")]

    def list_tags(self) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT t.name, COUNT(ct.city_id) AS cities FROM tags t "
                "LEFT JOIN city_tags ct ON ct.tag_id = t.id GROUP BY t.id ORDER BY cities DESC, t.name"
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- update / delete ----------
    def update(self, city_id: int, data: CityUpdate) -> dict[str, Any]:
        changes = data.model_dump(exclude_unset=True)
        tags = changes.pop("tags", None)
        with self.db.session() as conn:
            if not conn.execute("SELECT 1 FROM cities WHERE id = ?", (city_id,)).fetchone():
                raise CityNotFoundError(f"City with id {city_id} not found")
            if changes:
                sets = ", ".join(f"{k} = ?" for k in changes) + ", updated_at = datetime('now')"
                try:
                    conn.execute(f"UPDATE cities SET {sets} WHERE id = ?", [*changes.values(), city_id])
                except sqlite3.IntegrityError as exc:
                    raise DuplicateCityError("Another city with that name and country already exists") from exc
            if tags is not None:
                self._set_tags(conn, city_id, tags)
                if not changes:
                    conn.execute("UPDATE cities SET updated_at = datetime('now') WHERE id = ?", (city_id,))
            return self._to_dict(conn, conn.execute("SELECT * FROM cities WHERE id = ?", (city_id,)).fetchone())

    def delete(self, city_id: int) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM cities WHERE id = ?", (city_id,)).fetchone()
            if not row:
                raise CityNotFoundError(f"City with id {city_id} not found")
            city = self._to_dict(conn, row)
            conn.execute("DELETE FROM cities WHERE id = ?", (city_id,))
            return city


class KnowledgeService:
    """Stores the travel documents the RAG agent searches."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, title: str, content: str, city: str | None = None, source: str = "api") -> dict[str, Any]:
        title, content = title.strip(), content.strip()
        if not title or not content:
            raise KnowledgeError("title and content are required")
        with self.db.session() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO kb_documents (title, city, source, content) VALUES (?, ?, ?, ?)",
                    (title, (city or "").strip() or None, source, content),
                )
            except sqlite3.IntegrityError as exc:
                raise KnowledgeError(f"A document titled '{title}' already exists") from exc
            return {"id": cur.lastrowid, "title": title, "city": (city or "").strip() or None, "source": source}

    def list(self) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT id, title, city, source, length(content) AS chars, created_at "
                "FROM kb_documents ORDER BY title").fetchall()
            return [dict(r) for r in rows]

    def all_documents(self) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM kb_documents ORDER BY id")]

    def delete(self, doc_id: int) -> bool:
        with self.db.session() as conn:
            return conn.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,)).rowcount > 0

    def signature(self) -> tuple[int, int, int]:
        """Cheap fingerprint used by the retriever to know when to re-index."""
        with self.db.session() as conn:
            r = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m, COALESCE(SUM(length(content)), 0) AS s "
                "FROM kb_documents").fetchone()
            return (r["n"], r["m"], r["s"])

    def count(self) -> int:
        return self.signature()[0]

    def load_directory(self, directory: str) -> int:
        """Ingest markdown files:  first lines 'title: ..' / 'city: ..', blank line, then the body."""
        loaded = 0
        for path in sorted(Path(directory).glob("*.md")):
            meta: dict[str, str] = {}
            lines = path.read_text(encoding="utf-8").splitlines()
            body_start = 0
            for i, line in enumerate(lines):
                if ":" in line and line.split(":", 1)[0].strip().lower() in {"title", "city"}:
                    k, _, v = line.partition(":")
                    meta[k.strip().lower()] = v.strip()
                    body_start = i + 1
                else:
                    break
            body = "\n".join(lines[body_start:]).strip()
            try:
                self.add(meta.get("title", path.stem.replace("-", " ").title()), body,
                         meta.get("city") or None, source=f"file:{path.name}")
                loaded += 1
            except KnowledgeError:
                log.info("skipping %s (already loaded or empty)", path.name)
        return loaded


class SessionService:
    """Conversation memory used only in *stateful* mode."""

    def __init__(self, db: Database):
        self.db = db

    def create(self) -> str:
        sid = uuid.uuid4().hex
        with self.db.session() as conn:
            conn.execute("INSERT INTO sessions (id) VALUES (?)", (sid,))
        return sid

    def get(self, sid: str) -> dict[str, Any] | None:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            return dict(row) if row else None

    def append(self, sid: str, role: str, content: str, agent: str | None = None) -> None:
        with self.db.session() as conn:
            conn.execute("INSERT INTO session_messages (session_id, role, agent, content) VALUES (?, ?, ?, ?)",
                         (sid, role, agent, content))
            conn.execute("UPDATE sessions SET updated_at = datetime('now') WHERE id = ?", (sid,))

    def set_last_city(self, sid: str, city: str | None) -> None:
        with self.db.session() as conn:
            conn.execute("UPDATE sessions SET last_city = ?, updated_at = datetime('now') WHERE id = ?", (city, sid))

    def history(self, sid: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT role, agent, content, created_at FROM ("
                " SELECT * FROM session_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id", (sid, limit)).fetchall()
            return [dict(r) for r in rows]

    def delete(self, sid: str) -> bool:
        with self.db.session() as conn:
            return conn.execute("DELETE FROM sessions WHERE id = ?", (sid,)).rowcount > 0
