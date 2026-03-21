from __future__ import annotations

import ast
from dataclasses import dataclass

from ast_indexer.domain.models import SymbolRecord


@dataclass(frozen=True)
class ExtractedFile:
    path: str
    symbols: list[SymbolRecord]


@dataclass(frozen=True)
class ImportAliases:
    aliases: dict[str, str]


class PythonAstSymbolExtractor:
    """Extract function/class symbols and direct call graph edges from Python source."""

    def extract(self, repo: str, path: str, source: str) -> ExtractedFile:
        tree = ast.parse(source)
        symbols: list[SymbolRecord] = []
        import_aliases = self._collect_import_aliases(tree)

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                symbols.append(self._function_symbol(repo, path, node, import_aliases))
            elif isinstance(node, ast.AsyncFunctionDef):
                symbols.append(self._async_function_symbol(repo, path, node, import_aliases))
            elif isinstance(node, ast.ClassDef):
                symbols.append(self._class_symbol(repo, path, node))

                for child in node.body:
                    if isinstance(child, ast.FunctionDef):
                        symbols.append(
                            self._method_symbol(
                                repo,
                                path,
                                node.name,
                                child,
                                is_async=False,
                                import_aliases=import_aliases,
                            )
                        )
                    elif isinstance(child, ast.AsyncFunctionDef):
                        symbols.append(
                            self._method_symbol(
                                repo,
                                path,
                                node.name,
                                child,
                                is_async=True,
                                import_aliases=import_aliases,
                            )
                        )

        return ExtractedFile(path=path, symbols=symbols)

    def _function_symbol(self, repo: str, path: str, node: ast.FunctionDef, import_aliases: ImportAliases) -> SymbolRecord:
        return SymbolRecord(
            repo=repo,
            path=path,
            symbol=node.name,
            kind='function',
            line=node.lineno,
            signature=self._build_signature(node.name, node.args),
            docstring=ast.get_docstring(node),
            callees=self._collect_callees(node, import_aliases),
        )

    def _async_function_symbol(
        self,
        repo: str,
        path: str,
        node: ast.AsyncFunctionDef,
        import_aliases: ImportAliases,
    ) -> SymbolRecord:
        return SymbolRecord(
            repo=repo,
            path=path,
            symbol=node.name,
            kind='async_function',
            line=node.lineno,
            signature='async ' + self._build_signature(node.name, node.args),
            docstring=ast.get_docstring(node),
            callees=self._collect_callees(node, import_aliases),
        )

    def _class_symbol(self, repo: str, path: str, node: ast.ClassDef) -> SymbolRecord:
        return SymbolRecord(
            repo=repo,
            path=path,
            symbol=node.name,
            kind='class',
            line=node.lineno,
            signature=f'class {node.name}',
            docstring=ast.get_docstring(node),
            callees=(),
        )

    def _method_symbol(
        self,
        repo: str,
        path: str,
        class_name: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_async: bool,
        import_aliases: ImportAliases,
    ) -> SymbolRecord:
        method_name = f'{class_name}.{node.name}'
        prefix = 'async ' if is_async else ''
        kind = 'async_method' if is_async else 'method'

        return SymbolRecord(
            repo=repo,
            path=path,
            symbol=method_name,
            kind=kind,
            line=node.lineno,
            signature=prefix + self._build_signature(method_name, node.args),
            docstring=ast.get_docstring(node),
            callees=self._collect_callees(node, import_aliases, class_name=class_name),
        )

    def _build_signature(self, name: str, args: ast.arguments) -> str:
        parts = [arg.arg for arg in args.args]
        return f'def {name}(' + ', '.join(parts) + ')'

    def _collect_import_aliases(self, tree: ast.Module) -> ImportAliases:
        aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    exposed_name = alias.asname or alias.name.split('.')[0]
                    aliases[exposed_name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ''
                for alias in node.names:
                    if alias.name == '*':
                        continue
                    exposed_name = alias.asname or alias.name
                    aliases[exposed_name] = f'{module_name}.{alias.name}' if module_name else alias.name

        return ImportAliases(aliases=aliases)

    def _collect_callees(
        self,
        node: ast.AST,
        import_aliases: ImportAliases,
        class_name: str | None = None,
    ) -> tuple[str, ...]:
        names: list[str] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue

            fn = child.func
            if isinstance(fn, ast.Name):
                names.append(import_aliases.aliases.get(fn.id, fn.id))
            elif isinstance(fn, ast.Attribute):
                if isinstance(fn.value, ast.Name):
                    base_name = fn.value.id
                    if base_name in import_aliases.aliases:
                        names.append(f'{import_aliases.aliases[base_name]}.{fn.attr}')
                    elif class_name and base_name in {'self', 'cls'}:
                        names.append(f'{class_name}.{fn.attr}')
                    else:
                        names.append(fn.attr)
                else:
                    names.append(fn.attr)

        # Preserve order but remove duplicates
        ordered_unique = tuple(dict.fromkeys(names))
        return ordered_unique
