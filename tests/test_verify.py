"""make verify is the one command a reader runs, and what CI runs: the tests, the oracle and the rescore."""
import re

from copilot.evaluate import DEFAULT_ANCHOR, ROOT


def test_make_verify_runs_the_tests_the_oracle_and_the_rescore():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^verify: test oracle rescore$", make, re.M)
    anchor = re.search(r"^ANCHOR := (\S+)$", make, re.M).group(1)
    assert anchor == DEFAULT_ANCHOR.isoformat()
    oracle = re.search(r"^oracle:\n\t(.+)$", make, re.M).group(1)
    assert "eval --oracle --anchor $(ANCHOR)" in oracle and "--out" in oracle     # never into eval/results
    assert re.search(r"^rescore:\n\t.+ rescore --all$", make, re.M)
    assert re.search(r"^doctor:\n\t.+ doctor$", make, re.M)


def test_ci_runs_make_verify():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+run: make verify$", ci, re.M)
