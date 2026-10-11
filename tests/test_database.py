import sqlite3

import pytest


def test_schema_created(container):
    with container.db.session() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        triggers = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"cities", "tags", "city_tags", "city_events", "audit_log", "email_outbox", "kb_documents",
            "sessions", "session_messages"} <= tables
    assert {"trg_city_after_insert", "trg_city_after_update", "trg_city_after_delete"} <= triggers


def test_schema_init_is_idempotent(container):
    container.db.init_schema()
    container.db.init_schema()


def test_insert_trigger_creates_event_and_audit(container):
    with container.db.session() as conn:
        conn.execute("INSERT INTO cities (name, country) VALUES ('Rome', 'Italy')")
    with container.db.session() as conn:
        ev = conn.execute("SELECT * FROM city_events").fetchall()
        au = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(ev) == 1 and ev[0]["city_name"] == "Rome" and ev[0]["processed"] == 0
    assert au[0]["action"] == "INSERT" and "Rome" in au[0]["details"]


def test_update_and_delete_triggers(container):
    with container.db.session() as conn:
        conn.execute("INSERT INTO cities (name, country) VALUES ('Rome', 'Italy')")
        conn.execute("UPDATE cities SET region = 'Lazio' WHERE name = 'Rome'")
        conn.execute("DELETE FROM cities WHERE name = 'Rome'")
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["INSERT", "UPDATE", "DELETE"]


def test_city_name_unique_per_country_case_insensitive(container):
    with container.db.session() as conn:
        conn.execute("INSERT INTO cities (name, country) VALUES ('Paris', 'France')")
    with pytest.raises(sqlite3.IntegrityError):
        with container.db.session() as conn:
            conn.execute("INSERT INTO cities (name, country) VALUES ('PARIS', 'france')")
    with container.db.session() as conn:  # same name in another country is allowed
        conn.execute("INSERT INTO cities (name, country) VALUES ('Paris', 'USA')")


def test_blank_name_and_negative_budget_rejected(container):
    for sql in ("INSERT INTO cities (name) VALUES ('   ')",
                "INSERT INTO cities (name, avg_daily_budget_usd) VALUES ('X', -1)"):
        with pytest.raises(sqlite3.IntegrityError):
            with container.db.session() as conn:
                conn.execute(sql)


def test_many_to_many_cascade_on_delete(container):
    with container.db.session() as conn:
        conn.execute("INSERT INTO cities (name) VALUES ('A')")
        conn.execute("INSERT INTO tags (name) VALUES ('beach')")
        conn.execute("INSERT INTO city_tags VALUES (1, 1)")
        conn.execute("DELETE FROM cities WHERE id = 1")
        assert conn.execute("SELECT COUNT(*) FROM city_tags").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1


def test_session_rolls_back_on_error(container):
    with pytest.raises(RuntimeError):
        with container.db.session() as conn:
            conn.execute("INSERT INTO cities (name) VALUES ('Ghost')")
            raise RuntimeError("boom")
    with container.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0] == 0
