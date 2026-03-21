from __future__ import annotations

import json
from dataclasses import dataclass

from ast_indexer.application.index_job_dispatch_service import IndexJobDispatchService
from ast_indexer.ports.webhook_signature_verifier import WebhookSignatureVerifierPort


@dataclass(frozen=True)
class WebhookHttpResponse:
    status_code: int
    payload: dict


class GithubWebhookHttpHandler:
    def __init__(
        self,
        verifier: WebhookSignatureVerifierPort,
        dispatch: IndexJobDispatchService,
    ) -> None:
        self._verifier = verifier
        self._dispatch = dispatch

    def handle(self, headers: dict[str, str], body: bytes) -> WebhookHttpResponse:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        signature = normalized_headers.get('x-hub-signature-256')
        event = normalized_headers.get('x-github-event')
        delivery_id = normalized_headers.get('x-github-delivery', 'github-delivery-missing')
        correlation_id = normalized_headers.get('x-correlation-id') or normalized_headers.get('x-request-id')
        if not correlation_id:
            correlation_id = f'corr-{delivery_id}'

        if not self._verifier.verify(body=body, signature_header=signature):
            return WebhookHttpResponse(
                status_code=401,
                payload={'status': 'error', 'reason': 'invalid_signature', 'correlation_id': correlation_id},
            )

        if event == 'ping':
            return WebhookHttpResponse(
                status_code=202,
                payload={'status': 'ok', 'event': 'ping', 'correlation_id': correlation_id},
            )

        if event != 'push':
            return WebhookHttpResponse(
                status_code=202,
                payload={
                    'status': 'ignored',
                    'event': event or 'missing_event_header',
                    'correlation_id': correlation_id,
                },
            )

        payload = json.loads(body.decode('utf-8'))
        sender = payload.get('sender', {}) if isinstance(payload, dict) else {}
        user_id = sender.get('login') if isinstance(sender, dict) else None
        job = self._dispatch.enqueue_from_github_push_with_context(
            payload=payload,
            trace_id=f'push-{delivery_id}',
            correlation_id=correlation_id,
        )
        return WebhookHttpResponse(
            status_code=202,
            payload={
                'status': 'queued',
                'repo': job.repo,
                'changed_files': len(job.changed_paths),
                'deleted_files': len(job.deleted_paths),
                'trace_id': job.trace_id,
                'correlation_id': correlation_id,
                'user_id': user_id,
            },
        )
