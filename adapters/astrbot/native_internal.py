"""Bounded internal work on the pinned native AstrBot tool-loop runner.

No chat sender, database connection, timer or per-bot resident Agent lives here.
The caller supplies a current service identity, existing tools and effect fences.
"""
import asyncio,json,time
from dataclasses import dataclass,field
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.provider.entities import ProviderRequest
from mcp.types import CallToolResult,TextContent


class WorkStopped(Exception):pass
class WorkBudgetExceeded(Exception):pass


@dataclass
class RunControl:
    model_calls:int
    tool_calls:int
    tokens:int
    seconds:float
    current:object
    cancel:asyncio.Event=field(default_factory=asyncio.Event)
    used_models:int=0
    used_tools:int=0
    used_tokens:int=0
    estimated_usage:bool=False
    output_tokens:int=1536
    output_caps:list=field(default_factory=list)
    evidence:list=field(default_factory=list)
    tool_results:list=field(default_factory=list)
    checkpoints:list=field(default_factory=list)
    failure:str=''
    last_progress:float=field(default_factory=time.monotonic)

    def progress(self):self.last_progress=time.monotonic()

    def check(self):
        if self.cancel.is_set() or not self.current():raise WorkStopped('internal_work_stale')
        if self.failure:
            if self.failure.endswith('_budget'):raise WorkBudgetExceeded(self.failure)
            raise WorkStopped(self.failure)
        if self.used_tokens>=self.tokens:raise WorkBudgetExceeded('token_budget')


class BoundedProvider:
    def __init__(self,provider,control):self.provider=provider;self.control=control
    def __getattr__(self,key):return getattr(self.provider,key)
    async def text_chat(self,*args,**kwargs):
        c=self.control;c.check()
        if c.used_models>=c.model_calls:c.failure='model_call_budget';raise WorkBudgetExceeded(c.failure)
        # Conservative preflight limits prompt growth without installing a
        # resident tokenizer. Provider-reported usage remains authoritative.
        input_estimate=(len(json.dumps(kwargs,ensure_ascii=False,default=str).encode())+1)//2
        output_cap=min(c.output_tokens,c.tokens-c.used_tokens-input_estimate)
        if output_cap<min(512,c.output_tokens):c.failure='request_token_budget';raise WorkBudgetExceeded(c.failure)
        estimated=input_estimate+output_cap
        c.used_models+=1
        c.output_caps.append(output_cap)
        # The hosted pinned provider reads this context-local limit when it
        # builds the actual payload. A smaller remaining budget may still fit
        # a bounded reassessment; never increase the cycle's budget or lower
        # the conservative input estimate to make a request pass.
        from autonomy import decision_token_limit
        limit_token=decision_token_limit.set(output_cap)
        try:response=await self.provider.text_chat(*args,**kwargs)
        except BaseException:
            c.used_tokens+=estimated;c.estimated_usage=True;c.failure='model_request_unconfirmed';raise
        finally:decision_token_limit.reset(limit_token)
        usage=getattr(response,'usage',None)
        total=getattr(usage,'total',None)
        if type(total) is int and total>0:c.used_tokens+=total
        else:
            # Missing billing data cannot refund an unobserved model request.
            c.used_tokens+=estimated;c.estimated_usage=True
        c.progress();c.check();return response


