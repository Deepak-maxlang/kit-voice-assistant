"""Document knowledge base: downloads the college PDFs, chunks them and does BM25 retrieval.

* Documents are re-listed every ``docs_refresh_seconds`` so surprise circulars appear automatically.
* Superseded handbooks (older effective_date of the same type) are excluded from search.
* Circulars get a ranking boost because the latest circular prevails over the handbook (Rule 1.4).
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

from pypdf import PdfReader

log = logging.getLogger(__name__)
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")
_STOP = set("the a an of to and or in on for is are be by with as at from this that it its any not no may must "
            "can will shall who what when which how do does i my me you your".split())
_BOOST = {"circular": 1.5, "handbook": 1.0, "brochure": 1.1, "lesson_notes": 1.0}


def _norm(token: str) -> str:
    """Light normalisation: US->UK spelling (docs use 'normalisation') and crude plural stripping."""
    token = token.replace("ization", "isation").replace("izing", "ising").replace("ize", "ise")
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [_norm(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Chunk:
    doc_id: int
    title: str
    doc_type: str
    effective_date: str
    text: str
    tokens: list[str]


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n(?=\S{1,20}\n)", " ", text)  # re-join words the PDF split one-per-line
    text = re.sub(r"\n{2,}", "\n", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            end = cut if cut > 0 else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def active_documents(docs: list[dict], today: str | None = None) -> list[dict]:
    """Drop not-yet-effective docs and older editions of handbooks/brochures."""
    usable = [d for d in docs if not today or (d.get("effective_date") or "") <= today]
    latest: dict[str, str] = {}
    for d in usable:
        if d.get("type") in ("handbook", "brochure"):
            latest[d["type"]] = max(latest.get(d["type"], ""), d.get("effective_date") or "")
    return [d for d in usable
            if d.get("type") not in latest or (d.get("effective_date") or "") == latest[d["type"]]]


class KnowledgeBase:
    def __init__(self, kit_client=None):
        self.kit = kit_client
        self.documents: list[dict] = []
        self.chunks: list[Chunk] = []
        self._loaded: dict[str, list[Chunk]] = {}
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._lock = asyncio.Lock()
        self.last_refresh_ok: bool | None = None

    def add_document(self, meta: dict, text: str) -> None:
        key = f"{meta['id']}:{meta.get('version')}"
        self._loaded[key] = [Chunk(meta["id"], meta.get("title", ""), meta.get("type", ""),
                                   meta.get("effective_date", ""), c, tokenize(c)) for c in chunk_text(text)]

    def _reindex(self, active: list[dict]) -> None:
        keys = {f"{d['id']}:{d.get('version')}" for d in active}
        self.chunks = [c for k, cs in self._loaded.items() if k in keys for c in cs]
        self._df = Counter()
        for c in self.chunks:
            self._df.update(set(c.tokens))
        self._avgdl = (sum(len(c.tokens) for c in self.chunks) / len(self.chunks)) if self.chunks else 1.0

    async def refresh(self, today: str | None = None) -> None:
        async with self._lock:
            try:
                docs = await self.kit.documents()
                active = active_documents(docs, today)
                for d in active:
                    key = f"{d['id']}:{d.get('version')}"
                    if key in self._loaded:
                        continue
                    pdf = await self.kit.document_file(d["id"])
                    reader = PdfReader(io.BytesIO(pdf))
                    self.add_document(d, "\n".join(p.extract_text() or "" for p in reader.pages))
                    log.info("Loaded document %s (%s)", d.get("title"), key)
                self.documents = docs
                self._reindex(active)
                self.last_refresh_ok = True
            except Exception as exc:  # keep serving the previous index
                log.warning("Document refresh failed: %s", exc)
                self.last_refresh_ok = False

    def load_static(self, docs: list[tuple[dict, str]]) -> None:
        """Used by tests: index (meta, text) pairs without network access."""
        for meta, text in docs:
            self.add_document(meta, text)
        self.documents = [m for m, _ in docs]
        self._reindex(active_documents(self.documents))

    def search(self, query: str, k: int = 4, doc_types: set[str] | None = None) -> list[dict]:
        q = tokenize(query)
        if not q or not self.chunks:
            return []
        n = len(self.chunks)
        scored = []
        for c in self.chunks:
            if doc_types and c.doc_type not in doc_types:
                continue
            tf = Counter(c.tokens)
            dl = len(c.tokens) or 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * dl / self._avgdl))
            if s > 0:
                scored.append((s * _BOOST.get(c.doc_type, 1.0), c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"source": c.title, "type": c.doc_type, "effective_date": c.effective_date,
                 "text": c.text[:900]} for _, c in scored[:k]]

    def catalogue(self) -> list[dict]:
        return [{"id": d["id"], "title": d.get("title"), "type": d.get("type"),
                 "effective_date": d.get("effective_date"), "is_new": d.get("is_surprise", False)}
                for d in self.documents]
