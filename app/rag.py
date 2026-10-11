"""Retrieval for the Travel RAG agent: pure-Python BM25 over paragraph chunks.

No embeddings service or vector DB is required, so the project runs anywhere.
`Retriever` re-indexes automatically when the knowledge base changes.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .services import KnowledgeService

STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how i if in into is it its me my of on or our "
    "so than that the their them then there these they this to us was we what when where which who why will with "
    "you your about tell should would could please any some get give show".split()
)
MIN_COVERAGE = 1 / 3     # a chunk must match at least this share of the (distinct) query terms


def tokenize(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


@dataclass
class Chunk:
    doc_id: int
    title: str
    city: str | None
    text: str
    tokens: Counter
    length: int


class Retriever:
    def __init__(self, kb: KnowledgeService, k1: float = 1.5, b: float = 0.75):
        self.kb, self.k1, self.b = kb, k1, b
        self._sig: tuple | None = None
        self._chunks: list[Chunk] = []
        self._df: Counter = Counter()
        self._avg_len = 1.0

    # ---------- indexing ----------
    @staticmethod
    def _split(content: str) -> list[str]:
        parts = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
        merged: list[str] = []
        for p in parts:
            if merged and len(merged[-1]) < 60:       # glue tiny headings to the next paragraph
                merged[-1] += "\n" + p
            else:
                merged.append(p)
        return merged

    def _ensure_index(self) -> None:
        sig = self.kb.signature()
        if sig == self._sig:
            return
        chunks: list[Chunk] = []
        for doc in self.kb.all_documents():
            for part in self._split(doc["content"]):
                toks = tokenize(f"{doc['title']} {doc['city'] or ''} {part}")
                chunks.append(Chunk(doc["id"], doc["title"], doc["city"], part, Counter(toks), len(toks) or 1))
        self._chunks = chunks
        self._df = Counter(t for c in chunks for t in c.tokens)
        self._avg_len = (sum(c.length for c in chunks) / len(chunks)) if chunks else 1.0
        self._sig = sig

    # ---------- search ----------
    def search(self, query: str, city: str | None = None, top_k: int = 4) -> list[dict[str, Any]]:
        self._ensure_index()
        q_tokens = list(dict.fromkeys(tokenize(query)))
        if not q_tokens or not self._chunks:
            return []
        n = len(self._chunks)
        city_l = city.lower() if city else None
        scored: list[tuple[float, Chunk]] = []
        for ch in self._chunks:
            if city_l and ch.city and ch.city.lower() != city_l:
                continue                                  # other city's document
            score, matched = 0.0, 0
            for t in q_tokens:
                tf = ch.tokens.get(t, 0)
                if not tf:
                    continue
                matched += 1
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * ch.length / self._avg_len))
            if city_l and ch.city and ch.city.lower() == city_l:
                score *= 1.5                              # prefer documents about the asked city
            if matched and matched >= math.ceil(len(q_tokens) * MIN_COVERAGE - 1e-9):
                scored.append((score, ch))
        scored.sort(key=lambda x: (-x[0], x[1].doc_id))
        seen, hits = set(), []
        for score, ch in scored:
            if ch.text in seen:
                continue
            seen.add(ch.text)
            hits.append({"doc_id": ch.doc_id, "title": ch.title, "city": ch.city,
                         "score": round(score, 3), "text": ch.text})
            if len(hits) >= top_k:
                break
        return hits
