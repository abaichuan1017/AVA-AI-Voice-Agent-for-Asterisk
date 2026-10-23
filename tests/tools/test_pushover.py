"""Synthetic native notification acceptance; never sends a real notification."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from src.integrations.pushover import PushoverConfig, PushoverSender, render_message
from src.tools.context import ToolExecutionContext, PreCallContext, PostCallContext
from src.tools.pushover import (
    NotificationState,
    PushoverPhaseTool,
    configure_call_notifications,
    notification_values,
)
from src.tools.registry import ToolRegistry
from src.tools.base import ToolPhase
from src.post_call_summary import SummaryGenerationResult


def configured(**kwargs):
    return PushoverConfig.model_validate(
        {
            "enabled": True,
            "token_file": "app-file",
            "recipients": {"staff": {"label": "Staff", "key_file": "staff-file"}},
            "agents": {
                "sales": {
                    "enabled": True,
                    "pre_call": ["staff"],
                    "in_call": ["staff"],
                    "post_call": ["staff"],
                    "destination_key": "support",
                }
            },
            **kwargs,
        }
    )


def contexts():
    session = SimpleNamespace(
        conversation_history=[{"role": "user", "content": "Request a consultation"}],
        pre_call_results={"customer_name": "CRM Person", "company": "Example"},
        call_metadata={
            "customer_name": "Corrected",
            "customer_name_source": "caller_confirmed",
        },
        tool_calls=[],
        cleanup_in_progress=False,
        cleanup_completed=False,
    )
    live = ToolExecutionContext(
        call_id="synthetic",
        context_name="sales",
        caller_number="+15550102030",
        caller_name="Caller ID",
        session_store=SimpleNamespace(get_by_call_id=AsyncMock(return_value=session)),
    )
    post = PostCallContext(
        call_id="synthetic",
        context_name="sales",
        caller_number="+15550102030",
        conversation_history=session.conversation_history,
        tool_calls=session.tool_calls,
    )
    return live, post, session


def tool(phase="in_call", cfg=None, state=None, sender=None, generator=None):
    cfg = cfg or configured()
    return PushoverPhaseTool(
        cfg,
        cfg.agents["sales"],
        phase,
        "sales",
        state or NotificationState(),
        generator,
        sender=sender
        or SimpleNamespace(
            send=AsyncMock(return_value={"delivery_status": "accepted"})
        ),
        destination_aliases=("support", "Support team"),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"priority": 2},
        {"token": "secret"},
        {
            "phases": {
                "pre_call": {"generate_summary": True, "summary_provider": "x_llm"}
            }
        },
        {"phases": {"in_call": {"body": "{__class__}"}}},
        {"phases": {"post_call": {"generate_summary": True}}},
    ],
)
def test_configuration_fails_closed(change):
    with pytest.raises(ValidationError):
        configured(**change)


def test_render_is_single_pass_unicode_and_preserves_headers():
    cfg = configured(
        phases={
            "in_call": {
                "title": "{{AVA}} {name}",
                "body": "Call: {call_id}\n{summary}\nNext: {destination}",
            }
        }
    )
    rendered = render_message(
        cfg,
        "in_call",
        {
            "name": "{summary}",
            "call_id": "synthetic",
            "summary": "界" * 3000,
            "destination": "support",
        },
    )
    assert rendered["title"] == "{AVA} {summary}"
    assert len(rendered["message"]) <= 1024 and rendered["message"].endswith(
        "Next: support"
    )
    assert "sound" not in rendered and "url" not in rendered


@pytest.mark.asyncio
async def test_summary_and_dedup_are_per_call_and_legitimate_updates_work():
    live, _, session = contexts()
    sender = SimpleNamespace(
        send=AsyncMock(return_value={"delivery_status": "accepted"})
    )
    generator = AsyncMock(
        return_value=SummaryGenerationResult(
            text="Factual recap", provider="test_llm", status="ok"
        )
    )
    cfg = configured(
        phases={"in_call": {"generate_summary": True, "summary_provider": "test_llm"}}
    )
    notify = tool(cfg=cfg, sender=sender, generator=generator)
    a = await notify.execute({"update_key": "handoff"}, live)
    b = await notify.execute({"update_key": "handoff"}, live)
    assert (
        a["delivery_status"] == "accepted"
        and b["deduplicated"]
        and sender.send.await_count == 1
    )
    assert (
        generator.await_count == 1
        and "Factual recap" in sender.send.await_args.kwargs["message"]["message"]
    )
    session.conversation_history.append(
        {"role": "user", "content": "A separate request"}
    )
    await notify.execute({"update_key": "handoff"}, live)
    assert sender.send.await_count == 2
    session.tool_calls.append({"name": "blind_transfer", "status": "failure"})
    await notify.execute({"update_key": "handoff"}, live)
    assert sender.send.await_count == 3
    await notify.execute({"update_key": "handoff", "recipient": "arbitrary"}, live)
    assert sender.send.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,destination,accepted,expected",
    [
        ("transferred", "Support team", True, "suppressed"),
        ("caller_hangup", "Support team", True, "accepted"),
        ("transferred", "other", True, "accepted"),
        ("transferred", "Support team", False, "accepted"),
    ],
)
async def test_completion_suppression_requires_matching_accepted_briefing(
    outcome, destination, accepted, expected
):
    live, post, session = contexts()
    state = NotificationState()
    sender = SimpleNamespace(
        send=AsyncMock(
            return_value={"delivery_status": "accepted" if accepted else "uncertain"}
        )
    )
    await tool(state=state, sender=sender).execute({"update_key": "handoff"}, live)
    session.tool_calls.append({"name": "blind_transfer", "status": "success"})
    post.call_outcome = outcome
    post.transfer_destination = destination
    sender.send.return_value = {"delivery_status": "accepted"}
    result = await tool("post_call", state=state, sender=sender).execute(post)
    assert result["delivery_status"] == expected
    assert sender.send.await_count == (1 if expected == "suppressed" else 2)


@pytest.mark.asyncio
async def test_uncertain_send_and_cancellation_are_never_automatically_replayed():
    live, _, _ = contexts()
    state = NotificationState()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.Event().wait()

    sender = SimpleNamespace(send=slow)
    notify = tool(state=state, sender=sender)
    task = asyncio.create_task(notify.execute({"update_key": "handoff"}, live))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await notify.execute({"update_key": "handoff"}, live)
    assert result["deduplicated"] and result["delivery_status"] == "uncertain"


@pytest.mark.asyncio
async def test_disabled_deleted_cross_agent_and_hangup_fail_closed():
    live, _, session = contexts()
    notify = tool()
    sender = notify.sender
    live.context_name = "other"
    assert (await notify.execute({"update_key": "handoff"}, live))[
        "delivery_status"
    ] == "skipped"
    live.context_name = "sales"
    notify.config.recipients.clear()
    assert (await notify.execute({"update_key": "handoff"}, live))[
        "error_code"
    ] == "recipient_unavailable"
    assert not sender.send.called
    notify = tool()
    session.cleanup_in_progress = True
    assert (await notify.execute({"update_key": "handoff"}, live))[
        "delivery_status"
    ] == "skipped"
    assert not notify.sender.send.called


def test_generation_binding_does_not_broaden_or_change_existing_calls():
    cfg = configured().model_dump()
    registry = ToolRegistry.isolated()
    session = SimpleNamespace(context_name="sales", notification_state=None)
    bound = configure_call_notifications(registry, cfg, session, None)
    assert not registry.has("pushover_briefing") and bound.has("pushover_briefing")
    cfg["agents"]["sales"]["in_call"] = []
    cfg["recipients"].clear()
    assert bound.get("pushover_briefing").assignment.in_call == ["staff"]
    other = configure_call_notifications(
        registry, cfg, SimpleNamespace(context_name="other"), None
    )
    assert not other.has("pushover_briefing")
    assert (
        bound.get_tools_for_context(ToolPhase.IN_CALL, [], ["pushover_briefing"]) == []
    )


def test_identity_precedence_uses_source_and_unknown_values_truthfully():
    context = PreCallContext(
        call_id="synthetic", caller_number="123", caller_name="Unknown"
    )
    values = notification_values(context, phase="pre_call")
    assert values["name"] == "unknown" and values["crm_status"] == "unknown"
    values = notification_values(
        context,
        phase="pre_call",
        enrichment={"customer_name": "CRM"},
        metadata={
            "customer_name": "Correction",
            "customer_name_source": "caller_confirmed",
        },
    )
    assert (
        values["name"] == "Correction"
        and values["identity_source"] == "caller-confirmed"
    )
    values = notification_values(
        context,
        phase="pre_call",
        enrichment={"customer_name": "CRM"},
        metadata={"customer_name": "Seeded"},
    )
    assert values["name"] == "CRM"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (200, {"status": 1}, "accepted"),
        (400, {"status": 0, "errors": ["secret-echo"]}, "rejected"),
        (429, {"status": 0}, "rejected"),
        (500, {}, "uncertain"),
        (302, {}, "uncertain"),
        (200, {"status": 0}, "rejected"),
        (200, "malformed", "uncertain"),
    ],
)
async def test_sender_truthful_status_redaction_and_fixed_endpoint(
    tmp_path, status, payload, expected
):
    token = tmp_path / "app"
    token.write_text("a" * 30)
    user = tmp_path / "user"
    user.write_text("b" * 30)
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(
            status,
            content=(
                b"not-json" if payload == "malformed" else json.dumps(payload).encode()
            ),
        )

    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    result = await sender.send(
        token_file=str(token), key_file=str(user), message={"message": "synthetic"}
    )
    assert result["delivery_status"] == expected
    assert str(seen[0].url) == "https://api.pushover.net/1/messages.json"
    assert (
        "secret-echo" not in str(result)
        and "a" * 30 not in str(result)
        and len(seen) == 1
    )


@pytest.mark.asyncio
async def test_sender_caps_connections_across_independent_process_clients_and_releases(
    tmp_path,
):
    token = tmp_path / "key"
    token.write_text("a" * 30)
    active = 0
    maximum = 0

    async def handle(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.025)
            return httpx.Response(200, json={"status": 1})
        finally:
            active -= 1

    factory = lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(handle), **kw
    )
    senders = [
        PushoverSender(lock_dir=tmp_path / "locks", client_factory=factory)
        for _ in range(2)
    ]
    results = await asyncio.gather(
        *(
            senders[i % 2].send(
                token_file=str(token),
                key_file=str(token),
                message={"message": "synthetic"},
            )
            for i in range(8)
        )
    )
    assert maximum == 2 and all(r["delivery_status"] == "accepted" for r in results)
    assert all(s._pending == 0 for s in senders)


@pytest.mark.asyncio
async def test_validation_does_not_send_message_and_oversized_reply_is_bounded(
    tmp_path,
):
    token = tmp_path / "key"
    token.write_text("a" * 30)
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, content=b"x" * 20000)

    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kw
        ),
    )
    result = await sender.send(
        token_file=str(token), key_file=str(token), validate=True
    )
    assert result["error_code"] == "oversized_response"
    assert (
        seen[0].url.path == "/1/users/validate.json"
        and b"message=" not in seen[0].content
    )


@pytest.mark.asyncio
async def test_arrival_waits_for_enrichment_and_diagnostics_do_not_become_prompt_vars():
    from src.engine import Engine
    from src.core.models import CallSession

    engine = Engine.__new__(Engine)
    session = CallSession(
        call_id="synthetic-arrival",
        caller_channel_id="synthetic-arrival",
        context_name="sales",
    )
    order = []

    async def enrich(context):
        await asyncio.sleep(0)
        order.append("enrichment")
        return {"customer_name": "Synthetic CRM", "company": "Example"}

    enrichment = SimpleNamespace(
        definition=SimpleNamespace(
            name="lookup",
            timeout_ms=1000,
            hold_audio_file=None,
            hold_audio_threshold_ms=500,
        ),
        execute=enrich,
    )

    async def send(**kwargs):
        assert order == ["enrichment"]
        assert "Synthetic CRM" in kwargs["message"]["message"]
        order.append("arrival")
        return {"delivery_status": "accepted"}

    arrival = tool("pre_call", sender=SimpleNamespace(send=send))
    session.tool_runtime_registry = SimpleNamespace(
        get_tools_for_context=lambda **kwargs: [arrival, enrichment]
    )
    session.tool_runtime_config = {"tools": {}}
    engine.transport_orchestrator = SimpleNamespace(
        get_context_config=lambda *args: SimpleNamespace(
            pre_call_tools=[], disable_global_pre_call_tools=[]
        )
    )
    engine.config = SimpleNamespace()
    engine.ari_client = None
    engine._save_session = AsyncMock()
    result = await engine._execute_pre_call_tools(session.call_id, session)
    assert order == ["enrichment", "arrival"]
    assert result == {"customer_name": "Synthetic CRM", "company": "Example"}
    record = next(
        item
        for item in session.pre_call_tool_calls
        if item["name"] == "pushover_arrival"
    )
    assert record["delivery_status"] == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,expected", [("continue", 1), ("abort", 0)])
async def test_summary_failure_policy_and_empty_content_do_not_invent_success(
    policy, expected
):
    live, _, _ = contexts()
    cfg = configured(
        phases={
            "in_call": {
                "generate_summary": True,
                "summary_provider": "mock_llm",
                "summary_failure_policy": policy,
            }
        }
    )
    notify = tool(cfg=cfg)
    result = await notify.execute({"update_key": "handoff"}, live)
    assert notify.sender.send.await_count == expected
    assert result["summary_status"] == "error"
    assert result["delivery_status"] == ("accepted" if expected else "skipped")


@pytest.mark.asyncio
async def test_sender_waiter_capacity_and_timeout_do_not_send(tmp_path):
    sender = PushoverSender(lock_dir=tmp_path / "locks")
    sender._pending = 18
    result = await sender.send(token_file="unused", key_file="unused")
    assert result["delivery_status"] == "capacity" and sender._pending == 18


@pytest.mark.asyncio
async def test_explicit_rejection_opens_circuit_without_retry(tmp_path):
    token = tmp_path / "key"
    token.write_text("a" * 30)
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(429, json={"status": 0})

    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kw
        ),
    )
    first = await sender.send(
        token_file=str(token), key_file=str(token), message={"message": "synthetic"}
    )
    second = await sender.send(
        token_file=str(token),
        key_file="another-recipient",
        message={"message": "synthetic"},
    )
    assert (
        first["error_code"] == "quota_exceeded"
        and second["error_code"] == "rejection_cooldown"
        and len(seen) == 1
    )


def test_managed_name_collision_rejects_generation_before_any_call():
    from src.tools.runtime_config import ToolRuntimeGeneration, ToolConfigPolicyError

    cfg = {
        "pushover": configured().model_dump(),
        "in_call_tools": {
            "pushover_briefing": {
                "url": "https://example.com",
                "kind": "in_call_http_lookup",
            }
        },
    }
    with pytest.raises(ToolConfigPolicyError, match="Rename"):
        ToolRuntimeGeneration.build(generation_id=1, config=cfg)
    cfg["pushover"]["enabled"] = False
    assert ToolRuntimeGeneration.build(generation_id=2, config=cfg).registry.has(
        "pushover_briefing"
    )


@pytest.mark.asyncio
async def test_identical_words_in_a_later_caller_turn_are_a_distinct_update():
    live, _, session = contexts()
    notify = tool()
    await notify.execute({"update_key": "handoff"}, live)
    session.conversation_history.append(dict(session.conversation_history[-1]))
    await notify.execute({"update_key": "handoff"}, live)
    assert notify.sender.send.await_count == 2


@pytest.mark.asyncio
async def test_summary_abort_deduplicates_repeated_model_invocation():
    live, _, _ = contexts()
    cfg = configured(
        phases={
            "in_call": {
                "generate_summary": True,
                "summary_provider": "mock_llm",
                "summary_failure_policy": "abort",
            }
        }
    )
    generator = AsyncMock(
        return_value=SummaryGenerationResult(status="error", error_code="unavailable")
    )
    notify = tool(cfg=cfg, generator=generator)
    await notify.execute({"update_key": "handoff"}, live)
    second = await notify.execute({"update_key": "handoff"}, live)
    assert (
        generator.await_count == 1
        and second["deduplicated"]
        and not notify.sender.send.called
    )


@pytest.mark.asyncio
async def test_completion_reports_actual_destination_and_requires_verified_meeting_write():
    from src.tools.pushover import verified_meeting

    _, post, _ = contexts()
    post.call_outcome = "transferred"
    post.transfer_destination = "Actual support queue"
    post.call_metadata = {"confirmed_meeting": "Unverified conversational claim"}
    notify = tool("post_call")
    await notify.execute(post)
    body = notify.sender.send.await_args.kwargs["message"]["message"]
    assert "Actual support queue" in body
    values = notification_values(post, phase="post_call", metadata=post.call_metadata)
    assert values["meeting"] == "unknown"
    rows = [
        {
            "name": "microsoft_calendar",
            "status": "success",
            "action": "check_availability",
            "params": {
                "start_datetime": "2026-11-02T10:00:00-07:00",
                "end_datetime": "2026-11-02T10:30:00-07:00",
            },
        }
    ]
    assert verified_meeting(rows) == "unknown"
    rows.append({**rows[0], "action": "create_event", "target_id": "synthetic-event"})
    assert (
        verified_meeting(rows)
        == "2026-11-02T10:00:00-07:00 to 2026-11-02T10:30:00-07:00"
    )
    rows.append(
        {
            "name": "microsoft_calendar",
            "status": "success",
            "action": "delete_event",
            "target_id": "synthetic-event",
        }
    )
    assert verified_meeting(rows) == "unknown"


@pytest.mark.asyncio
async def test_sender_supports_backend_python_without_asyncio_timeout(
    tmp_path, monkeypatch
):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    token = tmp_path / "token"
    recipient = tmp_path / "recipient"
    token.write_text("a" * 30)
    recipient.write_text("b" * 30)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": 1})
    )
    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=transport, **kwargs
        ),
    )
    result = await sender.send(
        token_file=str(token), key_file=str(recipient), message={"message": "Synthetic"}
    )
    assert result["delivery_status"] == "accepted"
    assert sender._pending == 0
