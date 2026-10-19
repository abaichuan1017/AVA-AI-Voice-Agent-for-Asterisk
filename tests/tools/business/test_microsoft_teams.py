"""Pure, synthetic Teams body and model-output boundary regressions."""

import pytest

from src.tools.business.microsoft_booking import BookingValidationError
from src.tools.business.microsoft_teams import (
    replace_details,
    teams_body,
    join_url,
    teams_enabled,
)
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string


def test_body_refresh_preserves_every_byte_outside_owned_details_and_escapes_caller_text():
    prefix = '<html>\n<body><div class="outside">Microsoft content</div>'
    suffix = '<div><a href="https://teams.microsoft.com/l/synthetic?a=1&amp;b=2">Join</a></div></body></html>'
    body = {
        "contentType": "html",
        "content": prefix + teams_body("Original")["content"] + suffix,
    }
    updated = replace_details(
        body, '<div id="ava-booking-details">caller</div>\n{{literal}}'
    )

    assert updated["content"].startswith(prefix) and updated["content"].endswith(suffix)
    assert (
        "&lt;div id=&quot;ava-booking-details&quot;&gt;caller&lt;/div&gt;<br>{{literal}}"
        in updated["content"]
    )


@pytest.mark.parametrize(
    "prefix,old",
    [
        ("<p>Before\u2028</p>\n", "OLD DETAILS"),
        ("<p>Before\u2028</p>", "OLD DETAILS\nMORE OLD DETAILS"),
        ("<p>Before</p>", "OLD DETAILS\u2028\nMORE OLD DETAILS"),
    ],
)
def test_details_splice_uses_htmlparser_lf_offsets_with_unicode_separator(prefix, old):
    opening = '<div id="ava-booking-details">'
    suffix = "</div><p>Preserved Microsoft content</p>"
    result = replace_details(
        {"contentType": "html", "content": prefix + opening + old + suffix},
        "NEW DETAILS",
    )
    assert result["content"] == prefix + opening + "NEW DETAILS" + suffix
    assert "OLD DETAILS" not in result["content"]


@pytest.mark.parametrize(
    "content",
    [
        "<div>unknown</div>",
        '<div id="ava-booking-details">unclosed',
        '<div id="ava-booking-details">a</div><div id="ava-booking-details">b</div>',
        '<div id="ava-booking-details"><div id="ava-booking-details">nested</div></div>',
        '<div id="ava-booking-details"/>',
    ],
)
def test_unrecognizable_or_ambiguous_owned_body_refuses_rewrite(content):
    with pytest.raises(BookingValidationError):
        replace_details({"contentType": "HTML", "content": content}, "new")


@pytest.mark.parametrize(
    "value",
    [
        "http://teams.example/join",
        "javascript:alert(1)",
        "https://user:password@example.com/join",
        None,
        3,
    ],
)
def test_join_link_is_never_substituted_with_outlook_link(value):
    assert (
        join_url(
            {
                "onlineMeeting": {"joinUrl": value},
                "webLink": "https://outlook.example/event",
            }
        )
        is None
    )


def test_invalid_teams_config_does_not_silently_disable_mode():
    with pytest.raises(BookingValidationError):
        teams_enabled({"teams_enabled": "true"})
    assert teams_enabled({}) is False


def test_teams_structured_fields_survive_shared_provider_sanitization_without_body_or_tokens():
    fields = {
        "teams_join_url": "https://teams.microsoft.com/l/synthetic",
        "outlook_web_link": "https://outlook.example/event",
        "online_meeting_provider": "teamsForBusiness",
        "meeting_status": "ready",
        "changes_require_staff": True,
    }
    sanitized = sanitize_tool_result_for_json_string(
        {"status": "success", **fields, "body": "private notes", "token": "private"},
        tool_name="microsoft_calendar",
    )
    assert all(sanitized[key] == value for key, value in fields.items())
    assert "body" not in sanitized and "token" not in sanitized
