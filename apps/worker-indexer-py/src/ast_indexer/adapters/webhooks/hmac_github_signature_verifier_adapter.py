from __future__ import annotations

import hashlib
import hmac

from ast_indexer.ports.webhook_signature_verifier import WebhookSignatureVerifierPort


class HmacGithubSignatureVerifierAdapter(WebhookSignatureVerifierPort):
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode('utf-8')

    def verify(self, body: bytes, signature_header: str | None) -> bool:
        if not signature_header:
            return False

        expected = 'sha256=' + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)
