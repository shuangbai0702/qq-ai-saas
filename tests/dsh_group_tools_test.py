"""Trusted DSH group reads use original SDK/tools; no QQ or model calls."""
import asyncio
import datetime
import hashlib
import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

sys.path[:0]=[str(Path('adapters/astrbot').resolve()),str(Path('references/astrbot').resolve())]
from astrbot.core.platform.astrbot_message import MessageType
from astrbot.core.tools.message_tools import GetGroupMessageHistoryTool
from bot_pipeline import BotPipeline
from dsh_group_tools import GroupReadBridge,main_context_tool


class GroupRead(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');BotPipeline(self.db)
        self.db.execute('CREATE TABLE companion_members(scope TEXT,subject TEXT,seen REAL)')
        self.cfg={'groups':['55555'],'behavior':{'enabled':True,'ownerUin':'12345','memory':False}}
        self.settings={'botId':'bot','tenantId':'tenant','generation':1,'revision':2,'config':self.cfg}
        self.work=NS(bot_id='bot',scope='group:55555',subject='12345',event_id='event')
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES('group:55555','12345','event',?,'message','model','received','')",(time.time(),))
        self.parent={'bot_id':'bot','tenant_id':'tenant','generation':1,'config_revision':2,'authority':'foreground',
                     'origin_scope':'group:55555','sender_id':'12345','origin_event':'event',
                     'work_id':'foreground:'+hashlib.sha256(json.dumps(['bot','group:55555','12345','event'],ensure_ascii=False).encode()).hexdigest()}
        self.extra={'_hosted_pipeline':self.work};self.live=True;self.history_enabled=True
        self.bot=NS(get_login_info=AsyncMock(return_value={'user_id':'99999'}),get_group_list=AsyncMock(return_value=[{'group_id':55555}]),
                    get_group_member_info=AsyncMock(return_value={'user_id':'12345'}),get_group_member_list=AsyncMock(return_value=[{'user_id':12345,'nickname':'Owner'}]))
        self.event=NS(get_extra=lambda key,*default:self.extra.get(key,*default),set_extra=lambda key,value:self.extra.__setitem__(key,value),
                      get_sender_id=lambda:self.work.subject,get_group_id=lambda:'55555',get_self_id=lambda:'99999',get_platform_id=lambda:'platform',
                      get_message_type=lambda:MessageType.GROUP_MESSAGE,unified_msg_origin='platform:GroupMessage:55555',bot=self.bot)
        self.history=GetGroupMessageHistoryTool()
        self.manager=NS(get=AsyncMock(return_value=[NS(id=1,sender_id='12345',sender_name='Owner',created_at=datetime.datetime.now(),
                                                      content={'message':[{'type':'plain','text':'trusted original history'}]})]))
        plugin=NS(get_config=lambda **kw:{'provider_ltm_settings':{'group_message_history_enable':self.history_enabled}},
                  get_llm_tool_manager=lambda:NS(get_builtin_tool=lambda cls:self.history),message_history_manager=self.manager)
        self.context=NS(context=NS(event=self.event,context=plugin))
        self.bridge=GroupReadBridge(self.settings,self.event,lambda:self.live,self.db,self.context,self.parent)

    def tearDown(self):self.db.close()

    async def test_original_definitions_and_sdk_query(self):
        definitions={d['name']:d for d in self.bridge.definitions()}
        self.assertEqual(set(definitions),{'get_group_message_history','hosted_qq_group_context'})
        self.assertEqual(definitions['get_group_message_history']['parameters'],self.history.parameters)
        definitions['get_group_message_history']['parameters']['properties'].clear()
        self.assertIn('limit',self.history.parameters['properties'])
        result=await self.bridge.execute('get_group_message_history',{'keyword':'original'})
        self.assertIn('trusted original history',result)
        self.manager.get.assert_awaited_once_with(platform_id='platform',user_id=self.event.unified_msg_origin,page_size=700)

    async def test_group_context_uses_real_authorization_and_current_group(self):
        result=json.loads(await self.bridge.execute('hosted_qq_group_context',{}))
        self.assertEqual(result['group_id'],'55555')
        self.bot.get_group_member_info.assert_awaited_once_with(group_id=55555,user_id=12345,no_cache=True)
        with self.assertRaisesRegex(ValueError,'target_denied'):
            await self.bridge.execute('hosted_qq_group_context',{'group_id':'66666'})
        with self.assertRaisesRegex(ValueError,'arguments_invalid'):
            await self.bridge.execute('get_group_message_history',{'scope':'group:66666'})
        with self.assertRaisesRegex(ValueError,'unavailable'):
            await self.bridge.execute('hosted_qq_group_send',{'group_id':'55555','text':'no'})

    async def test_original_denial_not_fabricated_as_success(self):
        self.bot.get_group_member_info.return_value={'user_id':'different'}
        result=json.loads(await self.bridge.execute('hosted_qq_group_context',{}))
        self.assertIn('group_context_unavailable',json.dumps(result))

    async def test_late_sdk_result_and_completed_parent_are_rejected(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(**kwargs):entered.set();await release.wait();return []
        self.manager.get=delayed
        task=asyncio.create_task(self.bridge.execute('get_group_message_history',{}))
        await entered.wait();self.live=False;release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.live=True;self.db.execute("UPDATE pipeline_events SET status='completed'")
        with self.assertRaisesRegex(ValueError,'parent_stale'):self.bridge.definitions()

    async def test_private_completion_and_foreign_work_never_expand_scope(self):
        self.event.get_group_id=lambda:''
        with self.assertRaisesRegex(ValueError,'scope_mismatch'):self.bridge.definitions()
        self.event.get_group_id=lambda:'55555'
        self.extra['_hosted_tool_completion']={'id':'synthetic'}
        with self.assertRaisesRegex(ValueError,'foreground_work_required'):self.bridge.definitions()
        self.extra.pop('_hosted_tool_completion');self.work.subject='67890'
        with self.assertRaisesRegex(ValueError,'work_mismatch'):self.bridge.definitions()

    async def test_disabled_sdk_history_is_not_available(self):
        self.history_enabled=False
        self.assertEqual([d['name'] for d in self.bridge.definitions()],['hosted_qq_group_context'])
        with self.assertRaisesRegex(ValueError,'unavailable'):await self.bridge.execute('get_group_message_history',{})

    async def test_ordinary_sender_gets_only_original_current_group_history(self):
        self.cfg['behavior']['ownerUin']='67890'
        self.assertEqual([d['name'] for d in self.bridge.definitions()],['get_group_message_history'])
        self.assertIn('trusted original history',await self.bridge.execute('get_group_message_history',{}))
        with self.assertRaisesRegex(ValueError,'unavailable'):await self.bridge.execute('hosted_qq_group_context',{})
        self.bot.get_group_list.assert_not_awaited()

    async def test_config_identity_change_rejected_before_sdk_dispatch(self):
        self.settings['revision']=3
        with self.assertRaisesRegex(ValueError,'identity_config_revision'):
            await self.bridge.execute('get_group_message_history',{})
        self.manager.get.assert_not_awaited()

    async def test_main_routing_keeps_original_group_members_and_rejections(self):
        import group_actions
        from tool_outcome import structured
        original=group_actions.tools(self.event,self.cfg,self.db,lambda:self.live,lambda *a:None)[0]
        routing=main_context_tool(original)
        self.assertEqual(routing.parameters,original.parameters)
        members=json.loads(await structured(routing).call(self.context,group_id='55555'))
        self.assertEqual(members['data']['members'],{'12345':'Owner'})
        self.assertEqual(set(members['data']),{'group_id','source','members'})
        groups=json.loads(await routing.call(self.context))
        self.assertEqual(groups['groups'],[{'group_id':'55555','name':''}])
        self.assertEqual(set(groups),{'groups','source'})
        self.bot.get_group_member_info.return_value={'user_id':'foreign'}
        expected=await original.call(self.context,group_id='55555')
        self.assertEqual(await routing.call(self.context,group_id='55555'),expected)

    async def test_main_routing_excludes_message_provenance_while_dsh_original_keeps_it(self):
        from astrbot.core.agent.tool import FunctionTool
        from tool_outcome import structured
        payload={'group_id':'55555','source':'original source','members':[{'user_id':'12345','name':'Owner','extra':'member private field'}],
                 'recent_received':[{'text':'hidden recent message','sender_id':'12345','source_event':'hidden provenance'}],
                 'conversation_timeline':[{'text':'hidden timeline message'}],'future_history':'hidden future field'}
        class Original(FunctionTool):
            async def call(tool,context,**kwargs):
                self.assertIs(context,self.context)
                return json.dumps(payload)
        original=Original(name='hosted_qq_group_context',description='original',parameters={'type':'object','properties':{'group_id':{'type':'string'}}})
        main=json.loads(await structured(main_context_tool(original)).call(self.context,group_id='55555'))
        self.assertEqual(main['data']['members'],[{'user_id':'12345','name':'Owner'}])
        self.assertNotIn('hidden',json.dumps(main));self.assertNotIn('member private field',json.dumps(main))
        self.assertEqual(json.loads(await original.call(self.context)),payload)


if __name__=='__main__':unittest.main()
