"""Rescore a stored run without a model.

    python -m copilot rescore eval/results/20260924-1509-codex_gpt-6-sol.json
    python -m copilot rescore --all            # every model run in eval/results (all but the oracle's)

The model's stored SQL and the reference SQL run again on a fresh demo database seeded at the run's anchor
day (metrics.anchor, or metrics.date for runs made before the anchor was recorded), with dates pinned to that
day, for the run's company, question file and system (runs made before these were recorded: company A,
eval/questions.jsonl, the copilot). A copilot run's SQL goes through the guard and runs as copilot_reader; a
baseline run's SQL runs as written, as baseline_reader, as it did in the run. Strict, relaxed, refusal and
schema-recall scores are recomputed and printed next to the stored ones. No model is called: the answers are
the SQL the run stored. For runs that stored row hashes, the rows found now are checked against them too.
"""
import datetime as dt
import json
import re
from pathlib import Path

from . import db, guard
from .baseline_db import baseline_uri
from .dates import PinnedDB
from .evaluate import (RESULTS, SCORES, compare, compare_scores, demo_database, gold_problems, gold_results,
                       load_questions, rows_hash, summarise)

CHECKED = ("strict", "relaxed", "refused", "schema_recall", "gold_hash", "pred_hash")


def run_anchor(metrics):
    """The day a run's data was seeded at and its dates pinned to."""
    return dt.date.fromisoformat(str(metrics.get("anchor") or metrics["date"]))


def stored_runs():
    """Every model run in eval/results: each run JSON except the oracle's, which checks the harness, not a model."""
    return sorted(p for p in RESULTS.glob("*.json") if not re.match(r"\d{8}-\d{4}-oracle", p.name))


def rescore_results(results, reader, questions, gold=None, raw=None):
    """Recompute every record from its stored SQL and stored views. reader must be pinned to the run's anchor.
    raw, for a baseline run, is where its stored SQL runs unguarded (baseline_reader, pinned)."""
    by_id = {q["id"]: q for q in questions}
    missing = [r["id"] for r in results if r["id"] not in by_id]
    if missing:
        raise ValueError(f"Not in the question set: {', '.join(missing)}")
    gold = gold or gold_results(reader, [by_id[r["id"]] for r in results])
    out = []
    for r in results:
        q, new = by_id[r["id"]], dict(r)
        if q["expect"] != "sql":
            new["refused"] = r["kind"] == "refused"
            out.append(new)
            continue
        checked, g = gold[q["id"]]
        columns, rows, error = [], [], ""
        if r["kind"] == "data":
            if raw is not None:                                  # a baseline run: its SQL as written, unguarded
                res = raw.run(r["sql"])
                error = res.error
            else:
                pred = guard.check(r["sql"])
                res = reader.run(pred.sql) if pred.ok else None
                error = res.error if res else pred.reason
            if not error:
                columns, rows = res.columns, res.rows
        answered = r["kind"] == "data" and not error
        strict, relaxed = compare(g.rows, columns, rows, q["ordered"]) if answered else (False, False)
        views = r.get("views")
        new.update(strict=strict, relaxed=relaxed,
                   schema_recall=None if views is None else set(checked.views) <= set(views),
                   gold_views=checked.views, gold_rows=len(g.rows), gold_hash=rows_hash(g.rows),
                   pred_rows=len(rows), pred_hash=rows_hash(rows) if answered else None)
        if error:
            new["rescore_error"] = error
        out.append(new)
    return out


def changes(stored, recomputed):
    """Per question, what differs between the stored and the recomputed record. Hashes count only where stored."""
    out = []
    for a, b in zip(stored, recomputed):
        diff = {k: (a.get(k), b.get(k)) for k in CHECKED
                if k in a and a.get(k) != b.get(k)}
        if diff or b.get("rescore_error"):
            out.append({"id": a["id"], "diff": diff, "error": b.get("rescore_error", "")})
    return out


def run_setup(metrics):
    """(company, system, question file) of a stored run; runs made before these were recorded: A, copilot, dev set."""
    return metrics.get("company", "A"), metrics.get("system", "copilot"), metrics.get("question_file")


def rescore(path, reader=None, questions=None):
    """Rescore one stored run. reader: the run's company seeded at its anchor, with the baseline role when the
    run is a baseline run (made here if not given)."""
    run = json.loads(Path(path).read_text(encoding="utf-8"))
    stored, results = run["metrics"], run["results"]
    anchor = run_anchor(stored)
    company, system, qfile = run_setup(stored)
    reader = reader or db.ReadOnlyDB(demo_database(anchor, company=company, baseline=system == "baseline"))
    raw = PinnedDB(db.ReadOnlyDB(baseline_uri(reader.uri)), anchor) if system == "baseline" else None
    reader = PinnedDB(reader, anchor)
    questions = questions or load_questions(path=qfile)
    by_id = {q["id"]: q for q in questions}
    gold = gold_results(reader, [by_id[r["id"]] for r in results if r["id"] in by_id])
    new = rescore_results(results, reader, questions, gold, raw)
    recomputed = summarise(new, stored["model"], anchor, stored.get("note", ""))
    return {"path": str(path), "anchor": str(anchor), "stored": stored, "recomputed": recomputed, "results": new,
            "changes": changes(results, new), "gold_problems": gold_problems(gold)}


def report(r):
    """Print stored against recomputed scores. Returns True when they are the same."""
    diff = compare_scores(r["stored"], r["recomputed"])
    print(f"\n{Path(r['path']).name} · anchor {r['anchor']}")
    print(f"  {'score':18s} {'stored':>8s} {'recomputed':>11s}")
    for k in SCORES:
        a, b = r["stored"].get(k), r["recomputed"].get(k)
        print(f"  {k:18s} {str(a):>8s} {str(b):>11s}  {'DIFFERENT' if k in diff else 'same'}")
    for c in r["changes"]:
        parts = [f"{k} {a} -> {b}" for k, (a, b) in c["diff"].items()]
        if c["error"]:
            parts.append(f"error now: {c['error']}")
        print(f"  {c['id']}: " + "; ".join(parts))
    if not r["changes"]:
        print("  per question: no changes")
    if r["gold_problems"]:
        print("  reference results that cannot score anything at this anchor: " + ", ".join(r["gold_problems"]))
    return not diff and not r["changes"]


def main(paths=(), all_runs=False):
    paths = [Path(p) for p in paths] + (stored_runs() if all_runs else [])
    if not paths:
        raise SystemExit("Give a run's JSON report, or --all for every model run in eval/results.")
    questions, readers, same = {}, {}, True
    for path in paths:
        metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]
        anchor, (company, _, qfile) = run_anchor(metrics), run_setup(metrics)
        if (anchor, company) not in readers:                 # one database per anchor and company, for every system
            print(f"Seeding company {company} at {anchor} ...", flush=True)
            readers[anchor, company] = db.ReadOnlyDB(demo_database(anchor, company=company, baseline=True))
        if qfile not in questions:
            questions[qfile] = load_questions(path=qfile)
        same = report(rescore(path, readers[anchor, company], questions[qfile])) and same
    print("\nAll stored scores reproduced." if same else "\nSome stored scores were NOT reproduced (see above).")
    return same
