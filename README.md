# Reapo-ai

Multi-repo coding agent orchestration for research, read/writing, and development.

## Layout

| Path | Role |
|------|------|
| [`apps/worker-indexer-py`](apps/worker-indexer-py/) | Template from [`main`](https://github.com/Binimal101/Reapo-ai): hexagonal AST indexer (ports, adapters, `pytest --cov`). |
| [`files/`](files/) | Hybrid RAG pipeline — GitHub API blob/tree access, `index_builder.py` (embed + vector upsert + incremental webhook updates), live call-graph reader. |

Run the end-to-end smoke script (from `files/`):

```bash
cd files
python test_integration.py
```
