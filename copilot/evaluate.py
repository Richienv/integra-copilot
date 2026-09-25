"""Evaluation: 50 questions (41 with a hand-checked answer query, 9 that must be refused).

Each run builds a fresh demo database seeded at a fixed anchor day (2026-10-15 unless --anchor says
otherwise), and in both the reference SQL and the model's SQL the clock functions (CURRENT_DATE, NOW(), ...)
are pinned to that day after the guard and before execution (copilot/dates.py). A run therefore means the
same thing on any day it is repeated. The reference answers are checked first: if any is empty or all NULL
at the anchor, the run stops and names them. Then every question is asked and the copilot's result table is
compared with the reference result.

  strict execution accuracy   same columns and same rows (numbers within 0.5%)
  relaxed execution accuracy  every reference column is present, extra columns allowed
  refusal accuracy            the 9 unsafe or off-topic questions are refused
  false refusals              answerable questions the copilot refused
  schema recall               the views the reference query needs were among the views retrieved

    python -m copilot eval               # with the model from the environment (see llm.from_env)
    python -m copilot eval --workers 4   # four questions at a time: each thread gets its own connection
    python -m copilot eval --oracle      # a stand-in that returns the reference SQL: tests the harness only
    python -m copilot eval --anchor 2026-09-24                    # another anchor day
    python -m copilot eval --replay eval/cassettes/RUN.jsonl      # answer every model call from a recording
                                                                  # (the recorded run's questions and anchor)
    python -m copilot eval --system baseline   # the naive zero-shot baseline (copilot/baseline.py) instead
    python -m copilot eval --company B --questions FILE           # company B, with its own question file

The oracle must score 100%: an oracle run below that exits with an error, because the harness is broken.

Every scored record keeps the row counts and a sha256 of the sorted result rows (gold and predicted), the
steps and the summary. Every model call is recorded in eval/cassettes/<run name>.jsonl (copilot/cassette.py),
and `python -m copilot rescore` recomputes a stored run's scores without a model (copilot/rescore.py).
"""
import datetime as dt
import hashlib
import itertools
import json
import statistics
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db, guard
from .dates import PinnedDB
from .llm import ScriptedModel, from_env

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval" / "questions.jsonl"
RESULTS = ROOT / "eval" / "results"
CASSETTES = ROOT / "eval" / "cassettes"
DEFAULT_ANCHOR = dt.date(2026, 10, 15)         # mid-month, so "this month" never covers a single day
SCORES = ("strict_ex", "relaxed_ex", "refusal_accuracy", "schema_recall")


def questions_file(path=None):
    """The question file: eval/questions.jsonl by default; a relative path is looked up from the current folder,
    then from the repository."""
    p = Path(path or QUESTIONS)
    return p if p.is_absolute() or p.exists() else ROOT / p


def _label(path):
    """A path as a run records it: relative to the repository when inside it."""
    p = Path(path).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)


def _question(q):
    """One question as the evaluation reads it. A red-team attack (eval/redteam/) names its language `language`
    and has no `expect`: it counts as a question to refuse."""
    q.setdefault("lang", q.get("language", ""))
    q.setdefault("expect", "refuse")
    return q


def load_questions(ids=None, limit=None, path=None):
    text = questions_file(path).read_text(encoding="utf-8")
    qs = [_question(json.loads(l)) for l in text.splitlines() if l.strip()]
    if ids:
        qs = [q for q in qs if q["id"] in set(ids)]
    return qs[:limit] if limit else qs


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eq(a, b):
    if _num(a) and _num(b):
        return abs(a - b) <= max(0.01, 0.005 * abs(b))
    if a is None or b is None:
        return a is b
    return str(a).strip() == str(b).strip()


def _rows_equal(gold, pred, ordered):
    if len(gold) != len(pred):
        return False
    if ordered:
        return all(len(g) == len(p) and all(_eq(x, y) for x, y in zip(g, p)) for g, p in zip(gold, pred))
    unused = list(range(len(pred)))
    for g in gold:
        hit = next((i for i in unused if len(pred[i]) == len(g) and all(_eq(x, y) for x, y in zip(g, pred[i]))), None)
        if hit is None:
            return False
        unused.remove(hit)
    return True


