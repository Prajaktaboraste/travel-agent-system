"""Email Agent.

Consumes the `city_events` rows that the SQL trigger creates when a new city is
inserted, builds an email, stores it in `email_outbox` and (if SMTP is
configured) sends it. Without SMTP the mail is recorded as 'simulated', so the
whole flow can be demonstrated and tested offline.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from .config import Settings
from .database import Database

log = logging.getLogger(__name__)


class EmailAgent:
    name = "email_agent"

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    # ---------- composing ----------
    def compose(self, city: dict[str, Any]) -> tuple[str, str]:
        subject = f"New city added to City Master: {city['name']}"
        lines = [
            "Hello,",
            "",
            f"A new city was added to the City Master table.",
            "",
            f"  ID:          {city['id']}",
            f"  Name:        {city['name']}",
            f"  Country:     {city['country'] or '-'}",
            f"  Region:      {city['region'] or '-'}",
            f"  Best season: {city['best_season'] or '-'}",
            f"  Daily budget (USD): {city['avg_daily_budget_usd'] if city['avg_daily_budget_usd'] is not None else '-'}",
            f"  Tags:        {', '.join(city['tags']) or '-'}",
            "",
            "-- Travel Agent System (Email Agent)",
        ]
        return subject, "\n".join(lines)

    # ---------- sending ----------
    def _deliver(self, to_addr: str, subject: str, body: str) -> tuple[str, str]:
        """Returns (status, error)."""
        s = self.settings
        if not s.smtp_host:
            return "simulated", ""
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to_addr, subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_use_tls:
                    smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
            return "sent", ""
        except Exception as exc:  # noqa: BLE001 - we record any delivery failure
            log.warning("email delivery to %s failed: %s", to_addr, exc)
            return "failed", str(exc)[:500]

    def _store(self, event_id: int | None, to_addr: str, subject: str, body: str) -> dict[str, Any]:
        status, error = self._deliver(to_addr, subject, body)
        with self.db.session() as conn:
            cur = conn.execute(
                "INSERT INTO email_outbox (event_id, to_addr, subject, body, status, error, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 'failed' THEN NULL ELSE datetime('now') END)",
                (event_id, to_addr, subject, body, status, error, status),
            )
            return {"id": cur.lastrowid, "to": to_addr, "subject": subject, "status": status}

    # ---------- event processing ----------
    def process_pending(self, city: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Handle every unprocessed 'city_added' event. Idempotent."""
        from .services import CityService  # local import avoids a cycle

        results: list[dict[str, Any]] = []
        with self.db.session() as conn:
            events = conn.execute(
                "SELECT * FROM city_events WHERE processed = 0 AND event_type = 'city_added' ORDER BY id"
            ).fetchall()
        cities = CityService(self.db)
        for ev in events:
            try:
                data = city if city and city["id"] == ev["city_id"] else cities.get(ev["city_id"])
            except Exception:  # city already deleted again -> nothing to announce
                data = {"id": ev["city_id"], "name": ev["city_name"], "country": "", "region": "",
                        "best_season": "", "avg_daily_budget_usd": None, "tags": []}
            subject, body = self.compose(data)
            for to_addr in self.settings.notify_emails:
                results.append(self._store(ev["id"], to_addr, subject, body))
            with self.db.session() as conn:
                conn.execute("UPDATE city_events SET processed = 1 WHERE id = ?", (ev["id"],))
        return results

    def on_city_added(self, city: dict[str, Any]) -> None:
        self.process_pending(city)

    def skip_pending(self) -> int:
        """Mark events as handled without mailing (used when seeding demo data)."""
        with self.db.session() as conn:
            return conn.execute("UPDATE city_events SET processed = 1 WHERE processed = 0").rowcount

    def retry_failed(self) -> list[dict[str, Any]]:
        results = []
        with self.db.session() as conn:
            failed = conn.execute("SELECT * FROM email_outbox WHERE status = 'failed' ORDER BY id").fetchall()
        for row in failed:
            status, error = self._deliver(row["to_addr"], row["subject"], row["body"])
            with self.db.session() as conn:
                conn.execute(
                    "UPDATE email_outbox SET status = ?, error = ?, "
                    "sent_at = CASE WHEN ? = 'failed' THEN NULL ELSE datetime('now') END WHERE id = ?",
                    (status, error, status, row["id"]))
            results.append({"id": row["id"], "to": row["to_addr"], "status": status})
        return results

    def list_emails(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute("SELECT * FROM email_outbox ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
            return [dict(r) for r in rows]
