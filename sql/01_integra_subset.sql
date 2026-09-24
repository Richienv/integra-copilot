-- 01_integra_subset.sql
-- A subset of Integra ERP's real schema (prisma/schema.prisma in github.com/Richienv/ERP), used for the
-- local demo database. Table names, column names and enum values are identical to Integra's, so the view
-- layer in 02_copilot_views.sql runs unchanged against a real Integra database.
-- Only the columns the copilot reads are included; Integra has more.

-- Integra itself uses uuid_generate_v4() from the uuid-ossp extension. The demo uses PostgreSQL's built-in
-- gen_random_uuid(), which needs no extension; the seed script supplies every id anyway.

create type "CustomerType"      as enum ('INDIVIDUAL', 'COMPANY', 'GOVERNMENT');
create type "CreditStatus"      as enum ('GOOD', 'WATCH', 'HOLD', 'BLOCKED');
create type "TaxStatus"         as enum ('PKP', 'NON_PKP', 'EXEMPT');
create type "PaymentTermLegacy" as enum ('CASH', 'NET_15', 'NET_30', 'NET_45', 'NET_60', 'NET_90', 'COD');
create type "WarehouseType"     as enum ('RAW_MATERIAL', 'WORK_IN_PROGRESS', 'FINISHED_GOODS', 'GENERAL');
create type "ProductType"       as enum ('MANUFACTURED', 'TRADING', 'RAW_MATERIAL', 'WIP');
create type "TransactionType"   as enum ('PO_RECEIVE', 'PRODUCTION_IN', 'RETURN_IN', 'SO_SHIPMENT', 'PRODUCTION_OUT',
                                         'RETURN_OUT', 'SCRAP', 'TRANSFER', 'ADJUSTMENT', 'INITIAL', 'SUBCONTRACT_OUT',
                                         'SUBCONTRACT_IN', 'CUT_CONSUME', 'PRODUCTION_RETURN', 'MATERIAL_RETURN');
create type "SalesOrderStatus"  as enum ('DRAFT', 'CONFIRMED', 'IN_PROGRESS', 'DELIVERED', 'INVOICED', 'COMPLETED', 'CANCELLED');
create type "ProcurementStatus" as enum ('GAP_DETECTED', 'PR_CREATED', 'PO_DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'ORDERED',
                                         'VENDOR_CONFIRMED', 'SHIPPED', 'PARTIAL_RECEIVED', 'RECEIVED', 'COMPLETED',
                                         'REJECTED', 'CANCELLED');
create type "PaymentStatus"     as enum ('UNPAID', 'PARTIAL', 'PAID', 'OVERDUE');
create type "InvoiceType"       as enum ('INV_OUT', 'INV_IN');
create type "InvoiceStatus"     as enum ('DRAFT', 'ISSUED', 'PARTIAL', 'PAID', 'OVERDUE', 'CANCELLED', 'VOID', 'DISPUTED');
create type "PaymentMethod"     as enum ('CASH', 'TRANSFER', 'CHECK', 'GIRO', 'CREDIT_CARD', 'OTHER');
create type "AccountType"       as enum ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE');
create type "EntryStatus"       as enum ('DRAFT', 'POSTED', 'VOID');
create type "EmployeeStatus"    as enum ('ACTIVE', 'INACTIVE', 'ON_LEAVE', 'TERMINATED');
create type "AttendanceStatus"  as enum ('PRESENT', 'ABSENT', 'LEAVE', 'SICK', 'REMOTE');

create table categories (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  "isActive" boolean not null default true
);

create table warehouses (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  city text,
  province text,
  "isActive" boolean not null default true,
  "warehouseType" "WarehouseType" not null default 'GENERAL'
);

create table products (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  "categoryId" uuid references categories(id),
  "productType" "ProductType" not null default 'TRADING',
  unit text not null,
  "costPrice" numeric(20,2) not null default 0,
  "sellingPrice" numeric(20,2),
  "minStock" integer not null default 0,
  "reorderLevel" integer not null default 0,
  color text,
  composition text,
  "isActive" boolean not null default true
);

create table stock_levels (
  id uuid primary key default gen_random_uuid(),
  "productId" uuid not null references products(id),
  "warehouseId" uuid not null references warehouses(id),
  quantity numeric(18,4) not null default 0,
  "reservedQty" numeric(18,4) not null default 0,
  "availableQty" numeric(18,4) not null default 0,
  unique ("productId", "warehouseId")
);

create table customers (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  "customerType" "CustomerType" not null,
  npwp text,
  "taxStatus" "TaxStatus" not null default 'PKP',
  phone text,
  email text,
  "creditLimit" numeric(20,2) not null default 0,
  "creditTerm" integer not null default 30,
  "paymentTerm" "PaymentTermLegacy" not null default 'NET_30',
  "creditStatus" "CreditStatus" not null default 'GOOD',
  "isActive" boolean not null default true,
  "lastOrderDate" timestamp(3),
  "totalOrderValue" numeric(20,2) not null default 0
);

create table suppliers (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  phone text,
  "paymentTerm" "PaymentTermLegacy" not null default 'CASH',
  rating integer not null default 0,
  "onTimeRate" integer not null default 0,
  "qualityScore" numeric(5,2),
  "bankAccountNumber" text,
  npwp text,
  "isActive" boolean not null default true
);

