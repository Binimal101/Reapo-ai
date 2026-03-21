from datetime import datetime, timedelta, timezone
from pathlib import Path

from ast_indexer.adapters.oauth.sqlite_multitenant_oauth_store_adapter import SqliteMultiTenantOAuthStoreAdapter
from ast_indexer.ports.oauth import OAuthTokenRecord


def test_sqlite_multitenant_store_persists_oauth_token(tmp_path: Path) -> None:
    store = SqliteMultiTenantOAuthStoreAdapter(tmp_path / 'auth' / 'multitenant_auth.db')

    token = OAuthTokenRecord(
        user_id='alice',
        access_token='token-1',
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=('repo', 'read:org'),
        refresh_token='refresh-1',
    )

    store.save(token)
    fetched = store.get('alice')
    assert fetched is not None
    assert fetched.user_id == 'alice'
    assert fetched.access_token == 'token-1'
    assert fetched.refresh_token == 'refresh-1'
    assert fetched.scopes == ('repo', 'read:org')

    assert store.list_user_ids() == ['alice']


def test_sqlite_multitenant_store_tracks_projects_members_and_repo_access(tmp_path: Path) -> None:
    store = SqliteMultiTenantOAuthStoreAdapter(tmp_path / 'auth' / 'multitenant_auth.db')

    project = store.create_project(owner_user_id='alice', name='billing-platform')
    assert project['owner_user_id'] == 'alice'

    member = store.add_project_member(project_id=project['project_id'], user_id='bob', role='member')
    assert member['role'] == 'member'

    store.record_github_installation_for_user(user_id='alice', installation_id=123456, account_login='acme-inc')

    repo_link = store.add_repository_to_project(
        project_id=project['project_id'],
        owner='acme-inc',
        name='billing-api',
        added_by_user_id='alice',
        installation_id=123456,
        github_repo_id=987,
        visibility='private',
    )
    assert repo_link['full_name'] == 'acme-inc/billing-api'

    alice_projects = store.list_user_projects(user_id='alice')
    assert len(alice_projects) == 1
    assert alice_projects[0]['name'] == 'billing-platform'
    assert alice_projects[0]['role'] == 'owner'

    bob_projects = store.list_user_projects(user_id='bob')
    assert len(bob_projects) == 1
    assert bob_projects[0]['name'] == 'billing-platform'
    assert bob_projects[0]['role'] == 'member'

    project_repos = store.list_project_repositories(project_id=project['project_id'])
    assert len(project_repos) == 1
    assert project_repos[0]['full_name'] == 'acme-inc/billing-api'

    bob_accessible = store.list_user_accessible_repositories(user_id='bob')
    assert len(bob_accessible) == 1
    assert bob_accessible[0]['full_name'] == 'acme-inc/billing-api'
