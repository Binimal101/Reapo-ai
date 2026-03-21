# Phase 5 Production Build Bullets (VS Code)

## Scope

- Expand research breadth beyond Phase 4's bounded retrieval by introducing a larger candidate frontier.
- Add parallel relevancy scoring workers with confidence thresholding and collector behavior.
- Add reducer-style context compaction so large retrieval sets still fit practical token budgets.
- Keep production observability and CLI controls explicit and tunable.

## Build Bullets

- Add Phase 5 graph nodes to LangGraph pipeline:
  - `relevancy_node` for parallel scoring + threshold filtering.
  - `reducer_node` for compacted output under token budget.
- Broaden retrieval frontier before filtering:
  - Keep `top_k` as final output cap.
  - Add `candidate_pool_multiplier` so vector search can gather a larger pool.
- Implement parallel relevancy engine:
  - Use worker pool fan-out for candidate scoring.
  - Score with vector signal + objective/entity term matching.
  - Filter by confidence threshold, fallback to top-ranked set when all are below threshold.
- Implement reducer compaction:
  - Preserve mandatory metadata fields (repo/path/symbol/signature/callees).
  - Truncate bodies proportionally under a shared token budget.
  - Return compacted contexts plus estimated token metrics.
- Expand production CLI command surface:
  - `--candidate-pool-multiplier`
  - `--relevancy-threshold`
  - `--relevancy-workers`
  - `--reducer-token-budget`
  - `--reducer-max-contexts`
- Extend response payload for diagnostics:
  - `candidate_count`, `relevant_count`, `reduced_count`
  - relevancy confidence list
  - compacted reducer output list

## Test Bullets

- Add phase5 frontier test:
  - Assert candidate pool can exceed top-k.
  - Assert relevancy collector trims to top-k output.
- Add phase5 reducer test:
  - Force tight token budget and assert bodies are truncated.
  - Assert compacted output is within expected budget envelope.
- Run focused coverage gate:
  - `pytest tests/test_research_pipeline.py tests/test_research_phase5_pipeline.py --cov=ast_indexer.application.research_pipeline --cov-fail-under=85`

## Example Command

```powershell
cd apps/worker-indexer-py/src

python -m ast_indexer research `
  --workspace-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\apps" `
  --state-root "C:\Users\matth\OneDrive\Desktop\programs\personal_brand\Reapo-ai\state-phase5" `
  --repo "worker-indexer-py" `
  --repo "worker-orchestrator" `
  --prompt "Trace webhook signature verification across ingestion and dispatch" `
  --top-k 12 `
  --candidate-pool-multiplier 8 `
  --relevancy-threshold 0.4 `
  --relevancy-workers 8 `
  --reducer-token-budget 3500 `
  --reducer-max-contexts 12
```
