from __future__ import annotations

from typing import Protocol


class WebhookSignatureVerifierPort(Protocol):
    def verify(self, body: bytes, signature_header: str | None) -> bool:
        """Return True when webhook signature is valid for the request body."""
