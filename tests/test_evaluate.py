from copilot import evaluate
from copilot.agent import Copilot


def test_compare():
    assert evaluate.compare([[1, "a"]], ["x", "y"], [[1.001, "a"]], False) == (True, True)
    assert evaluate.compare([[1, "a"]], ["x", "extra", "y"], [[1, 9, "a"]], False) == (False, True)
    assert evaluate.compare([["a"], ["b"]], ["x"], [["b"], ["a"]], True) == (False, False)
    assert evaluate.compare([["a"], ["b"]], ["x"], [["b"], ["a"]], False) == (True, True)
    assert evaluate.compare([[5]], ["x"], [[6]], False) == (False, False)


def test_oracle_run_scores_perfectly(reader):
    qs = evaluate.load_questions()
    assert len(qs) == 50
    results = evaluate.evaluate(Copilot(reader, evaluate.oracle_model(qs)), reader, qs)
    m = evaluate.summarise(results, "oracle", "test")
    assert m["strict_ex"] == 100.0 and m["refusal_accuracy"] == 100.0 and m["schema_recall"] == 100.0


def test_parallel_run_matches_and_model_errors_are_misses(reader):
    qs = evaluate.load_questions()[:12]
    model = evaluate.oracle_model(qs)
    factory = lambda: (Copilot(reader.__class__(reader.uri), model), reader.__class__(reader.uri))
    results = evaluate.evaluate(None, None, qs, workers=4, factory=factory)
    assert [r["id"] for r in results] == [q["id"] for q in qs]
    assert all(r.get("relaxed") or r.get("refused") for r in results)

    class Broken:
        model = "broken"

        def chat(self, messages, json_mode=False, temperature=0.0):
            raise RuntimeError("model offline")

        def cost(self, usage):
            return 0.0
    rec = evaluate.score(Copilot(reader, Broken()), reader, qs[0])
    assert rec["kind"] == "error" and rec["relaxed"] is False and "offline" in rec["error"]
    assert evaluate.summarise([rec], "broken", "test")["errors"] == 1
