"""HTTP API and the demo page.

    python -m copilot serve         then open http://127.0.0.1:8000

This has no login. Run it locally or behind your own authentication; never expose it publicly as is.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import guard
from .semantic import CATALOG

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Integra Copilot")
_state = {}


def state():
    if not _state:
        from .service import build
        reader, copilot, tax_index = build()
        _state.update(reader=reader, copilot=copilot, tax_index=tax_index)
    return _state


class AskIn(BaseModel):
    question: str


class SqlIn(BaseModel):
    sql: str


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/health")
def health():
    s = state()
    return {"database_user": s["reader"].user(), "model": getattr(s["copilot"].llm, "model", None) if s["copilot"] else None,
            "tax_chunks": len(s["tax_index"])}


@app.get("/api/views")
def views():
    return [{"name": n, "about": v["about"], "columns": [c for c, _, _ in v["columns"]]} for n, v in CATALOG.items()]


@app.post("/api/ask")
def ask(body: AskIn):
    s = state()
    if not s["copilot"]:
        raise HTTPException(503, "No model configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL, or LLM_BACKEND=codex.")
    if not body.question.strip():
        raise HTTPException(400, "Empty question.")
    return s["copilot"].ask(body.question.strip()[:1000]).to_dict()


@app.post("/api/sql")
def run_sql(body: SqlIn):
    checked = guard.check(body.sql)
    if not checked.ok:
        return {"ok": False, "category": checked.category, "reason": checked.reason}
    r = state()["reader"].run(checked.sql)
    return {"ok": not r.error, "sql": checked.sql, "columns": r.columns, "rows": r.rows,
            "truncated": r.truncated, "ms": round(r.ms, 1), "error": r.error}
