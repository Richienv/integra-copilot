import pytest

from copilot.grounding import check_numbers, detect_language, numbers_in


@pytest.mark.parametrize("text,value", [
    ("Rp 12.500.000", 12_500_000), ("Rp 12,5 juta", 12_500_000), ("12.5 million", 12_500_000),
    ("1250万", 12_500_000), ("12,500,000.50", 12_500_000.5), ("31.025641025641026", 31.025641025641026),
    ("Rp 1,2 miliar", 1_200_000_000),
])
def test_number_formats(text, value):
    assert any(abs(v - value) < 1e-6 for v in numbers_in(text)[0])


def test_grounded_summary_passes():
    rows = [["Toko A", 12_480_000], ["CV B", 20_000_000]]
    assert check_numbers("Toko A masih punya piutang Rp 12,5 juta.", "", ["c", "v"], rows)[0]
    assert check_numbers("Totalnya Rp 32.480.000 dari 2 pelanggan.", "", ["c", "v"], rows)[0]


def test_invented_number_fails():
    ok, bad = check_numbers("Piutangnya Rp 99 juta.", "", ["c", "v"], [["Toko A", 12_480_000]])
    assert not ok and bad == [99_000_000]


def test_numbers_from_sql_and_question_are_allowed():
    rows = [["Toko A", 12_480_000]]
    assert check_numbers("Dalam 7 hari ke depan: Rp 12.480.000.", "minggu ini", ["c", "v"], rows,
                         "select ... where due_date <= current_date + 7")[0]


@pytest.mark.parametrize("text,lang", [
    ("Berapa piutang yang jatuh tempo minggu ini?", "id"), ("哪些产品库存低于补货点？", "zh"),
    ("Top 5 customers by sales", "en"), ("Siapa pelanggan terbesar?", "id"),
])
def test_language(text, lang):
    assert detect_language(text) == lang
