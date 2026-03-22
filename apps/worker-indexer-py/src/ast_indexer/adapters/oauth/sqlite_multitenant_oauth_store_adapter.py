from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from ast_indexer.ports.oauth import OAuthTokenRecord, OAuthTokenStorePort


class SqliteMultiTenantOAuthStoreAdapter(OAuthTokenStorePort):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def save(self, token: OAuthTokenRecord) -> None:
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            self._ensure_user(conn, token.user_id)
            conn.execute(
                """
                INSERT INTO oauth_accounts (
                    user_id,
                    provider,
                    provider_user_id,
                    github_login,
                    created_at,
                    updated_at
                ) VALUES (?, 'github', ?, ?, ?, ?)
                ON CONFLICT(user_id, provider)
                DO UPDATE SET
                    provider_user_id = excluded.provider_user_id,
                    github_login = excluded.github_login,
                    updated_at = excluded.updated_at
                """,
                (token.user_id, token.user_id, token.user_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO oauth_tokens (
                    user_id,
                    access_token,
                    refresh_token,
                    expires_at,
                    scopes_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    scopes_json = excluded.scopes_json,
                    updated_at = excluded.updated_at
                """,
                (
                    token.user_id,
                    token.access_token,
                    token.refresh_token,
                    token.expires_at.isoformat(),
                    json.dumps(list(token.scopes)),
                    now,
                ),
            )

    def get(self, user_id: str) -> OAuthTokenRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, access_token, refresh_token, expires_at, scopes_json
                FROM oauth_tokens
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                return None

            scopes_raw = json.loads(str(row['scopes_json']))
            scopes = tuple(item for item in scopes_raw if isinstance(item, str)) if isinstance(scopes_raw, list) else ()
            return OAuthTokenRecord(
                user_id=str(row['user_id']),
                access_token=str(row['access_token']),
                expires_at=datetime.fromisoformat(str(row['expires_at'])),
                scopes=scopes,
                refresh_token=(
                    str(row['refresh_token'])
                    if isinstance(row['refresh_token'], str) and row['refresh_token']
                    else None
                ),
            )

    def list_user_ids(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT user_id FROM tenant_users ORDER BY user_id").fetchall()
            return [str(row['user_id']) for row in rows]

    def create_project(self, *, owner_user_id: str, name: str, description: str | None = None) -> dict:
        if not owner_user_id.strip():
            raise ValueError('owner_user_id is required')
        if not name.strip():
            raise ValueError('project name is required')

        project_id = uuid4().hex
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            self._ensure_user(conn, owner_user_id)
            conn.execute(
                """
                INSERT INTO projects (
                    project_id,
                    owner_user_id,
                    name,
                    description,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, owner_user_id, name.strip(), description, now, now),
            )
            conn.execute(
                """
                INSERT INTO project_members (
                    project_id,
                    user_id,
                    role,
                    created_at
                ) VALUES (?, ?, 'owner', ?)
                """,
                (project_id, owner_user_id, now),
            )

        return {
            'project_id': project_id,
            'owner_user_id': owner_user_id,
            'name': name.strip(),
            'description': description,
            'created_at': now,
            'updated_at': now,
        }

    def update_project(self, *, project_id: str, owner_user_id: str, name: str, description: str | None = None) -> dict:
        if not project_id.strip():
            raise ValueError('project_id is required')
        if not owner_user_id.strip():
            raise ValueError('owner_user_id is required')
        if not name.strip():
            raise ValueError('project name is required')

        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT project_id, owner_user_id, created_at
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f'project not found: {project_id}')
            if str(row['owner_user_id']) != owner_user_id:
                raise PermissionError('project_owner_required')

            conn.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (name.strip(), description, now, project_id),
            )

            return {
                'project_id': str(row['project_id']),
                'owner_user_id': str(row['owner_user_id']),
                'name': name.strip(),
                'description': description,
                'created_at': str(row['created_at']),
                'updated_at': now,
            }

    def delete_project(self, *, project_id: str, owner_user_id: str) -> bool:
        if not project_id.strip():
            raise ValueError('project_id is required')
        if not owner_user_id.strip():
            raise ValueError('owner_user_id is required')

        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT owner_user_id
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f'project not found: {project_id}')
            if str(row['owner_user_id']) != owner_user_id:
                raise PermissionError('project_owner_required')

            cursor = conn.execute('DELETE FROM projects WHERE project_id = ?', (project_id,))
            return cursor.rowcount > 0

    def add_project_member(self, *, project_id: str, user_id: str, role: str = 'member') -> dict:
        if role not in {'owner', 'member', 'viewer'}:
            raise ValueError('role must be one of: owner, member, viewer')

        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            self._ensure_user(conn, user_id)
            exists = conn.execute('SELECT 1 FROM projects WHERE project_id = ?', (project_id,)).fetchone()
            if exists is None:
                raise KeyError(f'project not found: {project_id}')

            conn.execute(
                """
                INSERT INTO project_members (project_id, user_id, role, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, user_id)
                DO UPDATE SET role = excluded.role
                """,
                (project_id, user_id, role, now),
            )
            return {
                'project_id': project_id,
                'user_id': user_id,
                'role': role,
                'created_at': now,
            }

    def record_github_installation_for_user(
        self,
        *,
        user_id: str,
        installation_id: int,
        account_login: str | None = None,
    ) -> dict:
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            self._ensure_user(conn, user_id)
            self._ensure_github_account(conn, user_id)
            account_row = conn.execute(
                "SELECT account_id FROM oauth_accounts WHERE user_id = ? AND provider = 'github'",
                (user_id,),
            ).fetchone()
            if account_row is None:
                raise RuntimeError('missing github oauth account')

            conn.execute(
                """
                INSERT INTO github_installations (
                    installation_id,
                    account_id,
                    account_login,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(installation_id)
                DO UPDATE SET
                    account_id = excluded.account_id,
                    account_login = excluded.account_login,
                    updated_at = excluded.updated_at
                """,
                (installation_id, int(account_row['account_id']), account_login, now, now),
            )

            return {
                'installation_id': installation_id,
                'user_id': user_id,
                'account_login': account_login,
                'updated_at': now,
            }

    def add_repository_to_project(
        self,
        *,
        project_id: str,
        owner: str,
        name: str,
        added_by_user_id: str,
        installation_id: int | None = None,
        github_repo_id: int | None = None,
        visibility: str | None = None,
    ) -> dict:
        now = _utc_now_iso()
        owner_clean = owner.strip()
        name_clean = name.strip()
        if not owner_clean or not name_clean:
            raise ValueError('owner and name are required')

        full_name = f'{owner_clean}/{name_clean}'
        with self._lock, self._connect() as conn:
            self._ensure_user(conn, added_by_user_id)
            project = conn.execute('SELECT project_id FROM projects WHERE project_id = ?', (project_id,)).fetchone()
            if project is None:
                raise KeyError(f'project not found: {project_id}')

            conn.execute(
                """
                INSERT INTO repositories (
                    github_repo_id,
                    owner,
                    name,
                    full_name,
                    installation_id,
                    visibility,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(full_name)
                DO UPDATE SET
                    github_repo_id = COALESCE(excluded.github_repo_id, repositories.github_repo_id),
                    installation_id = COALESCE(excluded.installation_id, repositories.installation_id),
                    visibility = COALESCE(excluded.visibility, repositories.visibility),
                    updated_at = excluded.updated_at
                """,
                (github_repo_id, owner_clean, name_clean, full_name, installation_id, visibility, now, now),
            )

            repo_row = conn.execute(
                'SELECT repository_id FROM repositories WHERE full_name = ?',
                (full_name,),
            ).fetchone()
            if repo_row is None:
                raise RuntimeError(f'failed to upsert repository: {full_name}')

            conn.execute(
                """
                INSERT INTO project_repositories (
                    project_id,
                    repository_id,
                    added_by_user_id,
                    created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, repository_id)
                DO NOTHING
                """,
                (project_id, int(repo_row['repository_id']), added_by_user_id, now),
            )

            return {
                'project_id': project_id,
                'full_name': full_name,
                'repository_id': int(repo_row['repository_id']),
                'installation_id': installation_id,
                'added_by_user_id': added_by_user_id,
                'created_at': now,
            }

    def list_user_projects(self, *, user_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.project_id, p.owner_user_id, p.name, p.description, p.created_at, p.updated_at, pm.role
                FROM projects AS p
                INNER JOIN project_members AS pm ON pm.project_id = p.project_id
                WHERE pm.user_id = ?
                ORDER BY p.created_at ASC
                """,
                (user_id,),
            ).fetchall()
            return [
                {
                    'project_id': str(row['project_id']),
                    'owner_user_id': str(row['owner_user_id']),
                    'name': str(row['name']),
                    'description': row['description'],
                    'created_at': str(row['created_at']),
                    'updated_at': str(row['updated_at']),
                    'role': str(row['role']),
                }
                for row in rows
            ]

    def list_project_repositories(self, *, project_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.repository_id, r.github_repo_id, r.owner, r.name, r.full_name, r.installation_id, r.visibility
                FROM project_repositories AS pr
                INNER JOIN repositories AS r ON r.repository_id = pr.repository_id
                WHERE pr.project_id = ?
                ORDER BY r.full_name ASC
                """,
                (project_id,),
            ).fetchall()
            return [
                {
                    'repository_id': int(row['repository_id']),
                    'github_repo_id': row['github_repo_id'],
                    'owner': str(row['owner']),
                    'name': str(row['name']),
                    'full_name': str(row['full_name']),
                    'installation_id': row['installation_id'],
                    'visibility': row['visibility'],
                }
                for row in rows
            ]

    def remove_repository_from_project(self, *, project_id: str, repository_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM project_repositories
                WHERE project_id = ? AND repository_id = ?
                """,
                (project_id, repository_id),
            )
            return cursor.rowcount > 0

    def list_user_accessible_repositories(self, *, user_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT r.repository_id, r.github_repo_id, r.owner, r.name, r.full_name, r.installation_id, r.visibility
                FROM project_members AS pm
                INNER JOIN project_repositories AS pr ON pr.project_id = pm.project_id
                INNER JOIN repositories AS r ON r.repository_id = pr.repository_id
                WHERE pm.user_id = ?
                ORDER BY r.full_name ASC
                """,
                (user_id,),
            ).fetchall()
            return [
                {
                    'repository_id': int(row['repository_id']),
                    'github_repo_id': row['github_repo_id'],
                    'owner': str(row['owner']),
                    'name': str(row['name']),
                    'full_name': str(row['full_name']),
                    'installation_id': row['installation_id'],
                    'visibility': row['visibility'],
                }
                for row in rows
            ]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenant_users (
                    user_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oauth_accounts (
                    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_user_id TEXT,
                    github_login TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, provider),
                    UNIQUE(provider, provider_user_id),
                    FOREIGN KEY(user_id) REFERENCES tenant_users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    user_id TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES tenant_users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS github_installations (
                    installation_id INTEGER PRIMARY KEY,
                    account_id INTEGER,
                    account_login TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES oauth_accounts(account_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, name),
                    FOREIGN KEY(owner_user_id) REFERENCES tenant_users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_members (
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, user_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES tenant_users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS repositories (
                    repository_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    github_repo_id INTEGER,
                    owner TEXT NOT NULL,
                    name TEXT NOT NULL,
                    full_name TEXT NOT NULL UNIQUE,
                    installation_id INTEGER,
                    visibility TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(installation_id) REFERENCES github_installations(installation_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS project_repositories (
                    project_id TEXT NOT NULL,
                    repository_id INTEGER NOT NULL,
                    added_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, repository_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                    FOREIGN KEY(repository_id) REFERENCES repositories(repository_id) ON DELETE CASCADE,
                    FOREIGN KEY(added_by_user_id) REFERENCES tenant_users(user_id) ON DELETE RESTRICT
                );
                """
            )

    def _ensure_user(self, conn: sqlite3.Connection, user_id: str) -> None:
        now = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO tenant_users (user_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET updated_at = excluded.updated_at
            """,
            (user_id, now, now),
        )

    def _ensure_github_account(self, conn: sqlite3.Connection, user_id: str) -> None:
        now = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO oauth_accounts (
                user_id,
                provider,
                provider_user_id,
                github_login,
                created_at,
                updated_at
            ) VALUES (?, 'github', ?, ?, ?, ?)
            ON CONFLICT(user_id, provider)
            DO UPDATE SET
                provider_user_id = excluded.provider_user_id,
                github_login = excluded.github_login,
                updated_at = excluded.updated_at
            """,
            (user_id, user_id, user_id, now, now),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
