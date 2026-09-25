-- 02_copilot_views.sql
-- The copilot never touches Integra's tables directly. It reads these views, in their own schema, through a
-- login role with no grant on Integra's tables and no right to write (see the role at the end of this file for
-- what it does not stop). The views rename Integra's camelCase columns to plain snake_case, turn enums into
-- text, join in names, and leave out personal data: no salaries, BPJS numbers, NIK/NPWP numbers, phone
-- numbers, e-mails or bank accounts.
--
-- Runs unchanged on a real Integra database. Run it as the database owner; set a strong password for
-- copilot_reader outside this file (see README).

create schema if not exists copilot;

create or replace view copilot.customers as
select c.id, c.code, c.name,
       c."customerType"::text   as customer_type,
       c.npwp is not null        as has_npwp,
       c."taxStatus"::text       as tax_status,
       c."creditLimit"           as credit_limit,
       c."creditTerm"            as credit_term_days,
       c."creditStatus"::text    as credit_status,
       c."isActive"              as is_active,
       c."lastOrderDate"::date   as last_order_date,
       c."totalOrderValue"       as total_order_value
from public.customers c;

create or replace view copilot.suppliers as
select s.id, s.code, s.name,
       s."paymentTerm"::text as payment_term,
       s.rating,
       s."onTimeRate"        as on_time_rate_pct,
       s."qualityScore"      as quality_score,
       s."isActive"          as is_active
from public.suppliers s;

create or replace view copilot.products as
select p.id, p.code, p.name,
       c.name                as category,
       p."productType"::text as product_type,
       p.unit,
       p."costPrice"         as cost_price,
       p."sellingPrice"      as selling_price,
       p."minStock"          as min_stock,
       p."reorderLevel"      as reorder_level,
       p.color, p.composition,
       p."isActive"          as is_active
from public.products p
left join public.categories c on c.id = p."categoryId";

create or replace view copilot.warehouses as
select w.id, w.code, w.name, w.city, w.province,
       w."warehouseType"::text as warehouse_type,
       w."isActive"            as is_active
from public.warehouses w;

create or replace view copilot.stock as
select sl."productId"    as product_id,
       p.code            as product_code,
       p.name            as product_name,
       sl."warehouseId"  as warehouse_id,
       w.name            as warehouse_name,
       sl.quantity,
       sl."reservedQty"  as reserved_qty,
       sl."availableQty" as available_qty,
       p."reorderLevel"  as reorder_level,
       p.unit
from public.stock_levels sl
join public.products p   on p.id = sl."productId"
join public.warehouses w on w.id = sl."warehouseId";

create or replace view copilot.inventory_moves as
select t.id,
       t."productId"      as product_id,
       p.name             as product_name,
       t."warehouseId"    as warehouse_id,
       w.name             as warehouse_name,
       t.type::text       as move_type,
       t.quantity,
       t."unitCost"       as unit_cost,
       t."totalValue"     as total_value,
       t."purchaseOrderId" as purchase_order_id,
       t."salesOrderId"   as sales_order_id,
       t."createdAt"::date as move_date
from public.inventory_transactions t
join public.products p   on p.id = t."productId"
join public.warehouses w on w.id = t."warehouseId";

create or replace view copilot.sales_orders as
select so.id, so.number,
       so."customerId"      as customer_id,
       c.name               as customer_name,
       so."orderDate"::date as order_date,
       so.status::text      as status,
       so.subtotal,
       so."taxAmount"       as tax_amount,
       so."discountAmount"  as discount_amount,
       so.total
from public.sales_orders so
join public.customers c on c.id = so."customerId";

create or replace view copilot.sales_order_items as
select i.id,
       i."salesOrderId" as sales_order_id,
       i."productId"    as product_id,
       p.name           as product_name,
       i.quantity,
       i."unitPrice"    as unit_price,
       i.discount       as discount_pct,
       i."taxRate"      as tax_rate_pct,
       i."lineTotal"    as line_total
from public.sales_order_items i
join public.products p on p.id = i."productId";

create or replace view copilot.purchase_orders as
select po.id, po.number,
       po."supplierId"         as supplier_id,
       s.name                  as supplier_name,
       po."orderDate"::date    as order_date,
       po."expectedDate"::date as expected_date,
       po.status::text         as status,
       po."paymentStatus"::text as payment_status,
       po."totalAmount"        as amount_before_tax,
       po."taxAmount"          as tax_amount,
       po."netAmount"          as net_amount
