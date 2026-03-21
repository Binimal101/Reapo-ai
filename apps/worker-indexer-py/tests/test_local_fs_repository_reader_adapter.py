from pathlib import Path

from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter


def test_list_python_files_uses_gitignore_patterns(tmp_path: Path) -> None:
    repo_root = tmp_path / 'repo-a'
    (repo_root / 'src').mkdir(parents=True)
    (repo_root / 'ignored').mkdir(parents=True)
    (repo_root / 'src' / 'main.py').write_text('def run():\n    return 1\n', encoding='utf-8')
    (repo_root / 'ignored' / 'drop.py').write_text('def drop():\n    return 0\n', encoding='utf-8')
    (repo_root / '.gitignore').write_text('ignored/\n', encoding='utf-8')

    reader = LocalFsRepositoryReaderAdapter(tmp_path)
    files = reader.list_python_files('repo-a')

    assert files == ['src/main.py']


def test_list_python_files_always_excludes_venv_and_pycache(tmp_path: Path) -> None:
    repo_root = tmp_path / 'repo-b'
    (repo_root / 'src').mkdir(parents=True)
    (repo_root / '.venv' / 'lib').mkdir(parents=True)
    (repo_root / '__pycache__').mkdir(parents=True)
    (repo_root / 'src' / 'main.py').write_text('def run():\n    return 1\n', encoding='utf-8')
    (repo_root / '.venv' / 'lib' / 'pkg.py').write_text('def pkg():\n    return 2\n', encoding='utf-8')
    (repo_root / '__pycache__' / 'cached.py').write_text('def cached():\n    return 3\n', encoding='utf-8')
    (repo_root / '.gitignore').write_text('!**/.venv/**\n', encoding='utf-8')

    reader = LocalFsRepositoryReaderAdapter(tmp_path)
    files = reader.list_python_files('repo-b')

    assert files == ['src/main.py']
