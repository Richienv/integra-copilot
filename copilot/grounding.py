"""Two small safety checks around the model's words.

detect_language: answer in the language of the question (Indonesian, Chinese or English).
check_numbers:   every number in the model's one-line summary must come from the query result (a cell,
                 a column total, the row count), from the question, or from a date in the result.
                 If one doesn't, the summary is replaced by a plain template. Numbers come from the
                 database, never from the model.
"""
import re

ID_WORDS = set("""yang dan di ke dari untuk dengan berapa apa siapa mana kapan bulan minggu hari tahun ini lalu
depan terakhir tampilkan daftar semua total jumlah piutang hutang utang pelanggan pemasok stok gudang penjualan
pembelian karyawan absen hadir terlambat produk barang kain tagihan faktur jatuh tempo belum sudah lunas bayar
paling terbesar terbanyak tertinggi terendah rata saldo kas biaya beban gaji tolong coba lihat kita kami saya
ada tidak berapakah bagaimana""".split())
LANG_NAMES = {"id": "Bahasa Indonesia", "zh": "简体中文", "en": "English"}


def detect_language(text):
    if re.search(r"[一-鿿]", text or ""):
        return "zh"
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return "id" if len(words & ID_WORDS) >= 1 else "en"


_MULT = {"juta": 1e6, "jt": 1e6, "million": 1e6, "mio": 1e6, "m": 1e6, "ribu": 1e3, "rb": 1e3, "thousand": 1e3,
         "k": 1e3, "miliar": 1e9, "milyar": 1e9, "billion": 1e9, "bn": 1e9, "triliun": 1e12, "trillion": 1e12,
         "万": 1e4, "亿": 1e8}
_NUM = re.compile(r"(\d{1,3}(?:[.,]\d{3})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(juta|jt|million|mio|miliar|milyar|billion|bn|"
                  r"triliun|trillion|ribu|rb|thousand|万|亿|k\b|m\b)?", re.IGNORECASE)


def _readings(raw):
    """A number can be written 12.500.000 (Indonesian) or 12,500,000.00 (English). Return every sane reading."""
    out = set()
    for thousands, decimal in ((".", ","), (",", ".")):
        s = raw
        if thousands in s and re.fullmatch(r"\d{1,3}(\%s\d{3})+(\%s\d+)?" % (thousands, decimal), s):
            s = s.replace(thousands, "")
        s = s.replace(decimal, ".")
        try:
            out.add(float(s))
        except ValueError:
            pass
    return out


def numbers_in(text):
    """Each number found, as a set of possible values (to allow for separators and 'juta' / '万')."""
    found = []
    for m in _NUM.finditer(text or ""):
        mult = _MULT.get((m.group(2) or "").lower(), 1)
        found.append({v * mult for v in _readings(m.group(1))})
    return found


def _allowed_values(question, columns, rows, sql=""):
    values = {float(len(rows))}
    for vals in numbers_in(question) + numbers_in(sql):      # the period or limit the query itself used
        values |= vals
    numeric_cols = {}
    for r in rows:
        for i, v in enumerate(r):
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                values.add(float(v))
                numeric_cols.setdefault(i, []).append(float(v))
            else:
                for vals in numbers_in(str(v)):
                    values |= vals
                for part in re.findall(r"\d+", str(v)):
                    values.add(float(part))
    for vals in numeric_cols.values():
        values.add(sum(vals))
        values.add(sum(vals) / len(vals))
    return values


def check_numbers(summary, question, columns, rows, sql=""):
    """Return (ok, numbers_that_did_not_match)."""
    allowed = _allowed_values(question, columns, rows, sql)
    bad = []
    for readings in numbers_in(summary):
        if not any(abs(r - a) <= max(0.5, 0.006 * abs(a)) for r in readings for a in allowed):
            bad.append(sorted(readings)[0])
    return (not bad), bad
