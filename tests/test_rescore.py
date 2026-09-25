"""Rescoring a stored run from its stored SQL, with no model."""
import hashlib
import json

import psycopg
import pytest

from copilot import db, evaluate, llm, rescore
from conftest import ANCHOR
from data import seed
from test_evaluate import MIX, mixed_model

DEV_RUNS = {"20260924-1509-codex_gpt-6-sol.json": (51.2, 78.0), "20260924-1527-codex_gpt-6-sol.json": (63.4, 95.1),
            "20260924-1728-codex_gpt-6-sol.json": (63.4, 95.1)}


@pytest.fixture
def no_model(monkeypatch):
    """Any model call fails the test."""
    def refuse(*a, **kw):
        raise AssertionError("a model was called")
    for cls in (llm.ScriptedModel, llm.ChatModel, llm.CodexModel):
        monkeypatch.setattr(cls, "chat", refuse)


@pytest.mark.parametrize("name", DEV_RUNS)
def test_dev_runs_1_to_3_reproduce_exactly(name, reader, no_model):
    r = rescore.rescore(evaluate.RESULTS / name, reader)
    assert r["anchor"] == str(ANCHOR)                                     # old runs: metrics.date is the anchor
    assert (r["recomputed"]["strict_ex"], r["recomputed"]["relaxed_ex"]) == DEV_RUNS[name]
    assert evaluate.compare_scores(r["stored"], r["recomputed"]) == {} and r["changes"] == []
    assert r["gold_problems"] == []
    assert r["data_check"] == "the reference row hashes in eval/reference_hashes.json"      # the data is checked too


