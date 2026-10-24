"""Synthetic contract and failure-injection tests; never contacts Calendly."""
import asyncio
import copy
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_client import CalendlyClient, CalendlyError, appointment, resource_path, validate_config
from src.tools.business.calendly_service import CalendlyService, email
from src.tools.runtime_config import resolve_agent_tool_config
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string

USER = "https://api.calendly.com/users/alex"
ORG = "https://api.calendly.com/organizations/company"
TYPE = "https://api.calendly.com/event_types/consultation"
EVENT = "https://api.calendly.com/scheduled_events/booking"
INVITEE = EVENT + "/invitees/person"
IDENTITY = {"uri": USER, "current_organization": ORG, "name": "Alex", "timezone": "America/Los_Angeles"}


class FakeClient(CalendlyClient):
    def __init__(self):
        self.raw = {"uri": TYPE, "name": "Consultation", "duration": 30, "active": True,
                    "is_paid": False, "deleted_at": None, "kind": "solo", "pooling_type": None,
                    "type": "StandardEventType", "booking_method": "instant",
                    "profile": {"type": "User", "owner": USER, "name": "Alex"},
                    "custom_questions": [], "locations": [], "updated_at": "2026-01-01T00:00:00Z"}
        self.start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
        self.invitee = None
        self.event = None
        self.posts = 0
        self.lost = False
        self.reject = False
        self.calls = []
        self.accepted = asyncio.Event()
        self.pause = None
        self.no_match = False
        self.missing_marker = False
        self.overflow = False

    async def request(self, method, path, *, params=None, body=None, timeout=5):
        self.calls.append((method, path, params))
        if path == "/users/me":
            return {"resource": copy.deepcopy(IDENTITY)}
        if path == "/event_types":
            return {"collection": [copy.deepcopy(self.raw)], "pagination": {}}
        if path == "/event_types/consultation":
            return {"resource": copy.deepcopy(self.raw)}
        if path == "/event_type_available_times":
            return {"collection": [{"status": "available", "invitees_remaining": 1, "start_time": self.start}]}
        if method == "POST":
            self.posts += 1
            if self.reject:
                raise CalendlyError("upstream_400")
            self.invitee = {**copy.deepcopy(body["invitee"]), "uri": INVITEE, "event": EVENT,
                            "status": "active", "tracking": copy.deepcopy(body["tracking"]),
                            "questions_and_answers": copy.deepcopy(body["questions_and_answers"]),
                            "cancel_url": "https://calendly.com/cancellations/private",
                            "reschedule_url": "https://calendly.com/reschedulings/private"}
            self.event = {"uri": EVENT, "event_type": TYPE, "status": "active", "start_time": self.start,
                          "end_time": (datetime.fromisoformat(self.start)+timedelta(minutes=30)).isoformat(),
                          "event_memberships": [{"user": USER}]}
            self.accepted.set()
            if self.pause:
                await self.pause.wait()
            if self.lost:
                raise CalendlyError("network_unavailable", uncertain=True)
            return {"resource": copy.deepcopy(self.invitee)}
        if path == "/scheduled_events":
            rows = [] if self.no_match or not self.event or params.get("status") != self.event["status"] else [copy.deepcopy(self.event)]
            return {"collection": rows, "pagination": {"next_page_token": "more"} if self.overflow else {}}
        if path == "/scheduled_events/booking":
            return {"resource": copy.deepcopy(self.event)}
        if path == "/scheduled_events/booking/invitees":
            rows = [] if not self.invitee or params.get("status") != self.invitee["status"] else [copy.deepcopy(self.invitee)]
            if self.missing_marker:
                for row in rows:
                    row["tracking"] = {}
            return {"collection": rows, "pagination": {}}
        if path == "/scheduled_events/booking/invitees/person":
            return {"resource": copy.deepcopy(self.invitee)}
        raise AssertionError((method, path))


