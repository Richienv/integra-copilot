"""Date pinning: in evaluation, "today" is a fixed anchor day, not the day the run happens.

Questions like "this month" or "the last 30 days" depend on today. The demo data is generated relative to an
anchor day, so evaluation replaces everything that reads the clock with literals for that same day, in the
reference SQL and in the model's SQL alike: CURRENT_DATE, NOW() and the other clock functions, the strings
'today', 'now', 'yesterday' and 'tomorrow' where Postgres reads them as a date or a time (cast to one, as in
'today'::date, or compared with or added to one, as in due_date < 'today'), AGE(x) with one argument (which
counts from today) and TIMEOFDAY(). A run then gives the same answers on any day. 'today' used as a label
(select 'today' as period) is text, not a clock reading, and stays as it is.

Times are pinned to 12:00:00 on the anchor day in Western Indonesian Time (UTC+7), and every evaluated query
runs with its session time zone set to Asia/Jakarta, so "today in WIB" is the same on any machine; each
literal keeps the type of the function it replaces.
"""
import datetime as dt

import sqlglot
from sqlglot import exp

NOON = "12:00:00"
OFFSET = "+07"                     # WIB, Asia/Jakarta: no daylight saving
TIMEZONE = "Asia/Jakarta"          # the session time zone of every evaluated query
NOW_FUNCTIONS = {"transaction_timestamp", "statement_timestamp", "clock_timestamp"}   # Postgres synonyms of NOW()
CLOCK_WORDS = {"today": 0, "yesterday": -1, "tomorrow": 1}                            # day offsets; 'now' is noon
TEMPORAL = ("date", "timestamp", "timestamptz", "time", "timetz")
IMPLICIT = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Add, exp.Sub, exp.Between, exp.In)


def _clock_word(node):
    """'today', 'now', 'yesterday' or 'tomorrow' where Postgres reads it as a date or a time: cast to a date or
    time type, or an operand of a comparison, BETWEEN, IN, + or -. None for any other node."""
    if not (isinstance(node, exp.Literal) and node.is_string):
        return None
    word, parent = node.this.strip().lower(), node.parent
    if word not in CLOCK_WORDS and word != "now":
        return None
    if isinstance(parent, exp.Cast) and parent.to.is_type(*TEMPORAL) or isinstance(parent, IMPLICIT):
        return word
    return None


def _literal(node, day):
    """The literal that replaces one clock reading, or None to leave the node alone."""
    word = _clock_word(node)
    if word == "now":
        return exp.Literal.string(f"{day.isoformat()} {NOON}{OFFSET}")        # still a string, as 'now' was
    if word:
        return exp.Literal.string((day + dt.timedelta(days=CLOCK_WORDS[word])).isoformat())
    if isinstance(node, exp.CurrentDate):
        value, typ = day.isoformat(), "date"
    elif isinstance(node, exp.CurrentTimestamp):                     # CURRENT_TIMESTAMP and NOW()
        value, typ = f"{day.isoformat()} {NOON}{OFFSET}", "timestamptz"
    elif isinstance(node, exp.Anonymous) and str(node.this).lower() in NOW_FUNCTIONS:
        value, typ = f"{day.isoformat()} {NOON}{OFFSET}", "timestamptz"
    elif isinstance(node, exp.Anonymous) and str(node.this).lower() == "timeofday":
        value, typ = f"{day.isoformat()} {NOON}{OFFSET}", "text"
    elif isinstance(node, exp.Localtimestamp):
        value, typ = f"{day.isoformat()} {NOON}", "timestamp"
    elif isinstance(node, exp.CurrentTime):
        value, typ = f"{NOON}{OFFSET}", "timetz"
    elif isinstance(node, exp.Localtime):
        value, typ = NOON, "time"
    else:
        return None
    return exp.cast(exp.Literal.string(value), exp.DataType.build(typ, dialect="postgres"))


def pin_dates(sql, anchor):
    """Return sql with every clock reading replaced by a literal for the anchor day.
    SQL without a clock reading, or that does not parse, comes back unchanged."""
    day = dt.date.fromisoformat(str(anchor))
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.SqlglotError:                             # a parse or a tokenizer error
        return sql
    hits = []

    def swap(node):
        if isinstance(node, exp.Anonymous) and str(node.this).lower() == "age" and len(node.expressions) == 1:
            hits.append(node)                                       # AGE(x) counts from today at midnight
            node.set("expressions", [exp.cast(exp.Literal.string(day.isoformat()), "date"), *node.expressions])
            return node                                             # the same node: its argument is pinned too
        new = _literal(node, day)
        if new is None:
            return node
        hits.append(node)
        return new
    pinned = tree.transform(swap)
    return pinned.sql(dialect="postgres", comments=False) if hits else sql


class PinnedDB:
    """A reader whose queries all run as if today were the anchor day, in the Asia/Jakarta time zone."""

    def __init__(self, db, anchor):
        self.db, self.anchor = db, dt.date.fromisoformat(str(anchor))

    def run(self, sql):
        return self.db.run(pin_dates(sql, self.anchor), timezone=TIMEZONE)

    def __getattr__(self, name):
        return getattr(self.db, name)
