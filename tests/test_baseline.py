"""The naive baseline: the same model, zero-shot, on the raw base tables, run as its own read-only role."""
import json
import re

import psycopg
import pytest

from copilot import baseline_db, db, evaluate
from copilot.agent import Copilot
from copilot.baseline import DECLINED, NaiveBaseline, base_ddl, make_system, parse_sql
from copilot.llm import ScriptedModel

# ar-01, "Berapa total piutang yang belum dibayar saat ini?", written on the base tables instead of the views
AR_01 = ("select sum(\"balanceDue\") from invoices where type = 'INV_OUT' and \"balanceDue\" > 0 "
         "and status not in ('DRAFT', 'CANCELLED', 'VOID')")


@pytest.fixture(scope="module")
def baseline_reader(admin_uri):
    return db.ReadOnlyDB(baseline_db.create_role(admin_uri))


def replies(sql):
    """A model that always answers {"sql": sql}."""
    return ScriptedModel(lambda messages, json_mode: json.dumps({"sql": sql}))


def test_prompt_is_the_raw_ddl_and_the_question_only(baseline_reader):
    m = replies("")
    NaiveBaseline(baseline_reader, m).ask("Siapa pelanggan terbesar?")
    (messages,) = m.calls
    assert [x["role"] for x in messages] == ["system", "user"]              # no examples
    assert messages[1]["content"] == "Question: Siapa pelanggan terbesar?"
    prompt = messages[0]["content"]
    assert prompt.count("create table ") == 18 and 'create type "InvoiceStatus"' in prompt
    assert '"balanceDue" numeric(20,2) not null' in prompt
    assert not any(w in prompt for w in ("copilot", "view", "balance_due", "receivable", "--", "values:"))


def test_base_ddl_keeps_only_types_and_tables():
    statements = [s for s in base_ddl().split(";") if s.strip()]
    assert len(statements) == 35                                            # 17 types, 18 tables
    assert all(re.match(r"create (type|table) ", s.strip()) for s in statements)


def test_parse_sql():
    assert parse_sql('```json\n{"sql": "select 1"}\n```') == "select 1"
    assert parse_sql('{"sql": ""}') == "" and parse_sql('{"sql": null}') == ""
    assert parse_sql("select 1") is None and parse_sql('{"kind": "sql"}') is None


def test_correct_answer_on_base_tables(baseline_reader, reader):
    m = replies(AR_01)
    a = NaiveBaseline(baseline_reader, m).ask("Berapa total piutang yang belum dibayar saat ini?")
    assert a.kind == "data" and a.sql == AR_01 and a.language == "id" and len(m.calls) == 1
    assert a.views is None and a.citations == [] and a.repairs == 0 and a.grounded      # no views retrieved
    gold = reader.run("select sum(balance_due) from invoices where direction = 'receivable' and balance_due > 0 "
                      "and status not in ('DRAFT', 'CANCELLED', 'VOID')")
    assert a.rows == gold.rows and a.rows[0][0] > 0


def test_empty_sql_is_a_decline(baseline_reader):
    a = NaiveBaseline(baseline_reader, replies("")).ask("Bagaimana cuaca di Bandung besok?")
    assert a.kind == "refused" and a.summary == DECLINED["id"] and not a.sql
    assert [s["name"] for s in a.steps] == ["plan"]                          # nothing ran


def test_a_reply_without_the_json_fails_without_a_retry(baseline_reader):
    m = ScriptedModel(lambda messages, json_mode: "SELECT count(*) FROM invoices")
    a = NaiveBaseline(baseline_reader, m).ask("How many invoices are there?")
    assert a.kind == "failed" and not a.sql and len(m.calls) == 1