@pytest.fixture
def setup(tmp_path):
    store = CalendlyStore(str(tmp_path / "data" / "journal.db"), str(tmp_path / "secrets" / "token.json"))
    conn = store.save_connection(IDENTITY, "synthetic-private-token", 0)
    store.update_connection(conn["revision"], enabled=True)
    fake = FakeClient()
    service = CalendlyService(store, lambda token: fake)
    cfg = {"enabled": True, "appointments": {"consultation": {"uri": TYPE, "connection_id": conn["id"], "one_to_one_confirmed": True, "name": "Consultation", "duration": 30}}}
    return store, service, fake, cfg


async def propose(service, fake, cfg, operation="call:one", setup=False):
    start = datetime.now(timezone.utc) + timedelta(minutes=1)
    result = await service.slots(cfg, operation, "consultation", start.isoformat(), (start+timedelta(days=7)).isoformat(), "America/Los_Angeles", setup=setup)
    return {"slot_reference": result["slots"][0]["slot_reference"], "name": "Test Invitee", "email": "test@example.com", "answers": [], "booking_confirmed": True, "email_confirmed": True}


async def due(store, key):
    with store.db() as db:
        db.execute("UPDATE attempts SET lease=0,next_due=0 WHERE id=?", (key,))


async def test_success_duplicate_and_private_result(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    result = await service.book(cfg, "call:one", params)
    assert result["reservation_status"] == "confirmed"
    assert fake.posts == 1
    assert await service.book(cfg, "call:one", {**params, "email": "changed@example.com"}) == result
    assert "test@example.com" not in json.dumps(result)
    assert "cancellations" not in json.dumps(result)
    row = store.attempt(result["attempt_reference"])
    assert "payload" not in json.loads(row["private"])
    assert len(fake.invitee["tracking"]) == 6
    assert fake.invitee["tracking"]["utm_content"].startswith("ava-v1-")
    assert "call:one" not in json.dumps(fake.invitee["tracking"])


async def test_read_rate_limit_is_shared_after_restart(setup):
    store, service, fake, cfg = setup
    async def limited(*args, **kwargs):
        raise CalendlyError("upstream_429", cooldown=60)
    fake.request = limited
    with pytest.raises(CalendlyError, match="upstream_429"):
        await service.discovery()
    restarted = CalendlyStore(str(store.path), str(store.token_path))
    assert restarted.connection()["cooldown"] > time.time()
    with pytest.raises(CalendlyError, match="rate_limited"):
        restarted.credentials()


async def test_identity_denial_is_distinct_from_booking_eligibility(setup):
    _, service, fake, _ = setup
    async def denied(*args, **kwargs):
        raise CalendlyError("upstream_403")
    fake.request = denied
    with pytest.raises(CalendlyError, match="identity_access_denied"):
        await service.verify("synthetic-private-token")
    assert fake.posts == 0


async def test_verify_needs_candidate_or_saved_token_and_does_not_require_enabled_booking(setup):
    store, service, fake, _ = setup
    conn = store.connection()
    store.update_connection(conn['revision'], enabled=False)
    assert (await service.verify())['uri'] == USER
    assert fake.posts == 0
    store.disconnect(conn['revision'])
    with pytest.raises(CalendlyError, match='verification_token_required'):
        await service.verify()


async def test_setup_can_preview_while_paused_but_cannot_submit(setup):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()['revision'], enabled=False)
    params = await propose(service, fake, cfg, operation='setup:preview', setup=True)
    with pytest.raises(CalendlyError, match='connection_disabled'):
        await service.book(cfg, 'setup:preview', params)
    with pytest.raises(CalendlyError, match='connection_disabled'):
        await propose(service, fake, cfg)
    assert fake.posts == 0 and store.prior('setup:preview') is None


