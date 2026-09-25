"""The two demo companies: A (the development set) and B (the blind test). A's data must never change, B's
is frozen once published, the two share no names, and B keeps the same books rules and the same locks."""
import datetime as dt
import decimal
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from conftest import ANCHOR, LATE
from copilot import db, semantic, service
from copilot.__main__ import main
from data.seed import COMPANIES, generate

ROOT = Path(__file__).resolve().parent.parent

# sha256 of every row of all 16 copilot views (see views_digest). Company A's two values were taken from the
# code before company B existed (commit 45387db), run with PYTHONHASHSEED=0 (see
# test_data_does_not_depend_on_the_hash_seed).
FROZEN = {
    ("A", ANCHOR): "5ef5707932f06b609044300169684580a292a97d2a5efea1f86a931766fa1182",
    ("A", LATE): "898285376a3eff86edaed67dd4637222f17ea09ff81eac58a7327da95b46163c",
    ("B", LATE): "30789fe83fc544a0725d9a8e4fcd8bf22ad28c965f32e4343f1266de99358e5f",
}


def _canon(v):
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v if v is None or isinstance(v, (bool, int, str)) else str(v)


def view_contents(uri):
    """{view: {"columns": [...], "rows": [canonical JSON of each row, sorted]}} for every copilot view."""
    out = {}
    with psycopg.connect(uri) as conn:
        names = [r[0] for r in conn.execute(
            "select table_name from information_schema.views where table_schema = 'copilot' order by 1")]
        for name in names:
            cur = conn.execute(f"select * from copilot.{name}")
            out[name] = {"columns": [d.name for d in cur.description],
                         "rows": sorted(json.dumps([_canon(v) for v in r], ensure_ascii=False, separators=(",", ":"))
                                        for r in cur)}
    return out


def views_digest(uri):
    blob = json.dumps(view_contents(uri), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@pytest.fixture(scope="session")
def company_a_late_uri(admin_uri):
    uri = db.database(admin_uri, "company_a_20261015")
    db.create_demo(uri, anchor=LATE)
    return uri


@pytest.fixture(params=[("A", ANCHOR), ("A", LATE), ("B", LATE)], ids=lambda p: f"{p[0]}-{p[1]}")
def demo(request):
    """(company, anchor, admin URI) for each frozen database."""
    company, anchor = request.param
    fixture = {("A", ANCHOR): "admin_uri", ("A", LATE): "company_a_late_uri", ("B", LATE): "company_b_uri"}
    return company, anchor, request.getfixturevalue(fixture[company, anchor])


def test_view_contents_are_frozen(demo):
    company, anchor, uri = demo
    reader = db.reader_uri(uri)
    assert len(view_contents(reader)) == 16
    assert views_digest(reader) == FROZEN[company, anchor]


def test_data_does_not_depend_on_the_hash_seed():
    """The order of a Python set once decided which warehouse got which stock, so company A's data changed
    with PYTHONHASHSEED from one run to the next. Hash seeds 0, 1 and 3 gave three different datasets."""
    code = ("import datetime, hashlib, json; from data.seed import generate; "
            "print(hashlib.sha256(json.dumps([generate(datetime.date(2026, 10, 15), company=c) for c in 'AB'], "
            "default=str).encode()).hexdigest())")
    out = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": s},
                          capture_output=True, text=True, check=True).stdout for s in ("0", "1", "3")}
    assert len(out) == 1


def _names_and_codes(T):
    people = [" ".join(filter(None, (e["firstName"], e["lastName"]))) for e in T["employees"]]
    return {"customers": ([r["name"] for r in T["customers"]], [r["code"] for r in T["customers"]]),
            "suppliers": ([r["name"] for r in T["suppliers"]], [r["code"] for r in T["suppliers"]]),
            "warehouses": ([r["name"] for r in T["warehouses"]], [r["code"] for r in T["warehouses"]]),
            "products": ([r["name"] for r in T["products"]], [r["code"] for r in T["products"]]),
            "employees": (people, [e["employeeId"] for e in T["employees"]])}


def test_companies_share_no_names_or_codes():
    a, b = generate(LATE, company="A"), generate(LATE, company="B")
    for kind, (a_names, a_codes) in _names_and_codes(a).items():
        b_names, b_codes = _names_and_codes(b)[kind]
        assert not {c.lower() for c in a_codes} & {c.lower() for c in b_codes}, kind
        assert not [(x, y) for x in a_names for y in b_names if x.lower() in y.lower() or y.lower() in x.lower()], kind
    first = lambda T: {e["firstName"] for e in T["employees"]}
    assert not first(a) & first(b)


def test_companies_could_share_one_database_later():
    """No shared ids or document numbers, so the two can be loaded side by side for a tenant filter later."""
    a, b = generate(LATE, company="A"), generate(LATE, company="B")
    ids = lambda T: {r["id"] for rows in T.values() for r in rows}
    numbers = lambda T: {r["number"] for t in ("sales_orders", "purchase_orders", "invoices", "payments") for r in T[t]}
    assert not ids(a) & ids(b) and not numbers(a) & numbers(b)