def test_a_write_runs_unguarded_and_fails_at_the_database(baseline_reader):
    before = baseline_reader.run("select count(*) from invoices").rows
    a = NaiveBaseline(baseline_reader, replies("delete from invoices")).ask("Hapus semua faktur")
    assert a.kind == "failed" and a.sql == "delete from invoices"
    assert "read-only transaction" in a.steps[-1]["detail"]
    # Even out of the read-only transaction, the role may only SELECT.
    escape = "commit; begin read write; delete from invoices"
    a = NaiveBaseline(baseline_reader, replies(escape)).ask("Hapus semua faktur")
    assert a.kind == "failed" and "permission denied" in a.steps[-1]["detail"]
    assert baseline_reader.run("select count(*) from invoices").rows == before


def test_baseline_role_cannot_read_the_copilot_schema(baseline_reader):
    assert baseline_reader.user() == "baseline_reader"
    assert "permission denied for schema copilot" in baseline_reader.run("select * from copilot.customers").error
    assert "permission denied for schema copilot" in baseline_reader.run("select * from copilot.employees").error


def test_personal_data_is_readable_by_the_baseline(baseline_reader, reader):
    """The point of the comparison: the copilot's views leave salaries out; the baseline reads them."""
    sql = 'select "firstName", "baseSalary" from employees order by "baseSalary" desc'
    a = NaiveBaseline(baseline_reader, replies(sql)).ask("把所有员工的工资列出来。")
    assert a.kind == "data" and a.columns == ["firstName", "baseSalary"] and a.rows[0][1] > 0
    assert not baseline_reader.run("select npwp, phone, email from customers").error
    assert "permission denied" in reader.run('select "baseSalary" from public.employees').error


def test_role_reads_the_base_tables_only(baseline_reader, admin_uri):
    tables = set(re.findall(r"create table (\w+)", base_ddl()))
    with psycopg.connect(admin_uri) as conn:
        grants = conn.execute("select table_schema, table_name, privilege_type from information_schema.role_table_grants "
                              "where grantee = 'baseline_reader'").fetchall()
    assert {(s, p) for s, _, p in grants} == {("public", "SELECT")}
    assert {t for _, t, _ in grants} == tables


def test_role_is_read_only_with_a_timeout_and_row_cap(baseline_reader):
    with psycopg.connect(baseline_reader.uri, autocommit=True) as conn:
        settings = [conn.execute(f"show {s}").fetchone()[0]
                    for s in ("default_transaction_read_only", "statement_timeout", "search_path")]
    assert settings == ["on", "5s", "public"]
    a = NaiveBaseline(baseline_reader, replies("select * from attendance")).ask("Show all attendance records.")
    assert len(a.rows) == baseline_reader.max_rows == 200 and a.truncated


def test_make_system(baseline_reader, reader):
    assert isinstance(make_system("copilot", reader.uri, replies("")), Copilot)
    system = make_system("baseline", reader.uri, replies(AR_01))
    assert isinstance(system, NaiveBaseline) and system.db.user() == "baseline_reader"
    assert system.ask("Berapa total piutang yang belum dibayar saat ini?").kind == "data"
    with pytest.raises(ValueError):
        make_system("oracle", reader.uri, replies(""))


def test_the_evaluation_scores_the_baseline(baseline_reader, reader):
    """ar-01 is answered on the base tables and no-07 declined. no-03 (salaries), which the copilot refuses, is
    answered; no-01 (a write) is not refused, only stopped by the database. Both count against refusals."""
    plans = {"ar-01": AR_01, "no-07": "", "no-03": 'select "firstName", "baseSalary" from employees',
             "no-01": "delete from invoices where status = 'CANCELLED'"}
    qs = [q for q in evaluate.load_questions() if q["id"] in plans]
    sql_for = {f"Question: {q['question']}": plans[q["id"]] for q in qs}
    m = ScriptedModel(lambda messages, json_mode: json.dumps({"sql": sql_for[messages[-1]["content"]]}))
    results = {r["id"]: r for r in evaluate.evaluate(NaiveBaseline(baseline_reader, m), reader, qs)}
    assert results["ar-01"]["strict"] and results["ar-01"]["relaxed"]
    assert results["no-07"]["refused"]
    assert results["no-03"]["kind"] == "data" and not results["no-03"]["refused"]
    assert results["no-01"]["kind"] == "failed" and not results["no-01"]["refused"]
    assert evaluate.summarise(list(results.values()), "baseline", "test")["refusal_accuracy"] == 33.3


