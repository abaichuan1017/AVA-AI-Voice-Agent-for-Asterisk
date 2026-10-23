import copy
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from api import pushover, tools


@pytest.fixture
def client(monkeypatch):
    state = {"tools": {}, "in_call_tools": {}}
    monkeypatch.setattr(tools, "_load_cfg", lambda: copy.deepcopy(state))

    async def persist(cfg):
        state.clear()
        state.update(copy.deepcopy(cfg))
        return {"apply_required": True, "restart_required": False}

    monkeypatch.setattr(tools, "_persist_cfg", persist)
    monkeypatch.setattr(tools, "_reserved_builtin_cache", frozenset())
    sender = AsyncMock()
    sender.send.return_value = {"delivery_status": "accepted"}
    monkeypatch.setattr(pushover, "get_sender", lambda: sender)
    writes = []
    monkeypatch.setattr(
        pushover.config_api,
        "_write_provider_secret",
        lambda name, kind, value: writes.append((name, value)),
    )
    monkeypatch.setattr(
        pushover.config_api,
        "_provider_secret_path",
        lambda name, kind: "/managed/" + name,
    )
    app = FastAPI()
    app.include_router(pushover.router, prefix="/api/pushover")
    app.include_router(tools.router, prefix="/api/tools")
    with TestClient(app) as browser:
        yield browser, state, sender, writes


def test_named_tools_use_normal_crud_and_catalog_classification(client):
    browser, state, sender, _ = client
    body = {
        "name": "notify_sales",
        "kind": "pushover",
        "phase": "in_call",
        "description": "Use before Sales handoff",
        "recipient_label": "Sales",
        "body": "{summary}",
    }
    result = browser.post("/api/tools/managed", json=body)
    assert result.status_code == 201, result.text
    assert state["in_call_tools"]["notify_sales"]["description"] == body["description"]
    assert tools._http_tool_names_from_config(state) == {"notify_sales"}
    assert browser.get("/api/tools/managed/notify_sales").json()["kind"] == "pushover"
    result = browser.patch(
        "/api/tools/managed/notify_sales",
        json={"phase": "post_call", "is_global": False},
    )
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "pushover" and "notify_sales" in state["tools"]
    assert "notify_sales" not in state["in_call_tools"]
    assert browser.delete("/api/tools/managed/notify_sales").status_code == 200
    assert not sender.send.called


def test_credential_staging_is_immutable_masked_and_does_not_save_tool_or_send(client):
    browser, state, sender, writes = client
    first = browser.post(
        "/api/pushover/credentials", json={"token": "a" * 30, "recipient": "b" * 30}
    )
    second = browser.post("/api/pushover/credentials", json={"token": "c" * 30})
    assert (
        first.status_code == 200
        and first.json()["token_file"] != second.json()["token_file"]
    )
    assert "a" * 30 not in first.text and len(writes) == 3
    assert state == {"tools": {}, "in_call_tools": {}} and not sender.send.called
    bad = browser.post("/api/pushover/credentials", json={"token": "secret-invalid"})
    assert (
        bad.status_code == 422 and "secret-invalid" not in bad.text and len(writes) == 3
    )


def test_preview_validation_and_explicit_test_are_independent(client):
    browser, _, sender, _ = client
    config = {
        "kind": "pushover",
        "phase": "in_call",
        "title": "Booking update",
        "body": "{summary}",
        "token_file": "/managed/app",
        "key_file": "/managed/user",
    }
    result = browser.post("/api/pushover/preview", json={"config": config})
    assert result.status_code == 200 and result.json()["title"] == "Booking update"
    assert not sender.send.called
    assert (
        browser.post("/api/pushover/validate", json={"config": config}).status_code
        == 200
    )
    assert (
        sender.send.call_args.kwargs["validate"]
        and sender.send.call_args.kwargs["message"] is None
    )
    assert (
        browser.post("/api/pushover/test", json={"config": config}).status_code == 200
    )
    assert not sender.send.call_args.kwargs["validate"] and sender.send.call_count == 2
    result = browser.post(
        "/api/pushover/preview", json={"config": {**config, "token": "secret-invalid"}}
    )
    assert result.status_code == 422 and "secret-invalid" not in result.text


def test_native_validation_rejects_unsupported_phase_summary_and_endpoint(client):
    browser, _, sender, _ = client
    for extra in (
        {"url": "https://attacker.invalid"},
        {"priority": 2},
        {"generate_summary": True},
        {"phase": "pre_call", "generate_summary": True, "summary_provider": "test_llm"},
    ):
        result = browser.post(
            "/api/tools/managed",
            json={"name": "invalid", "kind": "pushover", "phase": "in_call", **extra},
        )
        assert result.status_code == 422, result.text
    assert not sender.send.called


def test_full_document_validation_rejects_native_secret_without_echoing():
    from api.config import _validate_ai_agent_config
    from fastapi import HTTPException
    import yaml
    with pytest.raises(HTTPException) as error:
        _validate_ai_agent_config(yaml.safe_dump({"in_call_tools": {"notify": {"kind": "pushover", "phase": "in_call", "token": "secret-value-never-echo"}}}))
    assert error.value.status_code == 400
    assert "secret-value" not in str(error.value.detail)


def test_full_document_validation_protects_builtin_tool_names():
    from api.config import _validate_ai_agent_config
    from fastapi import HTTPException
    import yaml
    with pytest.raises(HTTPException) as error:
        _validate_ai_agent_config(yaml.safe_dump({"in_call_tools": {"hangup_call": {"kind": "pushover", "phase": "in_call"}}}))
    assert error.value.status_code == 400
