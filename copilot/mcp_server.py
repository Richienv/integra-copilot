"""MCP server: the same safe tools, for Claude Code, Claude Desktop, Cursor or any MCP host.

    claude mcp add integra -- /full/path/integra-copilot/.venv/bin/python -m copilot mcp

With these tools the host model can explore the views and write its own SQL, while the guard and the
read-only role still decide what actually runs.
"""
from mcp.server.mcpserver import MCPServer

from . import guard
from .semantic import CATALOG

mcp = MCPServer("integra-copilot", instructions=(
    "Read-only access to Integra ERP data (an Indonesian textile and garment SME) through safe views. "
    "Call list_views, then describe_view, then run_sql with one SELECT. Money is in IDR."))
_state = {}


def _s():
    if not _state:
        from .service import build
        reader, copilot, tax_index = build()
        _state.update(reader=reader, copilot=copilot, tax_index=tax_index)
    return _state


@mcp.tool()
def list_views() -> list[dict]:
    """List the read-only views with one line about each."""
    return [{"name": n, "about": v["about"]} for n, v in CATALOG.items()]


@mcp.tool()
def describe_view(name: str) -> dict:
    """Columns of one view, with types and notes (for example the allowed status values)."""
    v = CATALOG.get(name)
    if not v:
        return {"error": f"No view named {name}. Call list_views."}
    return {"name": name, "about": v["about"],
            "columns": [{"name": c, "type": t, "note": note} for c, t, note in v["columns"]]}


@mcp.tool()
def run_sql(sql: str) -> dict:
    """Run one read-only SELECT over the copilot views. Anything else is refused before it reaches the database."""
    checked = guard.check(sql)
    if not checked.ok:
        return {"ok": False, "refused": checked.reason}
    r = _s()["reader"].run(checked.sql)
    return {"ok": not r.error, "sql": checked.sql, "columns": r.columns, "rows": r.rows[:100],
            "truncated": r.truncated or len(r.rows) > 100, "error": r.error}


@mcp.tool()
def ask(question: str) -> dict:
    """Ask in Indonesian, Chinese or English; Integra Copilot writes and checks the SQL itself."""
    c = _s()["copilot"]
    if not c:
        return {"error": "No model configured on the server. Use run_sql, or set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, or LLM_BACKEND=codex."}
    a = c.ask(question)
    return {k: v for k, v in a.to_dict().items() if k in ("kind", "summary", "sql", "columns", "rows", "citations")}


@mcp.tool()
def search_tax_docs(query: str) -> list[dict]:
    """Search the tax documents in data/tax_docs (only what you put there)."""
    return [{"doc": h["doc"], "chunk": h["chunk"], "text": h["text"][:800]} for h in _s()["tax_index"].search(query)]
