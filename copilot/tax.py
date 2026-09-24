"""Tax questions, answered only from documents you put in data/tax_docs/ (official PPN, PPh and e-Faktur
guidance as .md or .txt). Nothing is answered from the model's memory: if the documents don't contain the
answer, the copilot says so. This repository ships no tax text of its own.
"""
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from .grounding import LANG_NAMES
from .semantic import tokenize

NOT_FOUND = {"id": "Tidak ditemukan di dokumen pajak yang tersedia.",
             "zh": "现有税务文件中没有找到答案。",
             "en": "Not found in the available tax documents."}


def chunk(text, size=900, overlap=150):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces = []
    for p in paras:
        pieces += [p[i:i + size] for i in range(0, len(p), size)]
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > size:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n" + p
        else:
            cur = (cur + "\n" + p).strip()
    if cur:
        chunks.append(cur)
    return chunks


class TaxIndex:
    def __init__(self, records):
        self.records = records
        self.bm25 = BM25Okapi([tokenize(r["text"]) for r in records]) if records else None

    @classmethod
    def from_dir(cls, path):
        records = []
        for f in sorted(Path(path).glob("*")):
            if f.suffix.lower() not in (".md", ".txt") or f.name.lower() == "readme.md":
                continue
            for i, c in enumerate(chunk(f.read_text(encoding="utf-8"))):
                records.append({"doc": f.name, "chunk": i, "text": c})
        return cls(records)

    def __len__(self):
        return len(self.records)

    def search(self, question, k=4):
        """Chunks that share at least one word with the question, best BM25 score first.
        (With very few documents BM25 scores can be negative, so overlap decides what is relevant.)"""
        if not self.bm25:
            return []
        q = set(tokenize(question))
        scores = self.bm25.get_scores(list(q))
        overlap = [len(q & set(tokenize(r["text"]))) for r in self.records]
        best = sorted((i for i in range(len(scores)) if overlap[i]), key=lambda i: (-scores[i], -overlap[i]))[:k]
        return [dict(self.records[i], score=float(scores[i])) for i in best]


PROMPT = """Answer the question using only the numbered sources below. Write in {language}.
Cite the sources you use like [1]. If the sources do not contain the answer, reply exactly:
{not_found}

{sources}

Question: {question}"""


def answer(question, lang, llm, index):
    """lang is a language code: id, zh or en."""
    hits = index.search(question)
    if not hits:
        return NOT_FOUND[lang], [], None
    sources = "\n\n".join(f"[{n}] ({h['doc']}) {h['text']}" for n, h in enumerate(hits, 1))
    prompt = (PROMPT.replace("{language}", LANG_NAMES[lang]).replace("{not_found}", NOT_FOUND[lang])
              .replace("{sources}", sources).replace("{question}", question))
    text, usage = llm.chat([{"role": "user", "content": prompt}])
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(hits)})
    return text.strip(), [{"n": n, "doc": hits[n - 1]["doc"], "chunk": hits[n - 1]["chunk"],
                           "snippet": hits[n - 1]["text"][:240]} for n in cited], usage
