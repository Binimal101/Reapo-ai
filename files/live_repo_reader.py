"""
live_repo_reader.py — Live Call-Graph Resolution (Section 3.3).

Instead of a pre-baked call-graph store, this module resolves call-graphs
on demand by:
  1. Fetching the candidate's blob from GitHub (ETag cached)
  2. AST-walking to extract callees
  3. Looking up callees in the vector index to find their blob_sha + path
  4. Recursively fetching callee blobs up to a depth limit

The output is an in-memory LiveCallGraphNode (Section 6 schema) — never
persisted, always fresh.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from github_access import CachedBlobFetcher
from ast_extraction import extract_symbols, get_function_body, CallInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types — Section 6: Live Call-Graph Node (in-memory, not persisted)
# ---------------------------------------------------------------------------

@dataclass
class LiveCallGraphNode:
    """
    An in-memory call-graph node resolved live from GitHub.
    Matches the design doc Section 6 schema.
    """
    sig_id: str
    body: str
    blob_sha: str
    repo: str
    owner: str
    path: str
    callees: list[LiveCallGraphNode] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    cross_repo_refs: list[str] = field(default_factory=list)
    resolved_at: str = ""
    estimated_tokens: int = 0

    def to_dict(self, max_depth: int = 3, _depth: int = 0) -> dict[str, Any]:
        """Serialize for Langfuse span IO and relevancy agent input."""
        result: dict[str, Any] = {
            "sig_id": self.sig_id,
            "body": self.body,
            "blob_sha": self.blob_sha,
            "repo": self.repo,
            "estimated_tokens": self.estimated_tokens,
        }
        if _depth < max_depth and self.callees:
            result["callees"] = [
                c.to_dict(max_depth, _depth + 1) for c in self.callees
            ]
        elif self.callees:
            result["callees_truncated"] = [c.sig_id for c in self.callees]
        if self.cross_repo_refs:
            result["cross_repo_refs"] = self.cross_repo_refs
        return result


@dataclass
class EnrichedCandidate:
    """
    A vector search hit enriched with its live call-graph.
    This is what gets handed to the Parallel Relevancy System.
    """
    sig_id: str
    score: float
    metadata: dict[str, Any]
    call_graph: LiveCallGraphNode | None


# ---------------------------------------------------------------------------
# Index lookup protocol — the reader needs to resolve callee names to blobs
# ---------------------------------------------------------------------------

class IndexLookup:
    """
    Wraps the vector store to resolve a function name to its index record.
    The Live Repo Reader uses this to find the blob_sha + path for callees
    so it can fetch them from GitHub.
    """

    def __init__(self, vector_store: Any) -> None:
        self._store = vector_store

    def lookup_by_name(
        self,
        name: str,
        repo_hint: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Find an index record by function/qualified name.
        Returns the metadata dict or None.
        """
        # In production this would be a metadata filter query on the vector store.
        # For the in-memory store we do a linear scan.
        for rec in getattr(self._store, "_records", []):
            meta = rec.get("metadata", {})
            if meta.get("name") == name or rec.get("id", "").endswith(f"::{name}"):
                if repo_hint and meta.get("repo") != repo_hint:
                    continue
                return {**meta, "sig_id": rec["id"]}
        # Try qualified name match
        for rec in getattr(self._store, "_records", []):
            meta = rec.get("metadata", {})
            qname = rec.get("id", "").split("::", 1)[-1] if "::" in rec.get("id", "") else ""
            if qname.endswith(f".{name}") or qname == name:
                if repo_hint and meta.get("repo") != repo_hint:
                    continue
                return {**meta, "sig_id": rec["id"]}
        return None


# ---------------------------------------------------------------------------
# Live Repo Reader
# ---------------------------------------------------------------------------

