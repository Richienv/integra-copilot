"""Deterministic demo data for Integra Copilot: a fictional textile and garment SME in West Java.

Every name here is invented. Dates are relative to an anchor day (today by default, or SEED_ANCHOR=YYYY-MM-DD),
so questions like "due in the next 7 days" always have answers. The same random seed gives the same data.
Integrity rules the generator keeps: invoice totals = subtotal + tax; every journal entry balances;
customer totals match their orders; account balances are recomputed from journal lines.
"""
import datetime as dt
import os
import random
import uuid
from collections import defaultdict

import psycopg

TAX_RATE = 0.11  # Integra's default PPN rate on sales order lines


def _uid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _ts(day, hour=9, minute=0):
    return dt.datetime.combine(day, dt.time(hour, minute))


CATEGORIES = [("KAIN", "Kain"), ("BENANG", "Benang"), ("AKS", "Aksesoris"), ("JADI", "Pakaian Jadi")]
WAREHOUSES = [
    ("GU-BDG", "Gudang Utama Bandung", "Bandung", "Jawa Barat", "GENERAL"),
    ("GB-MJL", "Gudang Bahan Baku Majalaya", "Kabupaten Bandung", "Jawa Barat", "RAW_MATERIAL"),
    ("GJ-JKT", "Gudang Barang Jadi Jakarta", "Jakarta Barat", "DKI Jakarta", "FINISHED_GOODS"),
]
# code, name, category, product type, unit, cost, selling price, reorder level, colour, composition
PRODUCTS = [
    ("KN-001", "Katun Combed 30s", "KAIN", "RAW_MATERIAL", "meter", 28000, 36000, 400, "Putih", "100% Cotton"),
    ("KN-002", "Katun Combed 24s", "KAIN", "RAW_MATERIAL", "meter", 30000, 39000, 300, "Hitam", "100% Cotton"),
    ("KN-003", "Rayon Viscose", "KAIN", "RAW_MATERIAL", "meter", 24000, 32000, 350, "Navy", "100% Viscose"),
    ("KN-004", "Polyester Twill", "KAIN", "RAW_MATERIAL", "meter", 19000, 26000, 300, "Abu", "100% Polyester"),
    ("KN-005", "Denim 12 oz", "KAIN", "RAW_MATERIAL", "meter", 52000, 68000, 200, "Indigo", "98% Cotton 2% Spandex"),
    ("KN-006", "Drill Semen", "KAIN", "RAW_MATERIAL", "meter", 33000, 43000, 250, "Khaki", "65/35 Poly/Cotton"),
    ("KN-007", "Linen Blend", "KAIN", "RAW_MATERIAL", "meter", 61000, 79000, 120, "Cream", "55% Linen 45% Cotton"),
    ("KN-008", "Katun Toyobo", "KAIN", "RAW_MATERIAL", "meter", 35000, 46000, 200, "Maroon", "100% Cotton"),
    ("KN-009", "Crepe Premium", "KAIN", "RAW_MATERIAL", "meter", 27000, 35000, 250, "Mocca", "100% Polyester"),
    ("KN-010", "Jersey Spandex", "KAIN", "RAW_MATERIAL", "meter", 31000, 41000, 200, "Hitam", "95% Cotton 5% Spandex"),
    ("BN-001", "Benang Katun 30s", "BENANG", "RAW_MATERIAL", "kg", 62000, 0, 150, None, "100% Cotton"),
    ("BN-002", "Benang Polyester 150D", "BENANG", "RAW_MATERIAL", "kg", 41000, 0, 150, None, "100% Polyester"),
    ("BN-003", "Benang Jahit Tetoron", "BENANG", "RAW_MATERIAL", "cone", 9500, 14000, 500, "Putih", "Polyester"),
    ("AK-001", "Kancing Kemeja 14mm", "AKS", "TRADING", "gross", 18000, 26000, 100, "Putih", None),
    ("AK-002", "Resleting Nilon 20cm", "AKS", "TRADING", "pcs", 1200, 2000, 2000, "Hitam", None),
    ("AK-003", "Label Woven", "AKS", "TRADING", "pcs", 350, 600, 5000, None, None),
    ("AK-004", "Hangtag Kertas", "AKS", "TRADING", "pcs", 250, 450, 5000, None, None),
    ("JD-001", "Kaos Polos Katun 30s", "JADI", "MANUFACTURED", "pcs", 29000, 45000, 300, "Hitam", "100% Cotton"),
    ("JD-002", "Kemeja Flanel Pria", "JADI", "MANUFACTURED", "pcs", 72000, 115000, 150, "Merah Kotak", "100% Cotton"),
    ("JD-003", "Gamis Rayon", "JADI", "MANUFACTURED", "pcs", 68000, 125000, 150, "Navy", "100% Viscose"),
    ("JD-004", "Celana Chino", "JADI", "MANUFACTURED", "pcs", 64000, 110000, 150, "Khaki", "98% Cotton 2% Spandex"),
    ("JD-005", "Hoodie Fleece", "JADI", "MANUFACTURED", "pcs", 85000, 145000, 100, "Abu", "80/20 Cotton/Poly"),
    ("JD-006", "Seragam Kerja Drill", "JADI", "MANUFACTURED", "pcs", 78000, 125000, 200, "Khaki", "65/35 Poly/Cotton"),
    ("JD-007", "Kemeja Batik Cap", "JADI", "MANUFACTURED", "pcs", 95000, 175000, 80, "Coklat", "100% Cotton"),
    ("JD-008", "Tunik Crepe", "JADI", "MANUFACTURED", "pcs", 54000, 95000, 120, "Mocca", "100% Polyester"),
]
CUSTOMERS = [
    ("C-001", "Toko Kain Sumber Rejeki", "COMPANY", 30), ("C-002", "CV Maju Garmen", "COMPANY", 30),
    ("C-003", "PT Sandang Nusantara", "COMPANY", 45), ("C-004", "Toko Busana Melati", "COMPANY", 15),
    ("C-005", "CV Konveksi Berkah", "COMPANY", 30), ("C-006", "Butik Anggrek", "COMPANY", 15),
    ("C-007", "PT Seragam Prima", "COMPANY", 45), ("C-008", "Toko Grosir Jaya Abadi", "COMPANY", 30),
    ("C-009", "CV Hijab Cantik", "COMPANY", 30), ("C-010", "PT Garmen Sejahtera", "COMPANY", 60),
    ("C-011", "Toko Kain Pasar Baru", "COMPANY", 15), ("C-012", "CV Distro Kreatif", "COMPANY", 30),
    ("C-013", "UD Tekstil Makmur", "COMPANY", 30), ("C-014", "PT Retail Mode Indonesia", "COMPANY", 60),
    ("C-015", "Toko Batik Sekar", "COMPANY", 30), ("C-016", "CV Konveksi Sinar", "COMPANY", 30),
    ("C-017", "Ibu Ratna Dewi", "INDIVIDUAL", 0), ("C-018", "Bapak Hendra Wijaya", "INDIVIDUAL", 0),
    ("C-019", "Dinas Pendidikan Kota Contoh", "GOVERNMENT", 60), ("C-020", "PT Mitra Uniform", "COMPANY", 45),
    ("C-021", "Toko Kaos Polos 88", "COMPANY", 15), ("C-022", "CV Fashion Muslimah", "COMPANY", 30),
    ("C-023", "PT Kain Prima Ekspor", "COMPANY", 45), ("C-024", "Toko Jahit Rapi", "COMPANY", 15),
]
SUPPLIERS = [
    ("S-001", "PT Tekstil Majalaya Jaya", "NET_30", 5, 96), ("S-002", "CV Benang Mas", "NET_15", 4, 88),
    ("S-003", "PT Pewarna Nusantara", "NET_30", 4, 91), ("S-004", "PT Rayon Indo Makmur", "NET_45", 3, 79),
    ("S-005", "CV Aksesoris Garmen", "CASH", 4, 93), ("S-006", "PT Denim Karya", "NET_30", 5, 97),
    ("S-007", "UD Kancing Sejati", "CASH", 3, 84), ("S-008", "PT Katun Prima", "NET_30", 4, 90),
    ("S-009", "CV Label Kreasi", "NET_15", 4, 86), ("S-010", "PT Polyester Nusantara", "NET_45", 3, 81),
]
TERM_DAYS = {"CASH": 0, "NET_15": 15, "NET_30": 30, "NET_45": 45, "NET_60": 60, "NET_90": 90, "COD": 0}
FIRST = ["Agus", "Budi", "Citra", "Dewi", "Eko", "Fitri", "Gilang", "Hana", "Indra", "Joko", "Kartika", "Lina",
         "Made", "Nur", "Oki", "Putri", "Rizki", "Sari", "Teguh", "Umi", "Wahyu", "Yuni", "Zainal", "Asep"]
