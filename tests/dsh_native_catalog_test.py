"""Real SDK reset/provider ToolSet after SDK's native builtin append."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.agent.tool import FunctionTool,ToolSet
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.tools.message_tools import GetGroupMessageHistoryTool
from astrbot.core.provider.entities import ProviderRequest
import tenant_tools


class NativeCatalog(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings={'root':self.temp.name,'botId':'bot','tenantId':'tenant','generation':1,'revision':2}
        self.path=Path(self.temp.name)/'ai'/'dsh-bridge.json';self.path.parent.mkdir()
        self.bridge={'version':2,'mode':'production',**{k:self.settings[k] for k in ('botId','tenantId','generation','revision')},
                     'url':'http://127.0.0.1:1234/delegate','token':'a'*64}
        reset=ToolLoopAgentRunner.reset;handle=ToolLoopAgentRunner._handle_function_tools
        self.addCleanup(setattr,ToolLoopAgentRunner,'reset',reset)
        self.addCleanup(setattr,ToolLoopAgentRunner,'_handle_function_tools',handle)
        tenant_tools._install_tool_catalog(lambda event:True)

    async def request(self,*,native=True,internal=False,enabled=True,mode='full'):
        extras={'_hosted_lease':self.settings,'_hosted_internal_work':internal}
        event=NS(get_extra=lambda key:extras.get(key),unified_msg_origin='bot:GroupMessage:55555')
        plugin=NS(get_config=lambda **kw:{'provider_ltm_settings':{'group_message_history_enable':enabled}},
                  get_llm_tool_manager=lambda:NS(get_builtin_tool=lambda cls:cls()))
        context=NS(context=NS(event=event,context=plugin))
        keep=FunctionTool(name='dsh_delegate',description='delegate',parameters={'type':'object','properties':{}})
        # astr_main_agent appends this original SDK object before runner.reset.
        tools=[keep,GetGroupMessageHistoryTool()] if native else [keep]
        request=ProviderRequest(func_tool=ToolSet(tools=tools))
        runner=ToolLoopAgentRunner()
        await runner.reset(NS(provider_config={}),request,context,NS(),NS(),tool_schema_mode=mode)
        return runner

    async def test_ready_v2_removed_from_actual_provider_and_execution_sets(self):
        self.path.write_text(json.dumps(self.bridge),encoding='utf8')
        for native in (False,True):
            for mode in ('full','skills_like'):
                runner=await self.request(native=native,mode=mode)
                self.assertEqual(runner._func_tool_for_provider().names(),['dsh_delegate'])
                self.assertNotIn('get_group_message_history',json.dumps(runner._func_tool_for_provider().openai_schema()))
                executable=runner._skill_like_raw_tool_set if mode=='skills_like' else runner.req.func_tool
                self.assertEqual(executable.names(),['dsh_delegate'])

    async def test_no_manifest_preserves_sdk_baseline_and_private_append(self):
        for native in (False,True):
            runner=await self.request(native=native)
            self.assertEqual(set(runner._func_tool_for_provider().names()),{'dsh_delegate','get_group_message_history'})
        runner=await self.request(native=False,enabled=False)
        self.assertEqual(runner.req.func_tool.names(),['dsh_delegate'])

    async def test_invalid_manifest_fails_closed_and_internal_catalog_untouched(self):
        self.path.write_text(json.dumps({**self.bridge,'tenantId':'foreign'}),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'dsh_manifest_invalid'):await self.request()
        self.path.write_text(json.dumps(self.bridge),encoding='utf8')
        runner=await self.request(internal=True)
        self.assertIn('get_group_message_history',runner.req.func_tool.names())


if __name__=='__main__':unittest.main()
