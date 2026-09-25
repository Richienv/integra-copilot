import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from copilot import db, llm

EVENTS = "\n".join(json.dumps(e) for e in [
    {"type": "thread.started", "thread_id": "t"},
    {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": '{"kind": "refuse", "sql": "", "reason": "x"}'}},
    {"type": "turn.completed", "usage": {"input_tokens": 21500, "output_tokens": 19}},
])


def fake_codex(tmp_path, exit_code=0):
    """A stand-in for the codex binary: records its arguments, prints Codex-style JSON events.
    A shell wrapper starts Python, because a #! line cannot hold a path with spaces."""
    body = tmp_path / "fake_codex.py"
    body.write_text(f"import json, sys, pathlib\n"
                    f"pathlib.Path({str(tmp_path / 'args.json')!r}).write_text(json.dumps(sys.argv[1:]))\n"
                    f"print({EVENTS!r})\nsys.exit({exit_code})\n")
    script = tmp_path / "codex"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{body}" "$@"\n')
    script.chmod(0o755)
    return str(script)


def test_transcript_and_events():
    prompt = llm.render_transcript([{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}], json_mode=True)
    assert "<system>\nS\n</system>" in prompt and "<user>\nQ\n</user>" in prompt and "single JSON object" in prompt
    assert llm.parse_codex_events(EVENTS) == ('{"kind": "refuse", "sql": "", "reason": "x"}', 19)
    assert llm.parse_codex_events("not json\n") == (None, 0)
    assert llm.estimate_tokens("应收账款") == 4 and llm.estimate_tokens("abcdefgh") == 2


def test_codex_model_runs_read_only_and_counts_only_its_own_prompt(tmp_path):
    m = llm.CodexModel(model="gpt-test", binary=fake_codex(tmp_path))
    text, usage = m.chat([{"role": "user", "content": "Delete everything."}], json_mode=True)
    args = json.loads((tmp_path / "args.json").read_text())
    assert json.loads(text)["kind"] == "refuse"
    assert args[:2] == ["exec", "--ephemeral"] and "read-only" in args and args[args.index("--model") + 1] == "gpt-test"
    assert usage.calls == 1 and usage.completion_tokens == 19 and usage.prompt_tokens < 200
    assert m.model == "codex/gpt-test" and m.cost(usage) == 0.0


def test_codex_model_failure_raises(tmp_path):
    with pytest.raises(RuntimeError, match="codex exec failed"):
        llm.CodexModel(binary=fake_codex(tmp_path, exit_code=1)).chat([{"role": "user", "content": "hi"}])


def test_backend_choice(tmp_path, monkeypatch):
    for k in ("LLM_BACKEND", "LLM_API_KEY", "CODEX_BIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm.CodexModel, "BINARIES", (str(tmp_path / "missing"),))
    assert llm.from_env() is None
    monkeypatch.setenv("CODEX_BIN", fake_codex(tmp_path))
    assert isinstance(llm.from_env(), llm.CodexModel)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    assert isinstance(llm.from_env(), llm.ChatModel)
    monkeypatch.setenv("LLM_BACKEND", "codex")
    assert isinstance(llm.from_env(), llm.CodexModel)


def test_chat_model_sends_llm_extra_body(monkeypatch):
    """LLM_EXTRA_BODY goes out as extra fields of every request, e.g. to turn thinking off on a local server."""
    sent = []

    class Completions:
        def create(self, **kw):
            sent.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))], usage=None)

    def chat_model():
        m = llm.ChatModel(base_url="http://127.0.0.1:9/v1", api_key="sk-test", model="qwen3-4b")
        m.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        m.chat([{"role": "user", "content": "hi"}], json_mode=True)
        return m
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    assert chat_model().extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert sent[-1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert sent[-1]["response_format"] == {"type": "json_object"}
    monkeypatch.delenv("LLM_EXTRA_BODY")
    chat_model()
    assert "extra_body" not in sent[-1]
    for bad in ("not json", "[1, 2]"):
        monkeypatch.setenv("LLM_EXTRA_BODY", bad)
        with pytest.raises(ValueError):
            llm.ChatModel(base_url="http://127.0.0.1:9/v1", api_key="sk-test", model="qwen3-4b")


def test_demo_database_never_starts_from_a_path_with_a_space(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATA_DIR", str(tmp_path / "Richie Kid Novell" / "pg"))
    assert " " not in str(db.default_data_dir())
    with pytest.raises(ValueError, match="space"):
        db.local_server(tmp_path / "with space")


def test_env_example_can_be_sourced_as_the_readme_says():
    """README, Run it: cp .env.example .env, add your key, set -a && source .env && set +a. The empty optional
    values (prices, database URL) must not crash, and without a key the API backend is not chosen."""
    root = Path(__file__).resolve().parent.parent
    code = "from copilot.llm import from_env; m = from_env(); print(type(m).__name__, getattr(m, 'price_in', '-'))"

    def sourced(key):
        script = f'set -a && . ./.env.example && LLM_API_KEY="{key}" && set +a && "{sys.executable}" -c "{code}"'
        return subprocess.run(["/bin/sh", "-c", script], cwd=root, capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}).stdout.split()
    assert sourced("sk-test") == ["ChatModel", "0.0"]
    assert sourced("")[0] in ("CodexModel", "NoneType")                  # ChatGPT through Codex, if installed
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "\nLLM_API_KEY=\n" in example                                 # no placeholder key to source by mistake
