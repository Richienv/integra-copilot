"""The third lock: the database role itself. These run against the demo PostgreSQL."""


def test_reader_is_the_restricted_role(reader):
    assert reader.user() == "copilot_reader"


def test_base_tables_are_not_readable(reader):
    assert "permission denied" in reader.run("select * from public.employees").error


def test_views_hide_personal_data(reader):
    cols = reader.run("select * from employees limit 1").columns
    assert not {"baseSalary", "base_salary", "bpjsKesehatan", "email", "phone"} & set(cols)
    cols = reader.run("select * from customers limit 1").columns
    assert "npwp" not in cols and "has_npwp" in cols


def test_writes_fail_even_without_the_guard(reader):
    assert reader.run("create table x (a int)").error
    assert reader.run("delete from invoices").error


def test_statement_timeout(reader):
    reader.timeout_ms = 300
    try:
        assert "timeout" in reader.run("select pg_sleep(2)").error
    finally:
        reader.timeout_ms = 5000


def test_demo_books_balance(reader):
    r = reader.run("select sum(debit) - sum(credit) from journal_lines")
    assert r.rows[0][0] == 0


def test_invoice_totals_are_consistent(reader):
    r = reader.run("select count(*) from invoices where total_amount <> subtotal + tax_amount - discount_amount "
                   "or balance_due < 0 or balance_due > total_amount")
    assert r.rows[0][0] == 0


def test_row_cap(reader):
    reader.max_rows = 10
    try:
        r = reader.run("select * from attendance")
        assert len(r.rows) == 10 and r.truncated
    finally:
        reader.max_rows = 200
