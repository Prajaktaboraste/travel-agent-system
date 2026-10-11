"""Wires all components together (used by the web app and by the MCP server)."""
from __future__ import annotations

from dataclasses import dataclass

from .agents import CityMasterAgent, Router, TravelAssistant, TravelRagAgent
from .config import Settings
from .database import Database
from .email_agent import EmailAgent
from .jobs import JobManager
from .llm import LLMClient
from .models import CityCreate
from .rag import Retriever
from .reports import ReportService
from .services import CityService, KnowledgeService, SessionService
from .tools import ToolRegistry

DEMO_CITIES = [
    CityCreate(name="Goa", country="India", region="Goa", best_season="November-February",
               avg_daily_budget_usd=60, tags=["beach", "nightlife", "food"],
               description="Beach destination on India's west coast."),
    CityCreate(name="Jaipur", country="India", region="Rajasthan", best_season="October-March",
               avg_daily_budget_usd=50, tags=["heritage", "culture", "food", "shopping"],
               description="The Pink City, known for forts and palaces."),
    CityCreate(name="Paris", country="France", region="Ile-de-France", best_season="April-June",
               avg_daily_budget_usd=180, tags=["culture", "food", "museums", "romantic"],
               description="Capital of France, famous for art, cafes and landmarks."),
    CityCreate(name="Tokyo", country="Japan", region="Kanto", best_season="March-May",
               avg_daily_budget_usd=140, tags=["food", "technology", "culture", "shopping"],
               description="Japan's capital mixing tradition and technology."),
    CityCreate(name="Bali", country="Indonesia", region="Bali", best_season="April-October",
               avg_daily_budget_usd=70, tags=["beach", "wellness", "nature", "romantic"],
               description="Island known for beaches, temples and rice terraces."),
]


@dataclass
class Container:
    settings: Settings
    db: Database
    cities: CityService
    kb: KnowledgeService
    sessions: SessionService
    email_agent: EmailAgent
    retriever: Retriever
    tools: ToolRegistry
    llm: LLMClient
    city_agent: CityMasterAgent
    rag_agent: TravelRagAgent
    assistant: TravelAssistant
    reports: ReportService
    jobs: JobManager


def build_container(settings: Settings) -> Container:
    db = Database(settings.db_path)
    db.init_schema()
    cities, kb, sessions = CityService(db), KnowledgeService(db), SessionService(db)
    email_agent = EmailAgent(db, settings)
    cities.on_city_added.append(email_agent.on_city_added)     # the "new city trigger" -> Email Agent
    retriever = Retriever(kb)
    tools = ToolRegistry(cities, kb, retriever)
    llm = LLMClient(settings.anthropic_api_key, settings.llm_model)
    city_agent, rag_agent = CityMasterAgent(tools), TravelRagAgent(tools, cities, llm)
    assistant = TravelAssistant(Router(), city_agent, rag_agent, sessions)

    if settings.load_knowledge and kb.count() == 0:
        kb.load_directory(settings.knowledge_dir)
    if settings.seed_demo and cities.count() == 0:
        for demo in DEMO_CITIES:
            cities.add(demo, notify=False)
        email_agent.skip_pending()                              # don't mail about demo data

    return Container(settings, db, cities, kb, sessions, email_agent, retriever, tools, llm, city_agent,
                     rag_agent, assistant, ReportService(db), JobManager())