PERSONAL = {"customers": ["npwp", "phone", "email"], "suppliers": ["npwp", "phone", "bankAccountNumber"],
            "employees": ["email", "phone", "bpjsKesehatan", "bpjsKetenagakerjaan"]}


@pytest.mark.parametrize("company", ["A", "B"])
def test_personal_fields_hold_invalid_demo_values(company):
    T = generate(LATE, company=company)
    values = {(table, col): [r[col] for r in T[table] if r[col] is not None]
              for table, cols in PERSONAL.items() for col in cols}
    for (table, col), vals in values.items():
        ok = [v.endswith("@example.invalid") if col == "email" else v.startswith("DEMO-") for v in vals]
        assert all(ok), (table, col)
    filled = {key for key, vals in values.items() if vals}
    assert {("customers", "npwp"), ("customers", "phone"), ("customers", "email"), ("suppliers", "phone"),
            ("suppliers", "bankAccountNumber"), ("employees", "email"), ("employees", "phone")} <= filled


def test_views_show_no_personal_values(demo):
    blob = json.dumps(view_contents(db.reader_uri(demo[2])))
    assert "DEMO-" not in blob and "example.invalid" not in blob


# Each query counts what breaks a books rule; every count must be 0. %(anchor)s is the day the data is dated.
BOOKS = {
    "every journal entry balances":
        "select count(*) from (select entry_id from copilot.journal_lines group by 1 having sum(debit) <> sum(credit)) x",
    "assets + expenses = liabilities + equity + revenue":
        "select sum(case when account_type in ('ASSET', 'EXPENSE') then balance else -balance end) from copilot.gl_accounts",
    "account balances match the journal":
        "select count(*) from copilot.gl_accounts a left join (select account_code, sum(debit - credit) d "
        "from copilot.journal_lines group by 1) j on j.account_code = a.code "
        "where a.balance <> coalesce(j.d, 0) * case when a.account_type in ('ASSET', 'EXPENSE') then 1 else -1 end",
    "finished goods in stock at cost = the finished-goods account":
        "select (select balance from copilot.gl_accounts where code = %(fg)s) - (select sum(s.quantity * p.cost_price) "
        "from copilot.stock s join copilot.products p on p.id = s.product_id where p.product_type = 'MANUFACTURED')",
    "open receivables = the receivables account":
        "select (select balance from copilot.gl_accounts where code = %(ar)s) - "
        "(select sum(balance_due) from copilot.invoices where direction = 'receivable')",
    "open payables = the payables account":
        "select (select balance from copilot.gl_accounts where code = %(ap)s) - "
        "(select sum(balance_due) from copilot.invoices where direction = 'payable')",
    "invoice totals":
        "select count(*) from copilot.invoices where total_amount <> subtotal + tax_amount - discount_amount "
        "or balance_due < 0 or balance_due > total_amount",
    "invoice balance = total - payments":
        "select count(*) from copilot.invoices i where balance_due <> total_amount - "
        "coalesce((select sum(amount) from copilot.payments p where p.invoice_id = i.id), 0)",
    "invoice status follows balance and due date":
        "select count(*) from copilot.invoices where (status = 'PAID') <> (balance_due = 0) "
        "or (status = 'OVERDUE') <> (balance_due > 0 and due_date < %(anchor)s)",
    "payments match their invoice's party":
        "select count(*) from copilot.payments p left join copilot.invoices i on i.id = p.invoice_id where i.id is null "
        "or (p.direction = 'received') <> (i.direction = 'receivable') "
        "or p.customer_name is distinct from i.customer_name or p.supplier_name is distinct from i.supplier_name",
    "invoices belong to an order of the same party":
        "select count(*) from public.invoices i left join public.sales_orders so on so.id = i.\"salesOrderId\" "
        "left join public.purchase_orders po on po.id = i.\"purchaseOrderId\" "
        "where (i.type = 'INV_OUT' and so.\"customerId\" is distinct from i.\"customerId\") "
        "or (i.type = 'INV_IN' and po.\"supplierId\" is distinct from i.\"supplierId\")",
    "one journal entry per invoice and per payment":
        "select (select count(*) from public.invoices i where (select count(*) from public.journal_entries e "
        "where e.\"invoiceId\" = i.id and e.reference = i.number) <> 1) + "
        "(select count(*) from public.payments p where (select count(*) from public.journal_entries e "
        "where e.\"paymentId\" = p.id and e.reference = p.number) <> 1)",
    "purchase-order payment status follows its bill":
        "select count(*) from public.purchase_orders po left join public.invoices i on i.\"purchaseOrderId\" = po.id "
        "where po.\"paymentStatus\"::text <> case when i.id is null or i.status = 'ISSUED' then 'UNPAID' else i.status::text end",
    "order totals match their lines":
        "select (select count(*) from copilot.sales_orders so where total <> subtotal + tax_amount - discount_amount "
        "or subtotal <> (select sum(line_total) from copilot.sales_order_items i where i.sales_order_id = so.id)) + "
        "(select count(*) from copilot.purchase_orders po where net_amount <> amount_before_tax + tax_amount "
        "or amount_before_tax <> (select sum(total_price) from copilot.purchase_order_items i where i.purchase_order_id = po.id))",
    "customer totals match their orders":
        "select count(*) from copilot.customers c where total_order_value <> coalesce((select sum(total) "
        "from copilot.sales_orders so where so.customer_id = c.id and status not in ('CANCELLED', 'DRAFT')), 0) "
        "or last_order_date is distinct from (select max(order_date) from copilot.sales_orders so "
        "where so.customer_id = c.id and status not in ('CANCELLED', 'DRAFT'))",
    "shipments only for delivered orders, receipts only what was received":
        "select (select count(*) from copilot.inventory_moves m join copilot.sales_orders so on so.id = m.sales_order_id "
        "where m.move_type = 'SO_SHIPMENT' and (m.quantity >= 0 or so.status not in ('DELIVERED', 'INVOICED', 'COMPLETED'))) + "
        "(select count(*) from copilot.purchase_order_items i where i.received_qty <> coalesce((select sum(quantity) "
        "from copilot.inventory_moves m where m.purchase_order_id = i.purchase_order_id and m.product_id = i.product_id "
        "and m.move_type = 'PO_RECEIVE'), 0))",
    "available stock = on hand - reserved":
        "select count(*) from copilot.stock where available_qty <> quantity - reserved_qty or reserved_qty > quantity "
        "or quantity < 0",
    "nothing booked after the anchor":
        "select (select count(*) from copilot.journal_lines where entry_date > %(anchor)s) + "
        "(select count(*) from copilot.invoices where issue_date > %(anchor)s) + "
        "(select count(*) from copilot.payments where payment_date > %(anchor)s) + "
        "(select count(*) from copilot.sales_orders where order_date > %(anchor)s) + "
        "(select count(*) from copilot.inventory_moves where move_date > %(anchor)s) + "
        "(select count(*) from copilot.attendance where attendance_date >= %(anchor)s)",
}


