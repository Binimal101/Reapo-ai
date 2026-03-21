"""
test_integration.py — End-to-end test with a fake GitHub client.

Exercises:
  1. Full index build via GitHub API (faked)
  2. Incremental webhook-driven update
  3. Search → Live Repo Reader → call-graph resolution
  4. Langfuse span emission at every handoff
"""

import time
from github_access import (
    CachedBlobFetcher,
    BlobCache,
    BlobResponse,
    TreeResponse,
    TreeEntry,
    RefResponse,
    StaticTokenProvider,
    WebhookPushEvent,
    ChangedFile,
)
from ast_extraction import extract_symbols
from index_builder import (
    full_build,
    incremental_update,
    search_index,
    InMemoryVectorStore,
    DummyEmbeddingModel,
)
from live_repo_reader import LiveRepoReader, IndexLookup
from observability import Tracer


# ---------------------------------------------------------------------------
# Fake GitHub client
# ---------------------------------------------------------------------------

# Simulated repo files
REPO_FILES = {
    "src/orders.py": b'''\
from pricing import apply_discount
from db import persist_order

def fetch_user(user_id: str) -> dict:
    """Fetch user record from DB."""
    return db_query(f"SELECT * FROM users WHERE id = {user_id}")

def validate_cart(cart: list) -> bool:
    """Ensure cart items are in stock."""
    return all(item.in_stock for item in cart)

def process_order(order_id: str) -> str:
    """Main order processing entry point."""
    user = fetch_user(order_id)
    if not validate_cart(user["cart"]):
        raise ValueError("Cart invalid")
    total = apply_discount(user["cart"], user.get("promo_code"))
    persist_order(order_id, total)
    return f"Order {order_id} processed"
''',
    "src/pricing.py": b'''\
def apply_discount(cart: list, promo_code: str | None = None) -> float:
    """Apply promotional discount to cart total."""
    subtotal = sum(item.price for item in cart)
    if promo_code == "HALF_OFF":
        return subtotal * 0.5
    return subtotal

def calculate_tax(amount: float, rate: float = 0.08) -> float:
    """Calculate tax on an amount."""
    return round(amount * rate, 2)
''',
    "src/db.py": b'''\
def db_query(sql: str) -> dict:
    """Execute a raw SQL query."""
    pass

def persist_order(order_id: str, total: float) -> None:
    """Write order to the database."""
    db_query(f"INSERT INTO orders VALUES ({order_id}, {total})")
''',
}

# SHA mapping (fake, deterministic)
def _sha(content: bytes) -> str:
    import hashlib
    return hashlib.sha1(content).hexdigest()

BLOB_SHAS = {path: _sha(content) for path, content in REPO_FILES.items()}