class LiveRepoReader:
    """
    Resolves live call-graphs by fetching blobs from GitHub.

    For each candidate:
      1. Fetch blob content (ETag cached)
      2. Parse to get the function body + callee names
      3. For each callee: look up in index → fetch its blob → parse
      4. Recurse up to depth_limit

    Emits metrics for the Langfuse `live_repo_reader` span.
    """

    def __init__(
        self,
        blob_fetcher: CachedBlobFetcher,
        index_lookup: IndexLookup,
        *,
        depth_limit: int = 3,
    ) -> None:
        self._fetcher = blob_fetcher
        self._index = index_lookup
        self._depth_limit = depth_limit
        # Track visited to handle cycles (Section 7: circular call-graph)
        self._visited: set[str] = set()

    def enrich_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[EnrichedCandidate]:
        """
        Take raw vector search hits, fetch their blobs, resolve call-graphs,
        and return enriched candidates ready for relevancy scoring.
        """
        self._fetcher.reset_counters()
        self._visited.clear()
        t0 = time.monotonic()

        enriched: list[EnrichedCandidate] = []
        for cand in candidates:
            meta = cand.get("metadata", {})
            sig_id = cand.get("sig_id", cand.get("id", ""))
            score = cand.get("score", 0.0)

            try:
                cg_node = self._resolve_node(
                    sig_id=sig_id,
                    owner=meta.get("owner", ""),
                    repo=meta.get("repo", ""),
                    path=meta.get("path", ""),
                    blob_sha=meta.get("blob_sha", ""),
                    qualified_name=sig_id.split("::", 1)[-1] if "::" in sig_id else "",
                    depth=0,
                )
            except Exception as exc:
                logger.warning("Failed to resolve call-graph for %s: %s", sig_id, exc)
                cg_node = None

            enriched.append(EnrichedCandidate(
                sig_id=sig_id,
                score=score,
                metadata=meta,
                call_graph=cg_node,
            ))

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        fetch_stats = self._fetcher.reset_counters()
        logger.info(
            "LiveRepoReader enriched %d candidates: %d blobs fetched, "
            "%d cache hits (%.0f%% hit rate), %dms",
            len(enriched),
            fetch_stats["blobs_fetched"],
            fetch_stats["cache_hits"],
            fetch_stats["cache_hit_rate"] * 100,
            elapsed_ms,
        )
        return enriched

    def _resolve_node(
        self,
        sig_id: str,
        owner: str,
        repo: str,
        path: str,
        blob_sha: str,
        qualified_name: str,
        depth: int,
    ) -> LiveCallGraphNode:
        """Recursively resolve a single call-graph node."""
        # Cycle detection (Section 7)
        if sig_id in self._visited:
            return LiveCallGraphNode(
                sig_id=sig_id, body="# [cycle — already visited]",
                blob_sha=blob_sha, repo=repo, owner=owner, path=path,
            )
        self._visited.add(sig_id)

        # Fetch blob from GitHub (ETag cached)
        content = self._fetcher.fetch_blob(owner, repo, blob_sha)

        # Extract the specific function body
        body = get_function_body(content, qualified_name) or content.decode("utf-8", errors="replace")
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        # Parse to get callee names
        sigs, call_info_map = extract_symbols(
            content, repo=repo, owner=owner, path=path, blob_sha=blob_sha,
        )
        call_info = call_info_map.get(sig_id)
        callee_names = call_info.callee_names if call_info else []

        # Resolve callees recursively up to depth limit
        callee_nodes: list[LiveCallGraphNode] = []
        cross_repo: list[str] = []

        if depth < self._depth_limit:
            for callee_name in callee_names:
                # Skip builtins / common stdlib
                if callee_name in _SKIP_NAMES:
                    continue

                # Look up in index
                callee_meta = self._index.lookup_by_name(callee_name, repo_hint=repo)
                if callee_meta is None:
                    # Try without repo hint (cross-repo)
                    callee_meta = self._index.lookup_by_name(callee_name)
                    if callee_meta and callee_meta.get("repo") != repo:
                        cross_repo.append(f"{callee_meta['repo']}::{callee_name}")

                if callee_meta is None:
                    continue  # unresolved — might be stdlib or dynamic

                try:
                    callee_node = self._resolve_node(
                        sig_id=callee_meta.get("sig_id", f"{callee_meta['repo']}::{callee_name}"),
                        owner=callee_meta.get("owner", owner),
                        repo=callee_meta.get("repo", repo),
                        path=callee_meta.get("path", ""),
                        blob_sha=callee_meta.get("blob_sha", ""),
                        qualified_name=callee_name,
                        depth=depth + 1,
                    )
                    callee_nodes.append(callee_node)
                except Exception as exc:
                    logger.debug("Could not resolve callee %s: %s", callee_name, exc)

        token_estimate = int(len(body.split()) * 1.3)
        return LiveCallGraphNode(
            sig_id=sig_id,
            body=body,
            blob_sha=blob_sha,
            repo=repo,
            owner=owner,
            path=path,
            callees=callee_nodes,
            cross_repo_refs=cross_repo,
            resolved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            estimated_tokens=token_estimate,
        )

    def get_span_metrics(self) -> dict[str, Any]:
        """Return metrics suitable for the Langfuse live_repo_reader span."""
        return self._fetcher.reset_counters()


# Names to skip during callee resolution (builtins, common stdlib)
_SKIP_NAMES = frozenset({
    "print", "len", "range", "int", "str", "float", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "super", "property", "staticmethod", "classmethod",
    "enumerate", "zip", "map", "filter", "sorted", "reversed", "min", "max",
    "sum", "abs", "round", "all", "any", "next", "iter", "id", "hash",
    "repr", "format", "open", "input", "Exception", "ValueError", "TypeError",
    "KeyError", "IndexError", "AttributeError", "RuntimeError", "StopIteration",
    "NotImplementedError", "OSError", "IOError", "FileNotFoundError",
    "append", "extend", "insert", "remove", "pop", "clear", "copy",
    "update", "get", "keys", "values", "items", "join", "split", "strip",
    "replace", "startswith", "endswith", "lower", "upper", "format",
    "encode", "decode",
})
