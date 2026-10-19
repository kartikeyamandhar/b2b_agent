"""Indexing and retrieval over document_chunks (CLAUDE.md invariant 7).

Notices are ranked by Postgres full-text search only (model numbers are tokens, not meanings). Prose documents
(FAQ, corrective action plan, warranty) are ranked by full-text search and by pgvector cosine distance, fused with
reciprocal rank fusion. Results are ordered deterministically and carry stable references (kind#idx), never row
ids, so the agent's prompt replays byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from recallops.models import Document, DocumentChunk
from recallops.retrieval.documents import ProgramDocument, chunk
from recallops.retrieval.embed import Embedder

RetrievalMode = Literal["full", "keyword", "none"]
RRF_K = 60
CANDIDATES = 10
_WORD = re.compile(r"[A-Za-z0-9]+")
KIND_ORDER = {"faq": 0, "cap": 1, "warranty": 2, "notice": 3, "other": 4}


@dataclass(frozen=True)
class Hit:
    ref: str
    kind: str
    text: str
    score: float


def index_program(
    session: Session, firm_id: UUID, program_id: UUID, docs: list[ProgramDocument], embedder: Embedder
) -> int:
    """Replace the program's documents and chunks. Runs in the caller's transaction (tenant or internal)."""
    session.execute(delete(Document).where(Document.firm_id == firm_id, Document.program_id == program_id))
    count = 0
    for doc in docs:
        row = Document(firm_id=firm_id, program_id=program_id, kind=doc.kind, title=doc.title, text=doc.text)
        session.add(row)
        session.flush()
        pieces = chunk(doc.text)
        for idx, (piece, vector) in enumerate(zip(pieces, embedder.embed(pieces), strict=True)):
            session.add(
                DocumentChunk(
                    document_id=row.id,
                    firm_id=firm_id,
                    program_id=program_id,
                    idx=idx,
                    text=piece,
                    embedding=vector,
                    embedding_model=embedder.model_id,
                )
            )
            count += 1
    session.flush()
    return count


def _or_query(query: str) -> str:
    words = sorted({w.lower() for w in _WORD.findall(query) if len(w) > 1})
    return " or ".join(words)


class PgRetriever:
    def __init__(self, session: Session, firm_id: UUID, embedder: Embedder, mode: RetrievalMode = "full") -> None:
        self.session = session
        self.firm_id = firm_id
        self.embedder = embedder
        self.mode = mode

    def _keyword(self, program_id: UUID, query: str) -> list[tuple[str, str, int, str, float]]:
        q = _or_query(query)
        if not q:
            return []
        rows = self.session.execute(
            text(
                "SELECT c.id::text, d.kind, c.idx, c.text, ts_rank(c.search, websearch_to_tsquery('english', :q)) AS r "
                "FROM document_chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.firm_id = :f AND c.program_id = :p AND c.search @@ websearch_to_tsquery('english', :q) "
                "ORDER BY r DESC, d.kind, c.idx LIMIT :n"
            ),
            {"q": q, "f": self.firm_id, "p": program_id, "n": CANDIDATES},
        ).all()
        return [(r[0], r[1], int(r[2]), r[3], float(r[4])) for r in rows]

    def _vector(self, program_id: UUID, query: str) -> list[tuple[str, str, int, str, float]]:
        vector = self.embedder.embed([query])[0]
        literal = "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
        rows = self.session.execute(
            text(
                "SELECT c.id::text, d.kind, c.idx, c.text, 1 - (c.embedding <=> CAST(:v AS vector)) AS s "
                "FROM document_chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.firm_id = :f AND c.program_id = :p AND d.kind <> 'notice' "
                "ORDER BY c.embedding <=> CAST(:v AS vector), d.kind, c.idx LIMIT :n"
            ),
            {"v": literal, "f": self.firm_id, "p": program_id, "n": CANDIDATES},
        ).all()
        return [(r[0], r[1], int(r[2]), r[3], float(r[4])) for r in rows]

    def search(self, program_id: UUID, query: str, k: int = 3) -> list[Hit]:
        if self.mode == "none":
            return []
        lists = [self._keyword(program_id, query)]
        if self.mode == "full":
            lists.append(self._vector(program_id, query))
        fused: dict[str, float] = {}
        meta: dict[str, tuple[str, int, str]] = {}
        for ranked in lists:
            for rank, (cid, kind, idx, body, _) in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
                meta[cid] = (kind, idx, body)
        order = sorted(fused, key=lambda c: (-round(fused[c], 9), KIND_ORDER.get(meta[c][0], 9), meta[c][1]))
        return [
            Hit(ref=f"{meta[c][0]}#{meta[c][1]}", kind=meta[c][0], text=meta[c][2], score=round(fused[c], 6))
            for c in order[:k]
        ]