def compare(gold_rows, pred_cols, pred_rows, ordered):
    """Return (strict, relaxed). Relaxed looks for an assignment of reference columns to predicted columns."""
    if not gold_rows:
        return (not pred_rows, not pred_rows)
    if len(gold_rows) != len(pred_rows):
        return False, False
    ng, npred = len(gold_rows[0]), len(pred_cols)
    if npred < ng:
        return False, False
    # candidate predicted columns for each reference column: same values, ignoring row order
    cands = []
    for j in range(ng):
        gcol = [r[j] for r in gold_rows]
        cj = [i for i in range(npred) if _rows_equal([[v] for v in gcol], [[r[i]] for r in pred_rows], False)]
        if not cj:
            return False, False
        cands.append(cj)
    for n, combo in enumerate(itertools.product(*cands)):
        if n > 5000:
            break
        if len(set(combo)) < len(combo):
            continue
        projected = [[r[i] for i in combo] for r in pred_rows]
        if _rows_equal(gold_rows, projected, ordered):
            return npred == ng, True
    return False, False


def rows_hash(rows):
    """sha256 of the canonical JSON of the rows, sorted, so the same result has the same hash in any order."""
    canon = lambda r: json.dumps(r, ensure_ascii=False, separators=(",", ":"), default=str)
    text = json.dumps(sorted(rows, key=canon), ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class GoldError(ValueError):
    """A reference answer that cannot score anything."""


def gold_results(reader, questions):
    """Run every reference query once. Returns {id: (guard result, database result)}."""
    out = {}
    for q in questions:
        if q["expect"] == "sql":
            checked = guard.check(q["gold_sql"])
            out[q["id"]] = (checked, reader.run(checked.sql))
    return out


def gold_problems(gold):
    """The reference results that cannot score anything: an error, no rows, or only NULLs."""
    bad = []
    for qid, (checked, r) in gold.items():
        if not checked.ok or r.error:
            bad.append(f"{qid} (error: {checked.reason or r.error})")
        elif not r.rows:
            bad.append(f"{qid} (empty)")
        elif all(v is None for row in r.rows for v in row):
            bad.append(f"{qid} (all NULL)")
    return bad


def validate_gold(reader, questions, anchor=None):
    """Run the reference queries (dates pinned to the anchor, if given) and fail loudly, naming them, if any
    errors, is empty or is all NULL. Returns the reference results for scoring."""
    if anchor is not None:
        reader = PinnedDB(reader, anchor)
    gold = gold_results(reader, questions)
    bad = gold_problems(gold)
    if bad:
        at = f" at anchor {anchor}" if anchor is not None else ""
        raise GoldError(f"Reference queries that cannot be scored{at}: {', '.join(bad)}")
    return gold


def oracle_model(questions):
    """Returns the reference SQL (or a refusal) and a summary made of the first result row."""
    by_text = {q["question"]: q for q in questions}

    def fn(messages, json_mode):
        last = messages[-1]["content"]
        if json_mode:
            q = by_text.get(last.split("Question:")[-1].strip())
            if q and q["expect"] == "sql":
                return json.dumps({"kind": "sql", "sql": q["gold_sql"], "reason": "reference query"})
            return json.dumps({"kind": "refuse", "sql": "", "reason": "reference refusal"})
        rows = [l for l in last.split("\n") if l.startswith("[")]
        return ("First row: " + rows[0]) if rows else "No data."
    return ScriptedModel(fn, model="oracle")


def _views(system, question, ans):
    """The views the system looked at: the answer's own list, else its retriever's choice, else None (unknown)."""
    views = getattr(ans, "views", None)
    if views is None and hasattr(system, "retriever"):
        views = system.retriever.retrieve(question, getattr(system, "k", 4))
    return list(views) if views is not None else None


def score(system, reader, q, gold=None):
    """Ask one question and compare the answer with the reference. A model failure is a miss, not a crash.

    system is anything with ask(question) that returns an Answer-like object (kind, sql, columns, rows, ...).
    gold is the (guard result, database result) of the reference query; without it the query runs here."""
    t0 = time.perf_counter()
    try:
        ans, error = system.ask(q["question"]), None
    except Exception as e:
        ans, error = None, str(e)[:300]
    usage = getattr(ans, "usage", None) or {}
    kind = getattr(ans, "kind", "error") if ans is not None else "error"
    rows = (getattr(ans, "rows", None) or []) if kind == "data" else []
    ms = getattr(ans, "ms", None) if ans is not None else 0.0
    rec = {"id": q["id"], "lang": q["lang"], "expect": q["expect"], "kind": kind, "sql": getattr(ans, "sql", "") or "",
           "repairs": getattr(ans, "repairs", 0), "grounded": getattr(ans, "grounded", True),
           "ms": ms if ms is not None else round((time.perf_counter() - t0) * 1000, 1),
           "tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0), "cost": usage.get("cost", 0.0),
           "views": _views(system, q["question"], ans), "summary": getattr(ans, "summary", ""),
           "steps": getattr(ans, "steps", []), "pred_rows": len(rows), "pred_hash": rows_hash(rows) if kind == "data" else None}
    if error is not None:
        rec["error"] = error
    if q["expect"] == "sql":
        checked, g = gold or gold_results(reader, [q])[q["id"]]
        strict, relaxed = compare(g.rows, getattr(ans, "columns", []), rows, q["ordered"]) if kind == "data" else (False, False)
        rec.update(strict=strict, relaxed=relaxed,
                   schema_recall=None if rec["views"] is None else set(checked.views) <= set(rec["views"]),
                   gold_views=checked.views, gold_rows=len(g.rows), gold_hash=rows_hash(g.rows))
    else:
        rec.update(refused=kind == "refused")
    mark = "ok " if rec.get("relaxed") or rec.get("refused") else "MISS"
    print(f"{mark:4s} {q['id']:7s} {q['lang']}  {rec['kind']:8s} {rec['ms']:7.0f} ms  {q['question'][:60]}", flush=True)
    return rec


def evaluate(system, reader, questions, workers=1, factory=None, gold=None):
    """Score every question, in order. With workers > 1, factory() gives each thread its own system and reader.
    The reference answers are checked first (validate_gold): an empty or all-NULL one stops the run before any
    question is asked. Pass gold to reuse reference results already checked."""
    if gold is None:
        gold = validate_gold(reader if reader is not None else factory()[1], questions)
    if workers <= 1 or factory is None:
        return [score(system, reader, q, gold.get(q["id"])) for q in questions]
    local = threading.local()

    def run(q):
        if not hasattr(local, "pair"):
            local.pair = factory()
        return score(*local.pair, q, gold.get(q["id"]))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, questions))


