import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.post_call_summary import SummaryGenerationResult
from src.tools.context import ToolExecutionContext
from src.tools.http.in_call_lookup import InCallHTTPConfig, InCallHTTPTool
from src.tools.summary import generate_summary, summary_prompt


def make_tool(**kw):
    return InCallHTTPTool(InCallHTTPConfig(name="brief", url="https://example.com", generate_summary=True,
        summary_provider="mock_llm", **kw))


def make_context(generator=None, call_id="synthetic"):
    session = SimpleNamespace(conversation_history=[{"role": "user", "content": "Earlier request"}],
        pre_call_results={"company": "Example"}, call_metadata={"customer_name": "Corrected", "summary": "poison"},
        cleanup_in_progress=False, cleanup_completed=False)
    return ToolExecutionContext(call_id=call_id, user_input="Most recent caller words",
        session_store=SimpleNamespace(get_by_call_id=AsyncMock(return_value=session)),
        tool_registry=SimpleNamespace(summary_generator=generator)), session


@pytest.mark.asyncio
async def test_summary_snapshot_safe_json_and_combined_budget():
    generator = AsyncMock(return_value=SummaryGenerationResult(text='A "quote" and \n newline', provider="mock_llm", status="ok"))
    context, session = make_context(generator)
    tool = make_tool(parameters=[{"name": "summary"}, {"name": "summary_status"}])
    observed = []
    async def request(params, ctx, values):
        observed.append(values)
        assert json.loads(values["summary_json"]) == values["summary"]
        return {"status": "success"}
    tool._execute_request = request
    result = await tool.execute({"summary": "injected", "summary_status": "fake"}, context)
    args = generator.await_args.kwargs
    assert args["conversation_history"][-2]["content"] == "Most recent caller words"
    assert "Corrected" in args["conversation_history"][-1]["content"]
    assert observed[0]["summary"] != "injected" and observed[0]["summary_status"] == "ok"
    assert result["summary_status"] == "ok" and result["internal_notification"]
    assert tool.definition.timeout_ms == 20000
    assert len(session.conversation_history) == 1 and not hasattr(session, "summary")


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,sent", [("continue",True),("abort",False)])
async def test_summary_failure_is_explicit_and_operator_controls_delivery(policy, sent):
    context, _ = make_context()
    tool = make_tool(summary_failure_policy=policy)
    tool._execute_request = AsyncMock(return_value={"status":"success"})
    result = await tool.execute({}, context)
    assert tool._execute_request.await_count == int(sent)
    assert result["summary_error_code"] == "generator_unavailable"
    if sent:
        values=tool._execute_request.await_args.args[2]
        assert values["summary"] == "" and values["summary_status"] == "error"
    else:
        assert result["delivery_status"] == "skipped"


@pytest.mark.asyncio
async def test_hangup_during_summary_prevents_delivery_and_snapshot_does_not_change():
    context, session = make_context()
    async def generate(**kwargs):
        session.conversation_history[0]["content"] = "mutated"
        assert kwargs["conversation_history"][0]["content"] == "Earlier request"
        session.cleanup_in_progress = True
        return SummaryGenerationResult(text="Recap", provider="mock_llm", status="ok")
    context.tool_registry.summary_generator=generate
    tool=make_tool();tool._execute_request=AsyncMock()
    result=await tool.execute({},context)
    assert result["status"] == "cancelled" and not tool._execute_request.called


@pytest.mark.asyncio
async def test_concurrent_executions_have_distinct_jobs_and_results():
    ids=[]
    async def generate(**kwargs):
        ids.append(kwargs["call_id"])
        await asyncio.sleep(0)
        return SummaryGenerationResult(text=kwargs["conversation_history"][0]["content"],provider="mock_llm",status="ok")
    a,_=make_context(generate,"A"); b,s=make_context(generate,"B");s.conversation_history[0]["content"]="Different caller"
    tool=make_tool(); values=[]
    async def request(params,ctx,sub): values.append((ctx.call_id,sub["summary"]));return {"status":"success"}
    tool._execute_request=request
    await asyncio.gather(tool.execute({},a),tool.execute({},b),tool.execute({},a))
    assert len(set(ids))==3
    assert ("B","Different caller") in values and values.count(("A","Earlier request"))==2


@pytest.mark.asyncio
async def test_cancelled_summary_closes_work_without_request():
    started=asyncio.Event();closed=asyncio.Event()
    async def generate(**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    ctx,_=make_context(generate);tool=make_tool();tool._execute_request=AsyncMock()
    task=asyncio.create_task(tool.execute({},ctx));await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert closed.is_set() and not tool._execute_request.called


@pytest.mark.asyncio
async def test_timeout_and_empty_history_are_safe():
    async def slow(**kwargs): await asyncio.sleep(10)
    tool=make_tool(summary_timeout_ms=1)
    values,diag=await generate_summary(tool.config,slow,call_id="test",history=[],values={})
    assert values["summary_status"] == "timeout" and diag["summary_error_code"]=="timeout"
    generator=AsyncMock(return_value=SummaryGenerationResult(status="skipped",error_code="empty_transcript"))
    await generate_summary(tool.config,generator,call_id="test",history=[],values={"company":"Example"})
    assert generator.await_args.kwargs["conversation_history"]==[]


@pytest.mark.parametrize("bad", ["{other}","{max_words!r}","{max_words:>10000000}","{max_words.real}"])
def test_prompt_rejects_unsupported_formatting(bad):
    with pytest.raises(ValueError):summary_prompt(bad,100)

@pytest.mark.asyncio
async def test_summary_disabled_preserves_legacy_summary_parameter():
    context,_=make_context()
    context.session_store=None
    tool=InCallHTTPTool(InCallHTTPConfig(name='legacy',url='https://example.com',parameters=[{'name':'summary','type':'string','required':True}]))
    tool._execute_request=AsyncMock(return_value={'status':'success'})
    result=await tool.execute({'summary':'Legacy model-provided note'},context)
    assert result=={'status':'success'}
    assert tool._execute_request.await_args.args[2]['summary']=='Legacy model-provided note'
    assert [p.name for p in tool.definition.parameters]==['summary']
    enabled=make_tool(parameters=[{'name':'summary','type':'string','required':True}])
    assert enabled.definition.parameters==[]
