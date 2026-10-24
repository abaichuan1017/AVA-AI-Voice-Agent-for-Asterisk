"""Provider-neutral Calendly v1 native in-call tool."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.core.calendly_store import CalendlyStore
from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.context import resolve_scoped_tool_config
from src.tools.business.calendly_client import CalendlyError
from src.tools.business.calendly_service import CalendlyService

logger = logging.getLogger(__name__)


class CalendlyCalendarTool(Tool):
    @property
    def definition(self):
        props = {
            "action": {"type": "string", "enum": ["list_appointment_types", "get_available_slots", "create_booking"]},
            "appointment_key": {"type": "string"}, "start_datetime": {"type": "string"},
            "end_datetime": {"type": "string"}, "timezone": {"type": "string", "description": "Caller-confirmed IANA timezone"},
            "slot_reference": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string"},
            "booking_confirmed": {"type": "boolean"}, "email_confirmed": {"type": "boolean"},
            "answers": {"type": "array", "items": {"type": "object", "properties": {"position": {"type": "integer"}, "answer": {"type": "string"}}, "required": ["position", "answer"], "additionalProperties": False}},
        }
        return ToolDefinition(name="calendly_calendar", category=ToolCategory.BUSINESS, requires_channel=False,
                              max_execution_time=20,
                              description="Book one Calendly appointment in the current call. Use action values, never separate function names. List allowed appointment types, offer returned slots, collect required questions and read back the exact email. Recap date/time/timezone/duration/location/recipient and obtain explicit approval before create_booking with email_confirmed and booking_confirmed true. Never retry an uncertain booking or create another appointment to change a booking; use the Calendly confirmation or staff. Booking success does not prove invitation delivery.",
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
                return await service.slots(cfg, operation, parameters["appointment_key"], parameters.get("start_datetime", ""),
                                           parameters.get("end_datetime", ""), parameters.get("timezone", ""))
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
            return {"status": "pending" if exc.uncertain else "error", "code": exc.code,
                    "message": "Scheduling could not be confirmed. Clarify missing details or ask staff; never repeat an uncertain booking."}
        except (TimeoutError, OSError, ValueError):
            return {"status": "pending", "code": "scheduling_unavailable", "message": "Scheduling confirmation is unavailable. Ask staff to check before booking again."}
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Calendly execution unavailable")
            return {"status": "pending", "code": "scheduling_unavailable", "message": "Ask staff to verify this request before booking again."}