create table sales_orders (
  id uuid primary key default gen_random_uuid(),
  number text unique not null,
  "customerId" uuid not null references customers(id),
  "orderDate" timestamp(3) not null,
  "requestedDate" timestamp(3),
  subtotal numeric(20,2) not null default 0,
  "taxAmount" numeric(20,2) not null default 0,
  "discountAmount" numeric(20,2) not null default 0,
  total numeric(20,2) not null default 0,
  status "SalesOrderStatus" not null default 'DRAFT'
);

create table sales_order_items (
  id uuid primary key default gen_random_uuid(),
  "salesOrderId" uuid not null references sales_orders(id),
  "productId" uuid not null references products(id),
  quantity numeric(10,3) not null,
  "unitPrice" numeric(20,2) not null,
  discount numeric(5,2) not null default 0,
  "taxRate" numeric(5,2) not null default 11,
  "lineTotal" numeric(20,2) not null
);

create table purchase_orders (
  id uuid primary key default gen_random_uuid(),
  number text unique not null,
  "supplierId" uuid not null references suppliers(id),
  "orderDate" timestamp(3) not null,
  "expectedDate" timestamp(3),
  "totalAmount" numeric(20,2) not null default 0,
  "taxAmount" numeric(20,2) not null default 0,
  "netAmount" numeric(20,2) not null default 0,
  status "ProcurementStatus" not null default 'PO_DRAFT',
  "paymentStatus" "PaymentStatus" not null default 'UNPAID'
);

create table purchase_order_items (
  id uuid primary key default gen_random_uuid(),
  "purchaseOrderId" uuid not null references purchase_orders(id),
  "productId" uuid not null references products(id),
  quantity integer not null,
  "receivedQty" integer not null default 0,
  "unitPrice" numeric(20,2) not null,
  "totalPrice" numeric(20,2) not null
);

create table invoices (
  id uuid primary key default gen_random_uuid(),
  number text unique not null,
  "salesOrderId" uuid references sales_orders(id),
  "purchaseOrderId" uuid references purchase_orders(id),
  "customerId" uuid references customers(id),
  "supplierId" uuid references suppliers(id),
  type "InvoiceType" not null,
  "issueDate" timestamp(3) not null,
  "dueDate" timestamp(3) not null,
  subtotal numeric(20,2) not null,
  "taxAmount" numeric(20,2) not null,
  "discountAmount" numeric(20,2) not null default 0,
  "totalAmount" numeric(20,2) not null,
  "balanceDue" numeric(20,2) not null,
  "currencyCode" text not null default 'IDR',
  status "InvoiceStatus" not null default 'DRAFT'
);

create table payments (
  id uuid primary key default gen_random_uuid(),
  number text unique not null,
  "invoiceId" uuid references invoices(id),
  "customerId" uuid references customers(id),
  "supplierId" uuid references suppliers(id),
  date timestamp(3) not null,
  amount numeric(20,2) not null,
  method "PaymentMethod" not null default 'TRANSFER',
  reference text,
  "whtAmount" numeric(20,2)
);

create table gl_accounts (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  type "AccountType" not null,
  balance numeric(20,2) not null default 0
);

create table journal_entries (
  id uuid primary key default gen_random_uuid(),
  date timestamp(3) not null,
  description text not null,
  reference text,
  status "EntryStatus" not null default 'POSTED',
  "invoiceId" uuid references invoices(id),
  "paymentId" uuid references payments(id)
);

create table journal_lines (
  id uuid primary key default gen_random_uuid(),
  "entryId" uuid not null references journal_entries(id),
  "accountId" uuid not null references gl_accounts(id),
  description text,
  debit numeric(20,2) not null default 0,
  credit numeric(20,2) not null default 0
);

create table employees (
  id uuid primary key default gen_random_uuid(),
  "employeeId" text unique not null,
  "firstName" text not null,
  "lastName" text,
  email text,
  phone text,
  department text not null,
  position text not null,
  "joinDate" timestamp(3) not null,
  status "EmployeeStatus" not null default 'ACTIVE',
  "baseSalary" numeric(20,2) not null default 0,
  "bpjsKesehatan" text,
  "bpjsKetenagakerjaan" text
);

create table attendance (
  id uuid primary key default gen_random_uuid(),
  "employeeId" uuid not null references employees(id),
  date timestamp(3) not null,
  "checkIn" timestamp(3),
  "checkOut" timestamp(3),
  status "AttendanceStatus" not null default 'PRESENT',
  "isLate" boolean not null default false,
  unique ("employeeId", date)
);

create table inventory_transactions (
  id uuid primary key default gen_random_uuid(),
  "productId" uuid not null references products(id),
  "warehouseId" uuid not null references warehouses(id),
  type "TransactionType" not null,
  quantity integer not null,
  "unitCost" numeric(20,2),
  "totalValue" numeric(20,2),
  "purchaseOrderId" uuid references purchase_orders(id),
  "salesOrderId" uuid references sales_orders(id),
  "createdAt" timestamp(3) not null default now()
);