def summarise(results, model, anchor, note=""):
    sql = [r for r in results if r["expect"] == "sql"]
    ref = [r for r in results if r["expect"] == "refuse"]
    pct = lambda n, d: round(100 * n / d, 1) if d else 0.0
    ms = sorted(r["ms"] for r in results)
    recall = [r["schema_recall"] for r in sql if r.get("schema_recall") is not None]   # None: views unknown
    m = {
        "model": model, "date": str(anchor), "anchor": str(anchor),
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "questions": len(results),
        "strict_ex": pct(sum(r["strict"] for r in sql), len(sql)),
        "relaxed_ex": pct(sum(r["relaxed"] for r in sql), len(sql)),
        "refusal_accuracy": pct(sum(r["refused"] for r in ref), len(ref)),
        "false_refusals": sum(r["kind"] == "refused" for r in sql),
        "errors": sum(r["kind"] == "error" for r in results),
        "schema_recall": pct(sum(recall), len(recall)) if recall else None,
        "repaired_answers": sum(r["repairs"] > 0 and r.get("relaxed", False) for r in sql),
        "summaries_replaced": sum(not r["grounded"] for r in sql),
        "latency_p50_ms": round(statistics.median(ms)) if ms else 0,
        "latency_p95_ms": round(ms[int(0.95 * (len(ms) - 1))]) if ms else 0,
        "tokens_per_question": round(sum(r["tokens"] for r in results) / len(results)) if results else 0,
        "total_cost": round(sum(r["cost"] for r in results), 4),
        "note": note,
    }
    by_lang = defaultdict(list)
    for r in sql:
        by_lang[r["lang"]].append(r["relaxed"])
    m["relaxed_ex_by_language"] = {k: pct(sum(v), len(v)) for k, v in sorted(by_lang.items())}
    return m


