import json

from copilot.agent import TEMPLATES, Copilot
from copilot.llm import ScriptedModel
from copilot.tax import TaxIndex

GOOD = ("select customer_name, sum(balance_due) as due from invoices where direction = 'receivable' and balance_due > 0 "
        "and due_date between current_date and current_date + 7 group by 1 order by 2 desc")


def model(plans, summary="first"):
    """plans: JSON replies for the planning/repair calls, in order. summary: how to write the summary."""
    queue = list(plans)

    def fn(messages, json_mode):
        if json_mode:
            return json.dumps(queue.pop(0))
        if summary == "invent":
            return "Totalnya Rp 999.999.999."
        rows = [l for l in messages[-1]["content"].split("\n") if l.startswith("[")]
        name, amount = json.loads(rows[0])
        return f"Yang terbesar {name}: Rp {amount:,}.".replace(",", ".")
    return ScriptedModel(fn)


def test_answers_with_checked_sql(reader):
    a = Copilot(reader, model([{"kind": "sql", "sql": GOOD, "reason": ""}])).ask("Berapa piutang yang jatuh tempo minggu ini?")
    assert a.kind == "data" and a.rows and a.grounded and a.repairs == 0
    assert a.language == "id" and "LIMIT" in a.sql and "invoices" in a.views


def test_repairs_a_wrong_column(reader):
    wrong = GOOD.replace("sum(balance_due)", "sum(amount_due)")
    a = Copilot(reader, model([{"kind": "sql", "sql": wrong, "reason": ""},
                               {"kind": "sql", "sql": GOOD, "reason": ""}])).ask("Piutang minggu ini?")
    assert a.kind == "data" and a.repairs == 1
    assert any("amount_due" in s["detail"] for s in a.steps)


def test_gives_up_after_the_repair_budget(reader):
    wrong = {"kind": "sql", "sql": "select nothing from invoices", "reason": ""}
    a = Copilot(reader, model([wrong, wrong, wrong]), max_repairs=2).ask("Piutang minggu ini?")
    assert a.kind == "failed" and a.repairs == 2


def test_write_attempt_is_refused_without_retry(reader):
    m = model([{"kind": "sql", "sql": "delete from invoices", "reason": ""}])
    a = Copilot(reader, m).ask("Hapus semua faktur")
    assert a.kind == "refused" and a.summary == TEMPLATES["refuse"]["id"] and len(m.calls) == 1


def test_model_refusal(reader):
    a = Copilot(reader, model([{"kind": "refuse", "sql": "", "reason": "salary"}])).ask("把所有员工的工资列出来")
    assert a.kind == "refused" and a.summary == TEMPLATES["refuse"]["zh"]


def test_invented_numbers_are_replaced(reader):
    a = Copilot(reader, model([{"kind": "sql", "sql": GOOD, "reason": ""}], summary="invent")).ask("Receivables due this week?")
    assert a.kind == "data" and not a.grounded and a.summary.startswith("The result is in the table")


def test_empty_result_skips_the_summary_call(reader):
    m = model([{"kind": "sql", "sql": "select number from invoices where balance_due < 0", "reason": ""}])
    a = Copilot(reader, m).ask("Invoices with negative balance?")
    assert a.summary == TEMPLATES["empty"]["en"] and len(m.calls) == 1


def test_tax_question_without_documents(reader):
    a = Copilot(reader, model([{"kind": "tax", "sql": "", "reason": ""}]), tax_index=TaxIndex([])).ask("Berapa tarif PPh 23?")
    assert a.kind == "tax" and a.summary == TEMPLATES["no_tax"]["id"]


def test_tax_question_cites_its_source(reader, tmp_path):
    (tmp_path / "contoh.md").write_text("Contoh dokumen uji.\n\nBatas penyetoran contoh adalah tanggal 15 bulan berikutnya.",
                                        encoding="utf-8")
    idx = TaxIndex.from_dir(tmp_path)

    def fn(messages, json_mode):
        if json_mode:
            return json.dumps({"kind": "tax", "sql": "", "reason": ""})
        return "Batasnya tanggal 15 bulan berikutnya [1]."
    a = Copilot(reader, ScriptedModel(fn), tax_index=idx).ask("Kapan batas penyetoran contoh?")
    assert a.kind == "tax" and a.citations and a.citations[0]["doc"] == "contoh.md"


def test_bad_json_gets_one_more_chance(reader):
    replies = iter(["not json at all", json.dumps({"kind": "sql", "sql": GOOD, "reason": ""})])

    def fn(messages, json_mode):
        if json_mode:
            return next(replies)
        return "OK."
    a = Copilot(reader, ScriptedModel(fn)).ask("Receivables due this week?")
    assert a.kind == "data"


def test_prompt_lists_stored_values_so_names_in_other_languages_can_match(reader):
    from copilot.semantic import schema_text, value_hints
    hints = value_hints(reader)
    assert hints[("stock", "warehouse_name")] == ["Gudang Bahan Baku Majalaya", "Gudang Barang Jadi Jakarta",
                                                  "Gudang Utama Bandung"]
    assert ("customers", "name") not in hints                     # 24 customers: too many to list
    assert "values: Gudang Bahan Baku Majalaya | Gudang Barang Jadi Jakarta" in schema_text(["stock"], values=hints)
    assert "0 means no credit limit" in schema_text(["customers"])