async def run(provider,control,prompt,system,tools=(),handlers=None,context=None,contexts=None):
    """Return actual native feedback; cancellation settles the owned computation."""
    handlers=handlers or {};allowed={tool.name:tool for tool in tools}
    tool_set=ToolSet(tools=list(tools)) if tools else None
    cooling={}
    class Executor(BaseFunctionToolExecutor):
        @classmethod
        async def execute(cls,tool,run_context,**arguments):
            control.check()
            if tool.name not in allowed:raise WorkStopped('unavailable_tool')
            if control.used_tools>=control.tool_calls:control.failure='tool_call_budget';raise WorkBudgetExceeded(control.failure)
            control.used_tools+=1
            handler=handlers.get(tool.name)
            try:
                if tool.name in cooling:value=cooling[tool.name]
                elif handler is not None:value=await handler(arguments,run_context)
                else:value=await tool.call(run_context,**arguments)
            except BaseException:
                control.tool_results.append({'tool':tool.name,'status':'failed'});control.progress();raise
            parsed=None
            try:parsed=json.loads(value) if isinstance(value,str) else value
            except (TypeError,ValueError):pass
            unavailable=isinstance(parsed,dict) and parsed.get('available') is False
            item={'tool':tool.name,'status':'unavailable' if unavailable else 'failed' if isinstance(value,CallToolResult) and value.isError else 'returned'}
            from tool_outcome import project
            outcome=project(parsed if isinstance(parsed,dict) else value,write=bool(getattr(tool,'hosted_effect',False)))
            item['receipt']=outcome['receipt']
            item['evidence']=outcome.get('evidence',[])
            if unavailable:
                item.update(code=str(parsed.get('code') or 'unavailable')[:80],scope=str(parsed.get('scope') or 'tool')[:40])
            # These two hosted tools return controlled operation status only.
            # Preserve the refusal reason without exposing arbitrary tool data
            # (notably group chat bodies) to the outer decision model.
            if tool.name in ('hosted_qq_signature','hosted_qq_publish','hosted_qq_avatar') and isinstance(value,str):
                detail=parsed.get('data',{}).get('message') if isinstance(parsed,dict) and isinstance(parsed.get('data'),dict) else None
                item['detail']=(detail if isinstance(detail,str) else value)[:400]
            if unavailable and parsed.get('scope')=='service' and type(parsed.get('retryAfterSeconds')) in (int,float) and parsed['retryAfterSeconds']>0:
                # The service rejected all queries for this period. Withdraw
                # only this tool from this work; other useful tools still run.
                # Repeated calls already queued by the same model response
                # receive the real failure, without repeating network work.
                cooling[tool.name]=parsed
                if tool_set:tool_set.remove_tool(tool.name)
                item['retryAfterSeconds']=parsed['retryAfterSeconds']
            control.tool_results.append(item)
            control.progress()
            control.check()
            if isinstance(value,CallToolResult):yield value
            else:yield CallToolResult(content=[TextContent(type='text',text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))])
    runner=ToolLoopAgentRunner();started=time.monotonic()
    await runner.reset(provider=BoundedProvider(provider,control),request=ProviderRequest(prompt=prompt,system_prompt=system,contexts=contexts or [],func_tool=tool_set),run_context=ContextWrapper(context=context,tool_call_timeout=min(30,max(1,int(control.seconds)))),tool_executor=Executor(),agent_hooks=BaseAgentRunHooks(),request_max_retries=1)
    async def drive():
        # step_until_done() adds an extra forced-final request. Count our own
        # steps so its courtesy final response cannot exceed the caller budget.
        while not runner.done():
            control.check()
            if control.used_models>=control.model_calls:raise WorkBudgetExceeded('model_call_budget')
            async for _ in runner.step():pass
            # Native runners turn provider exceptions into an error response.
            # Recheck the authoritative control after even a terminal step.
            control.check()
        return runner.get_final_llm_resp()
    task=asyncio.create_task(drive());canceled=asyncio.create_task(control.cancel.wait())
    result=None;status='completed';reason=''
    try:
        control.progress()
        while True:
            remaining=max(0,control.last_progress+control.seconds-time.monotonic())
            done,_=await asyncio.wait((task,canceled),timeout=remaining,return_when=asyncio.FIRST_COMPLETED)
            if done or time.monotonic()-control.last_progress>=control.seconds:break
        if task not in done:
            status='canceled' if canceled in done else 'budget_exhausted';reason='interrupted' if canceled in done else 'idle_timeout'
            runner.request_stop();task.cancel()
        else:
            result=await task
            if runner.was_aborted():status='canceled';reason='native_runner_aborted'
            elif getattr(result,'role',None)=='err':status='blocked';reason='native_model_error'
    except WorkBudgetExceeded as error:status='budget_exhausted';reason=str(error)
    except (WorkStopped,asyncio.CancelledError) as error:status='blocked' if control.failure=='model_request_unconfirmed' else 'canceled';reason=str(error);runner.request_stop();task.cancel()
    except Exception as error:status='blocked';reason=type(error).__name__
    finally:
        canceled.cancel()
        if not task.done():task.cancel()
        await asyncio.gather(task,canceled,return_exceptions=True)
    raw=getattr(result,'raw_completion',None);choices=getattr(raw,'choices',None) or []
    return {'status':status,'text':getattr(result,'completion_text','') if result else '',
            'modelFinishReason':getattr(choices[0],'finish_reason',None) if choices else None,
            'reason':reason,'usage':{'modelCalls':control.used_models,'toolCalls':control.used_tools,'tokens':control.used_tokens,'estimated':control.estimated_usage,'outputTokenCaps':control.output_caps},
            'seconds':round(time.monotonic()-started,3),'evidence':control.evidence,'toolResults':control.tool_results,'checkpoints':control.checkpoints[-4:]}
