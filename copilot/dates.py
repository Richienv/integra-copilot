"""Date pinning: in evaluation, "today" is a fixed anchor day, not the day the run happens.

Questions like "this month" or "the last 30 days" depend on today. The demo data is generated relative to an
anchor day, so evaluation replaces CURRENT_DATE, NOW() and the other clock functions with literals for that
same day, in the reference SQL and in the model's SQL alike. A run then gives the same answers on any day.
Times are pinned to 12:00:00 on the anchor day; each literal keeps the type of the function it replaces.
"""
import datetime as dt

import sqlglot
from sqlglot import exp

NOON = "12:00:00"
NOW_FUNCTIONS = {"transaction_timestamp", "statement_timestamp", "clock_timestamp"}   # Postgres synonyms of NOW()


def _literal(node, day):
    """The literal that replaces one clock function, or None to leave the node alone."""
    if isinstance(node, exp.CurrentDate):
        value, typ = day.isoformat(), "date"
    elif isinstance(node, exp.CurrentTimestamp):                     # CURRENT_TIMESTAMP and NOW()
        value, typ = f"{day.isoformat()} {NOON}", "timestamptz"
    elif isinstance(node, exp.Anonymous) and str(node.this).lower() in NOW_FUNCTIONS:
        value, typ = f"{day.isoformat()} {NOON}", "timestamptz"
    elif isinstance(node, exp.Localtimestamp):
        value, typ = f"{day.isoformat()} {NOON}", "timestamp"
    elif isinstance(node, exp.CurrentTime):
        value, typ = NOON, "timetz"
    elif isinstance(node, exp.Localtime):
        value, typ = NOON, "time"
    else:
        return None
    return exp.cast(exp.Literal.string(value), exp.DataType.build(typ, dialect="postgres"))


def pin_dates(sql, anchor):
    """Return sql with every clock function replaced by a literal for the anchor day.
    SQL without a clock function, or that does not parse, comes back unchanged."""
    day = dt.date.fromisoformat(str(anchor))
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.SqlglotError:                             # a parse or a tokenizer error
        return sql
    hits = []

    def swap(node):
        new = _literal(node, day)
        if new is None:
            return node
        hits.append(node)
        return new
    pinned = tree.transform(swap)
    return pinned.sql(dialect="postgres", comments=False) if hits else sql


class PinnedDB:
    """A reader whose queries all run as if today were the anchor day."""

    def __init__(self, db, anchor):
        self.db, self.anchor = db, dt.date.fromisoformat(str(anchor))

    def run(self, sql):
        return self.db.run(pin_dates(sql, self.anchor))

    def __getattr__(self, name):
        return getattr(self.db, name)