def test_rescore_reproduces_a_new_run_and_its_row_hashes(reader, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(evaluate, "CASSETTES", tmp_path / "cassettes")
    qs = evaluate.load_questions(MIX)
    m, results, path = evaluate.run(mixed_model(qs), qs, reader.uri, ANCHOR)
    for cls in (llm.ScriptedModel, llm.ChatModel, llm.CodexModel):
        monkeypatch.setattr(cls, "chat", lambda *a, **kw: pytest.fail("a model was called"))

    r = rescore.rescore(path, reader)
    assert evaluate.compare_scores(m, r["recomputed"]) == {} and r["changes"] == []
    assert [x["pred_hash"] for x in r["results"]] == [x["pred_hash"] for x in results]
    assert rescore.report(r) is True

    run = json.loads(path.read_text(encoding="utf-8"))
    ar01 = next(x for x in run["results"] if x["id"] == "ar-01")
    ar01["sql"] = "SELECT COUNT(*) FROM invoices LIMIT 200"                       # a stored answer that no longer holds
    run["metrics"]["strict_ex"] = 99.9
    path.write_text(json.dumps(run), encoding="utf-8")
    r = rescore.rescore(path, reader)
    assert r["changes"][0]["id"] == "ar-01" and r["changes"][0]["diff"]["relaxed"] == (True, False)
    assert "pred_hash" in r["changes"][0]["diff"] and "strict_ex" in evaluate.compare_scores(r["stored"], r["recomputed"])
    assert rescore.report(r) is False


def test_rescore_command_over_all_codex_runs(reader, tmp_path, monkeypatch, no_model):
    runs = tmp_path / "results"
    runs.mkdir()
    for name in DEV_RUNS:
        (runs / name).write_bytes((evaluate.RESULTS / name).read_bytes())
    (runs / "20260924-1427-oracle.json").write_text("not read by --all")
    (runs / "20261016-0900-oracle-company-b.json").write_text("nor this: the oracle checks the harness")
    monkeypatch.setattr(rescore, "RESULTS", runs)
    assert [p.name for p in rescore.stored_runs()] == sorted(DEV_RUNS)       # every model run, no oracle run
    seeded = []
    monkeypatch.setattr(rescore, "demo_database", lambda anchor, **kw: seeded.append(anchor) or reader.uri)
    from copilot.__main__ import main
    with pytest.raises(SystemExit) as done:
        main(["rescore", "--all"])
    assert done.value.code == 0 and seeded == [ANCHOR]                  # one database for the three runs


def test_rescore_command_exits_with_an_error_when_a_run_is_not_reproduced(reader, tmp_path, monkeypatch, no_model):
    """make rescore relies on this: one stored score that no longer holds fails the whole command."""
    name = "20260924-1527-codex_gpt-6-sol.json"
    run = json.loads((evaluate.RESULTS / name).read_text(encoding="utf-8"))
    run["metrics"]["relaxed_ex"] = 97.6                                   # a stored score the SQL does not give
    path = tmp_path / name
    path.write_text(json.dumps(run), encoding="utf-8")
    monkeypatch.setattr(rescore, "demo_database", lambda anchor, **kw: reader.uri)
    from copilot.__main__ import main
    with pytest.raises(SystemExit) as done:
        main(["rescore", str(path)])
    assert done.value.code == 1


@pytest.fixture(scope="module")
def other_rows(admin_uri):
    """Company A at the same anchor from another random seed: the same questions over different rows."""
    uri = db.database(admin_uri, "company_a_seed_33")
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute((db.SQL_DIR / "01_integra_subset.sql").read_text())
        seed.load(conn, seed.generate(ANCHOR, seed=33))
        conn.execute((db.SQL_DIR / "02_copilot_views.sql").read_text())
    return db.ReadOnlyDB(db.reader_uri(uri))


def test_runs_1_to_3_fail_on_other_data(other_rows, no_model, capsys):
    """Runs 1 to 3 stored no row hashes, and on other rows their scores alone still reproduce. The committed
    reference hashes and the reference check catch it."""
    r = rescore.rescore(evaluate.RESULTS / "20260924-1527-codex_gpt-6-sol.json", other_rows)
    assert evaluate.compare_scores(r["stored"], r["recomputed"]) == {}              # the scores alone say nothing
    assert r["changes"] and all(set(c["diff"]) == {"gold_hash"} for c in r["changes"])
    assert "ar-03 (empty)" in r["gold_problems"]
    assert rescore.report(r) is False
    assert "FAILED: reference results that cannot score anything at this anchor: ar-03 (empty)" in \
        capsys.readouterr().out


def test_a_reference_that_scores_nothing_fails_and_unchecked_data_is_said(reader, tmp_path, monkeypatch, no_model,
                                                                          capsys):
    monkeypatch.setattr(rescore, "REFERENCE_HASHES", tmp_path / "none.json")
    r = rescore.rescore(evaluate.RESULTS / "20260924-1728-codex_gpt-6-sol.json", reader)
    assert r["data_check"] is None and rescore.report(r) is True
    assert "data: NOT checked" in capsys.readouterr().out
    assert rescore.report(dict(r, gold_problems=["ar-03 (empty)"])) is False


def test_rescore_refuses_a_question_file_that_changed(reader, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evaluate, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(evaluate, "CASSETTES", tmp_path / "cassettes")
    qfile = tmp_path / "questions.jsonl"
    qfile.write_text("".join(l + "\n" for l in evaluate.QUESTIONS.read_text(encoding="utf-8").splitlines()
                             if l.strip() and json.loads(l)["id"] in MIX), encoding="utf-8")
    qs = evaluate.load_questions(path=qfile)
    extra = {"question_file": evaluate._label(qfile),
             "question_file_sha256": hashlib.sha256(qfile.read_bytes()).hexdigest()}
    _, _, path = evaluate.run(mixed_model(qs), qs, reader.uri, ANCHOR, extra=extra)
    assert rescore.report(rescore.rescore(path, reader)) is True
    qfile.write_text(qfile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="has changed since the run"):
        rescore.rescore(path, reader)
    monkeypatch.setattr(rescore, "demo_database", lambda anchor, **kw: reader.uri)
    assert rescore.main([path]) is False
    assert "cannot be rescored" in capsys.readouterr().out


def test_the_committed_reference_hashes_are_those_of_every_reference_query(pinned):
    """eval/reference_hashes.json: the 41 reference results of the dev set, company A at 2026-09-24, dates pinned."""
    committed = rescore.reference_hashes("A", ANCHOR, None)
    assert len(committed) == 41
    assert committed == rescore.gold_hashes(evaluate.gold_results(pinned, evaluate.load_questions()))
