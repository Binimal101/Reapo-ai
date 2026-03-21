from __future__ import annotations


class ModulePathResolver:
    """Derive Python dotted module names from repository-relative file paths."""

    _SOURCE_PREFIXES = ('src/',)

    def path_to_module(self, path: str) -> str:
        """
        Convert a repository-relative file path to a dotted module name.

        Examples:
            src/orders.py            → orders
            src/checkout/service.py  → checkout.service
            src/checkout/__init__.py → checkout
            orders.py                → orders
            pricing/tools.py         → pricing.tools
        """
        normalized = path.replace('\\', '/')

        for prefix in self._SOURCE_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break

        if normalized.endswith('.py'):
            normalized = normalized[:-3]

        if normalized.endswith('/__init__'):
            normalized = normalized[:-9]

        return normalized.replace('/', '.')
