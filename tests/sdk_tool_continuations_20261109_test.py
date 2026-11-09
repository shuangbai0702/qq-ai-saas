"""Real SDK loop with a fixture-only 16-step budget; production remains at 5.

This is deterministic runner evidence, not a production 12-call acceptance test.
"""
import ast,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool,ToolSet
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor
from astrbot.core.provider.entities import LLMResponse,ProviderRequest
from mcp.types import CallToolResult,TextContent


class Continuations(unittest.IsolatedAsyncioTestCase):
    async def test_twelve_real_sdk_tool_continuations_with_explicit_fixture_budget(self):
        executed=[];requests=[]
        owner=SimpleNamespace(work_id='fixture-one-work',scope='private:12345006')
        class Read(FunctionTool):
            async def call(self,context,index):
                self_owner=context.context
                if self_owner is not owner:raise AssertionError('Work ownership changed')
                executed.append(index)
                return json.dumps({'index':index,'value':'fixture-'+str(index)})
        class Executor(BaseFunctionToolExecutor):
            @classmethod
            async def execute(cls,tool,run_context,**arguments):
                yield CallToolResult(content=[TextContent(type='text',text=await tool.call(run_context,**arguments))])
        class Provider:
            provider_config={'id':'fixture-twelve-reads','max_context_tokens':0}
            async def text_chat(self,**payload):
                # Capture wire values now; SDK request history mutates later.
                requests.append([m.model_dump(mode='json') if hasattr(m,'model_dump') else json.loads(json.dumps(m)) for m in payload['contexts']])
                n=len(requests)
                if n<=12:
                    return LLMResponse(role='assistant',tools_call_name=['fixture_read'],tools_call_args=[{'index':n}],tools_call_ids=['read-'+str(n)])
                return LLMResponse(role='assistant',completion_text='all twelve results read')
        runner=ToolLoopAgentRunner()
        tool=Read(name='fixture_read',description='Read deterministic fixture data',parameters={'type':'object','properties':{'index':{'type':'integer'}},'required':['index']})
        await runner.reset(provider=Provider(),request=ProviderRequest(prompt='Read twelve distinct fixture items',system_prompt='fixture system',func_tool=ToolSet(tools=[tool])),run_context=ContextWrapper(context=owner),tool_executor=Executor(),agent_hooks=BaseAgentRunHooks(),request_max_retries=1)
        async for _ in runner.step_until_done(max_step=16):pass
        self.assertTrue(runner.done())
        self.assertEqual(executed,list(range(1,13)))
        self.assertEqual(len(requests),13)
        self.assertEqual(runner.get_final_llm_resp().completion_text,'all twelve results read')
        for n,wire in enumerate(requests):
            results=[m for m in wire if m['role']=='tool']
            self.assertEqual([m['tool_call_id'] for m in results],['read-'+str(i) for i in range(1,n+1)])
            self.assertEqual([json.loads(m['content'])['index'] for m in results],list(range(1,n+1)))
        self.assertFalse(any(ToolLoopAgentRunner.MAX_STEPS_REACHED_PROMPT in str(wire) for wire in requests))

    def test_scope_activation_budget_counts_scheduler_passes_not_sdk_steps(self):
        tree=ast.parse((ROOT/'adapters/astrbot/behavior.py').read_text(encoding='utf8'))
        guard=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='guarded_execute')
        invocations=[n for n in ast.walk(guard) if isinstance(n,ast.Await) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='execute']
        self.assertEqual(len(invocations),1,'outer pass awaits the complete pipeline execution')
        increments=[n for n in ast.walk(guard) if isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='passes']
        self.assertEqual(len(increments),1)
        self.assertEqual(increments[0].value.value,1)
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('step','step_until_done') for n in ast.walk(guard)))
        self.assertIn('misc:{max_steps:5}',(ROOT/'adapters/astrbot/index.ts').read_text(encoding='utf8'),'production SDK budget remains five; this fixture does not establish production twelve-call support')


if __name__=='__main__':unittest.main()
