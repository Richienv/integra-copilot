"""The semantic layer: what each copilot view means, in words the model and the retriever can use.

Schema retrieval ("schema RAG") picks the few views a question needs, so the prompt stays short and the
approach scales from these 16 views to all 126 Integra models. Terms are in Indonesian, English and Chinese
because owners ask in all three.
"""
import re

from rank_bm25 import BM25Okapi

CATALOG = {
    "invoices": {
        "about": "Customer invoices (receivables, piutang) and supplier bills (payables, hutang): dates, amounts and unpaid balance.",
        "terms": "invoice faktur tagihan bill piutang hutang utang jatuh tempo due overdue telat terlambat outstanding sisa belum dibayar unpaid "
                 "receivable payable ar ap 发票 应收 应收账款 应付 应付账款 到期 逾期 欠款 未付 账单 余额",
        "columns": [
            ("number", "text", ""),
            ("direction", "text", "'receivable' = invoice to a customer (piutang); 'payable' = bill from a supplier (hutang)"),
            ("customer_id", "uuid", ""), ("customer_name", "text", "set for receivables"),
            ("supplier_id", "uuid", ""), ("supplier_name", "text", "set for payables"),
            ("issue_date", "date", ""), ("due_date", "date", ""),
            ("subtotal", "numeric", "before tax"), ("tax_amount", "numeric", "PPN"),
            ("discount_amount", "numeric", ""), ("total_amount", "numeric", "subtotal + tax - discount"),
            ("balance_due", "numeric", "amount still unpaid"),
            ("status", "text", "DRAFT | ISSUED | PARTIAL | PAID | OVERDUE | CANCELLED | VOID | DISPUTED"),
            ("currency", "text", "IDR"),
        ],
    },
    "payments": {
        "about": "Money received from customers and paid to suppliers, linked to invoices.",
        "terms": "payment payments paid pay pembayaran bayar dibayar penerimaan pelunasan transfer giro kas cash received "
                 "付款 付给 付了 付钱 收款 回款 支付 转账 钱",
        "columns": [
            ("number", "text", ""), ("invoice_id", "uuid", "joins invoices.id"),
            ("direction", "text", "'received' from a customer | 'paid' to a supplier"),
            ("customer_name", "text", ""), ("supplier_name", "text", ""),
            ("payment_date", "date", ""), ("amount", "numeric", ""),
            ("method", "text", "CASH | TRANSFER | CHECK | GIRO | CREDIT_CARD | OTHER"),
            ("withholding_tax", "numeric", "PPh withheld, if any"),
        ],
    },
    "customers": {
        "about": "Customers with credit limit, payment term, credit status and lifetime order value.",
        "terms": "customer pelanggan konsumen pembeli klien toko limit kredit plafon credit status 客户 顾客 买家 信用 额度",
        "columns": [
            ("id", "uuid", ""), ("code", "text", ""), ("name", "text", ""),
            ("customer_type", "text", "INDIVIDUAL | COMPANY | GOVERNMENT"), ("has_npwp", "boolean", "has a tax ID on file"),
            ("tax_status", "text", "PKP | NON_PKP | EXEMPT"), ("credit_limit", "numeric", "0 means no credit limit has been set"),
            ("credit_term_days", "integer", ""), ("credit_status", "text", "GOOD | WATCH | HOLD | BLOCKED"),
            ("is_active", "boolean", ""), ("last_order_date", "date", ""),
            ("total_order_value", "numeric", "all non-cancelled orders, incl. tax"),
        ],
    },
    "suppliers": {
        "about": "Suppliers with payment term, rating, on-time delivery rate and quality score.",
        "terms": "supplier pemasok vendor penyedia rating kualitas tepat waktu 供应商 厂商 评分 准时",
        "columns": [
            ("id", "uuid", ""), ("code", "text", ""), ("name", "text", ""),
            ("payment_term", "text", "CASH | NET_15 | NET_30 | NET_45 | NET_60 | NET_90 | COD"),
            ("rating", "integer", "1-5"), ("on_time_rate_pct", "integer", "0-100"),
            ("quality_score", "numeric", "0-100"), ("is_active", "boolean", ""),
        ],
    },
    "products": {
        "about": "Products: fabrics, yarn, accessories and finished garments, with category, unit, cost and selling price.",
        "terms": "product produk barang item kain benang aksesoris pakaian garmen garment garments finished clothing apparel "
                 "sku harga jual modal cost price kategori category "
                 "产品 商品 布料 面料 纱线 辅料 成衣 价格 成本 类别",
        "columns": [
            ("id", "uuid", ""), ("code", "text", ""), ("name", "text", ""),
            ("category", "text", "Kain | Benang | Aksesoris | Pakaian Jadi"),
            ("product_type", "text", "MANUFACTURED | TRADING | RAW_MATERIAL | WIP"), ("unit", "text", ""),
            ("cost_price", "numeric", ""), ("selling_price", "numeric", "null for items not sold"),
            ("min_stock", "integer", ""), ("reorder_level", "integer", ""),
            ("color", "text", ""), ("composition", "text", ""), ("is_active", "boolean", ""),
        ],
    },
    "stock": {
        "about": "Current stock per product per warehouse, with reserved and available quantity and the product's reorder level.",
        "terms": "stok stock persediaan inventory gudang warehouse tersedia available sisa reorder habis menipis kurang "
                 "库存 存货 仓库 可用 补货 缺货 不足",
        "columns": [
            ("product_id", "uuid", ""), ("product_code", "text", ""), ("product_name", "text", ""),
            ("warehouse_id", "uuid", ""), ("warehouse_name", "text", ""),
            ("quantity", "numeric", "on hand"), ("reserved_qty", "numeric", ""),
            ("available_qty", "numeric", "quantity - reserved"),
            ("reorder_level", "integer", "set per product; compare with the product's total across warehouses"),
            ("unit", "text", ""),
        ],
    },
    "warehouses": {
        "about": "Warehouses and their city.",
        "terms": "warehouse gudang lokasi kota 仓库 地点 城市",
        "columns": [("id", "uuid", ""), ("code", "text", ""), ("name", "text", ""), ("city", "text", ""),
                    ("province", "text", ""), ("warehouse_type", "text", "RAW_MATERIAL | WORK_IN_PROGRESS | FINISHED_GOODS | GENERAL"),
                    ("is_active", "boolean", "")],
    },
    "inventory_moves": {
        "about": "Stock movements: goods received from purchase orders, shipments to customers, adjustments.",
        "terms": "mutasi pergerakan stok barang masuk keluar penerimaan pengiriman kirim dikirim terkirim shipment shipped ship "
                 "shipping dispatched delivered units received movement "
                 "出入库 库存变动 收货 发货 出货",
        "columns": [
            ("product_id", "uuid", ""), ("product_name", "text", ""), ("warehouse_id", "uuid", ""),
            ("warehouse_name", "text", ""),
            ("move_type", "text", "PO_RECEIVE | SO_SHIPMENT | ADJUSTMENT | ... (quantity is negative for goods out)"),
            ("quantity", "integer", ""), ("unit_cost", "numeric", ""), ("total_value", "numeric", ""),
            ("purchase_order_id", "uuid", ""), ("sales_order_id", "uuid", ""), ("move_date", "date", ""),
        ],
    },
    "sales_orders": {
        "about": "Sales orders: customer, order date, status and totals.",
        "terms": "penjualan jual omzet omset pesanan order so sales revenue pendapatan transaksi terlaris "
                 "销售 销售额 订单 营业额 收入 卖",
        "columns": [
            ("id", "uuid", ""), ("number", "text", ""), ("customer_id", "uuid", ""), ("customer_name", "text", ""),
            ("order_date", "date", ""),
            ("status", "text", "DRAFT | CONFIRMED | IN_PROGRESS | DELIVERED | INVOICED | COMPLETED | CANCELLED"),
            ("subtotal", "numeric", "before tax"), ("tax_amount", "numeric", ""), ("discount_amount", "numeric", ""),
            ("total", "numeric", "incl. tax"),
        ],
    },
    "sales_order_items": {
        "about": "Lines of each sales order: product, quantity, price, discount and line total.",
        "terms": "item baris produk terjual kuantitas qty jumlah terlaris laris best seller 销售明细 销量 数量 畅销",
        "columns": [
            ("sales_order_id", "uuid", "joins sales_orders.id"), ("product_id", "uuid", ""), ("product_name", "text", ""),
            ("quantity", "numeric", ""), ("unit_price", "numeric", ""), ("discount_pct", "numeric", ""),
            ("tax_rate_pct", "numeric", ""), ("line_total", "numeric", "after discount, before tax"),
        ],
    },
    "purchase_orders": {
        "about": "Purchase orders to suppliers: dates, status, payment status and amounts.",
        "terms": "pembelian beli purchase order po pengadaan belanja bahan baku 采购 采购单 进货 订货",
        "columns": [
            ("id", "uuid", ""), ("number", "text", ""), ("supplier_id", "uuid", ""), ("supplier_name", "text", ""),
            ("order_date", "date", ""), ("expected_date", "date", ""),
            ("status", "text", "PO_DRAFT | PENDING_APPROVAL | APPROVED | ORDERED | VENDOR_CONFIRMED | SHIPPED | PARTIAL_RECEIVED | RECEIVED | COMPLETED | REJECTED | CANCELLED"),
            ("payment_status", "text", "UNPAID | PARTIAL | PAID | OVERDUE"),
            ("amount_before_tax", "numeric", ""), ("tax_amount", "numeric", ""), ("net_amount", "numeric", "incl. tax"),
        ],
    },
    "purchase_order_items": {
        "about": "Lines of each purchase order: product, quantity ordered and received, price.",
        "terms": "item pembelian kuantitas diterima received qty 采购明细 收货数量",
        "columns": [
            ("purchase_order_id", "uuid", "joins purchase_orders.id"), ("product_id", "uuid", ""), ("product_name", "text", ""),
            ("quantity", "integer", "ordered"), ("received_qty", "integer", ""), ("unit_price", "numeric", ""),
            ("total_price", "numeric", ""),
        ],
    },
    "gl_accounts": {
        "about": "Chart of accounts with current balance (Kas, Bank, Piutang, Hutang, Penjualan, beban ...).",
        "terms": "akun perkiraan coa saldo kas bank neraca laba rugi beban pendapatan modal account balance ledger cash "
                 "科目 会计科目 余额 现金 银行 资产 负债 损益 费用",
        "columns": [("code", "text", ""), ("name", "text", ""),
                    ("account_type", "text", "ASSET | LIABILITY | EQUITY | REVENUE | EXPENSE"), ("balance", "numeric", "")],
    },
    "journal_lines": {
        "about": "General-ledger journal lines with entry date, account, debit and credit. Use for revenue, expenses and profit over a period.",
        "terms": "jurnal buku besar debit kredit beban biaya pengeluaran pendapatan laba rugi profit periode gaji listrik sewa "
                 "expense expenses cost costs spending electricity salary payroll rent revenue income profit loss monthly "
                 "kotor bersih hpp pokok cogs gross margin 毛利 成本 "
                 "日记账 分录 借方 贷方 费用 支出 利润 收入 期间",
        "columns": [
            ("entry_id", "uuid", ""), ("entry_date", "date", ""), ("entry_description", "text", ""),
            ("reference", "text", ""), ("entry_status", "text", "DRAFT | POSTED | VOID"),
            ("account_code", "text", ""), ("account_name", "text", ""),
            ("account_type", "text", "ASSET | LIABILITY | EQUITY | REVENUE | EXPENSE"),
            ("debit", "numeric", ""), ("credit", "numeric", ""),
        ],
    },
    "employees": {
        "about": "Employees: code, name, department, position, join date, status. No salary or personal ID data.",
        "terms": "karyawan pegawai staf pekerja departemen divisi jabatan posisi 员工 职员 部门 职位",
        "columns": [("id", "uuid", ""), ("employee_code", "text", ""), ("full_name", "text", ""),
                    ("department", "text", "Produksi | Gudang | Keuangan | Penjualan | Pembelian | QC"),
                    ("position", "text", ""), ("join_date", "date", ""),
                    ("status", "text", "ACTIVE | INACTIVE | ON_LEAVE | TERMINATED")],
    },
    "attendance": {
        "about": "Daily attendance per employee: status, check-in and check-out time, and whether they were late.",
        "terms": "absensi kehadiran hadir absen izin sakit cuti terlambat telat masuk pulang attendance late "
                 "考勤 出勤 缺勤 迟到 请假 病假 签到",
        "columns": [("employee_id", "uuid", ""), ("employee_name", "text", ""), ("department", "text", ""),
                    ("attendance_date", "date", ""), ("check_in", "timestamp", ""), ("check_out", "timestamp", ""),
                    ("status", "text", "PRESENT | ABSENT | LEAVE | SICK | REMOTE"), ("is_late", "boolean", "")],
    },
}

