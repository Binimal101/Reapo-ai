"""
github_access.py — OAuth token lifecycle, ETag-cached blob fetching, and
webhook payload parsing for the GitHub Access Layer (Section 3.1).

All repo reads go through this module.  Nothing in the pipeline touches
the local filesystem or shells out to `git` — everything is GitHub API.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

@dataclass
class GitHubToken:
    """An installation access token from a GitHub App."""
    access_token: str
    expires_at: float          # Unix timestamp
    installation_id: int
    scopes: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - 300)   # 5-min buffer


class TokenProvider(Protocol):
    """
    Abstracts token refresh.  In production this calls the GitHub App
    private-key JWT flow; for tests it returns a static token.
    """
    def get_token(self, installation_id: int) -> GitHubToken: ...


@dataclass
class StaticTokenProvider:
    """Test double — returns a never-expiring token."""
    token: str
    installation_id: int = 1

    def get_token(self, installation_id: int) -> GitHubToken:
        return GitHubToken(
            access_token=self.token,
            expires_at=time.time() + 3600,
            installation_id=installation_id,
            scopes=["contents:read", "contents:write"],
        )


# ---------------------------------------------------------------------------
# GitHub API client abstraction
# ---------------------------------------------------------------------------

class GitHubClient(Protocol):
    """
    Minimal contract for the subset of the GitHub REST API we use.
    Production impl wraps httpx/aiohttp; tests use a fake.
    """

    def get_blob(
        self, owner: str, repo: str, blob_sha: str, *, etag: str | None = None,
    ) -> BlobResponse: ...

    def get_tree(
        self, owner: str, repo: str, tree_sha: str, *, recursive: bool = True,
    ) -> TreeResponse: ...

    def resolve_ref(
        self, owner: str, repo: str, ref: str,
    ) -> RefResponse: ...


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

@dataclass
class BlobResponse:
    """Wraps a GitHub GET /git/blobs/{sha} response."""
    content: bytes             # decoded blob content
    blob_sha: str
    etag: str                  # returned by GitHub, used for cache
    not_modified: bool = False # True when server returned 304
    size: int = 0


@dataclass
class TreeEntry:
    path: str
    mode: str
    sha: str
    type: str                  # "blob" or "tree"
    size: int | None = None


@dataclass
class TreeResponse:
    sha: str
    entries: list[TreeEntry]
    truncated: bool = False


@dataclass
class RefResponse:
    commit_sha: str
    tree_sha: str


# ---------------------------------------------------------------------------
# ETag cache  — keyed by (repo, path, blob_sha) per Section 3.2
# ---------------------------------------------------------------------------

class BlobCache:
    """
    In-memory ETag cache for blob content.

    Cache key: (owner/repo, blob_sha)
    A cache hit means the content hasn't changed — zero API bytes transferred.
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._max = max_entries
        self._store: dict[str, _CacheEntry] = {}

    def get(self, owner: str, repo: str, blob_sha: str) -> _CacheEntry | None:
        key = f"{owner}/{repo}:{blob_sha}"
        return self._store.get(key)

    def put(
        self,
        owner: str,
        repo: str,
        blob_sha: str,
        content: bytes,
        etag: str,
    ) -> None:
        key = f"{owner}/{repo}:{blob_sha}"
        if len(self._store) >= self._max:
            # Evict oldest (FIFO) — good enough; production would use LRU
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
        self._store[key] = _CacheEntry(content=content, etag=etag)

    @property
    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._store), "max": self._max}


@dataclass
class _CacheEntry:
    content: bytes
    etag: str


# ---------------------------------------------------------------------------
# High-level blob fetcher with cache + token refresh
# ---------------------------------------------------------------------------

