"""Three ways to reach a model, all with the same chat() method:

- ChatModel: any OpenAI-compatible chat API (Alibaba Cloud Bailian / Qwen, DeepSeek, OpenAI, ...).
- CodexModel: the Codex CLI signed in with ChatGPT, for a machine with no API key.
- ScriptedModel: a scripted stand-in used by the tests and by the evaluation's oracle mode.

    export LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    export LLM_API_KEY=sk-...
    export LLM_MODEL=qwen-plus
    export LLM_PRICE_IN=0.8  LLM_PRICE_OUT=2   # optional: price per million tokens, for cost reports
    export LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'   # optional: extra request fields

    export LLM_BACKEND=codex  CODEX_MODEL=gpt-6-sol   # or: no key set and the ChatGPT app installed
"""
import atexit
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def add(self, other):
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.seconds += other.seconds


class ChatModel:
    """LLM_EXTRA_BODY, a JSON object, is sent as extra fields of every request: a local mlx_lm.server running
    Qwen3, for example, takes {"chat_template_kwargs": {"enable_thinking": false}} to turn thinking off."""
    backend = "api"

    def __init__(self, base_url=None, api_key=None, model=None):
        from openai import OpenAI
        self.model = model or os.environ["LLM_MODEL"]
        self.client = OpenAI(base_url=base_url or os.environ["LLM_BASE_URL"],
                             api_key=api_key or os.environ["LLM_API_KEY"])
        self.price_in = float(os.environ.get("LLM_PRICE_IN") or 0)          # an empty value, as in .env.example, is 0
        self.price_out = float(os.environ.get("LLM_PRICE_OUT") or 0)
        self.extra_body = json.loads(os.environ["LLM_EXTRA_BODY"]) if os.environ.get("LLM_EXTRA_BODY") else None
        if self.extra_body is not None and not isinstance(self.extra_body, dict):
            raise ValueError("LLM_EXTRA_BODY must be a JSON object.")

    def chat(self, messages, json_mode=False, temperature=0.0):
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        if self.extra_body:
            extra["extra_body"] = self.extra_body
        start = time.perf_counter()
        r = self.client.chat.completions.create(model=self.model, messages=messages,
                                                temperature=temperature, **extra)
        u = Usage(1, r.usage.prompt_tokens if r.usage else 0, r.usage.completion_tokens if r.usage else 0,
                  time.perf_counter() - start)
        return r.choices[0].message.content or "", u

    def cost(self, usage):
        return (usage.prompt_tokens * self.price_in + usage.completion_tokens * self.price_out) / 1e6


@dataclass
class ScriptedModel:
    """Answers with a function of (messages, json_mode). Deterministic; costs nothing."""
    backend = "scripted"
    fn: object
    model: str = "scripted"
    calls: list = field(default_factory=list)

    def chat(self, messages, json_mode=False, temperature=0.0):
        self.calls.append(messages)
        text = self.fn(messages, json_mode)
        return text, Usage(1, sum(len(m["content"]) for m in messages) // 4, len(text) // 4, 0.0)

    def cost(self, usage):
        return 0.0


def estimate_tokens(text):
    """Rough count: one token per Chinese character, one per four other characters."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def render_transcript(messages, json_mode=False):
    """A chat transcript as one prompt, for a CLI agent that takes a single message."""
    head = ("Act as a chat-completion API. Do not run commands, read files or use tools. "
            "Continue the conversation below: write only the next assistant message, with no tags around it.")
    if json_mode:
        head += " That message must be a single JSON object."
    return "\n\n".join([head] + [f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in messages])


def parse_codex_events(stdout):
    """From `codex exec --json` output, return (last agent message or None, output tokens)."""
    text, out_tokens = None, 0
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text", "")
        elif ev.get("type") == "turn.completed":
            out_tokens += (ev.get("usage") or {}).get("output_tokens", 0)
    return text, out_tokens


_WORKDIR = None


def _empty_workdir():
    """One empty folder per process for Codex to run in, removed when the process exits."""
    global _WORKDIR
    if _WORKDIR is None or not os.path.isdir(_WORKDIR):
        _WORKDIR = tempfile.mkdtemp(prefix="copilot-codex-")
        atexit.register(shutil.rmtree, _WORKDIR, ignore_errors=True)
    return _WORKDIR


class CodexModel:
    """Each call runs `codex exec` once, signed in with ChatGPT: no API key, billed to the ChatGPT plan.

    Slower than an API (often 10 to 30 seconds a call), because Codex adds its own agent instructions,
    about 21,000 tokens, to every call. Those are left out of the token counts: prompt tokens are
    estimated from the Copilot's own messages. Temperature cannot be set.

        CODEX_MODEL   model name, default gpt-6-sol     CODEX_EFFORT   reasoning effort, default low
        CODEX_BIN     another codex binary
    """
    backend = "codex"
    BINARIES = ("/Applications/ChatGPT.app/Contents/Resources/codex",
                "/Applications/Codex.app/Contents/Resources/codex")

    def __init__(self, model=None, binary=None, effort=None, timeout=300):
        self.binary = binary or self.find_binary()
        if not self.binary:
            raise RuntimeError("Codex CLI not found. Install the ChatGPT app, or set CODEX_BIN.")
        self.name = model or os.environ.get("CODEX_MODEL") or "gpt-6-sol"
        self.model = "codex/" + self.name
        self.effort = effort or os.environ.get("CODEX_EFFORT") or "low"
        self.note = (f"Model: {self.name} through the Codex CLI signed in with ChatGPT (reasoning effort {self.effort}). "
                     "Token counts are estimates of the Copilot's own prompts; Codex adds about 21,000 tokens of its "
                     "own instructions to every call. Cost is zero because calls are billed to the ChatGPT plan.")
        self.timeout = timeout
        self.workdir = _empty_workdir()                             # empty: nothing for the agent to read
        self.price_in = float(os.environ.get("LLM_PRICE_IN") or 0)
        self.price_out = float(os.environ.get("LLM_PRICE_OUT") or 0)

    @classmethod
    def find_binary(cls):
        return os.environ.get("CODEX_BIN") or next((b for b in cls.BINARIES if os.path.exists(b)), None)

    def chat(self, messages, json_mode=False, temperature=0.0):
        prompt = render_transcript(messages, json_mode)
        cmd = [self.binary, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
               "--sandbox", "read-only", "--color", "never", "--json", "--cd", self.workdir,
               "--model", self.name, "--config", f'model_reasoning_effort="{self.effort}"', "--", prompt]
        start, error = time.perf_counter(), ""
        for _ in range(2):                                          # one retry
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                error = f"no reply within {self.timeout} s"
                continue
            text, out_tokens = parse_codex_events(p.stdout)
            if p.returncode == 0 and text is not None:
                return text, Usage(1, estimate_tokens(prompt), out_tokens, time.perf_counter() - start)
            error = (p.stderr or p.stdout).strip()[-400:]
        raise RuntimeError(f"codex exec failed: {error}")

    def cost(self, usage):
        """Zero unless LLM_PRICE_IN / LLM_PRICE_OUT are set: then what the same tokens would cost on an API."""
        return (usage.prompt_tokens * self.price_in + usage.completion_tokens * self.price_out) / 1e6


def from_env():
    """LLM_BACKEND=api or codex picks one. Unset: the API if LLM_API_KEY is set, else Codex if it is installed."""
    backend = os.environ.get("LLM_BACKEND", "").strip().lower()
    if backend == "codex" or (not backend and not os.environ.get("LLM_API_KEY") and CodexModel.find_binary()):
        return CodexModel()
    return ChatModel() if os.environ.get("LLM_API_KEY") else None