# Views that are almost always needed together.
DEPENDS = {
    "sales_order_items": ["sales_orders"], "purchase_order_items": ["purchase_orders"],
    "payments": ["invoices"], "attendance": ["employees"], "stock": ["products"],
}
FALLBACK = ["invoices", "sales_orders", "customers", "products"]


STOPWORDS = set("""a an the of for in on at by to per and or with is are was were be what which who how much many
show list give me my our we us this that these those than from all each every please tell
yang dan di ke dari untuk dengan ini itu ada apa berapa siapa mana tolong coba tampilkan lihat per kita kami saya
atau juga sudah belum akan bisa dalam pada oleh sebagai""".split())


def tokenize(text):
    """Latin words as they are; Chinese as single characters plus pairs, so 应收账款 matches 应收 and 账款."""
    text = text.lower()
    tokens = [t for t in re.findall(r"[a-z0-9_]+", text) if t not in STOPWORDS]
    for run in re.findall(r"[一-鿿]+", text):
        tokens += list(run) + [run[i:i + 2] for i in range(len(run) - 1)]
    return tokens


def _doc(name, view):
    cols = " ".join(f"{c} {note}" for c, _, note in view["columns"])
    return tokenize(f"{name.replace('_', ' ')} {view['about']} {view['terms']} {cols}")