LAST = ["Santoso", "Wijaya", "Lestari", "Pratama", "Hidayat", "Saputra", "Rahayu", "Kurniawan", "Nugroho",
        "Permata", "Setiawan", "Maharani", None]
DEPARTMENTS = [("Produksi", ["Operator Jahit", "Operator Potong", "Kepala Produksi"], 9),
               ("Gudang", ["Staf Gudang", "Kepala Gudang"], 4), ("Keuangan", ["Staf Keuangan", "Akuntan"], 3),
               ("Penjualan", ["Sales", "Admin Penjualan"], 4), ("Pembelian", ["Staf Pembelian"], 2),
               ("QC", ["Staf QC"], 2)]
ACCOUNTS = [("1010", "Kas", "ASSET"), ("1020", "Bank BCA", "ASSET"), ("1100", "Piutang Usaha", "ASSET"),
            ("1200", "Persediaan Bahan Baku", "ASSET"), ("1210", "Persediaan Barang Jadi", "ASSET"),
            ("1300", "PPN Masukan", "ASSET"), ("2100", "Hutang Usaha", "LIABILITY"),
            ("2200", "PPN Keluaran", "LIABILITY"), ("3000", "Modal Disetor", "EQUITY"),
            ("4000", "Penjualan", "REVENUE"), ("5000", "Harga Pokok Penjualan", "EXPENSE"),
            ("6100", "Beban Gaji", "EXPENSE"), ("6200", "Beban Listrik", "EXPENSE"),
            ("6300", "Beban Sewa", "EXPENSE"), ("6400", "Beban Transportasi", "EXPENSE")]


