# Phase 4 Production Build Bullets (VS Code)

## Scope

- Build a production-ready LangGraph read-only research pipeline.
- Use OpenAI for reasoning and semantic query generation.
- Preserve observability via Langfuse-compatible span flow.
- Validate with focused tests using real indexed data and >=85% coverage for Phase 4 modules.

## Build Bullets

- Add LangGraph pipeline runtime:
  - Implement `ResearchState` and graph nodes (`reasoning_node`, `prodder_node`, `vector_search_node`, `retrieval_node`).
  - Compile graph with deterministic node sequencing.
- Add OpenAI agent adapters:
  - Reasoning adapter that outputs a structured objective (`intent`, `entities`, `repos_in_scope`).
  - Query prodder adapter that outputs search query list.
- Add retrieval quality path:
  - Query vectors from configured embedding backend.
  - Rank indexed vectors with cosine similarity and dedupe by `(repo, path, symbol)`.
  - Read live file content from repository adapter and enrich with AST-derived symbol/callee details.
- Add production CLI surface:
  - New command: `python -m ast_indexer research`.
  - Support prompt, repo scoping, top-k, OpenAI model, observability backend, and trace ID.
- Keep production defaults:
  - Use OpenAI + Langfuse from environment by default.
  - Keep test-only fallback flags isolated to tests.

## Test Bullets

- Add real-data pipeline tests:
  - Index real local files to JSON index/vector artifacts.
  - Run LangGraph pipeline end-to-end against those artifacts.
  - Assert enriched context output and repo scoping behavior.
- Add OpenAI adapter tests:
  - Validate JSON response parsing for objective + query generation.
  - Validate fallback behavior for empty query payload.
- Run focused coverage gate:
  - `pytest ... --cov=ast_indexer.application.research_pipeline --cov=ast_indexer.application.research_openai_agents --cov-fail-under=85`

## Example Commands

```powershell
cd apps/worker-indexer-py/src

python -m ast_indexer index `
  --workspace-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\apps" `
  --repo "worker-indexer-py" `
  --state-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\state-phase4"

python -m ast_indexer research `
  --workspace-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\apps" `
  --state-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\state-phase4" `
  --repo "worker-indexer-py" `
  --prompt "How does webhook signature verification work end to end?" `
  --top-k 8
```
