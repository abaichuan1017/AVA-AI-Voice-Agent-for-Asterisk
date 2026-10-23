import copy
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from api import pushover
from src.integrations.pushover import PushoverConfig


@pytest.fixture
def client(monkeypatch):
    state = {
        "pushover": PushoverConfig.model_validate(
            {
                "token_file": "immutable-app",
                "recipients": {
                    "staff": {"label": "Staff", "key_file": "immutable-user"}
                },
            }
        ).model_dump()
    }
    monkeypatch.setattr(pushover.tools, "_load_cfg", lambda: copy.deepcopy(state))

    async def persist(cfg):
        state.clear()
        state.update(copy.deepcopy(cfg))
        return {"apply_required": True, "restart_required": False}

    monkeypatch.setattr(pushover.tools, "_persist_cfg", persist)
    monkeypatch.setattr(pushover, "read_key", AsyncMock(return_value="x" * 30))
    sender = AsyncMock()
    sender.send.return_value = {"delivery_status": "accepted"}
    monkeypatch.setattr(pushover, "get_sender", lambda: sender)
    app = FastAPI()
    app.include_router(pushover.router, prefix="/api/pushover")
    with TestClient(app) as browser:
        yield browser, state, sender


def writable(browser):
    value = browser.get("/api/pushover").json()
    value.pop("token_configured")
    for recipient in value["recipients"].values():
        recipient.pop("key_configured")
    return value


def test_masked_round_trip_never_sends(client):
    browser, state, sender = client
    response = browser.get("/api/pushover")
    assert response.json()["token_configured"]
    assert "immutable" not in response.text and "token_file" not in response.text
    value = writable(browser)
    value["enabled"] = True
    assert browser.put("/api/pushover", json=value).status_code == 200
    assert (
        state["pushover"]["token_file"] == "immutable-app"
        and state["pushover"]["recipients"]["staff"]["key_file"] == "immutable-user"
    )
    assert not sender.send.called


@pytest.mark.parametrize(
    "body",
    [
        {"token": "should-not-echo"},
        {"recipients": []},
        {"token_file": "/arbitrary/path"},
        {"phases": {"in_call": {"body": "{secret}"}}},
    ],
)
def test_invalid_config_is_sanitized(client, body):
    browser, _, sender = client
    result = browser.put("/api/pushover", json=body)
    assert (
        result.status_code == 422
        and "should-not-echo" not in result.text
        and not sender.send.called
    )


def test_preview_validate_and_test_are_separate(client):
    browser, _, sender = client
    result = browser.post(
        "/api/pushover/preview", json={"phase": "in_call", "config": {}}
    )
    assert (
        result.status_code == 200
        and "handoff requested" in result.json()["title"]
        and not sender.send.called
    )
    assert (
        browser.post("/api/pushover/validate", json={"recipient": "staff"}).status_code
        == 200
    )
    assert (
        sender.send.call_args.kwargs["validate"]
        and sender.send.call_args.kwargs["message"] is None
    )
    assert (
        browser.post("/api/pushover/test", json={"recipient": "staff"}).status_code
        == 200
    )
    assert not sender.send.call_args.kwargs["validate"]
    assert (
        browser.post("/api/pushover/test", json={"recipient": "missing"}).status_code
        == 422
    )
    assert sender.send.call_count == 2


def test_credential_rotation_is_immutable_and_redacted(client, monkeypatch):
    browser, state, sender = client
    written = []
    monkeypatch.setattr(
        pushover.config_api,
        "_write_provider_secret",
        lambda name, kind, value: written.append((name, kind, value)),
    )
    monkeypatch.setattr(
        pushover.config_api,
        "_provider_secret_path",
        lambda name, kind: "/managed/" + name,
    )
    value = "a" * 30
    first = browser.put(
        "/api/pushover/credentials/recipient?recipient=staff", json={"value": value}
    )
    path = state["pushover"]["recipients"]["staff"]["key_file"]
    second = browser.put(
        "/api/pushover/credentials/recipient?recipient=staff", json={"value": value}
    )
    assert first.status_code == second.status_code == 200
    assert path != state["pushover"]["recipients"]["staff"]["key_file"]
    assert (
        value not in first.text
        and "/managed/" not in second.text
        and len(written) == 2
        and not sender.send.called
    )
    invalid = browser.put(
        "/api/pushover/credentials/application",
        json={"value": "sensitive-invalid-value"},
    )
    assert invalid.status_code == 422 and "sensitive-invalid-value" not in invalid.text


def test_deleted_recipient_assignment_remains_fail_closed(client):
    browser, state, _ = client
    state["pushover"]["agents"] = {"sales": {"enabled": True, "in_call": ["staff"]}}
    cfg = writable(browser)
    cfg["recipients"] = {}
    assert browser.put("/api/pushover", json=cfg).status_code == 200
    assert (
        state["pushover"]["agents"]["sales"]["in_call"] == ["staff"]
        and not state["pushover"]["recipients"]
    )


def test_native_names_reserved_without_breaking_existing_http_names():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        pushover.tools._validate_tool_name("pushover_briefing")
    pushover.tools._validate_tool_name("pushover_legacy_webhook")
    assert pushover.tools._classify_tool_source("pushover_briefing", set()) == "builtin"
