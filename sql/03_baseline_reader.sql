-- 03_baseline_reader.sql
-- The role the naive baseline (copilot/baseline.py) runs its SQL as. It exists only to measure what the
-- copilot's layer adds, on the fictional demo database. Run it as the database owner after
-- 01_integra_subset.sql and 02_copilot_views.sql (copilot/baseline_db.py does).
--
-- It can read Integra's base tables, including the personal data the copilot's views leave out (salaries,
-- BPJS and NPWP numbers, phone numbers, e-mails, bank accounts): that is the comparison. It cannot read
-- the copilot schema, and copilot.db.ReadOnlyDB runs each of its statements in a read-only transaction with a
-- 5-second timeout. There is no guard in front of it, so, as for copilot_reader without the guard, a statement
-- that smuggles in its own SET can lift that timeout, and the role can read the system catalogue.
-- Never create this role on a database with real people's data.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'baseline_reader') then
    create role baseline_reader login;
  end if;
end $$;
revoke all on all tables in schema public from baseline_reader;
grant usage on schema public to baseline_reader;
grant select on public.categories, public.warehouses, public.products, public.stock_levels, public.customers,
                public.suppliers, public.sales_orders, public.sales_order_items, public.purchase_orders,
                public.purchase_order_items, public.invoices, public.payments, public.gl_accounts,
                public.journal_entries, public.journal_lines, public.employees, public.attendance,
                public.inventory_transactions
  to baseline_reader;
revoke all on schema copilot from baseline_reader;
alter role baseline_reader set default_transaction_read_only = on;
alter role baseline_reader set statement_timeout = '5s';
alter role baseline_reader set search_path = public;
