"""Rescore a stored run without a model.

    python -m copilot rescore eval/results/20260924-1509-codex_gpt-6-sol.json
    python -m copilot rescore --all            # every model run in eval/results (all but the oracle's)

The model's stored SQL and the reference SQL run again on a fresh demo database seeded at the run's anchor
day (metrics.anchor, or metrics.date for runs made before the anchor was recorded), with dates pinned to that
day, for the run's company, question file, system and data (poisoned or not; runs made before these were
recorded: company A, eval/questions.jsonl, the copilot, clean data). A copilot run's SQL goes through the guard
and runs as copilot_reader; a baseline run's SQL runs as written, as baseline_reader, as it did in the run.
Strict, relaxed, refusal and schema-recall scores are recomputed and printed next to the stored ones. No model
is called: the answers are the SQL the run stored.

A rescore passes only if every score and every per-question result is reproduced, every reference query still
returns something that can score (not empty, all NULL or all zero), and the question file is the one the run
used (when its sha256 was recorded). The rows are checked too: against the row hashes a run stored, or, for
runs 1 to 3, which stored none, against the reference row hashes committed in eval/reference_hashes.json. A run
with neither is rescored on data nothing checks, and the report says so.
"""
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from . import db, guard
from .baseline_db import baseline_uri
from .dates import PinnedDB
from .evaluate import (RESULTS, ROOT, SCORES, _label, compare, compare_scores, demo_database, gold_problems,
                       gold_results, load_questions, questions_file, rows_hash, summarise)

CHECKED = ("strict", "relaxed", "refused", "schema_recall", "gold_hash", "pred_hash")
REFERENCE_HASHES = ROOT / "eval" / "reference_hashes.json"


def run_anchor(metrics):
    """The day a run's data was seeded at and its dates pinned to."""
    return dt.date.fromisoformat(str(metrics.get("anchor") or metrics["date"]))


def stored_runs():
    """Every model run in eval/results: each run JSON except the oracle's, which checks the harness, not a model."""
    return sorted(p for p in RESULTS.glob("*.json") if not re.match(r"\d{8}-\d{4}-oracle", p.name))


def gold_hashes(gold):
    """{id: sha256 of the reference result rows} for the reference results of gold_results."""
    return {qid: rows_hash(r.rows) for qid, (_, r) in gold.items()}


def reference_hashes(company, anchor, qfile):
    """The committed reference row hashes (eval/reference_hashes.json) for a company, anchor and question file,
    or {} if none were committed."""
    if not REFERENCE_HASHES.exists():
        return {}
    committed = json.loads(REFERENCE_HASHES.read_text(encoding="utf-8"))
    return committed.get(f"{company} {anchor} {_label(questions_file(qfile))}", {})


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
    """(company, system, question file, poison) of a stored run; runs made before these were recorded:
    A, copilot, the dev set, clean data."""
    return (metrics.get("company", "A"), metrics.get("system", "copilot"), metrics.get("question_file"),
            bool(metrics.get("poison")))


def check_question_file(metrics):
    """Stop if the run's question file is not the one it recorded (by sha256). Runs 1 to 3 recorded none."""
    sha, path = metrics.get("question_file_sha256"), questions_file(metrics.get("question_file"))
    if sha and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError(f"{_label(path)} has changed since the run: its sha256 is not the one the run recorded.")


def rescore(path, reader=None, questions=None):
    """Rescore one stored run. reader: the run's company seeded at its anchor, with the baseline role when the
    run is a baseline run (made here if not given)."""
    run = json.loads(Path(path).read_text(encoding="utf-8"))
    stored, results = run["metrics"], run["results"]
    anchor = run_anchor(stored)
    company, system, qfile, poison = run_setup(stored)
    check_question_file(stored)
    reader = reader or db.ReadOnlyDB(demo_database(anchor, company=company, baseline=system == "baseline",
                                                   poison=poison))
    raw = PinnedDB(db.ReadOnlyDB(baseline_uri(reader.uri)), anchor) if system == "baseline" else None
    reader = PinnedDB(reader, anchor)
    questions = questions or load_questions(path=qfile)
    by_id = {q["id"]: q for q in questions}
    gold = gold_results(reader, [by_id[r["id"]] for r in results if r["id"] in by_id])
    new = rescore_results(results, reader, questions, gold, raw)
    recomputed = summarise(new, stored["model"], anchor, stored.get("note", ""))
    expected, data_check = results, "the row hashes the run stored"
    if not any("gold_hash" in r for r in results):              # runs 1 to 3: the committed reference hashes
        committed = reference_hashes(company, anchor, qfile)
        expected = [dict(r, gold_hash=committed[r["id"]]) if r["id"] in committed else r for r in results]
        data_check = f"the reference row hashes in {_label(REFERENCE_HASHES)}" if committed else None
    return {"path": str(path), "anchor": str(anchor), "stored": stored, "recomputed": recomputed, "results": new,
            "changes": changes(expected, new), "gold_problems": gold_problems(gold), "data_check": data_check}


def report(r):
    """Print stored against recomputed scores. Returns True when they are the same and every reference result
    can score something."""
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
        print("  FAILED: reference results that cannot score anything at this anchor: " + ", ".join(r["gold_problems"]))
    print(f"  data: checked against {r['data_check']}" if r.get("data_check") else
          "  data: NOT checked: the run stored no row hashes and none are committed for its company and anchor")
    return not diff and not r["changes"] and not r["gold_problems"]


def main(paths=(), all_runs=False):
    paths = [Path(p) for p in paths] + (stored_runs() if all_runs else [])
    if not paths:
        raise SystemExit("Give a run's JSON report, or --all for every model run in eval/results.")
    questions, readers, same = {}, {}, True
    for path in paths:
        metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]
        anchor, (company, _, qfile, poison) = run_anchor(metrics), run_setup(metrics)
        try:
            check_question_file(metrics)
        except ValueError as e:
            print(f"\n{path.name}: cannot be rescored: {e}")
            same = False
            continue
        if (anchor, company, poison) not in readers:          # one database per anchor, company and data
            print(f"Seeding company {company} at {anchor}{' (poisoned)' if poison else ''} ...", flush=True)
            readers[anchor, company, poison] = db.ReadOnlyDB(demo_database(anchor, company=company, baseline=True,
                                                                           poison=poison))
        if qfile not in questions:
            questions[qfile] = load_questions(path=qfile)
        same = report(rescore(path, readers[anchor, company, poison], questions[qfile])) and same
    print("\nAll stored scores reproduced." if same else "\nSome stored scores were NOT reproduced (see above).")
    return same
