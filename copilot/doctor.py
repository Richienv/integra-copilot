"""What reaches the model besides the copilot's own prompt, when the backend is the Codex CLI.

Codex adds its own agent instructions to every call. It also adds the user's personal instructions: an
AGENTS.md in the Codex home (CODEX_HOME, default ~/.codex). An evaluation must not carry someone's personal
instructions, so evaluation with Codex runs only from a home without them, unless --allow-personal-codex is
given. To set up an isolated home, sign in to it yourself (auth files are never copied), with the Codex the
copilot uses: the one inside the ChatGPT app, not necessarily the `codex` on your PATH. `doctor` prints this
command for the binary it found:

    CODEX_HOME=~/.codex-eval /Applications/ChatGPT.app/Contents/Resources/codex login
    CODEX_HOME=~/.codex-eval python -m copilot doctor      # isolation: isolated
    CODEX_HOME=~/.codex-eval python -m copilot eval --workers 4

`codex debug prompt-input` renders the prompt Codex would send, locally and without a model call; the check
runs it with the same environment and looks for a user-instructions block in it.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .llm import CodexModel

AGENTS_FILES = ("AGENTS.md", "AGENTS.override.md")
USER_BLOCKS = ("# AGENTS.md instructions", "<INSTRUCTIONS>", "<user_instructions>")


def login_command(binary):
    """The command that signs a separate, clean Codex home in with the binary the copilot uses."""
    return f'CODEX_HOME=~/.codex-eval "{binary}" login' if binary else None


def codex_home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def codex_version(binary, timeout=30):
    """The version `codex --version` prints, e.g. 0.155.0, or None."""
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = p.stdout.split()
    return words[-1] if p.returncode == 0 and words else None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def has_user_instructions(rendered):
    """True if the prompt Codex rendered carries a user-instructions (AGENTS.md) block beyond its own."""
    try:
        texts = list(_strings(json.loads(rendered)))
    except json.JSONDecodeError:
        texts = [rendered]
    return any(marker in t for t in texts for marker in USER_BLOCKS)


def codex_status(binary=None, env=None, timeout=120):
    """The Codex binary, its version, the Codex home, and whether personal instructions would reach the model.

    isolation is "isolated" when the home has no AGENTS.md and the rendered prompt has no user instructions,
    "personal" when either is found, and "unverified" when there is no AGENTS.md but the prompt could not
    be rendered."""
    env = dict(os.environ if env is None else env)
    binary = binary or CodexModel.find_binary()
    home = codex_home(env)
    status = {"binary": binary, "version": None, "codex_home": str(home),
              "agents_md": [n for n in AGENTS_FILES if (home / n).exists()], "user_instructions": None}
    if binary:
        status["version"] = codex_version(binary)
        with tempfile.TemporaryDirectory(prefix="copilot-codex-") as empty:     # like CodexModel's empty folder
            try:
                p = subprocess.run([binary, "debug", "prompt-input"], cwd=empty, env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=timeout)
                if p.returncode == 0 and p.stdout.strip():
                    status["user_instructions"] = has_user_instructions(p.stdout)
            except (OSError, subprocess.TimeoutExpired):
                pass
    if status["agents_md"] or status["user_instructions"]:
        status["isolation"] = "personal"
    else:
        status["isolation"] = "isolated" if status["user_instructions"] is False else "unverified"
    return status


def codex_gate(llm, allow_personal=False):
    """For evaluation: refuse a Codex run whose home carries personal instructions, unless allowed.
    Returns what the run metrics record; {} for other backends."""
    if getattr(llm, "backend", None) != "codex":
        return {}
    s = codex_status(llm.binary)
    if s["isolation"] == "personal" and not allow_personal:
        found = ", ".join(s["agents_md"]) or "a user-instructions block in the rendered prompt"
        raise SystemExit(f"Codex home {s['codex_home']} carries personal instructions ({found}), which would reach "
                         "the model in every call. Point CODEX_HOME to a home without them (sign one in with: "
                         f"{login_command(llm.binary)}), or pass --allow-personal-codex to run anyway.")
    if s["isolation"] == "unverified":
        print(f"Warning: could not render Codex's prompt to check for personal instructions ({s['codex_home']}).",
              flush=True)
    return {"codex_version": s["version"], "codex_home": Path(s["codex_home"]).name,
            "codex_isolation": s["isolation"], "allow_personal_codex": bool(allow_personal)}


def main():
    """python -m copilot doctor: print the Codex setup an evaluation would use."""
    backend = (os.environ.get("LLM_BACKEND", "").strip().lower()
               or ("api" if os.environ.get("LLM_API_KEY") else "codex" if CodexModel.find_binary() else "none"))
    print(f"backend            {backend} (LLM_BACKEND={os.environ.get('LLM_BACKEND') or 'unset'})")
    s = codex_status()
    print(f"codex binary       {s['binary'] or 'not found (install the ChatGPT app, or set CODEX_BIN)'}")
    print(f"codex version      {s['version'] or 'unknown'}")
    print(f"CODEX_HOME         {s['codex_home']}{'' if os.environ.get('CODEX_HOME') else ' (default)'}")
    print(f"AGENTS.md in home  {', '.join(s['agents_md']) or 'none'}")
    shown = {True: "yes", False: "no", None: "could not render the prompt"}[s["user_instructions"]]
    print(f"user instructions  {shown} (in the prompt from codex debug prompt-input)")
    verdict = {"isolated": "evaluation can run",
               "personal": "evaluation refuses to run without --allow-personal-codex",
               "unverified": "evaluation runs, with a warning"}[s["isolation"]]
    print(f"isolation          {s['isolation']}: {verdict}")
    if s["binary"]:
        print(f"clean home         {login_command(s['binary'])}   (once, signed in by you; then set CODEX_HOME)")
    return s
