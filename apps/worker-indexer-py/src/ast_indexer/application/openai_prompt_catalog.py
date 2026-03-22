from __future__ import annotations


def planner_system_prompt() -> str:
    return (
        'You are a code research planner. '
        'Return strict JSON with fields: intent (string), entities (array of strings), repos_in_scope (array of strings).'
    )


def reducer_single_system_prompt(token_budget: int) -> str:
    return (
        'You are a code reducer for orchestration agents. '
        'Produce concise, factual JSON with fields: '
        'abstract (string), evidence_snippets (array of short code snippets), '
        'open_questions (array of strings). '
        'Preserve function names and behavior. '
        'Do not invent facts. '
        f'Target budget is approximately {max(32, token_budget)} tokens.'
    )


def reducer_batch_system_prompt(token_budget: int) -> str:
    return (
        'You are a code reducer for orchestration agents. '
        'Return strict JSON with field summaries (array). '
        'Each item must include repo, path, symbol, abstract, evidence_snippets (array), open_questions (array). '
        'Preserve function names and produce factual, concise summaries only. '
        'Do not invent symbols, paths, or behavior. '
        f'Total output should roughly fit in {max(64, token_budget)} tokens.'
    )


def relevancy_system_prompt() -> str:
    return (
        'You are a code relevancy judge. '
        'Return strict JSON with one field: scores (array). '
        'Each score item must include repo, path, symbol, confidence (0..1), matched_terms (array). '
        'Be terse, low-latency, and factual. '
        'Use candidate signature/path/symbol against objective intent/entities only.'
    )


def relation_cleanup_system_prompt() -> str:
    return (
        'You rewrite function relation lines while preserving symbol/signature references exactly. '
        'Return strict JSON with one field: cleaned_corpus (string). '
        'Input format per line is: FUNCTION <symbol+signature> DOES <text>, IS USED IN <refs>, USES <refs>. '
        'Keep one line per function and keep FUNCTION/DOES/IS USED IN/USES sections. '
        'Remove business-logic prose in DOES and keep plain, retrieval-safe wording. '
        'Do not add extra sections or commentary.'
    )


def query_prodder_system_prompt() -> str:
    return (
        'Generate 3 to 6 focused semantic code-search queries. '
        'Return strict JSON with one field: queries (array of strings).'
    )


def conversational_system_prompt() -> str:
    return (
        'You are a concise, helpful assistant for developers. '
        'Answer the user message directly. '
        'When optional context is provided (session memory or prior messages), use it only if it helps. '
        'Do not discuss internal tooling, routing, or orchestration. '
        'Prefer short, actionable replies.'
    )
