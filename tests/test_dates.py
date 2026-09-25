"""Date pinning: in evaluation, today is the anchor day in every query."""
from copilot import db, guard
from copilot.dates import PinnedDB, pin_dates

CLOCKS = "select current_date, current_timestamp, now(), localtimestamp, localtime, current_time, clock_timestamp()"


def test_every_clock_function_becomes_a_literal_of_the_same_type():
    pinned = pin_dates(CLOCKS, "2026-10-15")
    assert pinned == ("SELECT CAST('2026-10-15' AS DATE), CAST('2026-10-15 12:00:00+07' AS TIMESTAMPTZ), "
                      "CAST('2026-10-15 12:00:00+07' AS TIMESTAMPTZ), CAST('2026-10-15 12:00:00' AS TIMESTAMP), "
                      "CAST('12:00:00' AS TIME), CAST('12:00:00+07' AS TIMETZ), CAST('2026-10-15 12:00:00+07' AS TIMESTAMPTZ)")


def test_sql_without_a_clock_is_left_alone():
    for sql in ("select count(*) from invoices limit 200", "select 'CURRENT_DATE' as word from invoices",
                "select 'today' as period, 'now' as word from invoices",           # labels, not clock readings
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


def test_clock_strings_one_argument_age_and_timeofday_are_pinned(reader):
    """'today'::date, date 'today', 'now', AGE(x) and TIMEOFDAY() all read the clock too."""
    r = PinnedDB(reader, "2026-10-15").run(
        "select 'today'::date, date 'today', 'now'::timestamp::date, cast('yesterday' as date), timestamp 'tomorrow', "
        "age(date '2026-01-01') = age(date '2026-10-15', date '2026-01-01'), timeofday()")
    assert r.rows == [["2026-10-15", "2026-10-15", "2026-10-15", "2026-10-14", "2026-10-16T00:00:00", True,
                       "2026-10-15 12:00:00+07"]]
    for sql in ("select count(*) from invoices where direction = 'receivable' and due_date < 'today'::date",
                "select max(age(due_date)) from invoices", "select count(*) from invoices where due_date > 'now'::date - 30",
                "select count(*) from invoices where due_date between 'yesterday' and 'today'"):
        checked = guard.check(sql)
        assert checked.ok and pin_dates(checked.sql, "2026-10-15") != checked.sql, sql


def test_the_pinned_instant_does_not_depend_on_the_server_time_zone(reader):
    """pgserver copies the host's time zone; a pinned run must give the same "today in WIB" on any machine."""
    sql = guard.check("select (now() at time zone 'Asia/Jakarta')::date, now()::date, current_date, "
                      "localtimestamp").sql
    for tz in ("UTC", "America/Los_Angeles", "Pacific/Kiritimati"):
        other = db.ReadOnlyDB(reader.uri)
        other._connection().execute(f"set timezone = '{tz}'")                 # as if the host were there
        assert PinnedDB(other, "2026-10-15").run(sql).rows == [["2026-10-15", "2026-10-15", "2026-10-15",
                                                                 "2026-10-15T12:00:00"]], tz
        assert PinnedDB(other, "2026-10-15").run("show timezone").rows == [["Asia/Jakarta"]]
        assert other.run("show timezone").rows == [[tz]]                      # only the pinned query's transaction
