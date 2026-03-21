from __future__ import annotations

import builtins
from dataclasses import dataclass

from ast_indexer.domain.models import CallEdge, SymbolRecord, UnresolvedCallEdge


_BUILTIN_CALLS = set(dir(builtins))
_COMMON_DYNAMIC_ATTR_CALLS = {
    'append',
    'clear',
    'close',
    'copy',
    'decode',
    'encode',
    'extend',
    'format',
    'get',
    'items',
    'join',
    'keys',
    'loads',
    'dumps',
    'lower',
    'pop',
    'read',
    'remove',
    'setdefault',
    'split',
    'startswith',
    'strip',
    'update',
    'values',
    'write',
}


@dataclass(frozen=True)
class CallGraphLinkReport:
    linked_edges: list[CallEdge]
    unresolved_edges: list[UnresolvedCallEdge]

    @property
    def total_edges(self) -> int:
        return len(self.linked_edges) + len(self.unresolved_edges)

    @property
    def actionable_unresolved_edges(self) -> list[UnresolvedCallEdge]:
        return [edge for edge in self.unresolved_edges if edge.actionable]

    @property
    def actionable_total_edges(self) -> int:
        return len(self.linked_edges) + len(self.actionable_unresolved_edges)

    @property
    def resolution_rate(self) -> float:
        if self.total_edges == 0:
            return 1.0
        return len(self.linked_edges) / self.total_edges

    @property
    def actionable_resolution_rate(self) -> float:
        if self.actionable_total_edges == 0:
            return 1.0
        return len(self.linked_edges) / self.actionable_total_edges


class CallGraphLinker:
    def link(self, symbols: list[SymbolRecord]) -> list[CallEdge]:
        return self.link_report(symbols).linked_edges

    def link_report(self, symbols: list[SymbolRecord]) -> CallGraphLinkReport:
        canonical_map: dict[str, SymbolRecord] = {}
        module_prefixes: set[str] = set()
        for symbol in sorted(symbols, key=lambda row: (row.path, row.line, row.symbol)):
            for canonical_name in self._canonical_names(symbol):
                canonical_map.setdefault(canonical_name, symbol)
            module_name = self._module_name(symbol.path)
            if module_name:
                module_prefixes.add(module_name.split('.')[0])

        linked_edges: list[CallEdge] = []
        unresolved_edges: list[UnresolvedCallEdge] = []
        for symbol in symbols:
            for callee in symbol.callees:
                target = canonical_map.get(callee)
                if target is None:
                    reason, actionable = self._classify_unresolved(callee, module_prefixes)
                    unresolved_edges.append(
                        UnresolvedCallEdge(
                            repo=symbol.repo,
                            caller_path=symbol.path,
                            caller_symbol=symbol.symbol,
                            callee=callee,
                            reason=reason,
                            actionable=actionable,
                        )
                    )
                    continue

                linked_edges.append(
                    CallEdge(
                        repo=symbol.repo,
                        caller_path=symbol.path,
                        caller_symbol=symbol.symbol,
                        callee=callee,
                        resolved_path=target.path,
                        resolved_symbol=target.symbol,
                        resolved_canonical=callee,
                    )
                )

        return CallGraphLinkReport(linked_edges=linked_edges, unresolved_edges=unresolved_edges)

    def _canonical_names(self, symbol: SymbolRecord) -> tuple[str, ...]:
        module = self._module_name(symbol.path)
        if module:
            return (symbol.symbol, f'{module}.{symbol.symbol}')
        return (symbol.symbol,)

    def _module_name(self, path: str) -> str:
        normalized = path.replace('\\', '/').strip('/')
        if not normalized.endswith('.py'):
            return ''

        module_path = normalized[:-3]
        if module_path.endswith('/__init__'):
            module_path = module_path[: -len('/__init__')]

        return module_path.replace('/', '.')

    def _classify_unresolved(self, callee: str, module_prefixes: set[str]) -> tuple[str, bool]:
        if callee in _BUILTIN_CALLS:
            return ('builtin_call', False)

        if callee in _COMMON_DYNAMIC_ATTR_CALLS:
            return ('dynamic_attr_call', False)

        if '.' in callee:
            head = callee.split('.', 1)[0]
            if head in module_prefixes or (head and head[0].isupper()):
                return ('no_matching_symbol', True)
            return ('external_qualified_call', False)

        return ('no_matching_symbol', True)
