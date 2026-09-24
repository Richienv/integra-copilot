"""Evaluation: 50 questions (41 with a hand-checked answer query, 9 that must be refused).

Each run builds a fresh demo database dated today, so date-relative questions stay meaningful, then asks
every question and compares the copilot's result table with the result of the reference query.

  strict execution accuracy   same columns and same rows (numbers within 0.5%)
  relaxed execution accuracy  every reference column is present, extra columns allowed
  refusal accuracy            the 9 unsafe or off-topic questions are refused
  false refusals              answerable questions the copilot refused
  schema recall               the views the reference query needs were among the views retrieved

    python -m copilot eval               # with the model from the environment (see llm.from_env)
    python -m copilot eval --workers 4   # four questions at a time: each thread gets its own connection
    python -m copilot eval --oracle      # a stand-in that returns the reference SQL: tests the harness only
"""
import datetime as dt
import itertools
import json
import statistics
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db, guard
from .agent import Copilot
from .llm import ScriptedModel, from_env

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval" / "questions.jsonl"
RESULTS = ROOT / "eval" / "results"


def load_questions(ids=None, limit=None):
    qs = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
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


def score(copilot, reader, q):
    """Ask one question and compare the answer with the reference. A model failure is a miss, not a crash."""
    try:
        ans = copilot.ask(q["question"])
    except Exception as e:
        ans = None
        rec = {"id": q["id"], "lang": q["lang"], "expect": q["expect"], "kind": "error", "sql": "", "repairs": 0,
               "grounded": True, "ms": 0.0, "tokens": 0, "cost": 0.0,
               "views": copilot.retriever.retrieve(q["question"], copilot.k), "error": str(e)[:300]}
    else:
        rec = {"id": q["id"], "lang": q["lang"], "expect": q["expect"], "kind": ans.kind, "sql": ans.sql,
               "repairs": ans.repairs, "grounded": ans.grounded, "ms": ans.ms,
               "tokens": ans.usage["prompt_tokens"] + ans.usage["completion_tokens"], "cost": ans.usage["cost"],
               "views": ans.views}
    if q["expect"] == "sql":
        gold = guard.check(q["gold_sql"])
        g = reader.run(gold.sql)
        ok = ans is not None and ans.kind == "data"
        strict, relaxed = compare(g.rows, ans.columns, ans.rows, q["ordered"]) if ok else (False, False)
        rec.update(strict=strict, relaxed=relaxed, schema_recall=set(gold.views) <= set(rec["views"]),
                   gold_views=gold.views)
    else:
        rec.update(refused=rec["kind"] == "refused")
    mark = "ok " if rec.get("relaxed") or rec.get("refused") else "MISS"
    print(f"{mark:4s} {q['id']:7s} {q['lang']}  {rec['kind']:8s} {rec['ms']:7.0f} ms  {q['question'][:60]}", flush=True)
    return rec


def evaluate(copilot, reader, questions, workers=1, factory=None):
    """Score every question, in order. With workers > 1, factory() gives each thread its own copilot and reader."""
    if workers <= 1 or factory is None:
        return [score(copilot, reader, q) for q in questions]
    local = threading.local()

    def run(q):
        if not hasattr(local, "pair"):
            local.pair = factory()
        return score(*local.pair, q)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, questions))


def summarise(results, model, anchor, note=""):
    sql = [r for r in results if r["expect"] == "sql"]
    ref = [r for r in results if r["expect"] == "refuse"]
    pct = lambda n, d: round(100 * n / d, 1) if d else 0.0
    ms = sorted(r["ms"] for r in results)
    m = {
        "model": model, "date": str(anchor), "questions": len(results),
        "strict_ex": pct(sum(r["strict"] for r in sql), len(sql)),
        "relaxed_ex": pct(sum(r["relaxed"] for r in sql), len(sql)),
        "refusal_accuracy": pct(sum(r["refused"] for r in ref), len(ref)),
        "false_refusals": sum(r["kind"] == "refused" for r in sql),
        "errors": sum(r["kind"] == "error" for r in results),
        "schema_recall": pct(sum(r["schema_recall"] for r in sql), len(sql)),
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
             "| Metric | Result |", "|---|---|"]
    labels = [("strict_ex", "Strict execution accuracy (%)"), ("relaxed_ex", "Relaxed execution accuracy (%)"),
              ("refusal_accuracy", "Refusal accuracy (%)"), ("false_refusals", "False refusals"),
              ("errors", "Model errors (counted as misses)"),
              ("schema_recall", "Schema recall (%)"), ("repaired_answers", "Correct after a repair"),
              ("summaries_replaced", "Summaries replaced by the number check"),
              ("latency_p50_ms", "Latency p50 (ms)"), ("latency_p95_ms", "Latency p95 (ms)"),
              ("tokens_per_question", "Tokens per question"), ("total_cost", "Total cost")]
    lines += [f"| {label} | {metrics[key]} |" for key, label in labels]
    if metrics.get("note"):
        lines += ["", metrics["note"]]
    lines += ["", "Relaxed accuracy by language: " + ", ".join(f"{k} {v}%" for k, v in metrics["relaxed_ex_by_language"].items()),
              "", "| Id | Lang | Expected | Got | Strict | Relaxed | Repairs | ms |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        ok = lambda k: "yes" if r.get(k) else ("—" if k not in r else "no")
        lines.append(f"| {r['id']} | {r['lang']} | {r['expect']} | {r['kind']} | {ok('strict')} | "
                     f"{ok('relaxed') if r['expect'] == 'sql' else ok('refused')} | {r['repairs']} | {r['ms']:.0f} |")
    return "\n".join(lines) + "\n"


def write_report(metrics, results):
    """Every run keeps its own JSON and Markdown report; latest.md is a copy of the newest one."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    name = f"{stamp}-{str(metrics['model']).replace('/', '_')}"
    (RESULTS / f"{name}.json").write_text(json.dumps({"metrics": metrics, "results": results}, ensure_ascii=False, indent=2,
                                                     default=str), encoding="utf-8")
    report = render_report(metrics, results)
    (RESULTS / f"{name}.md").write_text(report, encoding="utf-8")
    (RESULTS / "latest.md").write_text(report, encoding="utf-8")
    return RESULTS / f"{name}.json"


def main(oracle=False, limit=None, ids=None, workers=1):
    questions = load_questions(ids, limit)
    llm = oracle_model(questions) if oracle else from_env()
    if llm is None:
        raise SystemExit("No model: set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL, or LLM_BACKEND=codex with the "
                         "ChatGPT app signed in, or run with --oracle to test the harness.")
    anchor = dt.date.today()
    admin = db.local_server(tempfile.mkdtemp(prefix="copilot-eval-"), cleanup_mode="delete")
    db.create_demo(admin, anchor)
    uri = db.reader_uri(admin)
    reader = db.ReadOnlyDB(uri)

    def factory():
        r = db.ReadOnlyDB(uri)
        return Copilot(r, llm), r
    results = evaluate(Copilot(reader, llm), reader, questions, workers, factory)
    metrics = summarise(results, llm.model, anchor, getattr(llm, "note", ""))
    path = write_report(metrics, results)
    print("\n" + json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nWritten: {path} and {RESULTS / 'latest.md'}")
    return metrics
