"""
index_builder.py — Hybrid RAG Index builder (Section 3.2).

Two modes:
  1. Full build  — walk a repo tree via GitHub API, embed all signatures.
  2. Incremental — process a push webhook, re-embed only changed files.

Key design changes from the old local-git approach:
  - NO call-graph store.  Call-graphs resolved live by LiveRepoReader.
  - All blob content fetched via GitHub API (ETag cached).
  - Every index record carries tree_sha, blob_sha, access_level.
  - Incremental path driven by webhook payloads, not `git diff-tree`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from github_access import (
    CachedBlobFetcher,
    WebhookPushEvent,
    TreeEntry,
)
from ast_extraction import Signature, extract_symbols

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstractions
# ---------------------------------------------------------------------------

class EmbeddingModel(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimension(self) -> int: ...


class VectorStore(Protocol):
    def upsert(self, records: list[dict[str, Any]]) -> None: ...
    def delete(self, ids: list[str]) -> None: ...
    def search(self, vector: list[float], top_k: int = 20, filter: dict | None = None) -> list[dict]: ...


# ---------------------------------------------------------------------------
# In-memory implementations (test / small repos)
# ---------------------------------------------------------------------------

class InMemoryVectorStore:
    """Brute-force cosine store.  Replace with Qdrant / Pinecone in prod."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def upsert(self, records: list[dict[str, Any]]) -> None:
        existing_ids = {r["id"] for r in self._records}
        for rec in records:
            if rec["id"] in existing_ids:
                self._records = [r for r in self._records if r["id"] != rec["id"]]
            self._records.append(rec)

    def delete(self, ids: list[str]) -> None:
        id_set = set(ids)
        self._records = [r for r in self._records if r["id"] not in id_set]

    def search(
        self,
        vector: list[float],
        top_k: int = 20,
        filter: dict | None = None,
    ) -> list[dict]:
        import math

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb) if na and nb else 0.0

        scored = []
        for rec in self._records:
            if filter:
                meta = rec.get("metadata", {})
                if not all(meta.get(k) == v for k, v in filter.items()):
                    continue
            score = cosine(vector, rec["vector"])
            scored.append((score, rec))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [{**r, "score": s} for s, r in scored[:top_k]]

    def __len__(self) -> int:
        return len(self._records)


