"""Wiring: build a Copilot from environment variables.

COPILOT_DATABASE_URL  the copilot_reader connection to a real Integra database (optional).
                      Without it, a private local PostgreSQL is started in ~/.cache/integra-copilot/pg
                      (or COPILOT_DATA_DIR) with demo data.
COPILOT_COMPANY       which demo company: A (the default) or B, each in its own database on that server.
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   any OpenAI-compatible API.
LLM_BACKEND=codex     ChatGPT through the Codex CLI instead: the default when no key is set and the ChatGPT
                      app is installed. Without any model, /api/sql and the MCP tools still work;
                      asking in plain language needs a model.
"""
import os
from pathlib import Path

from . import db
from .agent import Copilot
from .llm import from_env
from .tax import TaxIndex

ROOT = Path(__file__).resolve().parent.parent


def reader_from_env(data_dir=None, anchor=None, company=None):
    url = os.environ.get("COPILOT_DATABASE_URL")
    if url:
        return db.ReadOnlyDB(url)
    company = (company or os.environ.get("COPILOT_COMPANY") or "A").upper()
    admin = db.demo_database(db.local_server(data_dir), company)
    db.create_demo(admin, anchor, company)
    return db.ReadOnlyDB(db.reader_uri(admin))


def build(reader=None, llm=None):
    reader = reader or reader_from_env()
    llm = llm or from_env()
    tax_index = TaxIndex.from_dir(ROOT / "data" / "tax_docs")
    return reader, (Copilot(reader, llm, tax_index) if llm else None), tax_index
