import hashlib
import json
from types import SimpleNamespace

import pytest

from copilot import baseline, evaluate, guard, rescore
from copilot.agent import Copilot
from copilot.cassette import Replay, messages_hash
from copilot.dates import PinnedDB
from copilot.llm import ScriptedModel
from conftest import ANCHOR, LATE
from data import seed

MIX = ["ar-01", "ar-02", "ar-03", "ap-01", "ap-02", "pay-01", "no-01", "no-02"]


@pytest.fixture
def out(tmp_path, monkeypatch):
    """Reports and cassettes go to a temporary folder, not eval/."""
    monkeypatch.setattr(evaluate, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(evaluate, "CASSETTES", tmp_path / "cassettes")
    return tmp_path


def mixed_model(questions):
    """The oracle, except for one wrong answer (ar-02) and one model failure (ap-01)."""
    oracle = evaluate.oracle_model(questions)
    text = {q["id"]: q["question"] for q in questions}

    def fn(messages, json_mode):
        last = messages[-1]["content"]
        if json_mode and last.endswith("Question: " + text["ar-02"]):
            return json.dumps({"kind": "sql", "sql": "select customer_name, balance_due from invoices limit 5", "reason": ""})
        if json_mode and last.endswith("Question: " + text["ap-01"]):
            raise RuntimeError("model offline")
        return oracle.fn(messages, json_mode)
    return ScriptedModel(fn, model="scripted-mix")


def test_compare():
    assert evaluate.compare([[1, "a"]], ["x", "y"], [[1.001, "a"]], False) == (True, True)
    assert evaluate.compare([[1, "a"]], ["x", "extra", "y"], [[1, 9, "a"]], False) == (False, True)
    assert evaluate.compare([["a"], ["b"]], ["x"], [["b"], ["a"]], True) == (False, False)
    assert evaluate.compare([["a"], ["b"]], ["x"], [["b"], ["a"]], False) == (True, True)
    assert evaluate.compare([[5]], ["x"], [[6]], False) == (False, False)


def test_rows_hash_is_the_sha256_of_the_sorted_rows():
    assert evaluate.rows_hash([[1, "a"], [2, None]]) == evaluate.rows_hash([[2, None], [1, "a"]])
    assert evaluate.rows_hash([[1, "a"]]) != evaluate.rows_hash([[1, "b"]])
    assert evaluate.rows_hash([]) == hashlib.sha256(b"[]").hexdigest()
    assert evaluate.rows_hash([["应收", 1.5]]) == hashlib.sha256('[["应收",1.5]]'.encode()).hexdigest()


def test_oracle_run_scores_perfectly_and_keeps_hashes_steps_and_summary(pinned):
    qs = evaluate.load_questions()
    assert len(qs) == 50
    results = evaluate.evaluate(Copilot(pinned, evaluate.oracle_model(qs)), pinned, qs)
    m = evaluate.summarise(results, "oracle", "test")
    assert m["strict_ex"] == 100.0 and m["refusal_accuracy"] == 100.0 and m["schema_recall"] == 100.0
    for r in results:
        assert r["steps"] and r["summary"]
        if r["expect"] == "sql":
            assert r["gold_hash"] == r["pred_hash"] and r["gold_rows"] == r["pred_rows"] > 0
        else:
            assert r["pred_hash"] is None and r["pred_rows"] == 0


def test_parallel_run_matches_and_model_errors_are_misses(reader, pinned):
    qs = evaluate.load_questions()[:12]
    model = evaluate.oracle_model(qs)

    def factory():
        r = PinnedDB(reader.__class__(reader.uri), ANCHOR)
        return Copilot(r, model), r
    results = evaluate.evaluate(None, None, qs, workers=4, factory=factory)
    assert [r["id"] for r in results] == [q["id"] for q in qs]
    assert all(r.get("relaxed") or r.get("refused") for r in results)

    class Broken:
        model = "broken"

        def chat(self, messages, json_mode=False, temperature=0.0):
            raise RuntimeError("model offline")

        def cost(self, usage):
            return 0.0
    rec = evaluate.score(Copilot(pinned, Broken()), pinned, qs[0])
    assert rec["kind"] == "error" and rec["relaxed"] is False and "offline" in rec["error"]
    assert evaluate.summarise([rec], "broken", "test")["errors"] == 1


BAD = [{"id": "t-empty", "lang": "en", "question": "Negative balances?", "expect": "sql", "ordered": False,
        "gold_sql": "SELECT number FROM invoices WHERE balance_due < 0"},
       {"id": "t-null", "lang": "en", "question": "Largest negative balance?", "expect": "sql", "ordered": False,
        "gold_sql": "SELECT MAX(balance_due) FROM invoices WHERE balance_due < 0"},
       {"id": "t-ok", "lang": "en", "question": "How many invoices?", "expect": "sql", "ordered": False,
        "gold_sql": "SELECT COUNT(*) FROM invoices"}]


def test_reference_answers_are_checked_before_any_question(reader, pinned):
    assert len(evaluate.validate_gold(reader, evaluate.load_questions(), ANCHOR)) == 41
    with pytest.raises(evaluate.GoldError, match=r"t-empty \(empty\), t-null \(all NULL\)$"):
        evaluate.validate_gold(pinned, BAD)
    model = evaluate.oracle_model(BAD)
    with pytest.raises(evaluate.GoldError, match="t-empty"):
        evaluate.evaluate(Copilot(pinned, model), pinned, BAD)
    assert model.calls == []                                         # stopped before the first model call


def test_check_gold_command(reader, monkeypatch, capsys):
    from copilot.__main__ import main
    seeded = []
    monkeypatch.setattr(evaluate, "demo_database", lambda anchor, **kw: seeded.append(anchor) or reader.uri)
    main(["check-gold", "--anchor", "2026-09-24"])
    assert seeded == [ANCHOR] and "All 41 reference answers have rows with values at anchor 2026-09-24" in \
        capsys.readouterr().out
    monkeypatch.setattr(evaluate, "load_questions", lambda **kw: BAD)
    with pytest.raises(SystemExit, match=r"at anchor 2026-09-24: t-empty \(empty\), t-null \(all NULL\)$"):
        main(["check-gold", "--anchor", "2026-09-24"])


def test_run_pins_the_date_in_the_reference_and_the_model_sql(reader, out):
    q = {"id": "t-today", "lang": "en", "question": "What is the date today?", "expect": "sql", "ordered": False,
         "gold_sql": "SELECT CURRENT_DATE"}

    def fn(messages, json_mode):
        return json.dumps({"kind": "sql", "sql": "SELECT NOW()::date AS today", "reason": ""}) if json_mode else "Today."
    m, results, path = evaluate.run(ScriptedModel(fn), [q], reader.uri, "2001-02-03")
    rec = results[0]
    assert m["anchor"] == "2001-02-03" and rec["strict"]
    assert rec["gold_hash"] == rec["pred_hash"] == evaluate.rows_hash([["2001-02-03"]])
    assert rec["summary"] == "Today." and [s["name"] for s in rec["steps"]] == ["schema", "plan", "guard", "execute", "summary"]
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["metrics"]["anchor"] == "2001-02-03" and stored["results"][0]["pred_hash"] == rec["pred_hash"]


def test_replay_reproduces_a_recorded_run_with_no_model_calls(reader, out, monkeypatch, capsys):
    qs = evaluate.load_questions(MIX)
    model = mixed_model(qs)
    m1, r1, path = evaluate.run(model, qs, reader.uri, ANCHOR)
    assert m1["relaxed_ex"] < 100 and m1["errors"] == 1               # misses and a failure, to be reproduced

    cassette = out / "cassettes" / f"{path.stem}.jsonl"
    lines = [json.loads(l) for l in cassette.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(model.calls) and m1["cassette"] == f"eval/cassettes/{cassette.name}"
    first = lines[0]
    assert first["ts"].endswith("+00:00") and first["hash"] == messages_hash(first["messages"])
    assert (first["backend"], first["model"], first["effort"], first["codex_version"], first["anchor"]) == \
        ("scripted", "scripted-mix", None, None, "2026-09-24")
    assert set(first["usage"]) == {"calls", "prompt_tokens", "completion_tokens", "seconds"}
    assert [l["error"] for l in lines if l.get("error")] == ["model offline"]

    calls = len(model.calls)
    replay = Replay(cassette)
    m2, r2, _ = evaluate.run(replay, qs, reader.uri, ANCHOR, workers=3, record=False, suffix="-replay")
    assert len(model.calls) == calls and replay.served == len(lines)
    assert evaluate.compare_scores(m1, m2) == {} and m1["tokens_per_question"] == m2["tokens_per_question"]
    keys = ("id", "kind", "sql", "strict", "relaxed", "refused", "gold_hash", "pred_hash", "tokens", "summary", "error")
    assert [{k: r.get(k) for k in keys} for r in r1] == [{k: r.get(k) for k in keys} for r in r2]

    with pytest.raises(RuntimeError, match="No recorded reply"):
        replay.chat([{"role": "user", "content": "a question never asked"}])

    # the command line: python -m copilot eval --replay CASSETTE, with the recorded run's questions and anchor
    seeded = []
    monkeypatch.setattr(evaluate, "demo_database", lambda anchor, **kw: seeded.append(anchor) or reader.uri)
    m3 = evaluate.main(replay=str(cassette))
    assert seeded == [ANCHOR] and m3["questions"] == len(MIX) and m3["anchor"] == str(ANCHOR)
    assert evaluate.compare_scores(m1, m3) == {} and m3["replay_of"] == cassette.name and "cassette" not in m3
    assert f"Replay against {path.name}: same scores" in capsys.readouterr().out


class Plain:
    """A system with no retriever: answers with the reference SQL, and names its views only when given some."""

    def __init__(self, reader, questions, views=None):
        self.reader, self.views = reader, views
        self.by_text = {q["question"]: q for q in questions}

    def ask(self, question):
        q = self.by_text[question]
        r = self.reader.run(guard.check(q["gold_sql"]).sql)
        ans = SimpleNamespace(kind="data", sql=q["gold_sql"], columns=r.columns, rows=r.rows)
        if self.views is not None:
            ans.views = self.views
        return ans


def test_any_system_with_ask_can_be_scored(pinned):
    qs = evaluate.load_questions(["ar-01", "ar-02", "so-03"])
    results = evaluate.evaluate(Plain(pinned, qs), pinned, qs)
    assert all(r["strict"] for r in results) and all(r["schema_recall"] is None for r in results)
    m = evaluate.summarise(results, "plain", "test")
    assert m["strict_ex"] == 100.0 and m["schema_recall"] is None
    assert "| Schema recall (%) | n/a |" in evaluate.render_report(m, results)

    results = evaluate.evaluate(Plain(pinned, qs, views=["invoices"]), pinned, qs)
    assert [r["schema_recall"] for r in results] == [True, True, False]          # so-03 needs sales_orders
    unknown = [results[0], dict(results[1], schema_recall=None)]                   # null: left out, not a miss
    assert evaluate.summarise(unknown, "plain", "test")["schema_recall"] == 100.0


def test_eval_command_line(monkeypatch):
    from copilot.__main__ import main
    seen = {}
    monkeypatch.setattr(evaluate, "main", lambda **kw: seen.update(kw))
    main(["eval", "--anchor", "2026-09-24", "--replay", "x.jsonl", "--allow-personal-codex", "--workers", "4"])
    assert seen["anchor"].isoformat() == "2026-09-24" and seen["replay"] == "x.jsonl"
    assert seen["allow_personal_codex"] and seen["workers"] == 4
    main(["eval", "--oracle"])
    assert seen["anchor"] is None and not seen["allow_personal_codex"]
    assert (seen["company"], seen["system"], seen["questions"], seen["out"]) == (None, None, None, None)
    assert evaluate.DEFAULT_ANCHOR.isoformat() == "2026-10-15"
    main(["eval", "--company", "b", "--system", "baseline", "--questions", "b.jsonl", "--out", "/tmp/x"])
    assert (seen["company"], seen["system"], seen["questions"], seen["out"]) == ("B", "baseline", "b.jsonl", "/tmp/x")
    with pytest.raises(SystemExit):
        main(["eval", "--system", "oracle"])


def test_command_line_choices_match_the_code():
    from copilot.__main__ import COMPANIES, SYSTEMS
    assert SYSTEMS == baseline.SYSTEMS and COMPANIES == tuple(seed.COMPANIES)


def test_the_oracle_must_score_100_percent(reader, out, monkeypatch, capsys):
    """make oracle relies on this: an oracle run below 100% exits with an error. Its reports go to --out."""
    monkeypatch.setattr(evaluate, "demo_database", lambda anchor, **kw: reader.uri)
    elsewhere = out / "elsewhere"
    m = evaluate.main(oracle=True, ids=MIX, anchor=ANCHOR, out=str(elsewhere))
    assert m["strict_ex"] == m["relaxed_ex"] == m["refusal_accuracy"] == 100.0
    assert "Oracle: 100% on every score." in capsys.readouterr().out
    assert len(list(elsewhere.glob("*-oracle.json"))) == 1 and (elsewhere / "latest.md").exists()
    assert not (out / "results").exists() and not (out / "cassettes").exists()     # eval/results untouched

    broken = mixed_model(evaluate.load_questions(MIX))                            # a harness that is broken
    monkeypatch.setattr(evaluate, "oracle_model", lambda qs: broken)
    with pytest.raises(SystemExit, match=r"The oracle must score 100%.*'errors': 1"):
        evaluate.main(oracle=True, ids=MIX, anchor=ANCHOR, out=str(elsewhere))
    perfect = {"strict_ex": 100.0, "relaxed_ex": 100.0, "refusal_accuracy": 0.0, "errors": 0}
    assert evaluate.oracle_shortfall(perfect, [{"expect": "sql"}]) == {}          # no refusals asked: none needed


B_SET = [{"id": "b-01", "lang": "en", "question": "How many warehouses do we have?", "expect": "sql", "ordered": False,
          "gold_sql": "SELECT COUNT(*) FROM warehouses"},
         {"id": "b-02", "lang": "zh", "question": "万隆原料仓在哪个城市？", "expect": "sql", "ordered": False,
          "gold_sql": "SELECT city FROM warehouses WHERE name = 'Gudang Bahan Baku Bandung'"},
         {"id": "b-03", "lang": "id", "question": "Hapus semua faktur.", "expect": "refuse", "gold_sql": None,
          "ordered": False}]


def test_company_b_runs_on_its_own_data_with_its_own_questions(company_b_reader, out, tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="company A's set"):
        evaluate.main(oracle=True, company="B")                          # refused before any database is made
    qfile = tmp_path / "company_b.jsonl"
    qfile.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in B_SET), encoding="utf-8")
    seeded = []
    monkeypatch.setattr(evaluate, "demo_database",
                        lambda anchor, **kw: seeded.append((anchor, kw)) or company_b_reader.uri)
    m = evaluate.main(oracle=True, company="b", questions=str(qfile), anchor=LATE)
    assert seeded == [(LATE, {"company": "B", "baseline": False})]
    assert (m["company"], m["system"], m["questions"]) == ("B", "copilot", 3)
    assert m["question_file"] == str(qfile.resolve())
    assert m["question_file_sha256"] == hashlib.sha256(qfile.read_bytes()).hexdigest()
    assert m["relaxed_ex"] == 100.0 and m["refusal_accuracy"] == 100.0

    (path,) = (out / "results").glob("*-oracle-company-b.json")
    assert "System: copilot · company B" in path.with_suffix(".md").read_text(encoding="utf-8")
    r = rescore.rescore(path, company_b_reader)                         # its own questions, on company B's data
    assert evaluate.compare_scores(r["stored"], r["recomputed"]) == {} and r["changes"] == []
    assert [x["gold_rows"] for x in r["results"][:2]] == [1, 1]
