"""A naive baseline, to show what the copilot's layer adds.

The same model, zero-shot, gets Integra's raw base-table DDL (the CREATE TYPE and CREATE TABLE statements of
sql/01_integra_subset.sql) and the question, and replies {"sql": "..."}; an empty sql means it declines.
Nothing else: no views, no schema retrieval, no stored values, no examples, no rules, no guard, no repair and
no model-written summary. One model call, then the SQL runs exactly as the model wrote it, as the
baseline_reader role (see baseline_db.py), through the same ReadOnlyDB as the copilot: read-only transaction,
statement timeout, row cap.

That role reads the base tables, so the personal data the copilot's views leave out (salaries, BPJS and NPWP
numbers, phone numbers, e-mails, bank accounts) IS readable by the baseline. That is the point of the
comparison: without the copilot's layer, only the model's own judgement stands between a question and those
columns. Writes still fail, at the database. Run it on the fictional demo database only.

    system = make_system("baseline", reader_uri, llm)     # or "copilot", for the agent itself
    answer = system.ask("Berapa total piutang yang belum dibayar?")
"""
import json
import re
import time
from dataclasses import asdict

from . import baseline_db, db
from .agent import TEMPLATES, Answer, Copilot, Step
from .grounding import detect_language
from .llm import Usage

PROMPT = """You write PostgreSQL queries for this database:

{ddl}

Answer the user's question with one SQL query. Reply with JSON only: {{"sql": "<query>"}}.
If the question cannot be answered with a query on this database, reply {{"sql": ""}}."""

DECLINED = {"id": "Model tidak menulis query untuk pertanyaan ini.", "zh": "模型没有为这个问题写查询。",
            "en": "The model wrote no query for this question."}

SYSTEMS = ("copilot", "baseline")


def base_ddl(path=db.SQL_DIR / "01_integra_subset.sql"):
    """The CREATE TYPE and CREATE TABLE statements of Integra's base schema, as written, without comments."""
    text = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("--"))
    statements = [s.strip() for s in text.split(";")]
    return "\n\n".join(s + ";" for s in statements if re.match(r"create (type|table)\b", s, flags=re.I))


def parse_sql(text):
    """The sql field of the model's JSON reply: "" when the model declines, None when the reply is not that JSON."""
    m = re.search(r"\{.*\}", text or "", flags=re.S)
    try:
        reply = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        reply = None
    if not isinstance(reply, dict) or "sql" not in reply:
        return None
    return str(reply["sql"] or "").strip()


class _NoRetrieval:
    """The baseline retrieves no views. evaluate.score asks for them when a model call fails."""

    def retrieve(self, question, k):
        return []


class NaiveBaseline:
    """Zero-shot text-to-SQL over the raw base tables. ask() returns an agent.Answer, like Copilot.ask()."""
    k = 0

    def __init__(self, db, llm):
        self.db, self.llm = db, llm
        self.prompt = PROMPT.format(ddl=base_ddl())
        self.retriever = _NoRetrieval()

    def ask(self, question):
        t0 = time.perf_counter()
        usage, steps, lang = Usage(), [], detect_language(question)

        def done(kind, summary, **kw):
            return Answer(question, lang, kind, summary, steps=[asdict(s) for s in steps],
                          usage={**asdict(usage), "cost": round(self.llm.cost(usage), 6)},
                          ms=round((time.perf_counter() - t0) * 1000, 1), **kw)

        t = time.perf_counter()
        text, u = self.llm.chat([{"role": "system", "content": self.prompt},
                                 {"role": "user", "content": f"Question: {question}"}], json_mode=True)
        usage.add(u)
        sql = parse_sql(text)
        steps.append(Step("plan", "no JSON with an sql field" if sql is None else "sql" if sql else "declined",
                          (time.perf_counter() - t) * 1000))
        if sql is None:
            return done("failed", TEMPLATES["failed"][lang])
        if not sql:
            return done("refused", DECLINED[lang])

        result = self.db.run(sql)
        steps.append(Step("execute", result.error or f"{len(result.rows)} rows", result.ms))
        if result.error:
            return done("failed", TEMPLATES["failed"][lang], sql=sql)
        summary = TEMPLATES["table" if result.rows else "empty"][lang].format(n=len(result.rows))
        return done("data", summary, sql=sql, columns=result.columns, rows=result.rows, truncated=result.truncated)


def make_system(name, reader_uri, llm):
    """A system the evaluation can score, by name. reader_uri is copilot_reader's URI on a demo database.
    "copilot" is the agent itself; "baseline" is NaiveBaseline as baseline_reader on the same server
    (create that role first, with baseline_db.create_role)."""
    if name == "copilot":
        return Copilot(db.ReadOnlyDB(reader_uri), llm)
    if name == "baseline":
        return NaiveBaseline(db.ReadOnlyDB(baseline_db.baseline_uri(reader_uri)), llm)
    raise ValueError(f"unknown system {name!r}: choose one of {', '.join(SYSTEMS)}")
