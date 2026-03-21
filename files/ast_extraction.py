"""
ast_extraction.py — Parse Python source bytes into signatures and call lists.

This module is intentionally stateless and git-agnostic: it takes raw source
bytes (from a GitHub blob fetch or any other source) and returns structured
symbols.  No call-graph *store* — call-graphs are resolved live by the
Live Repo Reader (Section 3.3) which calls back into this module for each
callee it fetches.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data types — matches Section 6 Index Record metadata
# ---------------------------------------------------------------------------

@dataclass
class Signature:
    """Embeddable signature record for the Vector DB index."""
    repo: str
    owner: str
    path: str
    kind: str                          # "function" | "class" | "method"
    name: str
    qualified_name: str                # e.g. "OrderService.processOrder"
    signature_text: str                # e.g. "def processOrder(order_id: str) -> OrderResult"
    docstring: str | None
    lineno: int
    end_lineno: int | None
    blob_sha: str | None = None        # from GitHub blob response

    @property
    def composite_key(self) -> str:
        """(function, repo) composite key — Section 3.2 design choice."""
        return f"{self.repo}::{self.qualified_name}"

    @property
    def embedding_input(self) -> str:
        """
        Signature-only text for the embedding vector.
        Body intentionally excluded (Section 3.2).
        """
        parts = [self.signature_text]
        if self.docstring:
            parts.append(self.docstring)
        return "\n".join(parts)


@dataclass
class CallInfo:
    """
    Lightweight call-edge data extracted from a single function body.
    NOT a persisted call-graph node — the Live Repo Reader assembles
    the full graph on the fly by fetching callee blobs recursively.
    """
    callee_names: list[str] = field(default_factory=list)    # raw names from AST
    import_sources: list[str] = field(default_factory=list)  # "from X import Y" modules


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class _SymbolVisitor(ast.NodeVisitor):
    """Extract signatures and per-function call lists from a Python module AST."""

    def __init__(self, source: str, repo: str, owner: str, path: str, blob_sha: str | None) -> None:
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.repo = repo
        self.owner = owner
        self.path = path
        self.blob_sha = blob_sha
        self.signatures: list[Signature] = []
        self.call_info: dict[str, CallInfo] = {}   # keyed by composite_key
        self.module_imports: list[str] = []         # top-level import sources
        self._class_stack: list[str] = []

    # ── helpers ──────────────────────────────────────────────────────

    def _body_source(self, node: ast.AST) -> str:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", None) or start + 1
        return "".join(self.lines[start:end])

    @staticmethod
    def _signature_text(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args_parts: list[str] = []
        all_args = node.args

        defaults_offset = len(all_args.args) - len(all_args.defaults)
        for i, arg in enumerate(all_args.args):
            part = arg.arg
            if arg.annotation:
                part += f": {ast.unparse(arg.annotation)}"
            di = i - defaults_offset
            if 0 <= di < len(all_args.defaults):
                part += f" = {ast.unparse(all_args.defaults[di])}"
            args_parts.append(part)

        if all_args.vararg:
            v = f"*{all_args.vararg.arg}"
            if all_args.vararg.annotation:
                v += f": {ast.unparse(all_args.vararg.annotation)}"
            args_parts.append(v)

        for i, arg in enumerate(all_args.kwonlyargs):
            part = arg.arg
            if arg.annotation:
                part += f": {ast.unparse(arg.annotation)}"
            if all_args.kw_defaults[i] is not None:
                part += f" = {ast.unparse(all_args.kw_defaults[i])}"
            args_parts.append(part)

        if all_args.kwarg:
            k = f"**{all_args.kwarg.arg}"
            if all_args.kwarg.annotation:
                k += f": {ast.unparse(all_args.kwarg.annotation)}"
            args_parts.append(k)

        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args_parts)}){ret}"

    @staticmethod
    def _extract_calls(node: ast.AST) -> list[str]:
        names: list[str] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                parts: list[str] = []
                cur = func
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                parts.reverse()
                names.append(".".join(parts))
                if len(parts) > 1:
                    names.append(parts[-1])
        return sorted(set(names))

    @staticmethod
    def _extract_imports(tree: ast.Module) -> list[str]:
        """Extract top-level import module sources for cross-repo resolution."""
        sources: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    sources.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                sources.append(node.module)
        return sources

    @staticmethod
    def _rough_tokens(text: str) -> int:
        return int(len(text.split()) * 1.3)

    # ── visitors ─────────────────────────────────────────────────────

    def _process_funcdef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        class_prefix = ".".join(self._class_stack)
        qname = f"{class_prefix}.{node.name}" if class_prefix else node.name
        kind = "method" if self._class_stack else "function"

        sig = Signature(
            repo=self.repo,
            owner=self.owner,
            path=self.path,
            kind=kind,
            name=node.name,
            qualified_name=qname,
            signature_text=self._signature_text(node),
            docstring=ast.get_docstring(node),
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", None),
            blob_sha=self.blob_sha,
        )
        self.signatures.append(sig)

        callee_names = self._extract_calls(node)
        self.call_info[sig.composite_key] = CallInfo(
            callee_names=callee_names,
            import_sources=self.module_imports,  # attached for cross-repo hint
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._process_funcdef(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._process_funcdef(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        sig = Signature(
            repo=self.repo,
            owner=self.owner,
            path=self.path,
            kind="class",
            name=node.name,
            qualified_name=(
                ".".join([*self._class_stack, node.name]) if self._class_stack else node.name
            ),
            signature_text=(
                f"class {node.name}"
                + (f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else "")
            ),
            docstring=ast.get_docstring(node),
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", None),
            blob_sha=self.blob_sha,
        )
        self.signatures.append(sig)

        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_symbols(
    source: bytes | str,
    *,
    repo: str,
    owner: str,
    path: str,
    blob_sha: str | None = None,
) -> tuple[list[Signature], dict[str, CallInfo]]:
    """
    Parse a single Python source blob and return (signatures, call_info_map).

    Parameters
    ----------
    source   : raw bytes or str (from GitHub blob fetch)
    repo     : repository name
    owner    : GitHub owner/org
    path     : repo-relative file path
    blob_sha : SHA of the blob (for staleness / cache key)

    Returns
    -------
    signatures : every function, method, and class found
    call_info  : per-function call edges, keyed by composite sig_id
    """
    if isinstance(source, bytes):
        source = source.decode("utf-8", errors="replace")

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return [], {}

    visitor = _SymbolVisitor(source, repo, owner, path, blob_sha)
    visitor.module_imports = visitor._extract_imports(tree)
    visitor.visit(tree)
    return visitor.signatures, visitor.call_info


def get_function_body(
    source: bytes | str,
    qualified_name: str,
) -> str | None:
    """
    Extract just the source body of a specific function/method from a blob.
    Used by the Live Repo Reader when assembling call-graph nodes.
    """
    if isinstance(source, bytes):
        source = source.decode("utf-8", errors="replace")

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    lines = source.splitlines(keepends=True)
    parts = qualified_name.split(".")

    def _find(node: ast.AST, remaining: list[str]) -> ast.AST | None:
        if not remaining:
            return node
        target = remaining[0]
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if child.name == target:
                    return _find(child, remaining[1:])
        return None

    found = _find(tree, parts)
    if found is None:
        return None

    start = found.lineno - 1
    end = getattr(found, "end_lineno", None) or start + 1
    return "".join(lines[start:end])
