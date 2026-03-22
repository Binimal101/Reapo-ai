from pathlib import Path

from ast_indexer.application.repo_agent_tools import build_repo_agent_tool_handlers


def test_get_folder_structure_is_paginated_and_stable(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    repo = workspace / 'acme' / 'checkout-service'
    (repo / 'src').mkdir(parents=True)
    (repo / 'README.md').write_text('hello\n', encoding='utf-8')
    (repo / 'src' / 'orders.py').write_text('def run():\n    return True\n', encoding='utf-8')
    (repo / 'src' / 'payments.py').write_text('def pay():\n    return True\n', encoding='utf-8')

    handlers = build_repo_agent_tool_handlers(workspace)
    get_folder_structure = handlers['get_folder_structure']

    page_1 = get_folder_structure(repo='acme/checkout-service', path='src', page=1, page_size=1)
    page_2 = get_folder_structure(repo='acme/checkout-service', path='src', page=2, page_size=1)

    assert page_1['ok'] is True
    assert page_1['has_more'] is True
    assert page_1['total_entries'] == 2
    assert len(page_1['entries']) == 1

    assert page_2['ok'] is True
    assert len(page_2['entries']) == 1
    assert page_2['has_more'] is False
    assert page_1['entries'][0]['path'] != page_2['entries'][0]['path']


def test_get_file_contents_enforces_max_tokens(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    repo = workspace / 'acme' / 'checkout-service'
    (repo / 'src').mkdir(parents=True)
    source = '\n'.join(
        [
            'def charge_customer(user_id, amount):',
            '    total = amount * 100',
            '    gateway_payload = {"user_id": user_id, "total": total}',
            '    return gateway_payload',
        ]
    )
    (repo / 'src' / 'billing.py').write_text(source + '\n', encoding='utf-8')

    handlers = build_repo_agent_tool_handlers(workspace)
    get_file_contents = handlers['get_file_contents']

    too_small = get_file_contents(
        repo='acme/checkout-service',
        path='src/billing.py',
        line_beginning=1,
        line_ending=4,
        max_tokens=8,
    )
    assert too_small['ok'] is False
    assert too_small['error'] == 'max_tokens_exceeded'

    enough = get_file_contents(
        repo='acme/checkout-service',
        path='src/billing.py',
        line_beginning=1,
        line_ending=2,
        max_tokens=80,
    )
    assert enough['ok'] is True
    assert enough['line_beginning'] == 1
    assert enough['line_ending'] == 2
    assert 'def charge_customer' in enough['content']
