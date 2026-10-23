"""Authenticated credential staging and explicit checks for managed Pushover tools.

Tool definitions use the normal tools/in_call_tools config and managed-tool API.
Only opaque immutable file references, never credential values, leave this API.
"""

import asyncio
import json
import re
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from . import config as config_api
from src.integrations.pushover import PushoverToolConfig, get_sender, render_message

router = APIRouter()


async def _body(request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 128000:
            raise HTTPException(413, "Request is too large")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise HTTPException(422, "Expected a JSON object") from None


async def _credentials(raw):
    if not isinstance(raw, dict) or set(raw) - {"token", "recipient"}:
        raise HTTPException(422, "Enter application and recipient credentials")
    # Validate all fields before creating any files. Never echo rejected input.
    if any(
        not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9]{30}", v)
        for v in raw.values()
    ):
        raise HTTPException(422, "Credentials require 30 alphanumeric characters")
    refs = {}
    for field, value in raw.items():
        identifier = "pushover-tool-" + uuid4().hex
        await asyncio.to_thread(
            config_api._write_provider_secret, identifier, "api-key", value.encode()
        )
        refs["token_file" if field == "token" else "key_file"] = (
            config_api._provider_secret_path(identifier, "api-key")
        )
    return refs


@router.post("/credentials")
async def stage_credentials(request: Request):
    # Config is staged separately by the usual Update tool → Save & Apply flow.
    return await _credentials(await _body(request))


def _config(raw):
    try:
        return PushoverToolConfig.model_validate(raw)
    except Exception:
        raise HTTPException(422, "Invalid Pushover tool configuration") from None


def preview_values(phase):
    return {
        "agent": "Example Agent",
        "stage": {
            "pre_call": "arrival",
            "in_call": "in-call update",
            "post_call": "completion",
        }[phase],
        "name": "Example Caller",
        "company": "Example Company",
        "caller_number": "+15550102030",
        "callback_number": "unknown",
        "call_id": "synthetic-preview",
        "crm_status": "unknown",
        "identity_source": "caller-confirmed",
        "destination": "support",
        "outcome": "caller_hangup" if phase == "post_call" else "pending",
        "language": "unknown",
        "meeting": "unknown",
        "summary": "Caller requested a consultation. Next step: staff follow-up.",
        "summary_status": "preview",
        "update_key": "",
    }


@router.post("/preview")
async def preview(request: Request):
    raw = await _body(request)
    cfg = _config(raw.get("config") or {})
    return {
        **render_message(cfg, cfg.phase, preview_values(cfg.phase)),
        "preview_only": True,
    }


async def _action(request, validate):
    raw = await _body(request)
    cfg = _config(raw.get("config") or {})
    refs = await _credentials(raw.get("credentials") or {})
    cfg = cfg.model_copy(update=refs)
    # Explicit tests use fixed synthetic content and do not call summary models.
    message = (
        None
        if validate
        else {
            "title": "AVA test notification",
            "message": "Explicit operator-requested AVA test. Acceptance does not prove display or acknowledgement.",
            "priority": cfg.priority,
            **({"sound": cfg.sound} if cfg.sound else {}),
        }
    )
    result = await get_sender().send(
        token_file=cfg.token_file,
        key_file=cfg.key_file,
        message=message,
        timeout_ms=cfg.timeout_ms,
        validate=validate,
    )
    return {**result, "credential_refs": refs}


@router.post("/validate")
async def validate_recipient(request: Request):
    return await _action(request, True)


@router.post("/test")
async def send_test(request: Request):
    return await _action(request, False)
