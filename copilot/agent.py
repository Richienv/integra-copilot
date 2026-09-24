"""The agent loop.

    question
      -> language            (detect_language)
      -> schema retrieval    (pick the views this question needs)
      -> plan                (model returns JSON: sql | tax | refuse)
      -> guard               (parse; SELECT on copilot views only)
      -> execute             (read-only role, timeout, row cap)
      -> repair              (on a guard or database error, send the error back; at most twice)
      -> summary             (one or two sentences; every number checked against the result)

Fixed in code: retrieval, the guard, the database limits, the retry budget, the number check, the templates.
Decided by the model: whether to answer, the SQL, and the wording of the summary.
"""
import json
import re
import time
from dataclasses import asdict, dataclass, field

from . import guard
from .grounding import LANG_NAMES, check_numbers, detect_language
from .llm import Usage
from .semantic import CATALOG, SchemaRetriever, schema_text, value_hints
from . import tax as taxmod

SYSTEM = """You are Integra Copilot, a read-only data analyst for Integra, an ERP used by Indonesian textile and garment SMEs.
Answer the user's question with ONE PostgreSQL SELECT query over the views listed by the user.

Rules:
1. Use only the listed views and columns. They are in the schema "copilot"; you may omit the prefix.
2. SELECT only: never INSERT, UPDATE, DELETE, DDL, or more than one statement.
3. Money is Indonesian rupiah (IDR). Dates are DATE values; today is CURRENT_DATE. "This week" means the next 7 days
   from today unless the user says otherwise; "this month" means the current calendar month; "the last N days"
   (N hari terakhir, 过去N天) means date >= CURRENT_DATE - N.
4. Receivables (piutang, 应收) are invoices with direction = 'receivable'; payables (hutang, 应付) have
   direction = 'payable'. The unpaid amount is balance_due.
5. Leave out draft, cancelled, rejected and void documents (DRAFT, PO_DRAFT, CANCELLED, REJECTED, VOID) unless the user asks for them.
6. Return readable columns (names, not only ids). Rankings need ORDER BY and a LIMIT.
7. Names in the question can be in another language than the data. When a column lists its values, match the
   question to one of them by meaning and filter on that exact value.
8. Set kind to "refuse" if the user asks to change data, asks for data the views do not hold (salaries, ID numbers,
   phone numbers), or asks about something that is not this company's data.
9. Set kind to "tax" if the question is about Indonesian tax rules rather than this company's numbers.

Reply with JSON only: {"kind": "sql" | "tax" | "refuse", "sql": "<query, or empty>", "reason": "<one short sentence>"}"""

EXAMPLES = [
    ("Tampilkan 5 produk dengan harga jual tertinggi.",
     {"kind": "sql", "sql": "SELECT code, name, selling_price FROM products WHERE is_active AND selling_price IS NOT NULL "
                            "ORDER BY selling_price DESC LIMIT 5", "reason": "Ranking products by selling price."}),
    ("每个仓库有多少种产品有库存？",
     {"kind": "sql", "sql": "SELECT warehouse_name, COUNT(*) AS products_in_stock FROM stock WHERE quantity > 0 "
                            "GROUP BY warehouse_name ORDER BY products_in_stock DESC", "reason": "Counting stocked products per warehouse."}),
    ("Delete all cancelled sales orders.",
     {"kind": "refuse", "sql": "", "reason": "The copilot can only read data."}),
    ("Berapa tarif PPh 23 untuk jasa?",
     {"kind": "tax", "sql": "", "reason": "This is a tax-rule question, not company data."}),
]

REPAIR = """That query failed.
Error: {error}
Fix it and reply with the same JSON shape. Use only the listed views and columns."""

SUMMARY = """Question: {question}
Query result ({n} rows, columns: {columns}):
{rows}

Write one or two sentences in {language} that answer the question from this result only.
Use only numbers that appear in the result or are totals of a column. Write rupiah like Rp 12.500.000.
No advice, no guesses, no markdown."""

TEMPLATES = {
    "table": {"id": "Hasilnya ada di tabel di bawah ({n} baris).", "zh": "结果见下表（共 {n} 行）。",
              "en": "The result is in the table below ({n} rows)."},
    "empty": {"id": "Tidak ada data yang cocok dengan pertanyaan ini.", "zh": "没有找到符合条件的数据。",
              "en": "No data matches this question."},
    "refuse": {"id": "Maaf, Copilot hanya bisa membaca data bisnis Integra; tidak bisa mengubah data atau membuka data pribadi.",
               "zh": "抱歉，Copilot 只能读取 Integra 的业务数据，不能修改数据，也不能查看个人信息。",
               "en": "Sorry, Copilot can only read Integra's business data. It can't change data or show personal information."},
    "failed": {"id": "Maaf, pertanyaan ini belum bisa dijawab. Coba tanyakan dengan cara lain.",
               "zh": "抱歉，暂时无法回答这个问题，请换一种问法。",
               "en": "Sorry, I couldn't answer that. Try asking another way."},
    "no_tax": {"id": "Belum ada dokumen pajak yang dimuat, jadi pertanyaan pajak belum bisa dijawab.",
               "zh": "还没有加载税务文件，所以暂时不能回答税务问题。",
               "en": "No tax documents are loaded yet, so tax questions can't be answered."},
}


@dataclass
class Step:
    name: str
    detail: str
    ms: float = 0.0


@dataclass
class Answer:
    question: str
    language: str
    kind: str                      # data | tax | refused | failed
    summary: str
    sql: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    views: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    repairs: int = 0
    grounded: bool = True
    steps: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    ms: float = 0.0

    def to_dict(self):
        return asdict(self)


