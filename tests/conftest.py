import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, Settings  # noqa: E402
from app.container import build_container  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN = {"X-Admin-Key": "test-admin"}


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(db_path=str(tmp_path / "test.db"), knowledge_dir=str(BASE_DIR / "data" / "knowledge"),
                admin_api_key="test-admin", notify_emails=("a@example.com", "b@example.com"),
                seed_demo=False, load_knowledge=False)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def container(settings):
    return build_container(settings)


@pytest.fixture
def seeded(tmp_path):
    """Container with the demo cities and the shipped knowledge base."""
    return build_container(make_settings(tmp_path, seed_demo=True, load_knowledge=True))


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path, seed_demo=True, load_knowledge=True))
    with TestClient(app) as c:      # context manager keeps the event loop alive for async jobs
        yield c


@pytest.fixture
def admin():
    return ADMIN