def test_books_rules_hold(demo):
    company, anchor, uri = demo
    roles = COMPANIES[company]["roles"]
    params = {"anchor": anchor, "fg": roles["fg"], "ar": roles["ar"], "ap": roles["ap"]}
    with psycopg.connect(uri) as conn:
        broken = {rule: n for rule, q in BOOKS.items() if (n := conn.execute(q, params).fetchone()[0]) != 0}
    assert not broken


def test_value_hints_list_company_b_names(company_b_reader):
    hints = semantic.value_hints(company_b_reader)
    assert "Gudang Bahan Baku Bandung" in hints[("warehouses", "name")]
    assert "Surakarta" in hints[("warehouses", "city")]
    assert not {w for _, w, *_ in COMPANIES["A"]["warehouses"]} & set(hints[("warehouses", "name")])


def test_one_company_per_database(admin_uri):
    with pytest.raises(ValueError, match="own database"):
        db.create_demo(admin_uri, anchor=ANCHOR, company="B")
    with pytest.raises(ValueError, match="Unknown demo company"):
        db.create_demo(admin_uri, anchor=ANCHOR, company="C")
    assert db.demo_database(admin_uri, "A") == admin_uri


def test_init_db_builds_company_b_in_its_own_database(admin_uri, monkeypatch, capsys):
    monkeypatch.delenv("COPILOT_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "local_server", lambda data_dir=None, cleanup_mode="stop": admin_uri)
    main(["init-db", "--company", "b"])
    assert "company_b" in capsys.readouterr().out
    reader = db.ReadOnlyDB(db.reader_uri(db.demo_database(admin_uri, "B")))
    assert reader.user() == "copilot_reader"
    names = [r[0] for r in reader.run("select name from warehouses").rows]
    assert "Gudang Bahan Baku Bandung" in names and "Gudang Utama Bandung" not in names


def test_copilot_company_picks_the_demo_company(admin_uri, monkeypatch):
    """The other commands (ask, sql, serve, mcp) read COPILOT_COMPANY; without it they use company A."""
    monkeypatch.delenv("COPILOT_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "local_server", lambda data_dir=None, cleanup_mode="stop": admin_uri)
    monkeypatch.setenv("COPILOT_COMPANY", "b")
    codes = [r[0] for r in service.reader_from_env().run("select code from warehouses").rows]
    assert "BB-BDG" in codes and "GU-BDG" not in codes
    monkeypatch.delenv("COPILOT_COMPANY")
    assert service.reader_from_env().uri == db.reader_uri(admin_uri)
