"""Authenticated native Pushover settings. Secrets never enter config responses."""

import asyncio
import copy
import re
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from . import config as config_api, tools
from src.integrations.pushover import (
    PushoverConfig,
    PHASES,
    get_sender,
    read_key,
    render_message,
)

router = APIRouter()
_write_lock = asyncio.Lock()


async def _body(request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 128000:
            raise HTTPException(413, "Request is too large")
        raw.extend(chunk)
    try:
        import json

        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise HTTPException(422, "Expected a JSON object") from None


def _configured():
    raw = tools._load_cfg().get("pushover") or {}
    try:
        return PushoverConfig.model_validate(raw)
    except Exception:
        raise HTTPException(
            422, "Repair invalid Pushover configuration before continuing"
        ) from None


async def _out(config):
    data = config.model_dump()
    data.pop("token_file", None)
    data["token_configured"] = bool(await read_key(config.token_file))
    for key, recipient in data["recipients"].items():
        recipient.pop("key_file", None)
        recipient["key_configured"] = bool(
            await read_key(config.recipients[key].key_file)
        )
    return data


@router.get("")
async def get_config():
    return await _out(await asyncio.to_thread(_configured))


@router.put("")
async def save_config(request: Request):
    raw = await _body(request)
    # Secret file references are server owned on this UI API. Raw YAML operators
    # may maintain references separately, but ordinary UI writes cannot redirect them.
    if not isinstance(raw.get("recipients", {}), dict):
        raise HTTPException(422, "Recipients must be an object")
    if "token_file" in raw or any(
        "key_file" in value
        for value in (raw.get("recipients") or {}).values()
        if isinstance(value, dict)
    ):
        raise HTTPException(422, "Credential references are managed separately")
    try:
        desired = PushoverConfig.model_validate(raw)
    except Exception:
        raise HTTPException(
            422, "Invalid Pushover settings, templates or summary provider"
        ) from None
    for phase, settings in desired.phases.items():
        if settings.generate_summary:
            await tools._validate_summary_provider_selection(
                {
                    "generate_summary": True,
                    "summary_provider": settings.summary_provider,
                    "phase": "in_call",
                }
            )
    async with _write_lock:
        cfg = await asyncio.to_thread(tools._load_cfg)
        current = PushoverConfig.model_validate(cfg.get("pushover") or {})
        desired.token_file = current.token_file
        for key, recipient in desired.recipients.items():
            recipient.key_file = (
                current.recipients[key].key_file if key in current.recipients else ""
            )
        cfg["pushover"] = desired.model_dump()
        result = await tools._persist_cfg(cfg)
    return {"config": await _out(desired), **tools._apply_metadata(result)}


@router.put("/credentials/{kind}")
async def replace_credential(kind: str, request: Request, recipient: str | None = None):
    raw = await _body(request)
    value = raw.get("value")
    if (
        set(raw) != {"value"}
        or not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9]{30}", value)
    ):
        raise HTTPException(
            422, "Enter a 30-character Pushover application or user/group key"
        )
    if kind not in {"application", "recipient"}:
        raise HTTPException(404, "Unknown credential type")
    async with _write_lock:
        cfg = await asyncio.to_thread(tools._load_cfg)
        current = PushoverConfig.model_validate(cfg.get("pushover") or {})
        if kind == "recipient" and recipient not in current.recipients:
            raise HTTPException(404, "Recipient does not exist")
        # Immutable files keep active calls on their captured credential/recipient.
        # Replaced/deleted files are retained for those calls; never overwritten.
        identifier = "pushover-" + uuid4().hex
        await asyncio.to_thread(
            config_api._write_provider_secret, identifier, "api-key", value.encode()
        )
        path = config_api._provider_secret_path(identifier, "api-key")
        if kind == "application":
            current.token_file = path
        else:
            current.recipients[recipient].key_file = path
        cfg["pushover"] = current.model_dump()
        result = await tools._persist_cfg(cfg)
    return {"config": await _out(current), **tools._apply_metadata(result)}


@router.delete("/credentials/application")
async def clear_application_credential():
    async with _write_lock:
        cfg = await asyncio.to_thread(tools._load_cfg)
        current = PushoverConfig.model_validate(cfg.get("pushover") or {})
        current.token_file = ""
        cfg["pushover"] = current.model_dump()
        result = await tools._persist_cfg(cfg)
    return {"config": await _out(current), **tools._apply_metadata(result)}


@router.post("/preview")
async def preview(request: Request):
    raw = await _body(request)
    phase = raw.get("phase")
    if phase not in PHASES:
        raise HTTPException(422, "Select a phase")
    try:
        cfg = PushoverConfig.model_validate(raw.get("config") or {})
    except Exception:
        raise HTTPException(422, "Invalid preview settings") from None
    values = {
        "agent": "Example Agent",
        "stage": {
            "pre_call": "arrival",
            "in_call": "handoff requested",
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
        "outcome": "pending" if phase != "post_call" else "caller_hangup",
        "language": "unknown",
        "meeting": "unknown",
        "summary": "Caller requested a consultation. Next step: staff follow-up.",
        "summary_status": "preview",
        "update_key": "handoff",
    }
    return {**render_message(cfg, phase, values), "preview_only": True}


async def _recipient_action(request, validate):
    raw = await _body(request)
    if set(raw) != {"recipient"}:
        raise HTTPException(422, "Select one saved recipient")
    cfg = await asyncio.to_thread(_configured)
    recipient = (
        cfg.recipients.get(raw["recipient"])
        if isinstance(raw["recipient"], str)
        else None
    )
    if not recipient or not recipient.enabled:
        raise HTTPException(422, "Recipient is unavailable")
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
    return await get_sender().send(
        token_file=cfg.token_file,
        key_file=recipient.key_file,
        message=message,
        timeout_ms=cfg.timeout_ms,
        validate=validate,
    )


@router.post("/validate")
async def validate_recipient(request: Request):
    return await _recipient_action(request, True)


@router.post("/test")
async def send_test(request: Request):
    return await _recipient_action(request, False)