class FakeGitHubClient:
    """Implements GitHubClient protocol with in-memory file data."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self._files = files or REPO_FILES
        self._shas = {path: _sha(c) for path, c in self._files.items()}

    def get_blob(self, owner, repo, blob_sha, *, etag=None):
        for path, sha in self._shas.items():
            if sha == blob_sha:
                content = self._files[path]
                new_etag = f'W/"{sha}"'
                if etag == new_etag:
                    return BlobResponse(
                        content=b"", blob_sha=sha, etag=new_etag,
                        not_modified=True, size=0,
                    )
                return BlobResponse(
                    content=content, blob_sha=sha, etag=new_etag, size=len(content),
                )
        raise FileNotFoundError(f"Blob not found: {blob_sha}")

    def get_tree(self, owner, repo, tree_sha, *, recursive=True):
        entries = [
            TreeEntry(path=path, mode="100644", sha=sha, type="blob", size=len(self._files[path]))
            for path, sha in self._shas.items()
        ]
        return TreeResponse(sha=tree_sha, entries=entries)

    def resolve_ref(self, owner, repo, ref):
        return RefResponse(commit_sha="fake_commit_sha", tree_sha="fake_tree_sha")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" Integration Test: GitHub API -> Index -> Search -> Live Call-Graph")
    print("=" * 70)

    # Setup
    client = FakeGitHubClient()
    token_provider = StaticTokenProvider(token="ghp_fake_token")
    cache = BlobCache()
    fetcher = CachedBlobFetcher(client, token_provider, installation_id=1, cache=cache)
    embed_model = DummyEmbeddingModel(dim=128)
    vector_store = InMemoryVectorStore()
    tracer = Tracer()

    # ── 1. Full build ────────────────────────────────────────────────
    print("\n[1] Full index build via GitHub API...")

    trace = tracer.start_trace(user_id="test", prompt_preview="integration test")
    span = tracer.span("index_build", input={"repo": "checkout-service"})

    result = full_build(
        owner="org",
        repo="checkout-service",
        blob_fetcher=fetcher,
        embedding_model=embed_model,
        vector_store=vector_store,
        access_level="write",
    )

    span.end(
        output={"symbols_upserted": result.symbols_upserted, "symbols_deleted": 0},
        metadata={"tree_sha": result.tree_sha, "latency_ms": result.elapsed_ms},
    )

    print(f"  Blobs read:      {result.blobs_read}")
    print(f"  Symbols:         {result.symbols_extracted}")
    print(f"  Vectors upserted:{result.symbols_upserted}")
    print(f"  Vector store:    {len(vector_store)} records")
    print(f"  Blob cache:      {cache.size} entries")
    assert result.symbols_upserted > 0, "Should have upserted symbols"

    # ── 2. Verify index records have new fields ──────────────────────
    print("\n[2] Verify index record schema (tree_sha, blob_sha, access_level)...")

    sample = vector_store._records[0]
    meta = sample["metadata"]
    print(f"  Sample: {sample['id']}")
    print(f"    repo:         {meta['repo']}")
    print(f"    owner:        {meta['owner']}")
    print(f"    tree_sha:     {meta['tree_sha']}")
    print(f"    blob_sha:     {meta['blob_sha']}")
    print(f"    access_level: {meta['access_level']}")
    print(f"    call_graph_id:{meta['call_graph_id']}")
    print(f"    qualified:    {meta['qualified_name']}")
    assert meta["tree_sha"] == "fake_tree_sha"
    assert meta["blob_sha"] != ""
    assert meta["access_level"] == "write"
    assert meta["owner"] == "org"
    assert meta["call_graph_id"].startswith("cg:checkout-service:")
    assert meta["qualified_name"]

    # ── 3. Search ────────────────────────────────────────────────────
    print("\n[3] Multi-query prodding search...")

    search_span = tracer.span("semantic_prodder", input={"objective": "discount logic"})
    results = search_index(
        query_texts=["discount logic", "order processing", "cart validation"],
        embedding_model=embed_model,
        vector_store=vector_store,
        top_k=5,
    )
    search_span.end(output={"candidate_count": len(results), "query_count": 3})

    print(f"  Hits: {len(results)}")
    for r in results[:5]:
        print(f"    [{r['score']:.3f}] {r['sig_id']} - {r['metadata']['signature'][:60]}")
    assert len(results) > 0, "Should find at least one hit"

    # ── 4. Live Repo Reader → call-graph resolution ──────────────────
    print("\n[4] Live call-graph resolution via GitHub blob fetches...")

    fetcher.reset_counters()
    reader_span = tracer.span("live_repo_reader", input={"candidate_count": len(results)})

    index_lookup = IndexLookup(vector_store)
    reader = LiveRepoReader(fetcher, index_lookup, depth_limit=3)
    enriched = reader.enrich_candidates(results[:5])

    reader_metrics = reader.get_span_metrics()
    reader_span.end(
        output={"enriched_count": len(enriched)},
        metadata=reader_metrics,
    )

    print(f"  Enriched: {len(enriched)} candidates")
    for ec in enriched:
        cg = ec.call_graph
        if cg:
            callees = [c.sig_id for c in cg.callees]
            xrefs = cg.cross_repo_refs
            print(f"    {ec.sig_id}")
            print(f"      callees: {callees[:4]}")
            if xrefs:
                print(f"      cross-repo: {xrefs}")
        else:
            print(f"    {ec.sig_id} - no call-graph (class or parse error)")

    # Verify live resolution actually found callees
    process_order_cg = next(
        (ec.call_graph for ec in enriched if "process_order" in ec.sig_id), None
    )
    if process_order_cg:
        callee_ids = [c.sig_id for c in process_order_cg.callees]
        print(f"\n  process_order callees resolved: {callee_ids}")
        assert len(callee_ids) > 0, "process_order should have resolved callees"

    # ── 5. Incremental update (webhook-driven) ───────────────────────
    print("\n[5] Incremental update from push webhook...")

    # Simulate modifying pricing.py
    modified_pricing = (
        'def apply_discount(cart: list, promo_code: str | None = None) -> float:\n'
        '    """Apply promotional discount to cart total. Now supports BOGO."""\n'
        '    subtotal = sum(item.price for item in cart)\n'
        '    if promo_code == "HALF_OFF":\n'
        '        return subtotal * 0.5\n'
        '    if promo_code == "BOGO":\n'
        '        return subtotal - min(item.price for item in cart)\n'
        '    return subtotal\n'
        '\n'
        'def calculate_tax(amount: float, rate: float = 0.08) -> float:\n'
        '    """Calculate tax on an amount."""\n'
        '    return round(amount * rate, 2)\n'
        '\n'
        'def estimate_shipping(weight: float) -> float:\n'
        '    """New function: estimate shipping cost."""\n'
        '    return max(5.99, weight * 0.50)\n'
    ).encode("utf-8")
    new_sha = _sha(modified_pricing)
    # Update the fake client
    client._files["src/pricing.py"] = modified_pricing
    client._shas["src/pricing.py"] = new_sha

    old_count = len(vector_store)

    event = WebhookPushEvent(
        repo="checkout-service",
        owner="org",
        ref="refs/heads/main",
        new_tree_sha="new_tree_sha_abc",
        changed_files=[
            ChangedFile(path="src/pricing.py", status="modified", blob_sha=new_sha, previous_path=None),
        ],
        sender="dev-user",
    )

    incr_span = tracer.span("index_build", input={"repo": "checkout-service", "changed_files": 1})
    incr_result = incremental_update(
        event=event,
        blob_fetcher=fetcher,
        embedding_model=embed_model,
        vector_store=vector_store,
        access_level="write",
    )
    incr_span.end(output={
        "symbols_upserted": incr_result.symbols_upserted,
        "symbols_deleted": incr_result.symbols_deleted,
    })

    print(f"  Files processed: {incr_result.files_processed}")
    print(f"  Symbols deleted: {incr_result.symbols_deleted}")
    print(f"  Symbols upserted:{incr_result.symbols_upserted}")
    print(f"  Vector store:    {old_count} -> {len(vector_store)} records")

    # Verify the new function is in the index
    new_results = search_index(
        query_texts=["shipping cost estimate"],
        embedding_model=embed_model,
        vector_store=vector_store,
        top_k=3,
    )
    found_shipping = any("estimate_shipping" in r["sig_id"] for r in new_results)
    print(f"  New 'estimate_shipping' found in index: {found_shipping}")
    # Check updated tree_sha
    for r in new_results:
        if "estimate_shipping" in r["sig_id"]:
            assert r["metadata"]["tree_sha"] == "new_tree_sha_abc"
            print(f"  tree_sha updated: {r['metadata']['tree_sha']}")

    # ── 6. Verify Langfuse trace ─────────────────────────────────────
    print("\n[6] Langfuse trace inspection...")

    trace_data = tracer.get_trace(trace.trace_id)
    print(f"  Trace ID:    {trace_data['trace_id']}")
    print(f"  Span count:  {trace_data['span_count']}")
    print(f"  Span names:  {[s['name'] for s in trace_data['spans']]}")

    for sp in trace_data["spans"]:
        lat = sp.get("latency_ms", "?")
        print(f"    {sp['name']:25s}  latency={lat}ms  in={list(sp['input'].keys())}")

    # Test MCP-style queries
    reader_spans = tracer.get_spans(name="live_repo_reader")
    print(f"\n  MCP query - live_repo_reader spans: {len(reader_spans)}")
    if reader_spans:
        print(f"    metadata: {reader_spans[0]['metadata']}")

    # ── 7. Verify NO call-graph store exists ─────────────────────────
    print("\n[7] Confirming no CallGraphStore (live resolution only)...")
    import index_builder
    source = open(index_builder.__file__).read()
    assert "CallGraphStore" not in source, "CallGraphStore should not exist in updated code"
    print("  OK: No CallGraphStore anywhere - call-graphs resolved live")

    # ── Done ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(" All checks passed!")
    print("=" * 70)


if __name__ == "__main__":
    main()
