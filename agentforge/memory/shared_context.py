"""Shared context backed by an embedded vector DB (ChromaDB).

Every agent is a *stateless function* — the only state in AgentForge lives in
the :class:`SharedContext`. The shared context offers three views:

1. A structured key/value store (for specs, module plans, artifacts).
2. A vector collection for natural-language memory / retrieval.
3. An append-only audit trail of agent events.

ChromaDB is used where available; when it is not installed (e.g. slim CI
container), we transparently fall back to an in-memory implementation that
supports the same API surface used by AgentForge. This keeps the test matrix
simple while preserving the production code path.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from agentforge.logging import get_logger
from agentforge.models import AuditEntry

logger = get_logger("memory")


class _InMemoryCollection:
    """Trivial substitute for a Chroma collection used when chromadb is not
    importable or the caller passed `persist_dir=None`.
    """

    def __init__(self) -> None:
        self._docs: dict[str, tuple[str, dict[str, Any]]] = {}

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        metas = metadatas or [{} for _ in ids]
        for i, doc, meta in zip(ids, documents, metas, strict=True):
            self._docs[i] = (doc, meta)

    def query(self, query_texts: list[str], n_results: int = 5) -> dict[str, Any]:
        # Naive ranking: return the most recent documents that contain any
        # word from the query (case-insensitive). Good enough for tests and
        # for the mock provider; production users will have chromadb.
        query = query_texts[0].lower() if query_texts else ""
        tokens = {t for t in query.split() if t}
        scored: list[tuple[int, str, str, dict[str, Any]]] = []
        for idx, (doc_id, (doc, meta)) in enumerate(self._docs.items()):
            score = sum(1 for t in tokens if t in doc.lower())
            scored.append((score + idx / 1e6, doc_id, doc, meta))
        scored.sort(reverse=True)
        top = scored[:n_results]
        return {
            "ids": [[s[1] for s in top]],
            "documents": [[s[2] for s in top]],
            "metadatas": [[s[3] for s in top]],
        }

    def get(self, ids: list[str] | None = None) -> dict[str, Any]:
        items = self._docs.items() if ids is None else [(i, self._docs[i]) for i in ids if i in self._docs]
        return {
            "ids": [i for i, _ in items],
            "documents": [v[0] for _, v in items],
            "metadatas": [v[1] for _, v in items],
        }


def _load_chroma(persist_dir: Path | None):
    """Try to construct a persistent Chroma collection; fall back on failure."""
    if persist_dir is None:
        return None
    try:
        import chromadb  # type: ignore
        from chromadb.config import Settings  # type: ignore

        persist_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        return client.get_or_create_collection("agentforge")
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("ChromaDB unavailable, using in-memory fallback: %s", exc)
        return None


class SharedContext:
    """Thread-safe shared state for the agent pipeline."""

    def __init__(self, persist_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._kv: dict[str, Any] = {}
        self._audit: list[AuditEntry] = []
        self._collection = _load_chroma(persist_dir)
        if self._collection is None:
            self._collection = _InMemoryCollection()
        self._using_chroma = not isinstance(self._collection, _InMemoryCollection)

    # --- Key/value store ---------------------------------------------------

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._kv[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._kv.get(key, default)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._kv.keys())

    # --- Vector memory -----------------------------------------------------

    def remember(self, text: str, *, metadata: dict[str, Any] | None = None) -> str:
        """Store a free-form memory and return the generated id."""
        doc_id = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        with self._lock:
            self._collection.add(
                ids=[doc_id],
                documents=[text],
                metadatas=[metadata or {}],
            )
        return doc_id

    def recall(self, query: str, *, top_k: int = 5) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            result = self._collection.query(query_texts=[query], n_results=top_k)
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        return list(zip(docs, metas, strict=False))

    # --- Audit trail -------------------------------------------------------

    def audit(self, actor: str, event: str, payload: dict[str, Any] | None = None) -> AuditEntry:
        entry = AuditEntry(actor=actor, event=event, payload=payload or {})
        with self._lock:
            self._audit.append(entry)
        logger.debug("audit %s/%s %s", actor, event, payload or {})
        return entry

    def audit_trail(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._audit)

    def dump_audit(self, path: Path) -> Path:
        with self._lock:
            entries = [e.model_dump() for e in self._audit]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2))
        return path

    # --- Introspection -----------------------------------------------------

    @property
    def backend(self) -> str:
        return "chromadb" if self._using_chroma else "in-memory"
