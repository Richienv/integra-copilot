"""Command line.

    python -m copilot init-db            create the local demo database (~/.cache/integra-copilot/pg)
    python -m copilot init-db --company B   the second demo company, in its own database;
                                            other commands use it with COPILOT_COMPANY=B
    python -m copilot ask "Berapa piutang yang jatuh tempo minggu ini?"
    python -m copilot sql "select name, balance from gl_accounts"
    python -m copilot serve              web page + API on http://127.0.0.1:8000
    python -m copilot mcp                MCP server over stdio
    python -m copilot eval               run the evaluation set (needs a model; --workers 4 runs four at once)
    python -m copilot eval --oracle      check the harness itself, no model needed
    python -m copilot eval --anchor 2026-09-24          seed the data and pin today's date to another day
    python -m copilot eval --replay eval/cassettes/RUN.jsonl   rerun from recorded model calls, no model needed
    python -m copilot rescore eval/results/RUN.json     recompute a stored run's scores, no model (--all: every codex run)
    python -m copilot check-gold --anchor 2026-09-24    are all reference answers non-empty at that day?
    python -m copilot doctor             the Codex setup: binary, version, CODEX_HOME, personal instructions
"""
import argparse
import datetime as dt
import json
import sys


def _table(columns, rows, limit=20):
    if not columns:
        return ""
    cells = [[str(c) for c in columns]] + [[f"{v:,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
                                            for v in r] for r in rows[:limit]]
    widths = [max(len(r[i]) for r in cells) for i in range(len(columns))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)) for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines) + (f"\n... {len(rows) - limit} more rows" if len(rows) > limit else "")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m copilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init-db"); i.add_argument("--company", type=str.upper, choices=["A", "B"])
    a = sub.add_parser("ask"); a.add_argument("question"); a.add_argument("--json", action="store_true")
    s = sub.add_parser("sql"); s.add_argument("sql")
    v = sub.add_parser("serve"); v.add_argument("--port", type=int, default=8000); v.add_argument("--host", default="127.0.0.1")
    sub.add_parser("mcp")
    e = sub.add_parser("eval"); e.add_argument("--oracle", action="store_true"); e.add_argument("--limit", type=int)
    e.add_argument("--ids", nargs="*"); e.add_argument("--workers", type=int, default=1)
    e.add_argument("--anchor", type=dt.date.fromisoformat, help="YYYY-MM-DD; default 2026-10-15")
    e.add_argument("--replay", help="a cassette: answer every model call from it")
    e.add_argument("--allow-personal-codex", action="store_true",
                   help="run with a Codex home that carries personal instructions (AGENTS.md)")
    r = sub.add_parser("rescore"); r.add_argument("runs", nargs="*"); r.add_argument("--all", action="store_true")
    g = sub.add_parser("check-gold"); g.add_argument("--anchor", type=dt.date.fromisoformat)
    sub.add_parser("doctor")
    args = p.parse_args(argv)

    if args.cmd == "init-db":
        from psycopg.conninfo import conninfo_to_dict
        from .service import reader_from_env
        r = reader_from_env(company=args.company)
        print(f"demo database ready: {conninfo_to_dict(r.uri).get('dbname')}; reader role: {r.user()}")
    elif args.cmd == "ask":
        from .service import build
        _, copilot, _ = build()
        if not copilot:
            sys.exit("No model: set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL, or LLM_BACKEND=codex (see README).")
        ans = copilot.ask(args.question)
        if args.json:
            print(json.dumps(ans.to_dict(), ensure_ascii=False, indent=2, default=str))
            return
        print(ans.summary, "\n")
        print(_table(ans.columns, ans.rows))
        if ans.sql:
            print("\nSQL:", ans.sql)
        print("\n" + "\n".join(f"  · {st['name']}: {st['detail']}" for st in ans.steps))
        print(f"\n{ans.usage['calls']} model calls · {ans.usage['prompt_tokens'] + ans.usage['completion_tokens']} tokens · {ans.ms:.0f} ms")
    elif args.cmd == "sql":
        from . import guard
        from .service import reader_from_env
        checked = guard.check(args.sql)
        if not checked.ok:
            sys.exit(f"Refused ({checked.category}): {checked.reason}")
        r = reader_from_env().run(checked.sql)
        print(r.error or _table(r.columns, r.rows))
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("copilot.api:app", host=args.host, port=args.port)
    elif args.cmd == "mcp":
        from .mcp_server import mcp
        mcp.run()
    elif args.cmd == "eval":
        from .evaluate import main as run_eval
        run_eval(oracle=args.oracle, limit=args.limit, ids=args.ids, workers=args.workers, anchor=args.anchor,
                 replay=args.replay, allow_personal_codex=args.allow_personal_codex)
    elif args.cmd == "rescore":
        from .rescore import main as run_rescore
        sys.exit(0 if run_rescore(args.runs, all_runs=args.all) else 1)
    elif args.cmd == "check-gold":
        from . import db
        from .evaluate import DEFAULT_ANCHOR, GoldError, demo_database, load_questions, validate_gold
        anchor = args.anchor or DEFAULT_ANCHOR
        qs = load_questions()
        try:
            gold = validate_gold(db.ReadOnlyDB(demo_database(anchor)), qs, anchor)
        except GoldError as err:
            sys.exit(str(err))
        print(f"All {len(gold)} reference answers have rows with values at anchor {anchor}.")
    elif args.cmd == "doctor":
        from .doctor import main as run_doctor
        run_doctor()


if __name__ == "__main__":
    main()
