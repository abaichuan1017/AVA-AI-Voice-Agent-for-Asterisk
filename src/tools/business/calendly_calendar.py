"""Provider-neutral Calendly v1 native in-call tool."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.core.calendly_store import CalendlyStore
from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.context import resolve_scoped_tool_config
from src.tools.business.calendly_client import CalendlyError, instant
from src.tools.business.calendly_service import CalendlyService

logger = logging.getLogger(__name__)


class CalendlyCalendarTool(Tool):
    @property
    def definition(self):
        props = {
            "action": {"type": "string", "enum": ["list_appointment_types", "get_available_slots", "create_booking"]},
            "appointment_key": {"type": "string", "description": "Exact key returned by list_appointment_types."},
            "start_datetime": {"type": "string", "description": "Availability window start, ISO 8601 with UTC offset. For next available, omit both dates to search the next seven days using the server clock."},
            "end_datetime": {"type": "string", "description": "Exclusive availability window end, ISO 8601 with UTC offset. Defaults to seven days after the start. For a specific local day, send local midnight to next midnight with the correct offsets."},
            "timezone": {"type": "string", "description": "Required caller-confirmed IANA timezone, e.g. America/Los_Angeles."},
            "slot_reference": {"type": "string", "description": "Exact opaque reference returned by get_available_slots. Never a date/time or invented value. Select a returned slot before create_booking."}, "name": {"type": "string"}, "email": {"type": "string"},
            "confirmation_reference": {"type": "string", "description": "Returned by the first create_booking call. Omit to prepare a new canonical recap; include only after the caller approves that exact recap."},
            "booking_confirmed": {"type": "boolean"}, "email_confirmed": {"type": "boolean"},
            "answers": {"type": "array", "items": {"type": "object", "properties": {"position": {"type": "integer"}, "answer": {"type": "string"}}, "required": ["position", "answer"], "additionalProperties": False}},
        }
        return ToolDefinition(name="calendly_calendar", category=ToolCategory.BUSINESS, requires_channel=False,
                              max_execution_time=20,
                              description="Book one Calendly appointment in the current call. Use action values, never separate function names. List allowed appointment types, offer returned slots, collect required questions. First call create_booking without confirmation_reference to prepare a canonical recap; this does not book. Spell the returned email including every dot and symbol, read date/time/timezone/duration/location/name/recipient, and ask for final approval. Only after that approval call create_booking with the returned confirmation_reference, unchanged details, email_confirmed and booking_confirmed true. Corrections require a new recap. Never retry an uncertain booking or create another appointment to change a booking; use the Calendly confirmation or staff. Booking success does not prove invitation delivery.",
                              input_schema={"type": "object", "properties": props, "required": ["action"], "additionalProperties": False})

    async def execute(self, parameters, context):
        try:
            async with asyncio.timeout(15):
                return await self._execute(parameters, context)
        except TimeoutError:
            return {"status": "pending", "code": "scheduling_timeout", "message": "Scheduling could not be confirmed. Ask staff to verify before booking again."}

    async def _execute(self, parameters, context):
        try:
            cfg = resolve_scoped_tool_config(context, "calendly_calendar", lambda: {})
            if not cfg.get("enabled") or not context.call_id:
                raise CalendlyError("tool_not_enabled")
            allowed = cfg.get("selected_appointments", list((cfg.get("appointments") or {}).keys()))
            operation = "call:" + context.call_id
            store = await asyncio.to_thread(CalendlyStore)
            service = CalendlyService(store)
            if parameters.get("action") == "list_appointment_types":
                conn, _ = await service.credentials(admission=True)
                entries = []
                for key in allowed:
                    value = service.binding(cfg, key, conn)
                    entries.append({"key": key, "name": str(value.get("name") or key)[:200], "duration_minutes": value.get("duration")})
                return {"status": "success", "appointment_types": entries}
            if parameters.get("action") == "get_available_slots":
                if parameters.get("appointment_key") not in allowed:
                    raise CalendlyError("appointment_not_allowed")
                start, end = parameters.get("start_datetime", ""), parameters.get("end_datetime", "")
                if not start and end:
                    raise CalendlyError("search_start_required")
                if not start:
                    start = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
                if not end:
                    end = (instant(start) + timedelta(days=7)).isoformat()
                return await service.slots(cfg, operation, parameters["appointment_key"], start,
                                           end, parameters.get("timezone", ""))
            if parameters.get("action") == "create_booking":
                async def active():
                    session = await context.get_session()
                    if not session or getattr(session, "cleanup_in_progress", False) or getattr(session, "cleanup_completed", False):
                        raise CalendlyError("call_not_active")
                    if await context.get_tool_block_response("calendly_calendar"):
                        raise CalendlyError("call_not_active")
                return await service.book(cfg, operation, parameters, active)
            raise CalendlyError("unsupported_action")
        except CalendlyError as exc:
            messages = {
                "invalid_timezone": "Ask for the caller's timezone, then check availability with its IANA name. For next available, omit both dates to search the next seven days. No booking was submitted.",
                "invalid_datetime": "Correct the availability dates using ISO 8601 with UTC offsets. For next available, omit both dates to search the next seven days. No booking was submitted.",
                "search_start_required": "Supply a search start along with the end, or omit both dates for the next seven days. No booking was submitted.",
                "invalid_search_window": "Search a future window of at most 31 days. For next available, omit both dates for the next seven days. For a specific day use midnight to next midnight in the caller's timezone. No booking was submitted.",
                "proposal_expired": "Check availability again and use an exact returned slot_reference; never use a date/time as a reference. Then prepare a new recap and obtain approval. No booking was submitted.",
            }
            return {"status": "pending" if exc.uncertain else "error", "code": exc.code,
                    "message": messages.get(exc.code, "Scheduling could not be confirmed. Clarify missing details or ask staff; never repeat an uncertain booking.")}
        except (TimeoutError, OSError, ValueError):
            return {"status": "pending", "code": "scheduling_unavailable", "message": "Scheduling confirmation is unavailable. Ask staff to check before booking again."}
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Calendly execution unavailable")
            return {"status": "pending", "code": "scheduling_unavailable", "message": "Ask staff to verify this request before booking again."}
