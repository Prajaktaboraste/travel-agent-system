from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: str = str(BASE_DIR / "data" / "travel.db")
    knowledge_dir: str = str(BASE_DIR / "data" / "knowledge")
    admin_api_key: str = "dev-admin-key"
    notify_emails: tuple[str, ...] = ("admin@example.com",)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "travel-agent@example.com"
    smtp_use_tls: bool = True
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5-5"
    seed_demo: bool = True          # insert demo cities on first start
    load_knowledge: bool = True     # ingest data/knowledge/*.md on first start

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(BASE_DIR / ".env")
        env = os.environ.get
        emails = tuple(e.strip() for e in (env("NOTIFY_EMAILS") or "admin@example.com").split(",") if e.strip())
        return cls(
            db_path=env("DB_PATH") or cls.db_path,
            knowledge_dir=env("KNOWLEDGE_DIR") or cls.knowledge_dir,
            admin_api_key=env("ADMIN_API_KEY") or cls.admin_api_key,
            notify_emails=emails,
            smtp_host=env("SMTP_HOST") or None,
            smtp_port=int(env("SMTP_PORT") or 587),
            smtp_user=env("SMTP_USER") or None,
            smtp_password=env("SMTP_PASSWORD") or None,
            smtp_from=env("SMTP_FROM") or cls.smtp_from,
            smtp_use_tls=_truthy(env("SMTP_USE_TLS"), True),
            anthropic_api_key=env("ANTHROPIC_API_KEY") or None,
            llm_model=env("LLM_MODEL") or cls.llm_model,
            seed_demo=_truthy(env("SEED_DEMO"), True),
            load_knowledge=_truthy(env("LOAD_KNOWLEDGE"), True),
        )
