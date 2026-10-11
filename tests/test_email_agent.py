import smtplib

from app.email_agent import EmailAgent
from app.models import CityCreate, CityUpdate


def add(container, name="Lisbon", **kw):
    return container.cities.add(CityCreate(name=name, country="Portugal", tags=["beach"], **kw))


def test_new_city_triggers_one_email_per_recipient(container):
    add(container)
    mails = container.email_agent.list_emails()
    assert len(mails) == 2 and {m["to_addr"] for m in mails} == {"a@example.com", "b@example.com"}
    assert all(m["status"] == "simulated" and "Lisbon" in m["subject"] for m in mails)
    assert "beach" in mails[0]["body"] and "Portugal" in mails[0]["body"]


def test_event_marked_processed_and_idempotent(container):
    add(container)
    assert container.email_agent.process_pending() == []            # nothing left to do
    assert len(container.email_agent.list_emails()) == 2
    with container.db.session() as conn:
        assert conn.execute("SELECT processed FROM city_events").fetchone()[0] == 1


def test_update_and_delete_do_not_send_email(container):
    add(container)
    container.cities.update(1, CityUpdate(region="X"))
    container.cities.delete(1)
    assert len(container.email_agent.list_emails()) == 2


def test_failed_insert_sends_nothing(container):
    add(container)
    from app.services import DuplicateCityError
    try:
        add(container)
    except DuplicateCityError:
        pass
    assert len(container.email_agent.list_emails()) == 2


def test_notify_false_and_skip_pending(container):
    container.cities.add(CityCreate(name="Quiet"), notify=False)
    assert container.email_agent.skip_pending() == 1
    assert container.email_agent.process_pending() == []
    assert container.email_agent.list_emails() == []


def test_email_failure_never_breaks_city_creation(container, tmp_path, monkeypatch):
    from tests.conftest import make_settings
    from app.container import build_container
    c = build_container(make_settings(tmp_path, smtp_host="smtp.invalid", db_path=str(tmp_path / "smtp.db")))

    class Boom:
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("no smtp")

    monkeypatch.setattr(smtplib, "SMTP", Boom)
    city = c.cities.add(CityCreate(name="Oslo"))
    assert city["name"] == "Oslo"
    mails = c.email_agent.list_emails()
    assert len(mails) == 2 and all(m["status"] == "failed" and "no smtp" in m["error"] for m in mails)

    sent = []

    class Ok:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg["To"])

    monkeypatch.setattr(smtplib, "SMTP", Ok)
    retried = c.email_agent.retry_failed()
    assert len(retried) == 2 and all(r["status"] == "sent" for r in retried)
    assert sorted(sent) == ["a@example.com", "b@example.com"]
    assert {m["status"] for m in c.email_agent.list_emails()} == {"sent"}


def test_compose_handles_missing_fields(container):
    subject, body = container.email_agent.compose(
        {"id": 1, "name": "X", "country": "", "region": "", "best_season": "", "avg_daily_budget_usd": None, "tags": []})
    assert "X" in subject and "Tags:        -" in body
