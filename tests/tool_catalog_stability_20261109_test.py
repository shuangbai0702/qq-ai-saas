"""Real SDK schemas/calls; runner reset sink avoids provider calls and sends."""
import asyncio,inspect,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.agent.tool import FunctionTool,ToolSet
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.platform.message_type import MessageType
from astrbot.core.tools.message_tools import GetGroupMessageHistoryTool
from inner_tools import completion_guard
import tenant_tools
import execution_memory


class Catalog(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_reset=ToolLoopAgentRunner.reset
        from astrbot.core.tools.message_tools import SendMessageToUserTool
        self.send_class=SendMessageToUserTool
        self.original_send=SendMessageToUserTool.call
        self.original_history=GetGroupMessageHistoryTool.call
        async def sink(runner,provider,request,run_context,**kwargs):
            runner.request=request
            runner.execution_tools={tool.name:tool for tool in request.func_tool.tools}
            return request.func_tool.openai_schema()
        ToolLoopAgentRunner.reset=sink
        self.members={'bot-A':{'generation':1,'revision':2}}
        tenant_tools.install.installed=False
        with patch('media_egress.install_downloads'):
            tenant_tools.install(self.members)
        self.assertEqual(list(inspect.signature(ToolLoopAgentRunner.reset).parameters),list(inspect.signature(sink).parameters))
        self.history=AsyncMock(return_value=[])

    async def asyncTearDown(self):
        ToolLoopAgentRunner.reset=self.original_reset
        self.send_class.call=self.original_send
        GetGroupMessageHistoryTool.call=self.original_history
        tenant_tools.install.installed=False

    def context(self,group=False,enabled=True,bot='bot-A',revision=2,**extra):
        extras={'_hosted_lease':{'generation':1,'revision':revision},**extra}
        event=SimpleNamespace(get_platform_id=lambda:bot,get_extra=lambda key:extras.get(key),
            get_message_type=lambda:MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE,
            unified_msg_origin=f'{bot}:GroupMessage:12345' if group else f'{bot}:FriendMessage:67890')
        plugin=SimpleNamespace(get_config=lambda **kwargs:{'provider_ltm_settings':{'group_message_history_enable':enabled}},
            get_llm_tool_manager=lambda:SimpleNamespace(get_builtin_tool=lambda cls:cls()),
            message_history_manager=SimpleNamespace(get=self.history))
        return SimpleNamespace(context=SimpleNamespace(event=event,context=plugin))

    async def reset(self,context,extra_tools=()):
        # Reproduce the SDK's scope-dependent native append, followed by the
        # hosted adapter's completion wrapping and alphabetical ordering.
        tools=[FunctionTool(name='z_existing',description='kept',parameters={'type':'object','properties':{}}),*extra_tools]
        event=context.context.event
        if event.get_message_type()==MessageType.GROUP_MESSAGE and context.context.context.get_config()['provider_ltm_settings']['group_message_history_enable']:
            tools.append(GetGroupMessageHistoryTool())
        request=SimpleNamespace(func_tool=ToolSet(tools=sorted([completion_guard(tool,event) for tool in tools],key=lambda tool:tool.name)))
        runner=ToolLoopAgentRunner()
        schema=await runner.reset(None,request,context)
        return runner,json.dumps(schema,sort_keys=True,separators=(',',':'))

    async def test_private_group_and_completion_definitions_and_execution_table(self):
        schemas=[]
        for group,completion in [(False,False),(True,False),(False,True),(True,True)]:
            context=self.context(group=group,_hosted_tool_completion=completion)
            runner,schema=await self.reset(context);schemas.append(schema)
            self.assertEqual(list(runner.execution_tools),['get_group_message_history','z_existing'])
            tool=runner.execution_tools['get_group_message_history']
            result=await tool.call(context,limit=3)
            if completion:self.assertEqual(json.loads(result)['code'],'completion_capability_denied')
            elif group:self.assertIn('has_more=false',result)
            else:self.assertIn('only available in a group',result)
            # Ordinary tool-loop continuation uses the same executable ToolSet.
            self.assertEqual(schema,json.dumps(runner.request.func_tool.openai_schema(),sort_keys=True,separators=(',',':')))
        self.assertEqual(len(set(schemas)),1)
        self.history.assert_awaited_once_with(platform_id='bot-A',user_id='bot-A:GroupMessage:12345',page_size=700)

    async def test_permissions_stay_in_execution_and_narrow_requests_stay_narrow(self):
        for context,expected in [(self.context(group=True,bot='bot-B'),'inactive'),(self.context(group=True,revision=1),'stale'),(self.context(group=True,enabled=False),'disabled')]:
            self.assertIn(expected,await GetGroupMessageHistoryTool().call(context))
        self.assertIn('target is fixed',await GetGroupMessageHistoryTool().call(self.context(group=True),group_id='999'))
        self.history.assert_not_awaited()
        for context in [self.context(enabled=False),self.context(_hosted_internal_work=True),self.context(bot='bot-B'),self.context(revision=1)]:
            runner,_=await self.reset(context)
            self.assertEqual(list(runner.execution_tools),['z_existing'])

    async def test_experience_catalog_survives_bridge_arrival_and_loss(self):
        settings={'config':{'behavior':{}}}
        schemas=[]
        for group,completion,bridge in [(False,False,None),(True,False,{'url':'bridge'}),(True,True,None),(False,True,{'url':'bridge'})]:
            context=self.context(group=group,_hosted_tool_completion=completion)
            event=context.context.event
            with patch('execution_memory.manifest',return_value=bridge):
                tools=execution_memory.tools(settings,event,'group:12345' if group else 'private:67890',lambda:True)
                runner,schema=await self.reset(context,tools);schemas.append(schema)
                tool=runner.execution_tools['hosted_recall_experience']
                if bridge is None:
                    result=json.loads(await tool.call(context))
                    self.assertEqual(result,{'status':'unavailable','reason':'memory_bridge_unavailable'})
                else:
                    # Completion access remains the same receipt-reader;
                    # the endpoint's scope/identity authorization is retained.
                    with patch('execution_memory.recall',new_callable=AsyncMock,return_value={'experiences':[]}) as recall:
                        self.assertEqual(json.loads(await tool.call(context,query='task')),{'experiences':[]})
                        self.assertEqual(recall.await_args.args[2],'group:12345' if group else 'private:67890')
        self.assertEqual(len(set(schemas)),1)
        self.assertEqual(execution_memory.tools({'config':{'behavior':{'memory':False}}},None,'private:67890',lambda:True),[])
        with patch('execution_memory.manifest',return_value=None):
            stale=execution_memory.tools(settings,None,'private:67890',lambda:False)[0]
            with self.assertRaises(asyncio.CancelledError):await stale.call(None)


if __name__=='__main__':unittest.main()
