"""Codex isolation: personal instructions (an AGENTS.md in the Codex home) must not reach an evaluation."""
import json
import sys

import pytest

from copilot import evaluate, llm
from copilot.doctor import codex_gate, codex_status, has_user_instructions

OWN = "<multi_agent_mode>Do not spawn sub-agents unless applicable AGENTS.md/skill instructions ask.</multi_agent_mode>"
PERSONAL = "# AGENTS.md instructions\n\n<INSTRUCTIONS>\nAlways answer like a pirate.\n</INSTRUCTIONS>"
FAKE = r'''import json, os, pathlib, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9-test")
elif args[:2] == ["debug", "prompt-input"]:
    home = pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex")
    pathlib.Path(LOG).write_text(json.dumps({"home": str(home), "cwd_files": os.listdir(".")}))
    if os.environ.get("FAKE_RENDER_FAILS"):
        sys.exit(1)
    user = [{"type": "input_text", "text": PERSONAL}] if (home / "AGENTS.md").exists() or os.environ.get("FAKE_EXTRA") else []
    print(json.dumps([{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": OWN}]},
                      {"type": "message", "role": "user", "content": user + [{"type": "input_text", "text": "<environment_context/>"}]}]))
elif args[:1] == ["exec"]:
    for e in [{"type": "item.completed", "item": {"type": "agent_message", "text": '{"kind": "refuse", "sql": "", "reason": "x"}'}},
              {"type": "turn.completed", "usage": {"output_tokens": 9}}]:
        print(json.dumps(e))
'''


@pytest.fixture
def codex(tmp_path, monkeypatch):
    """A fake codex binary and two Codex homes: a clean one, and a personal one with an AGENTS.md."""
    body = tmp_path / "fake_codex.py"
    body.write_text(f"LOG = {str(tmp_path / 'render.json')!r}\nOWN = {OWN!r}\nPERSONAL = {PERSONAL!r}\n" + FAKE)
    script = tmp_path / "codex"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{body}" "$@"\n')    # a #! line cannot hold a path with spaces
    script.chmod(0o755)
    (tmp_path / "home-clean").mkdir()
    (tmp_path / "home-personal").mkdir()
    (tmp_path / "home-personal" / "AGENTS.md").write_text("Always answer like a pirate.\n")
    for k in ("FAKE_RENDER_FAILS", "FAKE_EXTRA", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CODEX_BIN", str(script))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home-clean"))
    return tmp_path


def test_user_instructions_are_told_apart_from_codex_own():
    assert not has_user_instructions(json.dumps([{"content": [{"text": OWN}]}]))
    assert has_user_instructions(json.dumps([{"content": [{"text": OWN}, {"text": PERSONAL}]}]))
    assert has_user_instructions("<user_instructions>\nold Codex format\n</user_instructions>")


def test_clean_home_is_isolated(codex):
    s = codex_status()
    assert s["version"] == "9.9.9-test" and s["codex_home"] == str(codex / "home-clean")
    assert s["agents_md"] == [] and s["user_instructions"] is False and s["isolation"] == "isolated"
    seen = json.loads((codex / "render.json").read_text())
    assert seen == {"home": str(codex / "home-clean"), "cwd_files": []}     # same environment, an empty folder


def test_personal_home_is_found_by_file_and_by_rendered_prompt(codex, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(codex / "home-personal"))
    s = codex_status()
    assert s["agents_md"] == ["AGENTS.md"] and s["user_instructions"] is True and s["isolation"] == "personal"
    monkeypatch.setenv("CODEX_HOME", str(codex / "home-clean"))
    monkeypatch.setenv("FAKE_EXTRA", "1")                      # instructions from somewhere other than the file
    assert codex_status()["isolation"] == "personal"
    monkeypatch.delenv("FAKE_EXTRA")
    monkeypatch.setenv("FAKE_RENDER_FAILS", "1")
    s = codex_status()
    assert s["user_instructions"] is None and s["isolation"] == "unverified"


def test_evaluation_refuses_a_personal_codex_home(codex, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(codex / "home-personal"))
    model = llm.CodexModel()
    with pytest.raises(SystemExit, match="AGENTS.md.*--allow-personal-codex") as refused:
        codex_gate(model)
    assert f'CODEX_HOME=~/.codex-eval "{codex / "codex"}" login' in str(refused.value)
    assert codex_gate(model, allow_personal=True) == {"codex_version": "9.9.9-test", "codex_home": "home-personal",
                                                       "codex_isolation": "personal", "allow_personal_codex": True}
    assert codex_gate(llm.ScriptedModel(lambda m, j: "")) == {}
    monkeypatch.setenv("LLM_BACKEND", "codex")
    with pytest.raises(SystemExit, match="personal instructions"):
        evaluate.main(ids=["ar-01"])                            # refused before any database or model call


def test_codex_run_records_version_home_and_isolation(codex, reader, monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("CODEX_EFFORT", "low")
    monkeypatch.setattr(evaluate, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(evaluate, "CASSETTES", tmp_path / "cassettes")
    monkeypatch.setattr(evaluate, "demo_database", lambda anchor, **kw: reader.uri)
    m = evaluate.main(ids=["ar-01", "no-01"], anchor="2026-09-24")
    assert (m["codex_version"], m["codex_home"], m["codex_isolation"]) == ("9.9.9-test", "home-clean", "isolated")
    assert m["refusal_accuracy"] == 100.0 and m["false_refusals"] == 1
    lines = [json.loads(l) for l in (tmp_path / m["cassette"].replace("eval/", "")).read_text().splitlines()]
    assert len(lines) == 2 and {(l["backend"], l["model"], l["effort"], l["codex_version"]) for l in lines} == \
        {("codex", "codex/gpt-6-sol", "low", "9.9.9-test")}


def test_doctor_command(codex, capsys):
    from copilot.__main__ import main
    main(["doctor"])
    out = capsys.readouterr().out
    assert "codex version      9.9.9-test" in out and "AGENTS.md in home  none" in out
    assert "isolation          isolated" in out
    # the login command names the binary the copilot uses, not whatever `codex` is on the PATH
    assert f'clean home         CODEX_HOME=~/.codex-eval "{codex / "codex"}" login' in out