async def test_lost_response_reconciles_after_restart_without_post(setup):
    store, service, fake, cfg = setup
    conn = store.connection()
    store.update_connection(conn["revision"], qualified=True)
    fake.lost = True
    result = await service.book(cfg, "call:one", await propose(service, fake, cfg))
    assert result["status"] == "pending"
    key = result["attempt_reference"]
    await due(store, key)
    restarted = CalendlyService(CalendlyStore(str(store.path), str(store.token_path)), lambda token: fake)
    recovered = await restarted.reconcile(key)
    assert recovered["reservation_status"] == "confirmed"
    assert fake.posts == 1
    query = next(c[2] for c in fake.calls if c[1] == "/scheduled_events")
    assert query["user"] == USER and query["invitee_email"] == "test@example.com"
    assert "event_type" not in query and "utm_content" not in query


@pytest.mark.parametrize("failure", ["no_match", "missing_marker", "overflow"])
async def test_no_negative_proof_or_retry(setup, failure):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()["revision"], qualified=True)
    fake.lost = True
    params = await propose(service, fake, cfg)
    result = await service.book(cfg, "call:one", params)
    setattr(fake, failure, True)
    key = result["attempt_reference"]
    for _ in range(3):
        await due(store, key)
        result = await service.reconcile(key)
    assert result["reservation_status"] == "needs_operator"
    assert (await service.book(cfg, "call:one", params))["status"] == "pending"
    assert fake.posts == 1


async def test_unqualified_discovery_and_external_resolution(setup):
    store, service, fake, cfg = setup
    fake.lost = True
    params = await propose(service, fake, cfg)
    result = await service.book(cfg, "call:one", params)
    key = result["attempt_reference"]
    await due(store, key)
    assert (await service.reconcile(key))["reservation_status"] == "needs_operator"
    store.resolve(key, "admin", "Verified externally")
    assert (await service.book(cfg, "call:one", params))["reservation_status"] == "resolved_externally"
    assert fake.posts == 1


async def test_setup_readback_qualification(setup):
    store, service, fake, cfg = setup
    operation = "setup:123"
    result = await service.book(cfg, operation, await propose(service, fake, cfg, operation, setup=True))
    assert not store.connection()["qualified"]
    await service.reconcile(result["attempt_reference"], qualification=True)
    assert store.connection()["qualified"]
    assert fake.posts == 1


async def test_remote_cancelled_is_not_active_success(setup):
    store, service, fake, cfg = setup
    fake.lost = True
    store.update_connection(store.connection()["revision"], qualified=True)
    result = await service.book(cfg, "call:one", await propose(service, fake, cfg))
    fake.event["status"] = fake.invitee["status"] = "canceled"
    await due(store, result["attempt_reference"])
    result = await service.reconcile(result["attempt_reference"])
    assert result["reservation_status"] == "resolved_remote_cancelled"
    assert result["status"] != "success" and fake.posts == 1


