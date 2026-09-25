"""Cassettes: every model call of an evaluation run, recorded so the run can be replayed without a model.

Each line of eval/cassettes/<run name>.jsonl is one call: when it was made (UTC), the backend, the model,
its reasoning effort, the Codex version if any, the anchor day, the sha256 of the messages, the messages,
the reply and the token usage. A failed call is recorded with its error.

    python -m copilot eval --replay eval/cassettes/<run name>.jsonl

answers every call from the cassette, matched by the hash of its messages, and never calls a model. With the
same code and anchor, a replay reproduces the recorded run's scores exactly.
"""
import datetime as dt
import hashlib
import json
import threading
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

from .doctor import codex_version
from .llm import Usage


def messages_hash(messages):
    """sha256 of the messages as canonical JSON."""
    text = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def describe(llm):
    """Backend, model, reasoning effort and Codex version of a model, as recorded with every call."""
    backend = getattr(llm, "backend", type(llm).__name__)
    info = {"backend": backend, "model": llm.model, "effort": getattr(llm, "effort", None),
            "codex_version": codex_version(llm.binary) if backend == "codex" else None}
    if getattr(llm, "extra_body", None):
        info["extra_body"] = llm.extra_body
    return info


class Recorder:
    """Wraps a model and appends every call to a cassette. Safe to share between worker threads."""

    def __init__(self, llm, path, anchor=None):
        self.llm, self.path = llm, Path(path)
        self.model, self.note = llm.model, getattr(llm, "note", "")
        self.info = {**describe(llm), "anchor": str(anchor) if anchor else None}
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def chat(self, messages, json_mode=False, temperature=0.0):
        entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"), **self.info,
                 "json_mode": json_mode, "hash": messages_hash(messages), "messages": messages}
        try:
            text, usage = self.llm.chat(messages, json_mode=json_mode, temperature=temperature)
        except Exception as e:
            self._write({**entry, "response": None, "usage": None, "error": str(e)})
            raise
        self._write({**entry, "response": text, "usage": asdict(usage)})
        return text, usage

    def _write(self, entry):
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line)

    def cost(self, usage):
        return self.llm.cost(usage)


class Replay:
    """Answers every call from a cassette, matched by the hash of the messages. Never calls a model.
    Identical calls get the recorded replies in the recorded order; a call with no recorded reply fails."""
    backend = "replay"

    def __init__(self, path):
        self.path = Path(path)
        entries = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not entries:
            raise ValueError(f"{self.path} has no recorded calls.")
        self.replies = defaultdict(deque)
        for e in entries:
            self.replies[e["hash"]].append(e)
        self.model, self.anchor = entries[0]["model"], entries[0].get("anchor")
        self.note = f"Replayed from {self.path.name}: every model call answered from the cassette, none made."
        self.lock = threading.Lock()
        self.served = 0

    def chat(self, messages, json_mode=False, temperature=0.0):
        key = messages_hash(messages)
        with self.lock:
            queue = self.replies.get(key)
            if not queue:
                raise RuntimeError(f"No recorded reply for this call (messages sha256 {key[:12]}) in {self.path.name}.")
            e = queue.popleft()
            self.served += 1
        if e.get("error"):
            raise RuntimeError(e["error"])
        return e["response"], Usage(**e["usage"])

    def cost(self, usage):
        """Zero: a replay spends nothing."""
        return 0.0
