# Integra Copilot

[![CI](https://github.com/Richienv/integra-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Richienv/integra-copilot/actions/workflows/ci.yml)

**Ask your ERP in Bahasa Indonesia, 中文 or English.** Integra Copilot answers questions about an Indonesian
textile and garment business: receivables, payables, sales, stock, purchasing, the ledger and attendance.
It writes one read-only SQL query, checks it, runs it, and answers with the table, the SQL, and a summary in
which every number is checked against the result.

It sits on top of [Integra ERP](https://github.com/Richienv/ERP), a vertical ERP I built alone for Indonesian
textile and garment SMEs. The demo runs on a copy of Integra's own schema with fictional data.

On its 50-question development set in three languages it answers 95.1% of the 41 answerable questions and
refuses all 9 unsafe ones (GPT-6-Sol; the first run scored 78.0%, and the misses are explained
[below](#what-run-1-got-wrong-and-what-changed)). That set was used to tune the agent, so it is not a blind
test. A blind test on a fictional company the agent has never seen is in progress.

```
"Berapa piutang yang jatuh tempo minggu ini, per pelanggan?"

  schema     invoices, customers                      (retrieved: the views this question needs)
  plan       sql                                      (model, JSON output)
  guard      passed: invoices                         (parser: SELECT on copilot views only)
  execute    4 rows · 14 ms                           (read-only role, timeout, row cap)
  summary    every number checked against the result

  Ada 4 pelanggan dengan piutang jatuh tempo dalam 7 hari; terbesar CV Hijab Cantik: Rp 48.697.920.
```

## How it works

```mermaid
flowchart LR
  Q[Question] --> L[Language<br/>id / zh / en]
  L --> R[Schema retrieval<br/>BM25 over view descriptions]
  R --> V[Stored values<br/>of short name columns]
  V --> P[Plan<br/>model returns JSON:<br/>sql / tax / refuse]
  P -->|sql| G[SQL guard<br/>sqlglot parse]
  G -->|ok| E[Execute<br/>copilot_reader role]
  G -->|error| F[Repair<br/>error sent back, max 2]
  E -->|database error| F
  F --> G
  E --> S[Summary<br/>numbers checked]
  P -->|tax| T[Tax RAG<br/>your documents, cited]
  P -->|refuse| X[Polite refusal]
```

**What is fixed in code, and what the model decides.** Retrieval, the guard, the database limits, the retry
budget, the number check and the templates are code. The model decides whether to answer, writes the SQL,
and words the summary.

**Names in another language.** Owners ask "雅加达成品仓" or "the Jakarta finished-goods warehouse"; the data
says "Gudang Barang Jadi Jakarta". At start-up the copilot reads the stored values of every short text column
(12 values or fewer: warehouses, cities, departments, suppliers) and lists them in the prompt, so the model
filters on a value that exists instead of on a translation that matches nothing.

### Three independent locks

A language model can be talked into anything, so safety never depends on the prompt alone.

1. **The prompt** asks for one read-only SELECT on the listed views.
2. **The guard** (`copilot/guard.py`) parses the SQL with sqlglot and allows one SELECT over the 16 copilot
   views only. It rejects writes, DDL, `SELECT INTO`, `FOR UPDATE`, multiple statements, system catalogues,
   Integra's base tables, and functions such as `pg_sleep`, `pg_read_file`, `set_config` and `dblink`. It runs
   the SQL regenerated from the parse tree, so comments and tricks in the original text never reach the database.
3. **The database** (`sql/02_copilot_views.sql`): the `copilot_reader` role can read the views in the
   `copilot` schema and nothing else, every transaction is read-only, and every statement stops after 5 seconds.
   The views leave out salaries, BPJS numbers, NPWP/NIK numbers, phone numbers, e-mails and bank accounts.

The tests check each lock separately, including the database refusing writes when the guard is bypassed.

### Numbers come from the database, not the model

The summary is the only free text the model writes about data. `copilot/grounding.py` reads every number in
it, in Indonesian, English or Chinese formats (`Rp 12.500.000`, `Rp 12,5 juta`, `12.5 million`, `1250万`),
and accepts it only if it appears in the result, is a column total or the row count, or comes from the
question or the query itself. Otherwise the summary is replaced by a plain sentence and the table stands alone.

## Run it

Python 3.12. No database install needed: the demo starts a private PostgreSQL 16 from the `pgserver` package,
in `~/.cache/integra-copilot/pg` (set `COPILOT_DATA_DIR` to move it; the path must not contain a space).

```bash
uv venv -p 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest -q                         # 93 tests, no API key needed
cp .env.example .env                        # add your model API key, then:
set -a && source .env && set +a
python -m copilot ask "Berapa piutang yang jatuh tempo minggu ini, per pelanggan?"
python -m copilot serve                     # web page on http://127.0.0.1:8000
```

Without any model, `python -m copilot sql "select ..."`, the SQL console on the web page and the MCP tools
still work.

### No API key? Use ChatGPT

With no `LLM_API_KEY` set and the ChatGPT desktop app installed and signed in, the copilot sends each model
call through the Codex CLI that ships inside the app (`LLM_BACKEND=codex` forces this). Nothing to configure:

```bash
python -m copilot ask "Berapa piutang yang jatuh tempo minggu ini, per pelanggan?"
python -m copilot doctor                                  # which Codex, which Codex home, personal instructions or not
CODEX_HOME=~/.codex-eval codex login                      # once: a separate Codex home, signed in by you
CODEX_HOME=~/.codex-eval CODEX_MODEL=gpt-6-sol CODEX_EFFORT=low python -m copilot eval --workers 4
```

It is slower than an API, about 25 seconds a call, because Codex wraps every call in its own agent
instructions (about 21,000 tokens). The copilot runs it read-only, in an empty folder, with no saved session.
Token counts for this backend are estimates of the copilot's own prompts; cost shows zero because calls are
billed to the ChatGPT plan.

Codex also adds your own instructions to every call: the `AGENTS.md` in your Codex home (`~/.codex` unless
`CODEX_HOME` says otherwise). An evaluation must not carry them, so `python -m copilot eval` with Codex refuses
to run from a home that has them, unless `--allow-personal-codex` is given. `python -m copilot doctor` checks
the home and the prompt Codex renders for it (`codex debug prompt-input`: local, no model call). Each run records
the Codex version, the home's folder name and whether it was isolated.

### Use it from Claude Code (MCP)

```bash
claude mcp add integra -- "$PWD/.venv/bin/python" -m copilot mcp
```

Tools: `list_views`, `describe_view`, `run_sql` (guarded), `ask` (the full agent) and `search_tax_docs`.

### Against a real Integra database

`sql/02_copilot_views.sql` uses Integra's real table and column names, so it runs unchanged on an Integra
database. Run it as the database owner, give the role a password, and connect as that role:

```sql
alter role copilot_reader password 'a-long-random-password';
```

```bash
export COPILOT_DATABASE_URL="postgresql://copilot_reader:...@your-host:5432/postgres"
```

## Evaluation

`eval/questions.jsonl` has 50 questions: 41 with a hand-checked reference query, in Indonesian (17), English
(14) and Chinese (10), and 9 that must be refused (writes, salary and ID requests, a prompt injection,
off-topic questions).

Each run builds a fresh demo database seeded at a fixed anchor day, 15 October 2026 unless `--anchor` says
otherwise, and pins today's date to that day in both the reference SQL and the model's SQL: `CURRENT_DATE`,
`NOW()` and the other clock functions become literals after the guard and before execution (`copilot/dates.py`).
A run means the same thing on any day it is repeated, and the mid-month anchor keeps "this month" from covering
a single day. Before any model call, every reference query must return rows with values at the anchor; if one is
empty or all NULL, the run stops and names it. Then result tables are compared:

| Metric | Meaning |
|---|---|
| Strict execution accuracy | same columns and rows as the reference (numbers within 0.5%) |
| Relaxed execution accuracy | every reference column present; extra columns allowed |
| Refusal accuracy | the 9 unsafe or off-topic questions refused |
| False refusals | answerable questions refused |
| Schema recall | the views the reference query needs were retrieved |
| Latency, tokens, cost | per question |

```bash
python -m copilot eval --oracle         # checks the harness itself: returns the reference SQL, must score 100%
python -m copilot eval --workers 4      # the real run with your model; writes eval/results/latest.md
python -m copilot check-gold --anchor 2026-09-24   # does every reference query return values on that day?
```

### Replay and rescore, without a model

Every scored question keeps its steps, its summary, and for both the reference and the answer the row count and
a sha256 of the sorted result rows. Every model call is appended to `eval/cassettes/<run>.jsonl` with its UTC
time, backend, model, reasoning effort, Codex version, the sha256 of the messages, the messages, the reply and
the token counts.

```bash
python -m copilot eval --replay eval/cassettes/RUN.jsonl   # answers every call from the recording, calls no model
python -m copilot rescore eval/results/RUN.json            # reruns the stored SQL and the reference SQL
python -m copilot rescore --all                            # every eval/results/*-codex_*.json
```

A replay asks the recorded run's questions at its anchor and, with the same code, reproduces its scores exactly (cost
shows zero: a replay spends nothing). Rescoring seeds a demo database at the run's anchor, reruns the model's
stored SQL and the reference SQL with the date pinned, recomputes strict, relaxed, refusal and schema-recall
scores and prints them next to the stored ones.

### Results

GPT-6-Sol through the Codex CLI (ChatGPT sign-in, reasoning effort low), 24 September 2026, on a demo database
dated that day. Four questions at a time.

| Run | Strict EX | Relaxed EX | Refusals | False refusals | Schema recall | p50 latency | Tokens / question |
|---|---|---|---|---|---|---|---|
| 1 · first real run | 51.2% | 78.0% | 100% | 0 | 100% | 74 s | about 1,279 |
| 2 · after three fixes | 63.4% | 95.1% | 100% | 0 | 100% | 68 s | about 1,421 |
| 3 · run 2 again, nothing changed | 63.4% | 95.1% | 100% | 0 | 100% | 57 s | about 1,422 |

Run 3 repeated run 2 with nothing changed: the same scores and the same two misses. Relaxed accuracy by
language in runs 2 and 3: Indonesian 100%, English 92.9%, Chinese 90.0%. Full reports in `eval/results/`:
`20260924-1509-codex_gpt-6-sol.md` (run 1), `20260924-1527-codex_gpt-6-sol.md` (run 2) and
`20260924-1728-codex_gpt-6-sol.md` (run 3). Every run keeps its own report; `latest.md` is a copy of the newest.

Strict accuracy is lower because the model adds readable columns, such as codes and names, which rule 6 of
the prompt asks for; relaxed accuracy allows extra columns. Latency is mostly Codex's own overhead, about
25 seconds a call; through an API it would be a few seconds. Tokens are estimates of the copilot's own prompts.

### What run 1 got wrong, and what changed

| Questions | What happened | Fix |
|---|---|---|
| pay-03, so-01, hr-02, hr-03, hr-04 | "The last 30 days": the reference counts from the same date 30 days ago. The model switched between that and "30 days including today" from one question to the next. | The date rule is written into the prompt (rule 3). |
| st-02 | Asked in Chinese about 雅加达成品仓. The model filtered on that Chinese name, which is not in the data (the warehouse is Gudang Barang Jadi Jakarta), and got an empty result. | The stored values of short text columns are listed in the prompt (rule 7). |
| cu-02 | Counted a customer whose credit limit is 0 as owing "more than half" of it. In Integra, 0 is the default and means no limit has been set. | The meaning is in the column's note. |
| so-04 | Counted only delivered, invoiced and completed orders, against rule 5. | None. It passed in run 2 unchanged: the same model does not always follow the same rule. |
| gl-03 | The right numbers, as one row with two columns instead of two rows. | None. Still counted as a miss. |

The reference queries and the comparison did not change between the runs. Both misses in runs 2 and 3 have the right
numbers: gl-03 in the layout above, and so-02 with months written as `2026-04` instead of `2026-04-01`. Both
stay misses.

The fixes came from reading run 1's misses, so run 2 is not a blind measurement. The honest next step is a
fresh set of questions the copilot has never seen.

Two more caveats, found in review. Runs 1 to 3 went through the author's everyday Codex install, which adds his
personal Codex instructions to every call (`--ignore-user-config` skips only Codex's `config.toml`, not the
`AGENTS.md`); the evaluation now refuses to run that way unless told to. And many questions depend on today's
date ("this month"): runs 1 to 3 ran on the day their demo data was dated, and a rerun on another day would not
have been an exact replay. The evaluation now pins the date to an anchor day. Rescoring runs 1 to 3 at
24 September 2026, with the date pinned, reproduces every score above, question by question; the tests check
this on every push.

### Verify it yourself, without a model

```bash
python -m pytest -q                 # the locks, the guard, the number check, the evaluation harness, runs 1-3 rescored
python -m copilot eval --oracle     # the harness scores the reference answers: must be 100%
python -m copilot rescore --all     # every stored Codex run, rescored from its stored SQL
```

The first two run on every push (the CI badge above).

## Project layout

| Path | What it is |
|---|---|
| `sql/01_integra_subset.sql` | the part of Integra's schema the copilot reads, names identical to Integra |
| `sql/02_copilot_views.sql` | the view layer and the read-only role |
| `data/seed.py` | deterministic fictional data, dated relative to an anchor day; books balance |
| `copilot/semantic.py` | what each view means, in three languages; schema retrieval |
| `copilot/guard.py` | the SQL guard |
| `copilot/agent.py` | the loop: plan, guard, execute, repair, summary |
| `copilot/grounding.py` | language detection and the number check |
| `copilot/tax.py` | tax questions from your documents, with citations |
| `copilot/api.py`, `web/index.html` | HTTP API and the demo page |
| `copilot/mcp_server.py` | MCP tools |
| `copilot/evaluate.py`, `eval/` | evaluation set and runner |
| `copilot/dates.py` | pins today's date to the anchor day in every evaluated query |
| `copilot/cassette.py`, `eval/cassettes/` | every model call of a run, recorded; replay without a model |
| `copilot/rescore.py` | rescores a stored run from its stored SQL, without a model |
| `copilot/doctor.py` | the Codex check: binary, version, Codex home, personal instructions |
| `copilot/llm.py` | the model clients: any OpenAI-compatible API, or ChatGPT through the Codex CLI |
| `tests/` | 93 tests |

## Limits, honestly

- One company per database; no multi-tenant row filtering yet. On a shared database, add a tenant filter to the views.
- Date words follow fixed rules, written into the prompt: "this week" is the next 7 days, "this month" is the
  calendar month, and "the last N days" counts from the same date N days ago (`date >= CURRENT_DATE - N`).
  Other ambiguous questions get the model's reading; the SQL is always shown so a person can check it.
- The web API has no login. Run it locally or behind your own authentication.
- Tax answers are only as good and as current as the documents in `data/tax_docs/`. Not tax advice.

Built with an AI coding assistant (Claude Code).
