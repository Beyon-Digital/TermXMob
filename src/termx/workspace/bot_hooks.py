"""The signed webhook endpoint for bot triggers.

``POST /hooks/bots/{bot_id}/{schedule_id}`` is outside ``/api`` on purpose: callers are other services, not signed-in
users, and the only credential is the HMAC signature. A webhook trigger is off until the owner turns it on, and the
host's network exposure is the owner's choice. The payload is queued as data for the bot; it grants no authority.

Headers: ``X-Termx-Timestamp`` (unix seconds) and ``X-Termx-Signature`` (``sha256=`` and the hex HMAC-SHA256 of
``<timestamp>.<body>`` keyed with the trigger's secret).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from termx.workspace.bot_triggers import WEBHOOK_MAX_BYTES, WebhookError


def mount_bot_hooks(app, state):
    router = APIRouter()

    @router.post('/hooks/bots/{bot_id}/{schedule_id}', status_code=202)
    async def receive(bot_id: str, schedule_id: str, request: Request):
        declared = request.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > WEBHOOK_MAX_BYTES:
            raise HTTPException(413, 'Payload too large')
        body = bytearray()
        async for chunk in request.stream():  # a client that lies about its length is cut off at the cap
            body.extend(chunk)
            if len(body) > WEBHOOK_MAX_BYTES:
                raise HTTPException(413, 'Payload too large')
        try:
            state.workspace.bots.triggers.accept_webhook(
                bot_id, schedule_id, bytes(body), request.headers.get('x-termx-timestamp'), request.headers.get('x-termx-signature'))
        except WebhookError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        return {'accepted': True}

    app.include_router(router)
