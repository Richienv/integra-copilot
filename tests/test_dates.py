"""Date pinning: in evaluation, today is the anchor day in every query."""
from copilot import guard
from copilot.dates import PinnedDB, pin_dates

CLOCKS = "select current_date, current_timestamp, now(), localtimestamp, localtime, current_time, clock_timestamp()"


def test_every_clock_function_becomes_a_literal_of_the_same_type():
    pinned = pin_dates(CLOCKS, "2026-10-15")
    assert pinned == ("SELECT CAST('2026-10-15' AS DATE), CAST('2026-10-15 12:00:00' AS TIMESTAMPTZ), "
                      "CAST('2026-10-15 12:00:00' AS TIMESTAMPTZ), CAST('2026-10-15 12:00:00' AS TIMESTAMP), "
                      "CAST('12:00:00' AS TIME), CAST('12:00:00' AS TIMETZ), CAST('2026-10-15 12:00:00' AS TIMESTAMPTZ)")


def test_sql_without_a_clock_is_left_alone():
    for sql in ("select count(*) from invoices limit 200", "select 'CURRENT_DATE' as word from invoices",
                "select current_date(", "select 'current_date"):    # does not parse: unchanged, the database reports it
        assert pin_dates(sql, "2026-10-15") == sql


def test_guarded_sql_is_pinned_after_the_guard():
    checked = guard.check("select count(*) from invoices where due_date between current_date and current_date + 7")
    assert checked.ok
    assert pin_dates(checked.sql, "2026-10-15") == ("SELECT COUNT(*) FROM invoices WHERE due_date BETWEEN "
                                                    "CAST('2026-10-15' AS DATE) AND CAST('2026-10-15' AS DATE) + 7 LIMIT 200")


def test_pinned_reader_runs_as_if_today_were_the_anchor(reader):
    r = PinnedDB(reader, "2026-10-15").run("select current_date, now()::date, localtimestamp, current_date - 30, "
                                           "date_trunc('month', current_date)::date")
    assert r.rows == [["2026-10-15", "2026-10-15", "2026-10-15T12:00:00", "2026-09-15", "2026-10-01"]]
    assert PinnedDB(reader, "2026-10-15").user() == "copilot_reader"      # everything else passes through
