from __future__ import annotations

import os


def github_api_base_url() -> str:
    return (os.getenv('AST_INDEXER_GITHUB_API_BASE_URL') or 'https://api.github.com').rstrip('/')


def github_oauth_base_url() -> str:
    return (os.getenv('AST_INDEXER_GITHUB_OAUTH_BASE_URL') or 'https://github.com').rstrip('/')


def default_openai_model() -> str:
    return (
        os.getenv('AST_INDEXER_OPENAI_MODEL')
        or os.getenv('AST_INDEXER_RESEARCH_MODEL')
        or os.getenv('OPENAI_MODEL')
        or 'gpt-4o-mini'
    )


def default_bind_host() -> str:
    return os.getenv('AST_INDEXER_HOST') or '127.0.0.1'