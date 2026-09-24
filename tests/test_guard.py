import pytest

from copilot import guard

ALLOWED = [
    "select count(*) from invoices",
    "select customer_name, sum(balance_due) from copilot.invoices where direction = 'receivable' group by 1 order by 2 desc limit 5",
    "with t as (select * from sales_orders) select count(*) from t",
    "select date_trunc('month', order_date) m, sum(total) filter (where status <> 'CANCELLED') from sales_orders group by 1",
    "select g::date from generate_series(current_date - 6, current_date, interval '1 day') g",
    "select name from customers where name ilike '%toko%' union select name from suppliers",
    "select department, rank() over (order by count(*) desc) from attendance group by department",
]
BLOCKED = [
    ("delete from invoices", "write"),
    ("update invoices set status = 'PAID'", "write"),
    ("insert into invoices (number) values ('x')", "write"),
    ("drop table invoices", "write"),
    ("create table x (a int)", "write"),
    ("truncate invoices", "write"),
    ("grant select on invoices to public", "write"),
    ("copy invoices to '/tmp/x'", "write"),
    ("select * into x from invoices", "write"),
    ("select * from invoices for update", "write"),
    ("select 1; drop table invoices", "multi_statement"),
    ("select pg_sleep(10)", "forbidden_function"),
    ("select pg_read_file('/etc/passwd')", "forbidden_function"),
    ("select set_config('search_path', 'public', false)", "forbidden_function"),
    ("select * from public.employees", "forbidden_object"),
    ("select * from pg_catalog.pg_user", "forbidden_object"),
    ("select * from information_schema.tables", "forbidden_object"),
    ("select * from salaries", "forbidden_object"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    r = guard.check(sql)
    assert r.ok, r.reason


@pytest.mark.parametrize("sql,category", BLOCKED)
def test_blocked(sql, category):
    r = guard.check(sql)
    assert not r.ok and r.category == category, (r.category, r.reason)


def test_adds_limit_and_strips_comments():
    r = guard.check("select * from invoices -- ; drop table invoices")
    assert r.ok and r.sql.endswith("LIMIT 200") and "drop" not in r.sql.lower()


def test_reports_views_used():
    r = guard.check("select i.number from invoices i join payments p on p.invoice_id = i.id")
    assert r.views == ["invoices", "payments"]
