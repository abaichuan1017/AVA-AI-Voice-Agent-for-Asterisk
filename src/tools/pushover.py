"""Phase adapters for native Pushover; no telephony ownership or CRM writes."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field

from src.integrations.pushover import (
    PHASES,
    PhaseSettings,
    PushoverConfig,
    get_sender,
    render_message,
)
from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolParameter, ToolPhase
from src.tools.summary import generate_summary, snapshot_history


@dataclass
class NotificationState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    events: dict = field(default_factory=dict)
    briefings: dict = field(default_factory=dict)


def verified_meeting(tool_calls):
    """Use successful current-call calendar writes, never conversational claims."""
    active = {}
    for call in tool_calls:
        if (
            call.get("name") not in {"google_calendar", "microsoft_calendar"}
            or call.get("status") != "success"
        ):
            continue
        action = call.get("action")
        event = call.get("target_id")
        if action == "delete_event":
            if event:
                active.pop(event, None)
            else:
                active.clear()
        elif action in {"create_event", "reschedule_event"}:
            params = call.get("params") or {}
            start, end = params.get("start_datetime"), params.get("end_datetime")
            if (
                event
                and start
                and end
                and (action == "create_event" or event in active)
            ):
                active[event] = f"{start} to {end}"
    return next(reversed(active.values()), "unknown") if active else "unknown"


def notification_values(
    context, *, phase, enrichment=None, metadata=None, destination="", outcome=""
):
    enrichment = enrichment or {}
    metadata = metadata or {}
    # Corrections affect presentation only; never routing, consent or CRM authority.
    merged = {**enrichment, **metadata}
    corrected = (
        metadata.get("customer_name")
        if metadata.get("customer_name_source") == "caller_confirmed"
        else None
    ) or metadata.get("caller_confirmed_name")
    crm = (
        (enrichment.get("customer_name") or enrichment.get("contact_name"))
        if enrichment.get("match_status") in {None, "matched"}
        else None
    )
    caller_id = str(context.caller_name or "").strip()
    if (
        caller_id.lower()
        in {
            "unknown",
            "anonymous",
            "unavailable",
            "restricted",
            "private",
            "wireless caller",
            "out of area",
        }
        or caller_id == context.caller_number
    ):
        caller_id = ""
    name = corrected or crm or caller_id or "unknown"
    source = (
        "caller-confirmed"
        if corrected
        else "CRM" if crm else "caller ID" if caller_id else "unknown"
    )
    return {
        "agent": str(context.context_name or "AVA")[:100],
        "stage": {
            "pre_call": "arrival",
            "in_call": "handoff requested",
            "post_call": "completion",
        }[phase],
        "call_id": str(context.call_id)[:128],
        "caller_number": str(context.caller_number or "unknown")[:64],
        "callback_number": str(merged.get("callback_number") or "unknown")[:64],
        "name": str(name)[:100],
        "identity_source": source,
        "company": str(
            merged.get("company")
            or merged.get("company_name")
            or merged.get("customer_company")
            or "unknown"
        )[:100],
        "crm_status": str(
            enrichment.get("crm_status") or enrichment.get("match_status") or "unknown"
        )[:100],
        "destination": str(destination or "unknown")[:100],
        "outcome": outcome or "pending",
        "language": str(merged.get("language") or "unknown")[:64],
        # No inference of booked meetings from conversational intent.
        "meeting": "unknown",
        "summary": "",
        "summary_status": "disabled",
        "update_key": "",
    }


class PushoverPhaseTool(Tool):
    def __init__(
        self,
        cfg,
        assignment,
        phase,
        agent_slug,
        state,
        generator,
        sender=None,
        destination_aliases=(),
    ):
        self.config = cfg
        self.assignment = assignment
        self.phase = phase
        self.agent_slug = agent_slug
        self.state = state
        self.generator = generator
        self.destination_aliases = tuple(destination_aliases)
        self.sender = sender or get_sender()
        self.settings = cfg.phases.get(phase, PhaseSettings())
        self._definition = ToolDefinition(
            name={
                "pre_call": "pushover_arrival",
                "in_call": "pushover_briefing",
                "post_call": "pushover_completion",
            }[phase],
            description=(
                "Send an internal notification to the operator-configured recipients. Invoke before transfer. "
                "It reports handoff requested, never transfer success. Do not read acknowledgements or delivery "
                "diagnostics aloud. Continue the conversation and remaining tools naturally."
            ),
            category=ToolCategory.BUSINESS,
            phase=ToolPhase(phase),
            is_global=True,
            parameters=(
                [
                    ToolParameter(
                        name="update_key",
                        type="string",
                        description="Configured notification event",
                        required=True,
                        enum=assignment.update_keys,
                    )
                ]
                if phase == "in_call"
                else []
            ),
            timeout_ms=cfg.timeout_ms
            + (
                self.settings.summary_timeout_ms
                if self.settings.generate_summary
                else 0
            )
            + 250,
        )

    @property
    def definition(self):
        return self._definition

    async def execute(self, *args):
        params, context = args if len(args) == 2 else ({}, args[0])
        result = {
            "status": "skipped",
            "internal_notification": True,
            "delivery_status": "skipped",
            "message": "",
        }
        try:
            async with asyncio.timeout(self.definition.timeout_ms / 1000):
                result = await self._execute(params, context)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            result.update(status="timeout", delivery_status="timeout")
        except Exception:
            result.update(
                status="error",
                delivery_status="skipped",
                error_code="notification_unavailable",
            )
        if hasattr(context, "execution_results"):
            context.execution_results[self.definition.name] = result
        # Pre-call values are prompt enrichment only. Notification diagnostics must
        # never be inserted as enrichment or used as an authority signal.
        return {} if self.phase == "pre_call" else result

    async def _execute(self, params, context):
        base = {"status": "skipped", "internal_notification": True, "message": ""}
        if (
            context.context_name != self.agent_slug
            or not self.config.enabled
            or not self.assignment.enabled
        ):
            return {**base, "delivery_status": "skipped"}
        update = params.get("update_key") if self.phase == "in_call" else self.phase
        if self.phase == "in_call" and (
            set(params) - {"update_key"} or update not in self.assignment.update_keys
        ):
            return {
                **base,
                "status": "error",
                "delivery_status": "rejected",
                "error_code": "invalid_update",
            }
        if self.phase == "in_call":
            session = await context.get_session()
            if getattr(session, "cleanup_in_progress", False) or getattr(
                session, "cleanup_completed", False
            ):
                return {**base, "delivery_status": "skipped"}
            history = await snapshot_history(context)
            enrichment = copy.deepcopy(getattr(session, "pre_call_results", {}) or {})
            metadata = copy.deepcopy(getattr(session, "call_metadata", {}) or {})
            tool_calls = list(getattr(session, "tool_calls", []) or [])
        else:
            history = copy.deepcopy(getattr(context, "conversation_history", []) or [])
            enrichment = copy.deepcopy(getattr(context, "pre_call_results", {}) or {})
            metadata = copy.deepcopy(getattr(context, "call_metadata", {}) or {})
            tool_calls = list(getattr(context, "tool_calls", []) or [])
        # The previous transfer results distinguish retries from subsequent attempts.
        attempts = sum(
            1
            for call in tool_calls
            if call.get("name")
            in {"blind_transfer", "attended_transfer", "live_agent_transfer"}
        )
        last_user = next(
            (
                m.get("content", "")
                for m in reversed(history)
                if m.get("role") == "user"
            ),
            "",
        )
        event = hashlib.sha256(
            json.dumps(
                [
                    self.phase,
                    update,
                    attempts,
                    (
                        [
                            sum(
                                1
                                for message in history
                                if message.get("role") == "user"
                            ),
                            last_user,
                        ]
                        if self.phase == "in_call"
                        else ""
                    ),
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        recipients = list(dict.fromkeys(getattr(self.assignment, self.phase)))
        if not recipients or any(
            key not in self.config.recipients or not self.config.recipients[key].enabled
            for key in recipients
        ):
            return {
                **base,
                "delivery_status": "skipped",
                "error_code": "recipient_unavailable",
            }
        destination = (
            (getattr(context, "transfer_destination", None) or "unknown")
            if self.phase == "post_call"
            else self.assignment.destination_key
        )
        outcome = (
            getattr(context, "call_outcome", "")
            if self.phase == "post_call"
            else "pending"
        )
        values = notification_values(
            context,
            phase=self.phase,
            enrichment=enrichment,
            metadata=metadata,
            destination=destination,
            outcome=outcome,
        )
        values["meeting"] = verified_meeting(tool_calls)[:160]
        values["update_key"] = update
        async with self.state.lock:
            if event in self.state.events:
                return {**base, **self.state.events[event], "deduplicated": True}
            if len(self.state.events) >= 64:
                return {
                    **base,
                    "delivery_status": "capacity",
                    "error_code": "call_event_limit",
                }
            deliveries = {}
            actual_destination = getattr(context, "transfer_destination", None)
            for key in recipients:
                briefing = self.state.briefings.get(key)
                if (
                    self.phase == "post_call"
                    and self.config.suppress_transferred_completion
                    and outcome == "transferred"
                    and briefing
                    and briefing["destination"]
                    and actual_destination in briefing["aliases"]
                    and attempts == briefing["attempts"] + 1
                ):
                    deliveries[key] = {"delivery_status": "suppressed"}
            targets = [key for key in recipients if key not in deliveries]
            diagnostics = {}
            if targets and self.settings.generate_summary:
                summary_values, diagnostics = await generate_summary(
                    self.settings,
                    self.generator,
                    call_id=context.call_id,
                    history=history,
                    values=values,
                )
                values.update(
                    {key: summary_values[key] for key in ("summary", "summary_status")}
                )
                if (
                    values["summary_status"] != "ok"
                    and self.settings.summary_failure_policy == "abort"
                ):
                    failed = {
                        **base,
                        **diagnostics,
                        "delivery_status": "skipped",
                        "error_code": "summary_unavailable",
                    }
                    self.state.events[event] = failed
                    return failed
            message = render_message(self.config, self.phase, values)
            # Reserve before crossing the network boundary. Cancellation leaves an
            # uncertain event which will not be blindly resent by another invocation.
            reserved = {"status": "error", "delivery_status": "uncertain"}
            self.state.events[event] = reserved
            if self.phase == "in_call":
                live = await context.get_session()
                if getattr(live, "cleanup_in_progress", False) or getattr(
                    live, "cleanup_completed", False
                ):
                    self.state.events[event] = {
                        "status": "skipped",
                        "delivery_status": "skipped",
                    }
                    return {**base, "delivery_status": "skipped"}
            sent = await asyncio.gather(
                *(
                    self.sender.send(
                        token_file=self.config.token_file,
                        key_file=self.config.recipients[key].key_file,
                        message=message,
                        timeout_ms=self.config.timeout_ms,
                    )
                    for key in targets
                )
            )
            for key, result in zip(targets, sent):
                deliveries[key] = result
                if self.phase == "in_call" and result["delivery_status"] == "accepted":
                    self.state.briefings[key] = {
                        "destination": destination,
                        "aliases": self.destination_aliases,
                        "attempts": attempts,
                    }
            statuses = {entry["delivery_status"] for entry in deliveries.values()}
            delivery = next(iter(statuses)) if len(statuses) == 1 else "partial"
            result = {
                **base,
                **diagnostics,
                "status": (
                    "success" if statuses <= {"accepted", "suppressed"} else "error"
                ),
                "delivery_status": delivery,
                "notification_deliveries": deliveries,
            }
            self.state.events[event] = result
            return result


def configure_call_notifications(
    registry, config, session, generator, destinations=None
):
    cfg = PushoverConfig.model_validate(config or {})
    assignment = cfg.agents.get(session.context_name or "")
    if not cfg.enabled or not assignment or not assignment.enabled:
        return registry
    registry = registry.clone()
    if getattr(session, "notification_state", None) is None:
        session.notification_state = NotificationState()
    destination = (destinations or {}).get(assignment.destination_key) or {}
    aliases = [assignment.destination_key] + [
        str(destination.get(key) or "") for key in ("description", "target", "name")
    ]
    for phase in PHASES:
        if getattr(assignment, phase):
            native_name = {
                "pre_call": "pushover_arrival",
                "in_call": "pushover_briefing",
                "post_call": "pushover_completion",
            }[phase]
            if registry.has(native_name):
                raise ValueError(
                    "Native Pushover tool name conflicts with an existing managed tool"
                )
            registry.register_instance(
                PushoverPhaseTool(
                    cfg,
                    assignment,
                    phase,
                    session.context_name,
                    session.notification_state,
                    generator,
                    destination_aliases=aliases,
                )
            )
    return registry
