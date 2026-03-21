# Local Repository Indexing (MVP)

This guide documents local indexing with production defaults and the expected I/O artifacts.

## Production Behavior

- The indexer uses repository `.gitignore` rules during local file discovery.
- The indexer always excludes high-risk scan paths even if `.gitignore` tries to re-include them:
  - `__pycache__`
  - `.venv`
  - `venv`
  - `*venv*`
  - `.git`
  - `node_modules`
- Default backends come from environment (`.env`): embedding backend + observability backend (Langfuse expected in production).

## Run Command (Production Defaults)

```powershell
cd apps/worker-indexer-py/src

python -m ast_indexer index `
  --workspace-root "C:\path\to\parent" `
  --repo "repo-folder-name" `
  --state-root "C:\path\to\state-root"
```

## Tested Example (This Workspace)

```powershell
cd apps/worker-indexer-py/src

python -m ast_indexer index `
  --workspace-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\apps" `
  --repo "worker-indexer-py" `
  --state-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\test_indexer_state_langfuse_default"
```

Observed result fields included:

- `status: "ok"`
- `files_scanned: 60`
- `symbols_indexed: 323`
- `vectors_upserted: 323`
- `linked_edges: 104`
- `unresolved_edges: 942`
- `spans_file: .../observability/spans.jsonl`

## Arguments

- `--workspace-root`: Base folder containing the target repo directory.
- `--repo`: Folder name of the target repository under `workspace-root`.
- `--state-root`: Output folder for index artifacts and observability spans.

## Test-Only Overrides

Use these only for deterministic local test runs in `tests/` or isolated smoke checks:

```powershell
python -m ast_indexer index `
  --workspace-root "..." `
  --repo "..." `
  --state-root "..." `
  --embedding-backend hash `
  --observability-backend jsonl
```

## Input/Output Contract

### Input

The indexer recursively scans `workspace-root/repo/` for `.py` files after applying:

- hard default excludes,
- repository `.gitignore` patterns.

For each file it extracts AST symbols and callees.

### Output Summary (stdout)

The command prints a JSON summary including:

- run metadata: `status`, `repo`, `trace_id`, `duration_ms`
- indexing metrics: `files_scanned`, `changed_files`, `deleted_files`, `skipped_files`
- graph metrics: `linked_edges`, `unresolved_edges`, `resolution_rate`, `actionable_resolution_rate`, `linkage_quality`
- artifact paths: `index_file`, `manifest_file`, `call_edges_file`, `unresolved_edges_file`, `vectors_file`, `spans_file`

### Output Artifacts

Under `<state-root>/index/`:

1. `symbols.json`: extracted symbols and callee names.
2. `file_manifest.json`: per-file content hashes used for incremental runs.
3. `call_edges.json`: resolved caller -> callee edges.
4. `unresolved_call_edges.json`: unresolved calls with reason/actionability.
5. `vectors.json`: vector payloads with metadata (`repo`, `path`, `symbol`, `tree_sha`, `blob_sha`, `access_level`).

Under `<state-root>/observability/`:

1. `spans.jsonl`: persisted observability spans (Langfuse and/or local adapter pipeline output).
