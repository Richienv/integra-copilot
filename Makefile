# make verify: the tests, the oracle (must score 100%) and a rescore of every stored model run.
# No model and no network beyond the installed packages; a few minutes on a laptop. CI runs the same.
PYTHON ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python)
ANCHOR := 2026-10-15

.PHONY: test oracle rescore verify doctor

test:
	$(PYTHON) -m pytest -q

# The harness scores the reference answers at the pinned anchor. Below 100% it exits with an error.
# Its reports go to a temporary folder: eval/results is never touched.
oracle:
	$(PYTHON) -m copilot eval --oracle --anchor $(ANCHOR) --workers 4 --out "$$(mktemp -d)"

# Every stored model run, recomputed from its stored SQL. Any difference exits with an error.
rescore:
	$(PYTHON) -m copilot rescore --all

verify: test oracle rescore
	@echo "make verify: tests passed, oracle 100%, every stored run reproduced."

# The Codex setup an evaluation would use (renders Codex's prompt locally; no model call).
doctor:
	$(PYTHON) -m copilot doctor
