"""The SQL guard: the second of three independent locks.

1. The prompt asks for one read-only SELECT.        (the model can ignore it)
2. This guard parses the SQL and rejects anything else. (the model cannot talk its way past a parser)
3. The database role can only read the copilot views, in read-only transactions, with a timeout.

The guard also returns the SQL re-generated from the parse tree, so comments and tricks in the original
text never reach the database.
"""
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .semantic import CATALOG

WRITE_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable",
    "Command", "Into", "Lock", "Set", "Copy", "Grant", "Transaction", "Commit", "Rollback", "Use",
) if hasattr(exp, n))
SET_OPS = tuple(getattr(exp, n) for n in ("Union", "Intersect", "Except") if hasattr(exp, n))
DENY_FUNCTIONS = {"set_config", "current_setting", "dblink", "dblink_exec", "nextval", "setval", "currval",
                  "query_to_xml", "query_to_xml_and_xmlschema", "txid_current", "lo_import", "lo_export"}
DENY_PREFIXES = ("pg_", "lo_", "dblink")
SAFE_TABLE_FUNCTIONS = {"generate_series", "exploding_generate_series", "unnest", "explode"}


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    reason: str = ""
    category: str = ""                 # write | multi_statement | forbidden_object | forbidden_function | syntax
    views: list = field(default_factory=list)


def _function_name(node):
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return (node.sql_name() or "").lower()


def check(sql, allowed_views=None, max_rows=200):
    allowed = set(allowed_views or CATALOG)
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return GuardResult(False, reason="Empty query.", category="syntax")
    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        return GuardResult(False, reason=f"Could not parse the SQL: {str(e).splitlines()[0]}", category="syntax")
    if len(statements) != 1:
        return GuardResult(False, reason="Exactly one statement is allowed.", category="multi_statement")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Subquery) + SET_OPS):
        return GuardResult(False, reason=f"Only SELECT is allowed, not {tree.key.upper()}.", category="write")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            return GuardResult(False, reason=f"{node.key.upper()} is not allowed.", category="write")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    used = []
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func) or isinstance(table.this, exp.Anonymous):
            name = _function_name(table.this)
            if name not in SAFE_TABLE_FUNCTIONS:
                return GuardResult(False, reason=f"Function {name} is not allowed.", category="forbidden_function")
            continue
        name, schema = table.name.lower(), (table.db or "").lower()
        if not schema and name in ctes:
            continue
        if schema not in ("", "copilot") or name not in allowed:
            shown = f"{schema}.{name}" if schema else name
            return GuardResult(False, reason=f"{shown} is not one of the copilot views.", category="forbidden_object")
        used.append(name)

    for fn in tree.find_all(exp.Func):
        name = _function_name(fn)
        if name in DENY_FUNCTIONS or name.startswith(DENY_PREFIXES):
            return GuardResult(False, reason=f"Function {name} is not allowed.", category="forbidden_function")

    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(max_rows)
    return GuardResult(True, sql=tree.sql(dialect="postgres", comments=False), views=sorted(set(used)))