def generate(anchor=None, seed=32):
    """Return {table: [row dicts]} for every table in 01_integra_subset.sql."""
    rng = random.Random(seed)
    anchor = anchor or dt.date.today()
    T = defaultdict(list)

    cat_id = {}
    for code, name in CATEGORIES:
        cat_id[code] = _uid(rng)
        T["categories"].append({"id": cat_id[code], "code": code, "name": name})
    wh_id = {}
    for code, name, city, prov, wtype in WAREHOUSES:
        wh_id[code] = _uid(rng)
        T["warehouses"].append({"id": wh_id[code], "code": code, "name": name, "city": city, "province": prov,
                                "warehouseType": wtype})
    prod = {}
    for code, name, cat, ptype, unit, cost, sell, reorder, color, comp in PRODUCTS:
        pid = _uid(rng)
        prod[code] = {"id": pid, "cost": cost, "sell": sell, "type": ptype, "unit": unit}
        T["products"].append({"id": pid, "code": code, "name": name, "categoryId": cat_id[cat], "productType": ptype,
                              "unit": unit, "costPrice": cost, "sellingPrice": sell or None,
                              "minStock": reorder // 2, "reorderLevel": reorder, "color": color, "composition": comp})
        home = "GJ-JKT" if ptype == "MANUFACTURED" else ("GU-BDG" if ptype == "TRADING" else "GB-MJL")
        for wcode in {home, "GU-BDG"}:
            base = reorder * rng.uniform(0.3, 3.0)          # some products end up below their reorder level
            qty = round(base if wcode == home else base * 0.25)
            reserved = round(qty * rng.choice([0, 0, 0.1, 0.2]))
            T["stock_levels"].append({"id": _uid(rng), "productId": pid, "warehouseId": wh_id[wcode],
                                      "quantity": qty, "reservedQty": reserved, "availableQty": qty - reserved})

    cust = {}
    for code, name, ctype, term in CUSTOMERS:
        cid = _uid(rng)
        limit = 0 if ctype == "INDIVIDUAL" else rng.choice([50, 75, 100, 150, 250]) * 1_000_000
        cust[code] = {"id": cid, "term": term, "limit": limit, "total": 0, "last": None}
        T["customers"].append({"id": cid, "code": code, "name": name, "customerType": ctype,
                               "npwp": None if ctype == "INDIVIDUAL" else f"0{rng.randint(10, 99)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}.{rng.randint(1, 9)}-{rng.randint(100, 999)}.000",
                               "taxStatus": "NON_PKP" if ctype == "INDIVIDUAL" else "PKP",
                               "phone": f"08{rng.randint(1000000000, 9999999999)}", "email": None,
                               "creditLimit": limit, "creditTerm": term,
                               "paymentTerm": {0: "CASH", 15: "NET_15", 30: "NET_30", 45: "NET_45", 60: "NET_60"}[term],
                               "creditStatus": "GOOD", "isActive": code != "C-024",
                               "lastOrderDate": None, "totalOrderValue": 0})
    supp = {}
    for code, name, term, rating, ontime in SUPPLIERS:
        sid = _uid(rng)
        supp[code] = {"id": sid, "term": TERM_DAYS[term]}
        T["suppliers"].append({"id": sid, "code": code, "name": name, "phone": f"022{rng.randint(1000000, 9999999)}",
                               "paymentTerm": term, "rating": rating, "onTimeRate": ontime,
                               "qualityScore": round(rng.uniform(70, 98), 2),
                               "bankAccountNumber": str(rng.randint(10**9, 10**10 - 1)), "npwp": None})

    acct = {}
    for code, name, atype in ACCOUNTS:
        acct[code] = _uid(rng)
        T["gl_accounts"].append({"id": acct[code], "code": code, "name": name, "type": atype, "balance": 0})
    counters = defaultdict(int)

    def number(prefix, day):
        counters[(prefix, day.year)] += 1
        return f"{prefix}-{day.year}-{counters[(prefix, day.year)]:04d}"

    def journal(day, description, reference, lines, invoice_id=None, payment_id=None):
        assert round(sum(d for _, d, _ in lines), 2) == round(sum(c for _, _, c in lines), 2), description
        eid = _uid(rng)
        T["journal_entries"].append({"id": eid, "date": _ts(day, 16), "description": description,
                                     "reference": reference, "status": "POSTED",
                                     "invoiceId": invoice_id, "paymentId": payment_id})
        for code, debit, credit in lines:
            T["journal_lines"].append({"id": _uid(rng), "entryId": eid, "accountId": acct[code],
                                       "description": description, "debit": debit, "credit": credit})

    start = anchor - dt.timedelta(days=210)

    # ── sales: orders, items, AR invoices, customer payments, shipments, COGS ─────────────────────
    sellable = [c for c, *_ in PRODUCTS if prod[c]["sell"]]
    for _ in range(150):
        ccode = rng.choice(list(cust))
        c = cust[ccode]
        age = rng.randint(0, 200)
        day = anchor - dt.timedelta(days=age)
        if age > 60:
            status = rng.choices(["COMPLETED", "INVOICED", "CANCELLED"], [80, 14, 6])[0]
        elif age > 20:
            status = rng.choices(["COMPLETED", "INVOICED", "DELIVERED", "CANCELLED"], [35, 40, 20, 5])[0]
        else:
            status = rng.choices(["DRAFT", "CONFIRMED", "IN_PROGRESS", "DELIVERED", "INVOICED"], [8, 25, 25, 22, 20])[0]
        so_id = _uid(rng)
        lines, subtotal, cogs = [], 0, 0
        for pcode in rng.sample(sellable, rng.randint(1, 4)):
            p = prod[pcode]
            qty = rng.choice([50, 100, 150, 200, 300, 500]) if p["unit"] == "meter" else rng.choice([24, 48, 60, 100, 120, 240])
            disc = rng.choice([0, 0, 0, 5, 10])
            line_total = round(qty * p["sell"] * (1 - disc / 100))
            subtotal += line_total
            cogs += qty * p["cost"]
            lines.append({"id": _uid(rng), "salesOrderId": so_id, "productId": p["id"], "quantity": qty,
                          "unitPrice": p["sell"], "discount": disc, "taxRate": 11, "lineTotal": line_total})
        tax = round(subtotal * TAX_RATE)
        total = subtotal + tax
        so_number = number("SO", day)
        T["sales_orders"].append({"id": so_id, "number": so_number, "customerId": c["id"], "orderDate": _ts(day, 10),
                                  "requestedDate": _ts(day + dt.timedelta(days=14)), "subtotal": subtotal,
                                  "taxAmount": tax, "discountAmount": 0, "total": total, "status": status})
        T["sales_order_items"].extend(lines)
        if status not in ("CANCELLED", "DRAFT"):
            c["total"] += total
            c["last"] = max(c["last"] or day, day)
        if status in ("DELIVERED", "INVOICED", "COMPLETED"):
            ship_day = min(day + dt.timedelta(days=rng.randint(2, 6)), anchor)
            for ln in lines:
                pcode = next(k for k, v in prod.items() if v["id"] == ln["productId"])
                wh = "GJ-JKT" if prod[pcode]["type"] == "MANUFACTURED" else "GU-BDG"
                T["inventory_transactions"].append({"id": _uid(rng), "productId": ln["productId"], "warehouseId": wh_id[wh],
                                                    "type": "SO_SHIPMENT", "quantity": -int(ln["quantity"]),
                                                    "unitCost": prod[pcode]["cost"],
                                                    "totalValue": int(ln["quantity"]) * prod[pcode]["cost"],
                                                    "purchaseOrderId": None, "salesOrderId": so_id,
                                                    "createdAt": _ts(ship_day, 14)})
            journal(ship_day, f"HPP pengiriman {so_number}", so_number, [("5000", cogs, 0), ("1210", 0, cogs)])
        if status in ("INVOICED", "COMPLETED"):
            issue = min(day + dt.timedelta(days=rng.randint(3, 8)), anchor)
            due = issue + dt.timedelta(days=c["term"])
            inv_id = _uid(rng)
            inv_number = number("INV", issue)
            if status == "COMPLETED":
                paid = total
            else:
                paid = rng.choice([0, 0, 0, round(total * rng.choice([0.3, 0.5]))])
            balance = total - paid
            inv_status = "PAID" if balance == 0 else ("OVERDUE" if due < anchor else ("PARTIAL" if paid else "ISSUED"))
            T["invoices"].append({"id": inv_id, "number": inv_number, "salesOrderId": so_id, "purchaseOrderId": None,
                                  "customerId": c["id"], "supplierId": None, "type": "INV_OUT",
                                  "issueDate": _ts(issue), "dueDate": _ts(due), "subtotal": subtotal, "taxAmount": tax,
                                  "discountAmount": 0, "totalAmount": total, "balanceDue": balance,
                                  "currencyCode": "IDR", "status": inv_status})
            journal(issue, f"Faktur penjualan {inv_number}", inv_number,
                    [("1100", total, 0), ("4000", 0, subtotal), ("2200", 0, tax)], invoice_id=inv_id)
            if paid:
                pay_day = min(issue + dt.timedelta(days=rng.randint(5, max(6, c["term"] + 10))), anchor)
                pay_id = _uid(rng)
                pay_number = number("PAY", pay_day)
                method = rng.choice(["TRANSFER", "TRANSFER", "TRANSFER", "GIRO", "CASH"])
                T["payments"].append({"id": pay_id, "number": pay_number, "invoiceId": inv_id, "customerId": c["id"],
                                      "supplierId": None, "date": _ts(pay_day, 11), "amount": paid, "method": method,
                                      "reference": f"TRF{rng.randint(100000, 999999)}", "whtAmount": None})
                cash_account = "1010" if method == "CASH" else "1020"      # cash goes to Kas, the rest to the bank
                journal(pay_day, f"Penerimaan {pay_number} atas {inv_number}", pay_number,
                        [(cash_account, paid, 0), ("1100", 0, paid)], payment_id=pay_id)

    # ── purchasing: orders, items, receipts, AP invoices, supplier payments ───────────────────────
    raw = [c for c, *_ in PRODUCTS if prod[c]["type"] in ("RAW_MATERIAL", "TRADING")]
    for _ in range(60):
        scode = rng.choice(list(supp))
        s = supp[scode]
        age = rng.randint(0, 200)
        day = anchor - dt.timedelta(days=age)
        if age > 45:
            status = rng.choices(["COMPLETED", "RECEIVED", "CANCELLED"], [75, 18, 7])[0]
        elif age > 12:
            status = rng.choices(["COMPLETED", "RECEIVED", "PARTIAL_RECEIVED", "SHIPPED", "VENDOR_CONFIRMED"], [25, 25, 15, 20, 15])[0]
        else:
            status = rng.choices(["PO_DRAFT", "PENDING_APPROVAL", "APPROVED", "ORDERED", "VENDOR_CONFIRMED"], [10, 20, 20, 30, 20])[0]
        po_id = _uid(rng)
        items, amount = [], 0
        for pcode in rng.sample(raw, rng.randint(1, 3)):
            p = prod[pcode]
            qty = rng.choice([200, 300, 500, 800, 1000]) if p["unit"] in ("meter", "pcs") else rng.choice([20, 50, 100, 150])
            price = round(p["cost"] * rng.uniform(0.95, 1.05))
            received = qty if status in ("COMPLETED", "RECEIVED") else (qty // 2 if status == "PARTIAL_RECEIVED" else 0)
            items.append({"id": _uid(rng), "purchaseOrderId": po_id, "productId": p["id"], "quantity": qty,
                          "receivedQty": received, "unitPrice": price, "totalPrice": qty * price, "_code": pcode})
            amount += qty * price
        tax = round(amount * TAX_RATE)
        net = amount + tax
        po_number = number("PO", day)
        expected = day + dt.timedelta(days=rng.randint(7, 21))
        pay_status = "UNPAID"
        if status in ("COMPLETED", "RECEIVED", "PARTIAL_RECEIVED"):
            recv_day = min(expected, anchor)
            for it in items:
                if it["receivedQty"]:
                    wh = "GU-BDG" if prod[it["_code"]]["type"] == "TRADING" else "GB-MJL"
                    T["inventory_transactions"].append({"id": _uid(rng), "productId": it["productId"],
                                                        "warehouseId": wh_id[wh], "type": "PO_RECEIVE",
                                                        "quantity": it["receivedQty"], "unitCost": it["unitPrice"],
                                                        "totalValue": it["receivedQty"] * it["unitPrice"],
                                                        "purchaseOrderId": po_id, "salesOrderId": None,
                                                        "createdAt": _ts(recv_day, 13)})
            inv_id = _uid(rng)
            bill_number = number("BILL", recv_day)
            due = recv_day + dt.timedelta(days=s["term"])
            paid = net if status == "COMPLETED" else rng.choice([0, 0, round(net * 0.5)])
            balance = net - paid
            inv_status = "PAID" if balance == 0 else ("OVERDUE" if due < anchor else ("PARTIAL" if paid else "ISSUED"))
            pay_status = {"PAID": "PAID", "OVERDUE": "OVERDUE", "PARTIAL": "PARTIAL", "ISSUED": "UNPAID"}[inv_status]
            T["invoices"].append({"id": inv_id, "number": bill_number, "salesOrderId": None, "purchaseOrderId": po_id,
                                  "customerId": None, "supplierId": s["id"], "type": "INV_IN",
                                  "issueDate": _ts(recv_day), "dueDate": _ts(due), "subtotal": amount, "taxAmount": tax,
                                  "discountAmount": 0, "totalAmount": net, "balanceDue": balance,
                                  "currencyCode": "IDR", "status": inv_status})
            journal(recv_day, f"Tagihan pemasok {bill_number}", bill_number,
                    [("1200", amount, 0), ("1300", tax, 0), ("2100", 0, net)], invoice_id=inv_id)
            if paid:
                pay_day = min(recv_day + dt.timedelta(days=max(1, s["term"] - rng.randint(0, 5))), anchor)
                pay_id = _uid(rng)
                pay_number = number("PAY", pay_day)
                T["payments"].append({"id": pay_id, "number": pay_number, "invoiceId": inv_id, "customerId": None,
                                      "supplierId": s["id"], "date": _ts(pay_day, 15), "amount": paid,
                                      "method": "TRANSFER", "reference": f"TRF{rng.randint(100000, 999999)}",
                                      "whtAmount": None})
                journal(pay_day, f"Pembayaran {pay_number} atas {bill_number}", pay_number,
                        [("2100", paid, 0), ("1020", 0, paid)], payment_id=pay_id)
        T["purchase_orders"].append({"id": po_id, "number": po_number, "supplierId": s["id"], "orderDate": _ts(day, 9),
                                     "expectedDate": _ts(expected), "totalAmount": amount, "taxAmount": tax,
                                     "netAmount": net, "status": status, "paymentStatus": pay_status})
        for it in items:
            it.pop("_code")
        T["purchase_order_items"].extend(items)

    # ── monthly operating expenses ────────────────────────────────────────────────────────────────
    month = dt.date(start.year, start.month, 1)
    while month <= anchor:
        day = min(month + dt.timedelta(days=24), anchor)
        journal(day, f"Gaji karyawan {month:%m/%Y}", f"GAJI-{month:%Y%m}", [("6100", 118_000_000, 0), ("1020", 0, 118_000_000)])
        electricity = 21_000_000 + rng.randint(0, 6) * 500_000
        journal(day, f"Listrik pabrik {month:%m/%Y}", f"PLN-{month:%Y%m}", [("6200", electricity, 0), ("1020", 0, electricity)])
        journal(day, f"Sewa gudang {month:%m/%Y}", f"SEWA-{month:%Y%m}", [("6300", 35_000_000, 0), ("1020", 0, 35_000_000)])
        month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    # ── people ────────────────────────────────────────────────────────────────────────────────────
    n = 0
    for dept, positions, count in DEPARTMENTS:
        for i in range(count):
            n += 1
            first, last = FIRST[(n * 7) % len(FIRST)], LAST[(n * 5) % len(LAST)]
            status = "ACTIVE" if n not in (6, 19) else ("ON_LEAVE" if n == 6 else "INACTIVE")
            eid = _uid(rng)
            T["employees"].append({"id": eid, "employeeId": f"EMP-{n:03d}", "firstName": first, "lastName": last,
                                   "email": None, "phone": None, "department": dept,
                                   "position": positions[-1] if i == 0 and len(positions) > 1 and "Kepala" in positions[-1] else positions[i % len(positions)],
                                   "joinDate": _ts(anchor - dt.timedelta(days=rng.randint(60, 2400))),
                                   "status": status, "baseSalary": rng.randint(45, 120) * 100_000,
                                   "bpjsKesehatan": None, "bpjsKetenagakerjaan": None})
            if status != "ACTIVE":
                continue
            for back in range(1, 31):
                day = anchor - dt.timedelta(days=back)
                if day.weekday() == 6:          # Sunday off; Saturday is a working day in the factory
                    continue
                st = rng.choices(["PRESENT", "SICK", "LEAVE", "ABSENT"], [90, 4, 3, 3])[0]
                late = st == "PRESENT" and rng.random() < 0.1
                check_in = _ts(day, 8 if late else 7, rng.randint(10, 55)) if st == "PRESENT" else None
                T["attendance"].append({"id": _uid(rng), "employeeId": eid, "date": _ts(day, 0),
                                        "checkIn": check_in, "checkOut": _ts(day, 16, rng.randint(0, 50)) if check_in else None,
                                        "status": st, "isLate": late})

    # ── opening balances, dated at the start: capital in the bank, and finished goods sized so the
    #    finished-goods account ends at the value of the finished goods now in stock ─────────────────
    cogs_total = sum(ln["credit"] for ln in T["journal_lines"] if ln["accountId"] == acct["1210"])
    cost_of = {v["id"]: v["cost"] for v in prod.values()}
    fg_ids = {v["id"] for v in prod.values() if v["type"] == "MANUFACTURED"}
    fg_now = round(sum(float(s["quantity"]) * cost_of[s["productId"]] for s in T["stock_levels"] if s["productId"] in fg_ids))
    opening_fg = cogs_total + fg_now
    journal(start, "Saldo awal", "OPENING", [("1020", 2_500_000_000, 0), ("1210", opening_fg, 0),
                                             ("3000", 0, 2_500_000_000 + opening_fg)])

    # ── derived fields ────────────────────────────────────────────────────────────────────────────
    for row in T["customers"]:
        c = next(v for k, v in cust.items() if v["id"] == row["id"])
        row["totalOrderValue"] = c["total"]
        row["lastOrderDate"] = _ts(c["last"]) if c["last"] else None
        overdue = sum(i["balanceDue"] for i in T["invoices"]
                      if i["customerId"] == row["id"] and i["status"] == "OVERDUE")
        if row["creditLimit"] and overdue > 0.5 * row["creditLimit"]:
            row["creditStatus"] = "HOLD"
        elif overdue > 0:
            row["creditStatus"] = "WATCH"
    kind = {a["id"]: a["type"] for a in T["gl_accounts"]}
    bal = defaultdict(float)
    for ln in T["journal_lines"]:
        sign = 1 if kind[ln["accountId"]] in ("ASSET", "EXPENSE") else -1
        bal[ln["accountId"]] += sign * (ln["debit"] - ln["credit"])
    for a in T["gl_accounts"]:
        a["balance"] = round(bal[a["id"]], 2)
    return T


ORDER = ["categories", "warehouses", "products", "stock_levels", "customers", "suppliers", "sales_orders",
         "sales_order_items", "purchase_orders", "purchase_order_items", "invoices", "payments", "gl_accounts",
         "journal_entries", "journal_lines", "employees", "attendance", "inventory_transactions"]


def load(conn, tables):
    """Insert generated rows in dependency order. Column names are quoted to keep Integra's camelCase."""
    with conn.cursor() as cur:
        for name in ORDER:
            rows = tables[name]
            if not rows:
                continue
            cols = list(rows[0].keys())
            sql = f'insert into {name} ({", ".join(chr(34) + c + chr(34) for c in cols)}) values ({", ".join(["%s"] * len(cols))})'
            cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


if __name__ == "__main__":
    import sys
    anchor = dt.date.fromisoformat(os.environ["SEED_ANCHOR"]) if os.environ.get("SEED_ANCHOR") else None
    with psycopg.connect(sys.argv[1], autocommit=True) as conn:
        load(conn, generate(anchor))
    print("seeded")