class SchemaRetriever:
    def __init__(self, catalog=CATALOG):
        self.catalog = catalog
        self.names = list(catalog)
        self.bm25 = BM25Okapi([_doc(n, catalog[n]) for n in self.names])

    def retrieve(self, question, k=4):
        scores = self.bm25.get_scores(tokenize(question))
        top = max(scores) if len(scores) else 0
        ranked = [n for s, n in sorted(zip(scores, self.names), key=lambda x: -x[0])
                  if s > 0 and s >= 0.3 * top][:k]      # keep strong matches only
        if not ranked:
            ranked = list(FALLBACK)
        for name in list(ranked):
            for dep in DEPENDS.get(name, []):
                if dep not in ranked:
                    ranked.append(dep)
        return ranked


def value_hints(db, catalog=CATALOG, limit=12):
    """The stored values of short text columns (warehouse names, cities, departments), read once from the
    database. With them in the prompt, the model can match "雅加达成品仓" or "the Jakarta finished-goods
    warehouse" to the value in the data instead of filtering on a translation that matches nothing."""
    hints = {}
    for view, v in catalog.items():
        for col, typ, note in v["columns"]:
            if typ != "text" or note:                  # a note already lists the values or explains the column
                continue
            try:
                r = db.run(f"select distinct {col} from copilot.{view} where {col} is not null order by 1 limit {limit + 1}")
            except Exception:
                return hints                           # no database (some tests): no hints
            if not r.error and 0 < len(r.rows) <= limit:
                hints[(view, col)] = [str(row[0]) for row in r.rows]
    return hints


def schema_text(names, catalog=CATALOG, values=None):
    """The part of the prompt that describes the chosen views."""
    out = []
    for n in names:
        v = catalog[n]
        out.append(f"copilot.{n} -- {v['about']}")
        for col, typ, note in v["columns"]:
            vals = (values or {}).get((n, col))
            note = note or ("values: " + " | ".join(vals) if vals else "")
            out.append(f"  {col} {typ}" + (f"  -- {note}" if note else ""))
    return "\n".join(out)
