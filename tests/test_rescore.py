"""Rescoring a stored run from its stored SQL, with no model."""
import json

import pytest

from copilot import evaluate, llm, rescore
from conftest import ANCHOR
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