def render_report(metrics, results):
    """The Markdown report for one run."""
    lines = [f"# Evaluation · {metrics['model']} · {metrics['date']}", "",
             f"Demo data seeded at, and today's date pinned to, {metrics.get('anchor', metrics['date'])}.", ""]
    if metrics.get("system"):
        lines += [f"System: {metrics['system']} · company {metrics.get('company', 'A')} · "
                  f"questions `{metrics.get('question_file', 'eval/questions.jsonl')}`", ""]
    lines += ["| Metric | Result |", "|---|---|"]
    labels = [("strict_ex", "Strict execution accuracy (%)"), ("relaxed_ex", "Relaxed execution accuracy (%)"),
              ("refusal_accuracy", "Refusal accuracy (%)"), ("false_refusals", "False refusals"),
              ("errors", "Model errors (counted as misses)"),
              ("schema_recall", "Schema recall (%)"), ("repaired_answers", "Correct after a repair"),
              ("summaries_replaced", "Summaries replaced by the number check"),
              ("latency_p50_ms", "Latency p50 (ms)"), ("latency_p95_ms", "Latency p95 (ms)"),
              ("tokens_per_question", "Tokens per question"), ("total_cost", "Total cost")]
    lines += [f"| {label} | {'n/a' if metrics[key] is None else metrics[key]} |" for key, label in labels]
    if metrics.get("note"):
        lines += ["", metrics["note"]]
    if metrics.get("codex_version") or metrics.get("codex_isolation"):
        lines += ["", f"Codex {metrics.get('codex_version')}, home `{metrics.get('codex_home')}`, "
                      f"isolation: {metrics.get('codex_isolation')}."]
    if metrics.get("cassette"):
        lines += ["", f"Model calls recorded in `{metrics['cassette']}`."]
    lines += ["", "Relaxed accuracy by language: " + ", ".join(f"{k} {v}%" for k, v in metrics["relaxed_ex_by_language"].items()),
              "", "| Id | Lang | Expected | Got | Strict | Relaxed | Repairs | ms |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        ok = lambda k: "yes" if r.get(k) else ("—" if k not in r else "no")
        lines.append(f"| {r['id']} | {r['lang']} | {r['expect']} | {r['kind']} | {ok('strict')} | "
                     f"{ok('relaxed') if r['expect'] == 'sql' else ok('refused')} | {r['repairs']} | {r['ms']:.0f} |")
    return "\n".join(lines) + "\n"


def run_name(model, suffix="", out=None):
    """<date>-<time>-<model>, unique among the reports and cassettes already written."""
    out = Path(out or RESULTS)
    base = f"{dt.datetime.now():%Y%m%d-%H%M}-{str(model).replace('/', '_')}{suffix}"
    name, n = base, 1
    while (out / f"{name}.json").exists() or (CASSETTES / f"{name}.jsonl").exists():
        n += 1
        name = f"{base}-{n}"
    return name


def write_report(metrics, results, name=None, out=None):
    """Every run keeps its own JSON and Markdown report; latest.md is a copy of the newest one.
    out is the folder, eval/results by default."""
    out = Path(out or RESULTS)
    out.mkdir(parents=True, exist_ok=True)
    name = name or run_name(metrics["model"], out=out)
    (out / f"{name}.json").write_text(json.dumps({"metrics": metrics, "results": results}, ensure_ascii=False, indent=2,
                                                 default=str), encoding="utf-8")
    report = render_report(metrics, results)
    (out / f"{name}.md").write_text(report, encoding="utf-8")
    (out / "latest.md").write_text(report, encoding="utf-8")
    return out / f"{name}.json"


def demo_database(anchor, company="A", baseline=False):
    """A fresh private PostgreSQL with one demo company seeded at the anchor day. Returns the reader's URI.
    With baseline, the naive baseline's own role is created too (sql/03_baseline_reader.sql)."""
    admin = db.demo_database(db.local_server(tempfile.mkdtemp(prefix="copilot-eval-"), cleanup_mode="delete"), company)
    db.create_demo(admin, dt.date.fromisoformat(str(anchor)), company)
    if baseline:
        from .baseline_db import create_role
        create_role(admin)
    return db.reader_uri(admin)


def run(llm, questions, uri, anchor, workers=1, record=True, suffix="", extra=None, system="copilot", out=None):
    """One evaluation run on the demo database at uri, which must be seeded at the anchor day.

    system is "copilot" (the agent) or "baseline" (copilot/baseline.py; its role must exist on that database).
    Every query runs with its dates pinned to the anchor. The reference answers are checked before any model
    call. With record=True every model call is appended to eval/cassettes/<run name>.jsonl. The reports go to
    out (eval/results by default). Returns (metrics, results, path of the JSON report)."""
    from .baseline import make_system
    anchor = dt.date.fromisoformat(str(anchor))
    name = run_name(llm.model, suffix, out)
    reader = PinnedDB(db.ReadOnlyDB(uri), anchor)
    gold = validate_gold(reader, questions)
    if record:
        from .cassette import Recorder
        llm = Recorder(llm, CASSETTES / f"{name}.jsonl", anchor)

    def factory():
        return make_system(system, uri, llm, anchor), PinnedDB(db.ReadOnlyDB(uri), anchor)
    results = evaluate(make_system(system, uri, llm, anchor), reader, questions, workers, factory, gold)
    metrics = summarise(results, llm.model, anchor, getattr(llm, "note", ""))
    metrics["system"] = system
    metrics.update(extra or {})
    if record:
        metrics["cassette"] = f"eval/cassettes/{name}.jsonl"
    path = write_report(metrics, results, name, out)
    return metrics, results, path


def compare_scores(a, b):
    """The scores (SCORES) on which two metrics dicts differ: {key: (a, b)}."""
    return {k: (a.get(k), b.get(k)) for k in SCORES if a.get(k) != b.get(k)}


def oracle_shortfall(metrics, questions):
    """The scores on which an oracle run is below 100% ({} when it is perfect), and its model errors."""
    kinds = {q["expect"] for q in questions}
    keys = (("strict_ex", "relaxed_ex") if "sql" in kinds else ()) + (("refusal_accuracy",) if "refuse" in kinds else ())
    short = {k: metrics[k] for k in keys if metrics[k] != 100.0}
    if metrics["errors"]:
        short["errors"] = metrics["errors"]
    return short


def main(oracle=False, limit=None, ids=None, workers=1, anchor=None, replay=None, allow_personal_codex=False,
         company=None, system=None, questions=None, out=None):
    """python -m copilot eval. company, system and questions default to the recorded run's when replaying,
    else to company A, the copilot and eval/questions.jsonl."""
    from .doctor import codex_gate
    recorded = RESULTS / f"{Path(replay).stem}.json" if replay else None
    recorded = json.loads(recorded.read_text(encoding="utf-8")) if recorded and recorded.exists() else None
    was = recorded["metrics"] if recorded else {}
    company = (company or was.get("company") or "A").upper()
    system = system or was.get("system") or "copilot"
    qfile = questions_file(questions or was.get("question_file"))
    if company != "A" and qfile.resolve() == QUESTIONS.resolve():
        raise SystemExit(f"eval/questions.jsonl is company A's set; give company {company}'s own with --questions.")
    if recorded and not (ids or limit):                      # a replay asks the questions the recorded run asked
        ids = [r["id"] for r in recorded["results"]]
    qs = load_questions(ids, limit, qfile)
    extra = {"company": company, "question_file": _label(qfile),
             "question_file_sha256": hashlib.sha256(qfile.read_bytes()).hexdigest()}
    suffix = ("-baseline" if system == "baseline" else "") + (f"-company-{company.lower()}" if company != "A" else "")
    if replay:
        from .cassette import Replay
        llm = Replay(replay)
        anchor = anchor or llm.anchor
        extra["replay_of"], suffix = Path(replay).name, suffix + "-replay"
    else:
        llm = oracle_model(qs) if oracle else from_env()
    if llm is None:
        raise SystemExit("No model: set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL, or LLM_BACKEND=codex with the "
                         "ChatGPT app signed in, or run with --oracle to test the harness.")
    anchor = dt.date.fromisoformat(str(anchor or DEFAULT_ANCHOR))
    extra.update(codex_gate(llm, allow_personal_codex))
    try:
        uri = demo_database(anchor, company=company, baseline=system == "baseline")
        metrics, _, path = run(llm, qs, uri, anchor, workers, record=not (oracle or replay), suffix=suffix,
                               extra=extra, system=system, out=out)
    except GoldError as e:
        raise SystemExit(str(e))
    print("\n" + json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nWritten: {path} and {path.parent / 'latest.md'}")
    if recorded:
        diff = compare_scores(recorded["metrics"], metrics)
        print(f"Replay against {Path(replay).stem}.json: " + ("same scores" if not diff else f"DIFFERENT {diff}"))
    if oracle:
        short = oracle_shortfall(metrics, qs)
        if short:
            raise SystemExit(f"The oracle must score 100%, and did not: {short}. The harness is broken.")
        print("Oracle: 100% on every score.")
    return metrics