class CachedBlobFetcher:
    """
    Fetches blob content from GitHub, transparently using the ETag cache.
    Handles token refresh when tokens are near expiry.
    """

    def __init__(
        self,
        client: GitHubClient,
        token_provider: TokenProvider,
        installation_id: int,
        cache: BlobCache | None = None,
    ) -> None:
        self._client = client
        self._token_provider = token_provider
        self._installation_id = installation_id
        self._cache = cache or BlobCache()
        self._token: GitHubToken | None = None
        # Counters for Langfuse span metrics
        self.fetches: int = 0
        self.cache_hits: int = 0

    def _ensure_token(self) -> GitHubToken:
        if self._token is None or self._token.is_expired:
            self._token = self._token_provider.get_token(self._installation_id)
        return self._token

    def fetch_blob(
        self,
        owner: str,
        repo: str,
        blob_sha: str,
    ) -> bytes:
        """
        Fetch a single blob.  Returns raw decoded content.

        1. Check ETag cache → hit = free
        2. Miss → call GitHub API with If-None-Match
        3. 304 → promote cache hit
        4. 200 → store in cache, return content
        """
        self._ensure_token()

        cached = self._cache.get(owner, repo, blob_sha)
        etag = cached.etag if cached else None

        resp = self._client.get_blob(owner, repo, blob_sha, etag=etag)
        self.fetches += 1

        if resp.not_modified and cached is not None:
            self.cache_hits += 1
            return cached.content

        # Fresh content
        self._cache.put(owner, repo, blob_sha, resp.content, resp.etag)
        return resp.content

    def fetch_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
    ) -> TreeResponse:
        self._ensure_token()
        return self._client.get_tree(owner, repo, tree_sha, recursive=True)

    def resolve_ref(self, owner: str, repo: str, ref: str = "HEAD") -> RefResponse:
        self._ensure_token()
        return self._client.resolve_ref(owner, repo, ref)

    def reset_counters(self) -> dict[str, int]:
        """Return and reset fetch counters (for Langfuse span metrics)."""
        stats = {
            "blobs_fetched": self.fetches,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": (
                round(self.cache_hits / self.fetches, 3) if self.fetches else 0.0
            ),
        }
        self.fetches = 0
        self.cache_hits = 0
        return stats


# ---------------------------------------------------------------------------
# Webhook payload parsing (Section 3.1 — push events)
# ---------------------------------------------------------------------------

@dataclass
class WebhookPushEvent:
    """Parsed push webhook payload — drives incremental index updates."""
    repo: str
    owner: str
    ref: str                   # e.g. "refs/heads/main"
    new_tree_sha: str
    changed_files: list[ChangedFile]
    sender: str


@dataclass
class ChangedFile:
    path: str
    status: str                # "added" | "modified" | "removed" | "renamed"
    blob_sha: str | None       # None for removed files
    previous_path: str | None  # set for renames


def parse_push_webhook(payload: dict[str, Any]) -> WebhookPushEvent:
    """
    Parse a GitHub push webhook JSON payload into a structured event.
    """
    repo_data = payload["repository"]
    owner = repo_data["owner"]["login"]
    repo = repo_data["name"]

    changed: list[ChangedFile] = []
    for commit in payload.get("commits", []):
        for path in commit.get("added", []):
            changed.append(ChangedFile(path=path, status="added", blob_sha=None, previous_path=None))
        for path in commit.get("modified", []):
            changed.append(ChangedFile(path=path, status="modified", blob_sha=None, previous_path=None))
        for path in commit.get("removed", []):
            changed.append(ChangedFile(path=path, status="removed", blob_sha=None, previous_path=None))

    # Deduplicate by path, last status wins
    by_path: dict[str, ChangedFile] = {}
    for cf in changed:
        by_path[cf.path] = cf
    changed = list(by_path.values())

    head_commit = payload.get("head_commit", {})

    return WebhookPushEvent(
        repo=repo,
        owner=owner,
        ref=payload.get("ref", ""),
        new_tree_sha=head_commit.get("tree_id", payload.get("after", "")),
        changed_files=changed,
        sender=payload.get("sender", {}).get("login", "unknown"),
    )