def parse_plan(text):
    """Pull the JSON object out of the model's reply, tolerating code fences and stray words."""
    m = re.search(r"\{.*\}", text or "", flags=re.S)
    try:
        plan = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        plan = {}
    kind = str(plan.get("kind", "")).lower()
    return {"kind": kind if kind in ("sql", "tax", "refuse") else "invalid",
            "sql": str(plan.get("sql") or ""), "reason": str(plan.get("reason") or "")}


class Copilot:
    def __init__(self, db, llm, tax_index=None, k=4, max_repairs=2, catalog=CATALOG):
        self.db, self.llm, self.tax_index = db, llm, tax_index
        self.k, self.max_repairs, self.catalog = k, max_repairs, catalog
        self.retriever = SchemaRetriever(catalog)
        self.values = value_hints(db, catalog)

    def _call(self, usage, messages, json_mode=False):
        text, u = self.llm.chat(messages, json_mode=json_mode)
        usage.add(u)
        return text

    def ask(self, question):
        t0 = time.perf_counter()
        usage = Usage()
        steps = []
        lang = detect_language(question)
        views = self.retriever.retrieve(question, self.k)
        steps.append(Step("schema", ", ".join(views)))

        def done(kind, summary, **kw):
            a = Answer(question, lang, kind, summary, views=views, steps=[asdict(s) for s in steps],
                       usage={**asdict(usage), "cost": round(self.llm.cost(usage), 6)},
                       ms=round((time.perf_counter() - t0) * 1000, 1), **kw)
            return a

        messages = [{"role": "system", "content": SYSTEM}]
        for q, plan in EXAMPLES:
            messages.append({"role": "user", "content": f"Question: {q}"})
            messages.append({"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"Views:\n{schema_text(views, self.catalog, self.values)}\n\nQuestion: {question}"})

        t = time.perf_counter()
        raw = self._call(usage, messages, json_mode=True)
        plan = parse_plan(raw)
        if plan["kind"] == "invalid":
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": 'Reply with valid JSON only: {"kind": ..., "sql": ..., "reason": ...}'}]
            raw = self._call(usage, messages, json_mode=True)
            plan = parse_plan(raw)
        steps.append(Step("plan", f"{plan['kind']}: {plan['reason']}", (time.perf_counter() - t) * 1000))

        if plan["kind"] in ("refuse", "invalid"):
            return done("refused", TEMPLATES["refuse"][lang])
        if plan["kind"] == "tax":
            if not self.tax_index or not len(self.tax_index):
                return done("tax", TEMPLATES["no_tax"][lang])
            t = time.perf_counter()
            text, cites, u = taxmod.answer(question, lang, self.llm, self.tax_index)
            if u:
                usage.add(u)
            steps.append(Step("tax", f"{len(cites)} sources cited", (time.perf_counter() - t) * 1000))
            return done("tax", text, citations=cites)

        sql, repairs, result, checked = plan["sql"], 0, None, None
        for attempt in range(self.max_repairs + 1):
            checked = guard.check(sql, self.catalog)
            if not checked.ok:
                steps.append(Step("guard", f"blocked ({checked.category}): {checked.reason}"))
                if checked.category in ("write", "multi_statement", "forbidden_function"):
                    return done("refused", TEMPLATES["refuse"][lang], sql=sql)
                error = checked.reason
            else:
                steps.append(Step("guard", "passed: " + ", ".join(checked.views)))
                result = self.db.run(checked.sql)
                steps.append(Step("execute", result.error or f"{len(result.rows)} rows", result.ms))
                if not result.error:
                    break
                error = result.error
            if attempt == self.max_repairs:
                return done("failed", TEMPLATES["failed"][lang], sql=sql, repairs=repairs)
            repairs += 1
            t = time.perf_counter()
            messages += [{"role": "assistant", "content": json.dumps({"kind": "sql", "sql": sql, "reason": plan["reason"]})},
                         {"role": "user", "content": REPAIR.format(error=error)}]
            plan = parse_plan(self._call(usage, messages, json_mode=True))
            steps.append(Step("repair", f"attempt {repairs}: {plan['kind']}", (time.perf_counter() - t) * 1000))
            if plan["kind"] != "sql":
                return done("refused", TEMPLATES["refuse"][lang], sql=sql, repairs=repairs)
            sql = plan["sql"]

        if not result.rows:
            return done("data", TEMPLATES["empty"][lang], sql=checked.sql, columns=result.columns,
                        repairs=repairs, truncated=result.truncated)

        t = time.perf_counter()
        shown = result.rows[:30]
        prompt = SUMMARY.format(question=question, n=len(result.rows), columns=", ".join(result.columns),
                                rows="\n".join(json.dumps(r, ensure_ascii=False) for r in shown), language=LANG_NAMES[lang])
        summary = self._call(usage, [{"role": "user", "content": prompt}]).strip()
        ok, bad = check_numbers(summary, question, result.columns, result.rows, checked.sql)
        if not ok or not summary:
            steps.append(Step("summary", f"replaced by template: numbers not in the result {bad[:3]}",
                              (time.perf_counter() - t) * 1000))
            summary = TEMPLATES["table"][lang].format(n=len(result.rows))
        else:
            steps.append(Step("summary", "every number checked against the result", (time.perf_counter() - t) * 1000))
        return done("data", summary, sql=checked.sql, columns=result.columns, rows=result.rows,
                    truncated=result.truncated, repairs=repairs, grounded=ok)
