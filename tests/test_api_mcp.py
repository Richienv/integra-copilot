import asyncio
import json

from fastapi.testclient import TestClient

from copilot import api, mcp_server
from copilot.agent import Copilot
from copilot.llm import ScriptedModel
from copilot.tax import TaxIndex


def _plan(sql):
    def fn(messages, json_mode):
        return json.dumps({"kind": "sql", "sql": sql, "reason": ""}) if json_mode else "Done."
    return ScriptedModel(fn)


def test_api(reader):
    api._state.clear()
    api._state.update(reader=reader, copilot=Copilot(reader, _plan("select count(*) as n from customers")),
                      tax_index=TaxIndex([]))
    c = TestClient(api.app)
    assert c.get("/").status_code == 200
    assert {v["name"] for v in c.get("/api/views").json()} >= {"invoices", "stock"}
    assert c.post("/api/sql", json={"sql": "drop table invoices"}).json()["ok"] is False
    assert c.post("/api/sql", json={"sql": "select count(*) from customers"}).json()["rows"] == [[24]]
    body = c.post("/api/ask", json={"question": "How many customers?"}).json()
    assert body["kind"] == "data" and body["rows"] == [[24]]
    assert c.get("/api/health").json()["database_user"] == "copilot_reader"


def test_mcp_tools(reader):
    mcp_server._state.clear()
    mcp_server._state.update(reader=reader, copilot=None, tax_index=TaxIndex([]))
    names = [t.name for t in asyncio.run(mcp_server.mcp.list_tools())]
    assert names == ["list_views", "describe_view", "run_sql", "ask", "search_tax_docs"]
    assert mcp_server.run_sql("delete from invoices")["ok"] is False
    assert mcp_server.run_sql("select count(*) from suppliers")["rows"] == [[10]]
    assert "no model" in mcp_server.ask("anything")["error"].lower()
    assert mcp_server.describe_view("stock")["columns"][0]["name"] == "product_id"
