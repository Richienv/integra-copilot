"""Deterministic demo data for Integra Copilot: two fictional textile and garment SMEs on the same schema.

Company A makes fabric and garments in West Java (Bandung, Majalaya, Jakarta); the development set is written
on it. Company B makes batik, knitwear and uniforms in Central and East Java (Pekalongan, Solo, Semarang,
Surabaya) and buys its raw material in Bandung; it is the unseen company for the blind test. The two share no
customer, supplier, warehouse, employee or product name or code. One company per database.

Every name here is invented. Personal fields that no view shows (phone, NPWP, bank account, e-mail) hold
clearly invalid DEMO- values and example.invalid addresses. Dates are relative to an anchor day (today by
default, or SEED_ANCHOR=YYYY-MM-DD), so questions like "due in the next 7 days" always have answers. The same
company, seed and anchor give the same data.
Integrity rules the generator keeps: invoice totals = subtotal + tax; every journal entry balances;
customer totals match their orders; account balances are recomputed from journal lines; the finished-goods
account equals the finished goods in stock at cost.
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


# ── company A: fabric and garments, West Java ──────────────────────────────────────────────────────
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


def _staff(departments):
    """(department, position) for each employee, in order: positions repeat in turn, and when the last one is
    a Kepala (head) role, the first person also gets it."""
    return [(dept, positions[-1] if i == 0 and len(positions) > 1 and "Kepala" in positions[-1]
             else positions[i % len(positions)])
            for dept, positions, count in departments for i in range(count)]


# ── company B: batik, knitwear and uniforms, Central and East Java ─────────────────────────────────
CATEGORIES_B = [("MORI", "Kain Mori"), ("RAJUT", "Benang Rajut"), ("WARNA", "Malam dan Pewarna"),
                ("PELENGKAP", "Bahan Pelengkap"), ("SETENGAH", "Barang Setengah Jadi"), ("BATIK", "Batik"),
                ("RAJUTAN", "Rajutan"), ("SERAGAM", "Seragam")]
WAREHOUSES_B = [
    ("BB-BDG", "Gudang Bahan Baku Bandung", "Bandung", "Jawa Barat", "RAW_MATERIAL"),
    ("GP-SMG", "Gudang Pusat Semarang", "Semarang", "Jawa Tengah", "GENERAL"),
    ("BT-PKL", "Gudang Batik Pekalongan", "Pekalongan", "Jawa Tengah", "FINISHED_GOODS"),
    ("BJ-SBY", "Gudang Barang Jadi Surabaya", "Surabaya", "Jawa Timur", "FINISHED_GOODS"),
    ("SJ-SLO", "Gudang Setengah Jadi Solo", "Surakarta", "Jawa Tengah", "WORK_IN_PROGRESS"),
]
PRODUCTS_B = [
    ("MR-101", "Kain Mori Primissima", "MORI", "RAW_MATERIAL", "meter", 21500, 29000, 600, "Putih", "100% Cotton"),
    ("MR-102", "Kain Mori Prima", "MORI", "RAW_MATERIAL", "meter", 17500, 24000, 500, "Putih", "100% Cotton"),
    ("MR-103", "Kain Mori Biru", "MORI", "RAW_MATERIAL", "meter", 13000, 18500, 450, "Putih Kebiruan", "100% Cotton"),
    ("MR-104", "Kain Dobby Katun", "MORI", "RAW_MATERIAL", "meter", 26000, 35000, 300, "Putih Tulang", "100% Cotton"),
    ("MR-105", "Kain Sutra ATBM", "MORI", "RAW_MATERIAL", "meter", 118000, 155000, 80, "Gading", "100% Silk"),
    ("MR-106", "Kain American Drill", "MORI", "RAW_MATERIAL", "meter", 36500, 47000, 400, "Biru Dongker", "65/35 Poly/Cotton"),
    ("MR-107", "Kain Oxford Seragam", "MORI", "RAW_MATERIAL", "meter", 29500, 39500, 350, "Putih", "100% Polyester"),
    ("MR-108", "Kain Tropical Wol", "MORI", "RAW_MATERIAL", "meter", 44000, 58000, 250, "Abu Tua", "70/30 Poly/Wool"),
    ("BR-201", "Benang Akrilik 2/32", "RAJUT", "RAW_MATERIAL", "kg", 58000, 0, 200, None, "100% Acrylic"),
    ("BR-202", "Benang Katun Rajut 20s", "RAJUT", "RAW_MATERIAL", "kg", 71000, 0, 180, None, "100% Cotton"),
    ("BR-203", "Benang Wol Campur", "RAJUT", "RAW_MATERIAL", "kg", 96000, 128000, 90, "Abu Muda", "50/50 Wool/Acrylic"),
    ("WR-301", "Malam Batik Klowong", "WARNA", "RAW_MATERIAL", "kg", 43000, 0, 120, None, "Parafin dan Gondorukem"),
    ("WR-302", "Malam Batik Tembokan", "WARNA", "RAW_MATERIAL", "kg", 38000, 0, 120, None, "Parafin dan Gondorukem"),
    ("WR-303", "Pewarna Naptol", "WARNA", "RAW_MATERIAL", "kg", 185000, 0, 40, None, None),
    ("WR-304", "Pewarna Indigosol", "WARNA", "RAW_MATERIAL", "kg", 240000, 0, 30, None, None),
    ("WR-305", "Serbuk Kulit Tingi", "WARNA", "RAW_MATERIAL", "kg", 62000, 88000, 60, "Coklat Soga", None),
    ("PL-401", "Kancing Batok Kelapa", "PELENGKAP", "TRADING", "lusin", 9000, 15000, 400, "Coklat", None),
    ("PL-402", "Bordir Logo Instansi", "PELENGKAP", "TRADING", "pcs", 3500, 6000, 3000, None, None),
    ("PL-403", "Emblem Bahu Seragam", "PELENGKAP", "TRADING", "pcs", 4200, 7500, 2000, "Kuning Emas", None),
    ("PL-404", "Kotak Kemasan Batik", "PELENGKAP", "TRADING", "pcs", 6500, 11000, 1000, "Coklat Kraft", None),
    ("PL-405", "Resleting Jaket 50cm", "PELENGKAP", "TRADING", "pcs", 3800, 6200, 1500, "Hitam", None),
    ("SJ-501", "Kain Batik Belum Dilorod", "SETENGAH", "WIP", "meter", 48000, 0, 150, "Soga", "100% Cotton"),
    ("SJ-502", "Panel Rajut Belum Dijahit", "SETENGAH", "WIP", "pcs", 31000, 0, 200, "Abu Muda", "100% Acrylic"),
    ("BT-601", "Kemeja Batik Tulis Parang", "BATIK", "MANUFACTURED", "pcs", 265000, 485000, 40, "Soga", "100% Cotton"),
    ("BT-602", "Hem Batik Cap Kawung", "BATIK", "MANUFACTURED", "pcs", 118000, 205000, 90, "Biru Indigo", "100% Cotton"),
    ("BT-603", "Kain Batik Tulis Mega Mendung", "BATIK", "MANUFACTURED", "lembar", 340000, 620000, 25, "Merah Bata", "100% Silk"),
    ("BT-604", "Dress Batik Lasem", "BATIK", "MANUFACTURED", "pcs", 86000, 149000, 120, "Merah Lasem", "100% Rayon"),
    ("BT-605", "Sarimbit Batik Keluarga", "BATIK", "MANUFACTURED", "set", 310000, 545000, 30, "Hijau Botol", "100% Cotton"),
    ("RJ-701", "Sweater Rajut Kerah V", "RAJUTAN", "MANUFACTURED", "pcs", 94000, 159000, 150, "Abu Muda", "100% Acrylic"),
    ("RJ-702", "Kardigan Rajut Wanita", "RAJUTAN", "MANUFACTURED", "pcs", 102000, 175000, 120, "Krem", "100% Acrylic"),
    ("RJ-703", "Syal Rajut Wol", "RAJUTAN", "MANUFACTURED", "pcs", 41000, 72000, 200, "Merah Marun", "50/50 Wool/Acrylic"),
    ("RJ-704", "Rompi Rajut Sekolah", "RAJUTAN", "MANUFACTURED", "pcs", 56000, 92000, 300, "Biru Dongker", "100% Acrylic"),
    ("SR-801", "Seragam PDH Dinas", "SERAGAM", "MANUFACTURED", "pcs", 138000, 215000, 200, "Khaki", "65/35 Poly/Cotton"),
    ("SR-802", "Seragam Sekolah Putih Abu", "SERAGAM", "MANUFACTURED", "set", 89000, 139000, 400, "Putih/Abu", "100% Polyester"),
    ("SR-803", "Wearpack Proyek", "SERAGAM", "MANUFACTURED", "pcs", 162000, 255000, 120, "Oranye", "65/35 Poly/Cotton"),
    ("SR-804", "Jas Almamater", "SERAGAM", "MANUFACTURED", "pcs", 176000, 289000, 100, "Biru Dongker", "70/30 Poly/Wool"),
    ("SR-805", "Kemeja Seragam Batik Kantor", "SERAGAM", "MANUFACTURED", "pcs", 97000, 165000, 180, "Coklat Sogan", "100% Cotton"),
]
CUSTOMERS_B = [
    ("PLG-101", "Toko Batik Parang Kencana", "COMPANY", 30), ("PLG-102", "CV Wastra Adiluhung", "COMPANY", 45),
    ("PLG-103", "PT Busana Kerja Mandiri", "COMPANY", 60), ("PLG-104", "Butik Kawung Asri", "COMPANY", 15),
    ("PLG-105", "Koperasi Karyawan Pelabuhan Contoh", "COMPANY", 30),
    ("PLG-106", "SD Negeri Contoh 7", "GOVERNMENT", 60), ("PLG-107", "Dinas Perhubungan Kabupaten Contoh", "GOVERNMENT", 90),
    ("PLG-108", "RSUD Contoh Waras", "GOVERNMENT", 60), ("PLG-109", "PT Karya Baja Konstruksi", "COMPANY", 45),
    ("PLG-110", "Toko Rajutan Hangat", "COMPANY", 15), ("PLG-111", "CV Sandang Pesisir Utara", "COMPANY", 30),
    ("PLG-112", "UD Batik Tiga Negeri", "COMPANY", 30), ("PLG-113", "PT Gerai Busana Timur", "COMPANY", 60),
    ("PLG-114", "Galeri Batik Laweyan Asri", "COMPANY", 30), ("PLG-115", "CV Mitra Seragam Sekolah", "COMPANY", 30),
    ("PLG-116", "Toko Kain Kauman", "COMPANY", 15), ("PLG-117", "PT Hotel Contoh Nirwana", "COMPANY", 45),
    ("PLG-118", "Yayasan Pendidikan Contoh Bakti", "COMPANY", 30), ("PLG-119", "Ibu Sulastri Handayani", "INDIVIDUAL", 0),
    ("PLG-120", "Bapak Wiryo Kusumo", "INDIVIDUAL", 0), ("PLG-121", "Ibu Maria Susanti", "INDIVIDUAL", 0),
    ("PLG-122", "CV Rajut Kencana Abadi", "COMPANY", 30), ("PLG-123", "Toko Oleh-Oleh Semarangan", "COMPANY", 15),
    ("PLG-124", "PT Ekspor Kriya Jawa", "COMPANY", 45), ("PLG-125", "Pemerintah Kota Contoh Timur", "GOVERNMENT", 60),
    ("PLG-126", "Butik Sogan Anggun", "COMPANY", 15), ("PLG-127", "CV Konfeksi Surya Timur", "COMPANY", 30),
    ("PLG-128", "Toko Seragam Tugu", "COMPANY", 15), ("PLG-129", "PT Distribusi Tekstil Jatim", "COMPANY", 60),
    ("PLG-130", "Koperasi Batik Pesisir", "COMPANY", 30),
]
SUPPLIERS_B = [
    ("PMS-01", "PT Mori Katun Sejahtera", "NET_30", 5, 95), ("PMS-02", "CV Malam Batik Lestari", "CASH", 4, 89),
    ("PMS-03", "PT Kimia Warna Indah", "NET_45", 4, 92), ("PMS-04", "CV Benang Rajut Makmur", "NET_15", 3, 81),
    ("PMS-05", "PT Sutra Alam Priangan", "NET_30", 4, 87), ("PMS-06", "UD Kancing Batok Jepara", "CASH", 3, 78),
    ("PMS-07", "PT Tekstil Drill Andalan", "NET_60", 5, 97), ("PMS-08", "CV Bordir Rapi Tasik", "NET_15", 4, 90),
    ("PMS-09", "PT Wol Timur Raya", "NET_45", 3, 83), ("PMS-10", "CV Kemasan Kraft Mandiri", "NET_30", 4, 91),
    ("PMS-11", "PT Kain Seragam Utama", "NET_30", 4, 88), ("PMS-12", "CV Soga Alam Wiradesa", "CASH", 2, 74),
    ("PMS-13", "PT Emblem Logam Presisi", "NET_30", 4, 85), ("PMS-14", "CV Resleting Kuat Sentosa", "NET_15", 3, 80),
]
FIRST_B = ["Bambang", "Sri", "Tri", "Wulan", "Sutrisno", "Endang", "Suharti", "Pujiono", "Wening", "Darmaji",
           "Ratih", "Slamet", "Yulianti", "Hartono", "Retno", "Anjar", "Siswanto", "Parmin", "Handoko", "Dyah",
           "Ngadimin", "Sulistyo", "Nining", "Widodo", "Rukmini", "Suyatno", "Ambar", "Tukiman", "Winarsih", "Bayu",
           "Heru", "Ika", "Laras", "Mulyono", "Novi", "Paijo", "Rini", "Samsul", "Titik", "Utari"]
LAST_B = ["Wibowo", "Susilo", "Purnomo", "Hartati", "Suharjo", "Kartono", "Utomo", "Wardani", "Sulistyowati",
          "Priyambodo", "Mangunkusumo", None, "Anggraini"]
STAFF_B = [(dept, position) for dept, positions in [
    ("Pembatikan", ["Kepala Pembatikan"] + ["Pembatik Tulis"] * 4 + ["Pembatik Cap"] * 4),
    ("Pewarnaan", ["Kepala Pewarnaan"] + ["Operator Celup"] * 3),
    ("Rajut", ["Kepala Rajut"] + ["Operator Mesin Rajut"] * 4 + ["Operator Linking"]),
    ("Penjahitan", ["Kepala Penjahitan"] + ["Penjahit Seragam"] * 4 + ["Operator Obras"] * 2),
    ("Gudang", ["Kepala Gudang"] + ["Petugas Gudang"] * 2),
    ("Keuangan", ["Manajer Keuangan", "Akuntan", "Kasir"]),
    ("Pemasaran", ["Manajer Pemasaran", "Tenaga Pemasaran", "Tenaga Pemasaran", "Admin Pemasaran"]),
    ("Pengadaan", ["Staf Pengadaan"] * 2)] for position in positions]
ACCOUNTS_B = [("1101", "Kas Besar", "ASSET"), ("1102", "Bank Mandiri", "ASSET"), ("1131", "Piutang Dagang", "ASSET"),
              ("1141", "Persediaan Bahan Baku", "ASSET"), ("1143", "Persediaan Barang Jadi", "ASSET"),
              ("1151", "PPN Masukan", "ASSET"), ("2101", "Utang Dagang", "LIABILITY"), ("2131", "PPN Keluaran", "LIABILITY"),
              ("3101", "Modal Pemilik", "EQUITY"), ("4101", "Pendapatan Penjualan", "REVENUE"),
              ("5101", "Harga Pokok Penjualan", "EXPENSE"), ("6101", "Beban Gaji dan Upah", "EXPENSE"),
              ("6102", "Beban Listrik dan Air", "EXPENSE"), ("6103", "Beban Sewa Gudang", "EXPENSE"),
              ("6104", "Beban Pengiriman", "EXPENSE")]

# Everything that differs between the two companies. "home" is a product's own warehouse, by category or
# else by product type; every product also has some stock in the "general" warehouse. Monthly costs given as
# (base, n, step) are base plus a random 0..n steps. Company A's values are the ones it always had.
COMPANIES = {
    "A": {"seed": 32, "categories": CATEGORIES, "warehouses": WAREHOUSES, "products": PRODUCTS,
          "home": {"MANUFACTURED": "GJ-JKT", "TRADING": "GU-BDG", "RAW_MATERIAL": "GB-MJL"}, "general": "GU-BDG",
          "customers": CUSTOMERS, "inactive": {"C-024"}, "credit_limits": [50, 75, 100, 150, 250],
          "suppliers": SUPPLIERS, "names": (FIRST, LAST), "staff": _staff(DEPARTMENTS),
          "away": {6: "ON_LEAVE", 19: "INACTIVE"}, "employee_code": "EMP-{:03d}",
          "accounts": ACCOUNTS, "roles": {"cash": "1010", "bank": "1020", "ar": "1100", "raw": "1200", "fg": "1210",
                                          "vat_in": "1300", "ap": "2100", "vat_out": "2200", "capital": "3000",
                                          "sales": "4000", "cogs": "5000", "salary": "6100", "power": "6200",
                                          "rent": "6300"},
          "number": "{}-{}-{:04d}", "sales_orders": 150, "purchase_orders": 60,
          "so_qty": ([50, 100, 150, 200, 300, 500], [24, 48, 60, 100, 120, 240]),
          "po_qty": ([200, 300, 500, 800, 1000], [20, 50, 100, 150]),
          "salary": 118_000_000, "power": (21_000_000, 6, 500_000), "rent": 35_000_000, "freight": None,
          "capital": 2_500_000_000},
    "B": {"seed": 1015, "categories": CATEGORIES_B, "warehouses": WAREHOUSES_B, "products": PRODUCTS_B,
          "home": {"BATIK": "BT-PKL", "MANUFACTURED": "BJ-SBY", "WIP": "SJ-SLO", "TRADING": "GP-SMG",
                   "RAW_MATERIAL": "BB-BDG"}, "general": "GP-SMG",
          "customers": CUSTOMERS_B, "inactive": {"PLG-110", "PLG-128"}, "credit_limits": [40, 60, 90, 120, 200, 350],
          "suppliers": SUPPLIERS_B, "names": (FIRST_B, LAST_B), "staff": STAFF_B,
          "away": {4: "ON_LEAVE", 15: "ON_LEAVE", 27: "INACTIVE", 33: "TERMINATED"}, "employee_code": "KRY-{:04d}",
          "accounts": ACCOUNTS_B, "roles": {"cash": "1101", "bank": "1102", "ar": "1131", "raw": "1141", "fg": "1143",
                                            "vat_in": "1151", "ap": "2101", "vat_out": "2131", "capital": "3101",
                                            "sales": "4101", "cogs": "5101", "salary": "6101", "power": "6102",
                                            "rent": "6103", "freight": "6104"},
          "number": "{}/{}/{:05d}", "sales_orders": 190, "purchase_orders": 80,
          "so_qty": ([30, 60, 100, 150, 250, 400], [12, 24, 36, 72, 120, 180, 300]),
          "po_qty": ([250, 400, 600, 900, 1200], [25, 40, 60, 90, 120]),
          "salary": 164_500_000, "power": (26_000_000, 8, 750_000), "rent": 42_000_000,
          "freight": (9_000_000, 10, 400_000), "capital": 3_750_000_000},
}
TERMS = {0: "CASH", 15: "NET_15", 30: "NET_30", 45: "NET_45", 60: "NET_60", 90: "NET_90"}


def generate(anchor=None, seed=None, company="A"):
    """Return {table: [row dicts]} for every table in 01_integra_subset.sql, for company "A" or "B".
    Without a seed, each company uses its own, so the two never share an id."""
    P = COMPANIES[company]
    R = P["roles"]
    rng = random.Random(P["seed"] if seed is None else seed)
    anchor = anchor or dt.date.today()
    T = defaultdict(list)

    cat_id = {}
    for code, name in P["categories"]:
        cat_id[code] = _uid(rng)
        T["categories"].append({"id": cat_id[code], "code": code, "name": name})
    wh_id = {}
    for code, name, city, prov, wtype in P["warehouses"]:
        wh_id[code] = _uid(rng)
        T["warehouses"].append({"id": wh_id[code], "code": code, "name": name, "city": city, "province": prov,
                                "warehouseType": wtype})
    prod = {}
    for code, name, cat, ptype, unit, cost, sell, reorder, color, comp in P["products"]:
        pid = _uid(rng)
        home = P["home"].get(cat) or P["home"][ptype]
        prod[code] = {"id": pid, "cost": cost, "sell": sell, "type": ptype, "unit": unit, "home": home}
        T["products"].append({"id": pid, "code": code, "name": name, "categoryId": cat_id[cat], "productType": ptype,
                              "unit": unit, "costPrice": cost, "sellingPrice": sell or None,
                              "minStock": reorder // 2, "reorderLevel": reorder, "color": color, "composition": comp})
        for wcode in dict.fromkeys((home, P["general"])):  # a fixed order: a set's order changes with PYTHONHASHSEED
            base = reorder * rng.uniform(0.3, 3.0)          # some products end up below their reorder level
            qty = round(base if wcode == home else base * 0.25)
            reserved = round(qty * rng.choice([0, 0, 0.1, 0.2]))
            T["stock_levels"].append({"id": _uid(rng), "productId": pid, "warehouseId": wh_id[wcode],
                                      "quantity": qty, "reservedQty": reserved, "availableQty": qty - reserved})

    cust = {}
    for code, name, ctype, term in P["customers"]:
        cid = _uid(rng)
        limit = 0 if ctype == "INDIVIDUAL" else rng.choice(P["credit_limits"]) * 1_000_000
        cust[code] = {"id": cid, "term": term, "limit": limit, "total": 0, "last": None}
        T["customers"].append({"id": cid, "code": code, "name": name, "customerType": ctype,
                               "npwp": None if ctype == "INDIVIDUAL" else f"DEMO-0{rng.randint(10, 99)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}.{rng.randint(1, 9)}-{rng.randint(100, 999)}.000",
                               "taxStatus": "NON_PKP" if ctype == "INDIVIDUAL" else "PKP",
                               "phone": f"DEMO-08{rng.randint(1000000000, 9999999999)}",
                               "email": f"{code.lower()}@example.invalid",
                               "creditLimit": limit, "creditTerm": term, "paymentTerm": TERMS[term],
                               "creditStatus": "GOOD", "isActive": code not in P["inactive"],
                               "lastOrderDate": None, "totalOrderValue": 0})
    supp = {}
    for code, name, term, rating, ontime in P["suppliers"]:
        sid = _uid(rng)
        supp[code] = {"id": sid, "term": TERM_DAYS[term]}
        T["suppliers"].append({"id": sid, "code": code, "name": name, "phone": f"DEMO-022{rng.randint(1000000, 9999999)}",
                               "paymentTerm": term, "rating": rating, "onTimeRate": ontime,
                               "qualityScore": round(rng.uniform(70, 98), 2),
                               "bankAccountNumber": f"DEMO-{rng.randint(10**9, 10**10 - 1)}", "npwp": None})

    acct = {}
    for code, name, atype in P["accounts"]:
        acct[code] = _uid(rng)
        T["gl_accounts"].append({"id": acct[code], "code": code, "name": name, "type": atype, "balance": 0})
    counters = defaultdict(int)

    def number(prefix, day):
        counters[(prefix, day.year)] += 1
        return P["number"].format(prefix, day.year, counters[(prefix, day.year)])

    def journal(day, description, reference, lines, invoice_id=None, payment_id=None):
        assert round(sum(d for _, d, _ in lines), 2) == round(sum(c for _, _, c in lines), 2), description
        eid = _uid(rng)
        T["journal_entries"].append({"id": eid, "date": _ts(day, 16), "description": description,
                                     "reference": reference, "status": "POSTED",
                                     "invoiceId": invoice_id, "paymentId": payment_id})
        for role, debit, credit in lines:
            T["journal_lines"].append({"id": _uid(rng), "entryId": eid, "accountId": acct[R[role]],
                                       "description": description, "debit": debit, "credit": credit})

    start = anchor - dt.timedelta(days=210)

    # ── sales: orders, items, AR invoices, customer payments, shipments, COGS ─────────────────────
    sellable = [c for c, *_ in P["products"] if prod[c]["sell"]]
    for _ in range(P["sales_orders"]):
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
            qty = rng.choice(P["so_qty"][0]) if p["unit"] == "meter" else rng.choice(P["so_qty"][1])
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
                wh = prod[pcode]["home"] if prod[pcode]["type"] == "MANUFACTURED" else P["general"]
                T["inventory_transactions"].append({"id": _uid(rng), "productId": ln["productId"], "warehouseId": wh_id[wh],
                                                    "type": "SO_SHIPMENT", "quantity": -int(ln["quantity"]),
                                                    "unitCost": prod[pcode]["cost"],
                                                    "totalValue": int(ln["quantity"]) * prod[pcode]["cost"],
                                                    "purchaseOrderId": None, "salesOrderId": so_id,
                                                    "createdAt": _ts(ship_day, 14)})
            journal(ship_day, f"HPP pengiriman {so_number}", so_number, [("cogs", cogs, 0), ("fg", 0, cogs)])
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
                    [("ar", total, 0), ("sales", 0, subtotal), ("vat_out", 0, tax)], invoice_id=inv_id)
            if paid:
                pay_day = min(issue + dt.timedelta(days=rng.randint(5, max(6, c["term"] + 10))), anchor)
                pay_id = _uid(rng)
                pay_number = number("PAY", pay_day)
                method = rng.choice(["TRANSFER", "TRANSFER", "TRANSFER", "GIRO", "CASH"])
                T["payments"].append({"id": pay_id, "number": pay_number, "invoiceId": inv_id, "customerId": c["id"],
                                      "supplierId": None, "date": _ts(pay_day, 11), "amount": paid, "method": method,
                                      "reference": f"TRF{rng.randint(100000, 999999)}", "whtAmount": None})
                cash_account = "cash" if method == "CASH" else "bank"      # cash goes to Kas, the rest to the bank
                journal(pay_day, f"Penerimaan {pay_number} atas {inv_number}", pay_number,
                        [(cash_account, paid, 0), ("ar", 0, paid)], payment_id=pay_id)

    # ── purchasing: orders, items, receipts, AP invoices, supplier payments ───────────────────────
    raw = [c for c, *_ in P["products"] if prod[c]["type"] in ("RAW_MATERIAL", "TRADING")]
    for _ in range(P["purchase_orders"]):
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
            qty = rng.choice(P["po_qty"][0]) if p["unit"] in ("meter", "pcs") else rng.choice(P["po_qty"][1])
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
                    wh = prod[it["_code"]]["home"]
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
                    [("raw", amount, 0), ("vat_in", tax, 0), ("ap", 0, net)], invoice_id=inv_id)
            if paid:
                pay_day = min(recv_day + dt.timedelta(days=max(1, s["term"] - rng.randint(0, 5))), anchor)
                pay_id = _uid(rng)
                pay_number = number("PAY", pay_day)
                T["payments"].append({"id": pay_id, "number": pay_number, "invoiceId": inv_id, "customerId": None,
                                      "supplierId": s["id"], "date": _ts(pay_day, 15), "amount": paid,
                                      "method": "TRANSFER", "reference": f"TRF{rng.randint(100000, 999999)}",
                                      "whtAmount": None})
                journal(pay_day, f"Pembayaran {pay_number} atas {bill_number}", pay_number,
                        [("ap", paid, 0), ("bank", 0, paid)], payment_id=pay_id)
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
        journal(day, f"Gaji karyawan {month:%m/%Y}", f"GAJI-{month:%Y%m}", [("salary", P["salary"], 0), ("bank", 0, P["salary"])])
        base, steps, step = P["power"]
        electricity = base + rng.randint(0, steps) * step
        journal(day, f"Listrik pabrik {month:%m/%Y}", f"PLN-{month:%Y%m}", [("power", electricity, 0), ("bank", 0, electricity)])
        journal(day, f"Sewa gudang {month:%m/%Y}", f"SEWA-{month:%Y%m}", [("rent", P["rent"], 0), ("bank", 0, P["rent"])])
        if P["freight"]:
            base, steps, step = P["freight"]
            freight = base + rng.randint(0, steps) * step
            journal(day, f"Ongkos kirim {month:%m/%Y}", f"KIRIM-{month:%Y%m}", [("freight", freight, 0), ("bank", 0, freight)])
        month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    # ── people ────────────────────────────────────────────────────────────────────────────────────
    first_names, last_names = P["names"]
    for n, (dept, position) in enumerate(P["staff"], 1):
        first, last = first_names[(n * 7) % len(first_names)], last_names[(n * 5) % len(last_names)]
        status = P["away"].get(n, "ACTIVE")
        eid = _uid(rng)
        code = P["employee_code"].format(n)
        T["employees"].append({"id": eid, "employeeId": code, "firstName": first, "lastName": last,
                               "email": f"{code.lower()}@example.invalid", "phone": f"DEMO-08{n:010d}",
                               "department": dept, "position": position,
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
    cogs_total = sum(ln["credit"] for ln in T["journal_lines"] if ln["accountId"] == acct[R["fg"]])
    cost_of = {v["id"]: v["cost"] for v in prod.values()}
    fg_ids = {v["id"] for v in prod.values() if v["type"] == "MANUFACTURED"}
    fg_now = round(sum(float(s["quantity"]) * cost_of[s["productId"]] for s in T["stock_levels"] if s["productId"] in fg_ids))
    opening_fg = cogs_total + fg_now
    journal(start, "Saldo awal", "OPENING", [("bank", P["capital"], 0), ("fg", opening_fg, 0),
                                             ("capital", 0, P["capital"] + opening_fg)])

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
        load(conn, generate(anchor, company=os.environ.get("SEED_COMPANY", "A")))
    print("seeded")