class DummyEmbeddingModel:
    """Deterministic hash-based fake embeddings for testing."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        results = []
        for text in texts:
            h = hashlib.sha256(text.encode()).digest()
            raw = h * ((self._dim // len(h)) + 1)
            vec = [((b / 255.0) * 2 - 1) for b in raw[: self._dim]]
            norm = sum(x * x for x in vec) ** 0.5
            results.append([x / norm for x in vec])
        return results


# ---------------------------------------------------------------------------
# Index record builder — Section 6 schema
# ---------------------------------------------------------------------------

def _make_index_record(
    sig: Signature,
    vector: list[float],
    *,
    tree_sha: str,
    access_level: str = "read",
) -> dict[str, Any]:
    """Build a Vector DB record matching the updated Section 6 schema."""
    return {
        "id": sig.composite_key,
        "vector": vector,
        "metadata": {
            "repo": sig.repo,
            "owner": sig.owner,
            "path": sig.path,
            "kind": sig.kind,
            "name": sig.name,
            "signature": sig.signature_text,
            "docstring": sig.docstring or "",
            "tree_sha": tree_sha,          # staleness detection
            "blob_sha": sig.blob_sha or "", # ETag cache key
            "access_level": access_level,   # from GitHub App scope
        },
    }


# ---------------------------------------------------------------------------
# Full build — walk tree via GitHub API
# ---------------------------------------------------------------------------

@dataclass
class IndexBuildResult:
    repo: str = ""
    owner: str = ""
    tree_sha: str = ""
    blobs_read: int = 0
    symbols_extracted: int = 0
    symbols_upserted: int = 0
    elapsed_ms: int = 0


PYTHON_EXTENSIONS = frozenset({".py"})


def full_build(
    owner: str,
    repo: str,
    blob_fetcher: CachedBlobFetcher,
    embedding_model: EmbeddingModel,
    vector_store: VectorStore,
    *,
    ref: str = "HEAD",
    extensions: frozenset[str] = PYTHON_EXTENSIONS,
    access_level: str = "read",
    embed_batch_size: int = 64,
) -> IndexBuildResult:
    """
    Full index build for a single repo by walking the tree via GitHub API.

    1. Resolve ref → tree SHA
    2. GET tree (recursive)
    3. For each blob matching extensions: fetch, parse, collect signatures
    4. Batch embed
    5. Upsert to vector store
    """
    t0 = time.monotonic()
    result = IndexBuildResult(repo=repo, owner=owner)

    # Resolve ref
    ref_resp = blob_fetcher.resolve_ref(owner, repo, ref)
    tree_sha = ref_resp.tree_sha
    result.tree_sha = tree_sha

    # Walk tree
    tree_resp = blob_fetcher.fetch_tree(owner, repo, tree_sha)
    blob_entries = [
        e for e in tree_resp.entries
        if e.type == "blob" and _has_extension(e.path, extensions)
    ]

    # Parse all blobs
    all_sigs: list[Signature] = []
    for entry in blob_entries:
        result.blobs_read += 1
        content = blob_fetcher.fetch_blob(owner, repo, entry.sha)
        sigs, _ = extract_symbols(
            content, repo=repo, owner=owner, path=entry.path, blob_sha=entry.sha,
        )
        all_sigs.extend(sigs)

    result.symbols_extracted = len(all_sigs)

    # Batch embed
    if not all_sigs:
        result.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return result

    embedding_inputs = [sig.embedding_input for sig in all_sigs]
    all_vectors: list[list[float]] = []
    for i in range(0, len(embedding_inputs), embed_batch_size):
        batch = embedding_inputs[i : i + embed_batch_size]
        all_vectors.extend(embedding_model.embed(batch))

    # Upsert
    records = [
        _make_index_record(sig, vec, tree_sha=tree_sha, access_level=access_level)
        for sig, vec in zip(all_sigs, all_vectors)
    ]
    vector_store.upsert(records)
    result.symbols_upserted = len(records)

    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "Full build %s/%s: %d blobs → %d symbols → %d vectors (%dms)",
        owner, repo, result.blobs_read, result.symbols_extracted,
        result.symbols_upserted, result.elapsed_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Incremental build — driven by webhook push events (Section 3.1 / 3.2)
# ---------------------------------------------------------------------------

@dataclass
class IncrementalResult:
    repo: str = ""
    owner: str = ""
    new_tree_sha: str = ""
    files_processed: int = 0
    symbols_upserted: int = 0
    symbols_deleted: int = 0
    elapsed_ms: int = 0


def incremental_update(
    event: WebhookPushEvent,
    blob_fetcher: CachedBlobFetcher,
    embedding_model: EmbeddingModel,
    vector_store: VectorStore,
    *,
    extensions: frozenset[str] = PYTHON_EXTENSIONS,
    access_level: str = "read",
) -> IncrementalResult:
    """
    Process a push webhook event: re-embed changed files, delete removed files.

    Driven by WebhookPushEvent — no local git needed.
    """
    t0 = time.monotonic()
    result = IncrementalResult(
        repo=event.repo,
        owner=event.owner,
        new_tree_sha=event.new_tree_sha,
    )

    ids_to_delete: list[str] = []
    new_sigs: list[Signature] = []

    for cf in event.changed_files:
        if not _has_extension(cf.path, extensions):
            continue
        result.files_processed += 1

        if cf.status == "removed":
            # We need to find which sig_ids were in this file.
            # Query the vector store for records matching this repo + path.
            # In production, the vector store supports metadata filtering.
            ids_to_delete.extend(
                _find_ids_by_path(vector_store, event.repo, cf.path)
            )
        elif cf.status in ("added", "modified"):
            # Delete old entries for this path first
            ids_to_delete.extend(
                _find_ids_by_path(vector_store, event.repo, cf.path)
            )
            # Fetch new blob content and parse
            if cf.blob_sha:
                blob_sha = cf.blob_sha
            else:
                # blob_sha not in webhook payload — resolve from tree
                tree_resp = blob_fetcher.fetch_tree(event.owner, event.repo, event.new_tree_sha)
                entry = next((e for e in tree_resp.entries if e.path == cf.path), None)
                if entry is None:
                    continue
                blob_sha = entry.sha

            content = blob_fetcher.fetch_blob(event.owner, event.repo, blob_sha)
            sigs, _ = extract_symbols(
                content,
                repo=event.repo,
                owner=event.owner,
                path=cf.path,
                blob_sha=blob_sha,
            )
            new_sigs.extend(sigs)

    # Delete old
    if ids_to_delete:
        vector_store.delete(ids_to_delete)
        result.symbols_deleted = len(ids_to_delete)

    # Embed and upsert new
    if new_sigs:
        texts = [sig.embedding_input for sig in new_sigs]
        vectors = embedding_model.embed(texts)
        records = [
            _make_index_record(
                sig, vec,
                tree_sha=event.new_tree_sha,
                access_level=access_level,
            )
            for sig, vec in zip(new_sigs, vectors)
        ]
        vector_store.upsert(records)
        result.symbols_upserted = len(records)

    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "Incremental %s/%s: %d files → +%d -%d symbols (%dms)",
        event.owner, event.repo, result.files_processed,
        result.symbols_upserted, result.symbols_deleted, result.elapsed_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Query-time helpers (used by Research Pipeline — Section 3.3)
# ---------------------------------------------------------------------------

def search_index(
    query_texts: list[str],
    embedding_model: EmbeddingModel,
    vector_store: VectorStore,
    *,
    top_k: int = 20,
    repo_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Multi-query prodding search (Section 3.2 / 3.3).

    Embeds multiple query strings, searches, deduplicates.
    Returns raw hits — the Live Repo Reader enriches them with call-graphs.
    """
    seen_ids: set[str] = set()
    results: list[dict[str, Any]] = []

    filt = {"repo": repo_filter} if repo_filter else None

    for text in query_texts:
        vec = embedding_model.embed([text])[0]
        hits = vector_store.search(vec, top_k=top_k, filter=filt)
        for hit in hits:
            sig_id = hit["id"]
            if sig_id in seen_ids:
                continue
            seen_ids.add(sig_id)
            results.append({
                "sig_id": sig_id,
                "score": hit.get("score", 0.0),
                "metadata": hit["metadata"],
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_extension(path: str, extensions: frozenset[str]) -> bool:
    import os
    _, ext = os.path.splitext(path)
    return ext in extensions


def _find_ids_by_path(store: VectorStore, repo: str, path: str) -> list[str]:
    """Find all sig_ids in the store for a given repo + path."""
    ids: list[str] = []
    # In-memory store — linear scan.  Production store uses metadata filter.
    for rec in getattr(store, "_records", []):
        meta = rec.get("metadata", {})
        if meta.get("repo") == repo and meta.get("path") == path:
            ids.append(rec["id"])
    return ids
