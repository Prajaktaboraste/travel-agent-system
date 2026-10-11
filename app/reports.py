"""Admin reports: the "complex queries" on the City Master + admin (audit) tables.

They use CTEs, window functions, relational division and self-joins - all plain SQLite.
"""
from __future__ import annotations

from typing import Any

from .database import Database


class ReportService:
    def __init__(self, db: Database):
        self.db = db

    def _rows(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def country_summary(self) -> list[dict[str, Any]]:
        """Cities per country with budget stats, rank and share of all cities (CTE + window functions)."""
        return self._rows("""
            WITH per_country AS (
                SELECT CASE WHEN country = '' THEN 'Unknown' ELSE country END AS country,
                       COUNT(*) AS city_count,
                       ROUND(AVG(avg_daily_budget_usd), 2) AS avg_budget,
                       MIN(avg_daily_budget_usd) AS min_budget,
                       MAX(avg_daily_budget_usd) AS max_budget,
                       GROUP_CONCAT(name, ', ') AS city_names
                FROM cities GROUP BY 1
            )
            SELECT country, city_count, avg_budget, min_budget, max_budget, city_names,
                   RANK() OVER (ORDER BY city_count DESC) AS rank_by_cities,
                   ROUND(100.0 * city_count / SUM(city_count) OVER (), 1) AS pct_of_all_cities
            FROM per_country ORDER BY rank_by_cities, country""")

    def tag_popularity(self) -> list[dict[str, Any]]:
        """N:N report - how many cities carry each tag and their average budget."""
        return self._rows("""
            SELECT t.name AS tag, COUNT(DISTINCT ct.city_id) AS city_count,
                   ROUND(AVG(c.avg_daily_budget_usd), 2) AS avg_budget,
                   GROUP_CONCAT(DISTINCT c.name) AS cities,
                   DENSE_RANK() OVER (ORDER BY COUNT(DISTINCT ct.city_id) DESC) AS popularity_rank
            FROM tags t JOIN city_tags ct ON ct.tag_id = t.id JOIN cities c ON c.id = ct.city_id
            GROUP BY t.id ORDER BY city_count DESC, t.name""")

    def cities_with_all_tags(self, tags: list[str]) -> list[dict[str, Any]]:
        """Relational division: cities that have EVERY requested tag."""
        wanted = sorted({t.strip().lower() for t in tags if t.strip()})
        if not wanted:
            return []
        marks = ",".join("?" for _ in wanted)
        return self._rows(f"""
            SELECT c.id, c.name, c.country, c.avg_daily_budget_usd
            FROM cities c JOIN city_tags ct ON ct.city_id = c.id JOIN tags t ON t.id = ct.tag_id
            WHERE t.name IN ({marks})
            GROUP BY c.id HAVING COUNT(DISTINCT t.name) = ?
            ORDER BY c.name""", [*wanted, len(wanted)])

    def budget_buckets(self) -> list[dict[str, Any]]:
        return self._rows("""
            WITH b AS (
                SELECT name, CASE WHEN avg_daily_budget_usd IS NULL THEN '4. unknown'
                                  WHEN avg_daily_budget_usd < 60  THEN '1. budget (<60)'
                                  WHEN avg_daily_budget_usd < 120 THEN '2. mid (60-119)'
                                  ELSE '3. premium (120+)' END AS bucket
                FROM cities)
            SELECT bucket, COUNT(*) AS city_count, GROUP_CONCAT(name, ', ') AS cities
            FROM b GROUP BY bucket ORDER BY bucket""")

    def similar_cities(self, city_id: int, limit: int = 5) -> list[dict[str, Any]]:
        """Jaccard similarity on tag sets (CTEs + self-join)."""
        return self._rows("""
            WITH mine   AS (SELECT tag_id FROM city_tags WHERE city_id = :id),
                 shared AS (SELECT ct.city_id, COUNT(*) AS shared FROM city_tags ct
                            JOIN mine USING (tag_id) WHERE ct.city_id <> :id GROUP BY ct.city_id),
                 sizes  AS (SELECT city_id, COUNT(*) AS n FROM city_tags GROUP BY city_id)
            SELECT c.id, c.name, c.country, s.shared AS shared_tags,
                   ROUND(1.0 * s.shared / ((SELECT COUNT(*) FROM mine) + z.n - s.shared), 3) AS jaccard
            FROM shared s JOIN sizes z ON z.city_id = s.city_id JOIN cities c ON c.id = s.city_id
            ORDER BY jaccard DESC, c.name LIMIT :limit""", {"id": city_id, "limit": limit})

    def activity_by_day(self) -> list[dict[str, Any]]:
        """Admin-table report: inserts/updates/deletes per day with a running total of net cities."""
        return self._rows("""
            WITH daily AS (
                SELECT date(created_at) AS day,
                       SUM(action = 'INSERT') AS inserts,
                       SUM(action = 'UPDATE') AS updates,
                       SUM(action = 'DELETE') AS deletes
                FROM audit_log WHERE entity = 'city' GROUP BY date(created_at))
            SELECT day, inserts, updates, deletes,
                   SUM(inserts - deletes) OVER (ORDER BY day) AS running_city_total
            FROM daily ORDER BY day""")

    def email_stats(self) -> list[dict[str, Any]]:
        return self._rows("SELECT status, COUNT(*) AS emails FROM email_outbox GROUP BY status ORDER BY status")

    def audit_log(self, limit: int = 50, action: str | None = None) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM audit_log WHERE (:a IS NULL OR action = :a) ORDER BY id DESC LIMIT :n",
            {"a": action.upper() if action else None, "n": max(1, min(limit, 500))})
