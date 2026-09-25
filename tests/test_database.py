"""The third lock: the database role itself. These run against the demo PostgreSQL, once for each company."""


def test_reader_is_the_restricted_role(any_reader):
    assert any_reader.user() == "copilot_reader"


def test_base_tables_are_not_readable(any_reader):
    assert "permission denied" in any_reader.run("select * from public.employees").error


def test_views_hide_personal_data(any_reader):
    cols = any_reader.run("select * from employees limit 1").columns
    assert not {"baseSalary", "base_salary", "bpjsKesehatan", "email", "phone"} & set(cols)
    cols = any_reader.run("select * from customers limit 1").columns
    assert "npwp" not in cols and "has_npwp" in cols


def test_writes_fail_even_without_the_guard(any_reader):
    assert any_reader.run("create table x (a int)").error
    assert any_reader.run("delete from invoices").error


def test_statement_timeout(any_reader):
    any_reader.timeout_ms = 300
    try:
        assert "timeout" in any_reader.run("select pg_sleep(2)").error
    finally:
        any_reader.timeout_ms = 5000


def test_demo_books_balance(any_reader):
    r = any_reader.run("select sum(debit) - sum(credit) from journal_lines")
    assert r.rows[0][0] == 0


def test_invoice_totals_are_consistent(any_reader):
    r = any_reader.run("select count(*) from invoices where total_amount <> subtotal + tax_amount - discount_amount "
                       "or balance_due < 0 or balance_due > total_amount")
    assert r.rows[0][0] == 0


def test_row_cap(any_reader):
    any_reader.max_rows = 10
    try:
        r = any_reader.run("select * from attendance")
        assert len(r.rows) == 10 and r.truncated
    finally:
        any_reader.max_rows = 200
