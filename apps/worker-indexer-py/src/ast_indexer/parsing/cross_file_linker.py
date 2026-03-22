from __future__ import annotations

from ast_indexer.domain.models import SymbolRecord
from ast_indexer.parsing.module_path_resolver import ModulePathResolver


class CrossFileLinker:
    """
    Resolve raw callee strings to canonical symbol IDs across all indexed files.

    For each SymbolRecord, inspects its `callees` (raw call targets produced by
    the AST extractor) and attempts to match them against known symbols in the
    same repository.  Resolved callees are written to `linked_callees` as
    ``"path::symbol"`` strings (e.g. ``"src/pricing.py::apply_discount"``).

    Resolution strategy (in order):
    1. Exact name match — callee equals a symbol's ``symbol`` field; only
       accepted when the name is unambiguous (exactly one symbol holds it).
    2. Exact module-qualified match — callee equals ``module.symbol`` where
       ``module`` is derived from the symbol's file path via ModulePathResolver.
    3. Suffix match — callee is a suffix of a ``module.symbol`` key (handles
       partial module paths); accepted only when the suffix is unambiguous.
    """

    def link(
        self,
        symbols: list[SymbolRecord],
        resolver: ModulePathResolver,
    ) -> list[SymbolRecord]:
        by_name: dict[str, list[SymbolRecord]] = {}
        by_module_sym: dict[str, SymbolRecord] = {}

        for sym in symbols:
            by_name.setdefault(sym.symbol, []).append(sym)
            module = resolver.path_to_module(sym.path)
            by_module_sym[f'{module}.{sym.symbol}'] = sym

        result: list[SymbolRecord] = []
        for sym in symbols:
            linked: list[str] = []
            for callee in sym.callees:
                resolved = self._resolve(callee, by_name, by_module_sym)
                if resolved is not None:
                    key = f'{resolved.path}::{resolved.symbol}'
                    linked.append(key)

            result.append(
                SymbolRecord(
                    repo=sym.repo,
                    path=sym.path,
                    symbol=sym.symbol,
                    kind=sym.kind,
                    line=sym.line,
                    signature=sym.signature,
                    docstring=sym.docstring,
                    callees=sym.callees,
                    linked_callees=tuple(dict.fromkeys(linked)),
                )
            )

        return result

    def _resolve(
        self,
        callee: str,
        by_name: dict[str, list[SymbolRecord]],
        by_module_sym: dict[str, SymbolRecord],
    ) -> SymbolRecord | None:
        # 1. Exact name match (simple functions and ClassName.method)
        if callee in by_name:
            candidates = by_name[callee]
            if len(candidates) == 1:
                return candidates[0]
            return None  # ambiguous

        # 2. Exact module-qualified match
        if callee in by_module_sym:
            return by_module_sym[callee]

        # 3. Suffix match for partial module paths
        suffix = '.' + callee
        matches = [sym for key, sym in by_module_sym.items() if key.endswith(suffix)]
        if len(matches) == 1:
            return matches[0]

        return None