from public.purchase_orders po
join public.suppliers s on s.id = po."supplierId";

create or replace view copilot.purchase_order_items as
select i.id,
       i."purchaseOrderId" as purchase_order_id,
       i."productId"       as product_id,
       p.name              as product_name,
       i.quantity,
       i."receivedQty"     as received_qty,
       i."unitPrice"       as unit_price,
       i."totalPrice"      as total_price
from public.purchase_order_items i
join public.products p on p.id = i."productId";

create or replace view copilot.invoices as
select i.id, i.number,
       case i.type when 'INV_OUT' then 'receivable' else 'payable' end as direction,
       i."customerId"      as customer_id,
       cu.name             as customer_name,
       i."supplierId"      as supplier_id,
       su.name             as supplier_name,
       i."issueDate"::date as issue_date,
       i."dueDate"::date   as due_date,
       i.subtotal,
       i."taxAmount"       as tax_amount,
       i."discountAmount"  as discount_amount,
       i."totalAmount"     as total_amount,
       i."balanceDue"      as balance_due,
       i.status::text      as status,
       i."currencyCode"    as currency
from public.invoices i
left join public.customers cu on cu.id = i."customerId"
left join public.suppliers su on su.id = i."supplierId";

create or replace view copilot.payments as
select pay.id, pay.number,
       pay."invoiceId"   as invoice_id,
       case when pay."customerId" is not null then 'received' else 'paid' end as direction,
       cu.name           as customer_name,
       su.name           as supplier_name,
       pay.date::date    as payment_date,
       pay.amount,
       pay.method::text  as method,
       pay."whtAmount"   as withholding_tax
from public.payments pay
left join public.customers cu on cu.id = pay."customerId"
left join public.suppliers su on su.id = pay."supplierId";

create or replace view copilot.gl_accounts as
select a.code, a.name, a.type::text as account_type, a.balance
from public.gl_accounts a;

create or replace view copilot.journal_lines as
select l.id,
       e.id              as entry_id,
       e.date::date      as entry_date,
       e.description     as entry_description,
       e.reference,
       e.status::text    as entry_status,
       a.code            as account_code,
       a.name            as account_name,
       a.type::text      as account_type,
       l.debit, l.credit
from public.journal_lines l
join public.journal_entries e on e.id = l."entryId"
join public.gl_accounts a     on a.id = l."accountId";

create or replace view copilot.employees as
select e.id,
       e."employeeId"      as employee_code,
       trim(e."firstName" || ' ' || coalesce(e."lastName", '')) as full_name,
       e.department, e.position,
       e."joinDate"::date  as join_date,
       e.status::text      as status
from public.employees e;

create or replace view copilot.attendance as
select a.id,
       a."employeeId"    as employee_id,
       trim(e."firstName" || ' ' || coalesce(e."lastName", '')) as employee_name,
       e.department,
       a.date::date      as attendance_date,
       a."checkIn"       as check_in,
       a."checkOut"      as check_out,
       a.status::text    as status,
       a."isLate"        as is_late
from public.attendance a
join public.employees e on e.id = a."employeeId";

-- The reader role: can log in and SELECT from the copilot views; it has no grant on Integra's base tables and
-- cannot write. What it does NOT stop, on its own: like any role it can read the system catalogue
-- (pg_catalog, information_schema) and call system functions such as pg_get_viewdef, and the settings below
-- are only defaults. copilot.db.ReadOnlyDB makes each transaction read-only and sets its timeout again, but a
-- statement that smuggles in its own SET (or COMMIT; BEGIN READ WRITE; ALTER ROLE copilot_reader SET ...) can
-- lift the timeout or change these defaults. The guard (copilot/guard.py) stops all of that before SQL gets
-- here; the red-team tests show it (tests/test_redteam.py, stack L1+L3).
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'copilot_reader') then
    create role copilot_reader login;
  end if;
end $$;
revoke all on schema public from copilot_reader;
revoke all on all tables in schema public from copilot_reader;
grant usage on schema copilot to copilot_reader;
grant select on all tables in schema copilot to copilot_reader;
alter role copilot_reader set default_transaction_read_only = on;
alter role copilot_reader set statement_timeout = '5s';
alter role copilot_reader set search_path = copilot;