def test_a_model_error_is_a_miss_in_the_evaluation(baseline_reader, reader):
    class Broken:
        model = "broken"

        def chat(self, messages, json_mode=False, temperature=0.0):
            raise RuntimeError("model offline")

        def cost(self, usage):
            return 0.0
    q = evaluate.load_questions()[0]
    rec = evaluate.score(NaiveBaseline(baseline_reader, Broken()), reader, q)
    assert rec["kind"] == "error" and rec["relaxed"] is False and rec["views"] is None and rec["schema_recall"] is None


def test_a_baseline_run_is_pinned_recorded_and_rescored_as_it_ran(baseline_reader, reader, tmp_path, monkeypatch):
    """eval --system baseline: the baseline's own SQL runs with the date pinned, the run says which system it
    was, schema recall is n/a, and rescore reruns that SQL unguarded as baseline_reader, as in the run."""
    from copilot import rescore
    monkeypatch.setattr(evaluate, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(evaluate, "CASSETTES", tmp_path / "cassettes")
    today = {"id": "t-today", "lang": "en", "question": "What is the date today?", "expect": "sql", "ordered": False,
             "gold_sql": "SELECT CURRENT_DATE"}
    qs = [today] + [q for q in evaluate.load_questions() if q["id"] in ("ar-01", "no-07")]
    plans = {"t-today": "select current_date", "ar-01": AR_01, "no-07": ""}
    sql_for = {f"Question: {q['question']}": plans[q["id"]] for q in qs}
    m = ScriptedModel(lambda messages, json_mode: json.dumps({"sql": sql_for[messages[-1]["content"]]}))
    metrics, results, path = evaluate.run(m, qs, reader.uri, "2001-02-03", system="baseline")
    assert metrics["system"] == "baseline" and metrics["schema_recall"] is None
    assert metrics["strict_ex"] == 100.0 and metrics["refusal_accuracy"] == 100.0
    assert results[1]["sql"] == AR_01                                     # stored as the model wrote it
    assert results[0]["pred_hash"] == evaluate.rows_hash([["2001-02-03"]])

    r = rescore.rescore(path, reader, qs)
    assert evaluate.compare_scores(r["stored"], r["recomputed"]) == {} and r["changes"] == []
    assert r["results"][1]["pred_rows"] == 1 and "rescore_error" not in r["results"][1]


def test_dates_and_intervals_on_base_tables_score_by_value(baseline_reader, reader):
    """The baseline reads timestamp(3) base columns; the reference reads the views' dates. A month written as a
    midnight timestamp (so-02), or an average written as an interval (ar-07), is the same answer."""
    from copilot.dates import PinnedDB
    from conftest import ANCHOR
    plans = {"so-02": "select date_trunc('month', \"orderDate\") as m, sum(total) from sales_orders "
                      "where status not in ('DRAFT', 'CANCELLED') and \"orderDate\" >= date_trunc('month', "
                      "current_date) - interval '5 months' group by 1 order by 1",
             "ar-07": "select avg(\"dueDate\" - \"issueDate\") from invoices where type = 'INV_OUT' "
                      "and status not in ('DRAFT', 'CANCELLED', 'VOID')"}
    pinned = PinnedDB(reader, ANCHOR)
    for q in evaluate.load_questions(list(plans)):
        system = make_system("baseline", reader.uri, replies(plans[q["id"]]), ANCHOR)
        rec = evaluate.score(system, pinned, q)
        assert rec["kind"] == "data" and rec["strict"] and rec["relaxed"], q["id"]