async def test_concurrent_same_operation_one_post(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    fake.pause = asyncio.Event()
    first = asyncio.create_task(service.book(cfg, "call:one", params))
    await asyncio.wait_for(fake.accepted.wait(), 3)
    second = await service.book(cfg, "call:one", params)
    assert second["status"] == "pending"
    fake.pause.set()
    assert (await first)["status"] == "success"
    assert fake.posts == 1


async def test_cancel_after_remote_commit_retains_journal(setup):
    store, service, fake, cfg = setup
    fake.pause = asyncio.Event()
    params = await propose(service, fake, cfg)
    task = asyncio.create_task(service.book(cfg, "call:one", params))
    await asyncio.wait_for(fake.accepted.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.prior("call:one")["state"] == "uncertain"
    assert (await service.book(cfg, "call:one", params))["status"] == "pending"
    assert fake.posts == 1


async def test_disconnect_and_different_account_fail_closed(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    store.disconnect(store.connection()["revision"])
    with pytest.raises(CalendlyError):
        await service.book(cfg, "call:one", params)
    conn = store.save_connection({**IDENTITY, "uri": USER + "2"}, "new-synthetic-token", store.connection()["revision"])
    store.update_connection(conn["revision"], enabled=True)
    with pytest.raises(CalendlyError):
        await service.book(cfg, "call:one", params)
    assert fake.posts == 0


@pytest.mark.parametrize("field", ["email_confirmed", "booking_confirmed"])
async def test_no_confirmation_no_post(setup, field):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    params[field] = False
    with pytest.raises(CalendlyError, match="confirmation_required"):
        await service.book(cfg, "call:one", params)
    assert fake.posts == 0


async def test_metadata_change_requires_reconfirmation(setup):
    _, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    fake.raw["duration"] = 45
    with pytest.raises(CalendlyError, match="proposal_changed_reconfirm"):
        await service.book(cfg, "call:one", params)
    assert fake.posts == 0


async def test_required_questions_exact_payload_and_redaction(setup):
    _, service, fake, cfg = setup
    fake.raw["custom_questions"] = [{"name": "Company?", "position": 2, "enabled": True, "required": True, "type": "single_select", "answer_choices": ["One", "Two"]}]
    params = await propose(service, fake, cfg)
    with pytest.raises(CalendlyError):
        await service.book(cfg, "call:one", params)
    params["answers"] = [{"position": 2, "answer": "One"}]
    assert (await service.book(cfg, "call:one", params))["status"] == "success"
    assert fake.invitee["questions_and_answers"] == [{"question": "Company?", "position": 2, "answer": "One"}]


@pytest.mark.parametrize("patch", [{"kind": "group"}, {"pooling_type": "round_robin"}, {"is_paid": True}, {"duration_options": [15, 30]}, {"active": False}, {"booking_method": "poll"}])
def test_unsupported_metadata(setup, patch):
    _, _, fake, _ = setup
    assert not appointment({**fake.raw, **patch}, USER)["supported"]


def test_paths_config_email_and_scope(setup):
    _, _, _, cfg = setup
    validate_config(cfg)
    with pytest.raises(ValueError):
        validate_config({**cfg, "token": "must-not-be-in-yaml"})
    for uri in ["https://evil.invalid/users/a", "http://api.calendly.com/users/a", "https://api.calendly.com/users/../a", USER+"?token=x"]:
        with pytest.raises(CalendlyError): resource_path(uri)
    assert email("Test+tag@EXAMPLE.com") == "Test+tag@example.com"


def test_existing_calls_do_not_gain_calendly_without_explicit_tool_assignment():
    from src.tools.base import ToolPhase
    from src.tools.registry import ToolRegistry
    from src.tools.business.calendly_calendar import CalendlyCalendarTool
    registry = ToolRegistry.isolated()
    registry.register(CalendlyCalendarTool())
    assert registry.get_tools_for_context(ToolPhase.IN_CALL, context_tool_names=[]) == []
    assert [tool.definition.name for tool in registry.get_tools_for_context(
        ToolPhase.IN_CALL, context_tool_names=['calendly_calendar'])] == ['calendly_calendar']
    with pytest.raises(CalendlyError): email("test@example.com\r\nBcc: other@example.com")
    scoped = resolve_agent_tool_config({"tools": {"calendly_calendar": cfg}}, {"calendly_calendar": {"appointment_policy": "selected", "appointment_keys": []}})
    assert scoped.config["tools"]["calendly_calendar"]["selected_appointments"] == []


def test_permission_and_retention_tombstone(setup):
    store, _, _, cfg = setup
    assert store.token_path.stat().st_mode & 0o077 == 0
    assert store.path.stat().st_mode & 0o077 == 0
    payload = {"event_type": TYPE, "start_time": "2030-01-01T10:00:00+00:00", "invitee": {"email": "test@example.com"}}
    row, admitted = store.admit("call:retained", store.connection(), payload, {})
    assert admitted
    store.cleanup(["retained"])
    assert store.attempt(row["id"])["private"] == "{}"
    assert store.prior("call:retained") is not None
    with store.db() as db:
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 1000


def test_provider_structured_slots_survive():
    result = sanitize_tool_result_for_json_string({"status": "success", "slots": [{"slot_reference": "safe", "start_datetime": "2030-01-01T12:00:00Z"}], "questions": [{"question": "Purpose?"}], "private": {"token": "secret"}}, tool_name="calendly_calendar")
    assert result["slots"][0]["slot_reference"] == "safe"
    assert "private" not in result
