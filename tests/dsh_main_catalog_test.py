"""Actual feature ToolSet acceptance with isolated SQLite/manifests; no network."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS,ModuleType
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.provider.entities import ProviderRequest
from behavior import BehaviorStore
from bot_pipeline import Work
import bilibili_tools
import inner_tools

# Capture the task-start HEAD for reproducibility after this change is committed.
BASE_COMMIT='6e7438f098fd583c326c7f21e403340e8a669d02'


class MainCatalog(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'ai').mkdir()
        self.store=BehaviorStore(self.root/'behavior.sqlite');self.addCleanup(self.store.close)
        self.settings={'root':str(self.root),'botId':'bot','tenantId':'tenant','generation':1,'revision':2,
                       'config':{'groups':['55555'],'behavior':{'ownerUin':'12345678','qqProfile':True,'qzone':True}}}
        operations=[{'operation':op,'write':op in ('like','coin','comment'),'scope':'media.analyze' if op=='analyze' else 'public.read',
                     'args':{'type':'object','properties':{}}} for op in list(bilibili_tools.DESCRIPTIONS)[:35]]
        self.assertEqual(len(operations),35)
        (self.root/'ai'/'bilibili-bridge.json').write_text(json.dumps({'version':1,'botId':'bot','generation':1,'revision':2,
            'settingsRevision':1,'allowAutonomous':True,'url':'http://127.0.0.1:1','token':'fixture','operations':operations}),encoding='utf8')
        self.scope='group:55555';self.person='12345678'
        work=Work('bot',self.scope,self.person,'99999999','event','message',time.time(),self.settings)
        self.extras={'_hosted_pipeline':work,'_hosted_lease':self.settings}
        bot=NS(get_login_info=AsyncMock(return_value={'user_id':'99999999'}),get_group_list=AsyncMock(return_value=[{'group_id':55555,'group_name':'Target'}]),
               get_group_member_info=AsyncMock(return_value={'user_id':self.person}),get_group_member_list=AsyncMock(return_value=[{'user_id':self.person,'nickname':'Owner'}]))
        self.event=NS(get_extra=lambda key,default=None:self.extras.get(key,default),set_extra=lambda key,value:self.extras.__setitem__(key,value),
            get_sender_id=lambda:self.person,get_self_id=lambda:'99999999',get_group_id=lambda:'55555',get_message_str=lambda:'fixture',
            get_messages=lambda:[],message_obj=NS(message_id='event',raw_message={}),bot=bot)
        self.context=NS(context=NS(event=self.event))
        self.store.reflection.incoming(self.scope,self.person,'earlier','fixture deep message must go through DSH',time.time())

    def features(self):
        return {tool.name:tool for tool in inner_tools.features(self.settings,self.event,self.store.db,self.scope,lambda:True,evidence=[])}

    def enable_dsh(self):
        (self.root/'ai'/'dsh-bridge.json').write_text(json.dumps({'version':2,'mode':'production',
            **{k:self.settings[k] for k in ('botId','tenantId','generation','revision')},
            'url':'http://127.0.0.1:1/delegate','token':'a'*64}),encoding='utf8')

    async def test_real_main_feature_contract_and_schema_measurement(self):
        before=self.features()
        self.assertIn('hosted_recall_experience',before)
        self.assertEqual(before['hosted_qq_favorites'].parameters['properties']['operation']['enum'],['sources','list','prepare','save'])
        old_group=json.loads(await before['hosted_qq_group_context'].call(self.context,group_id='55555'))
        self.assertIn('fixture deep message',json.dumps(old_group))
        self.enable_dsh();after=self.features()
        baseline_source=subprocess.check_output(['git','show',BASE_COMMIT+':adapters/astrbot/inner_tools.py'],cwd=ROOT,encoding='utf8')
        baseline_namespace={'__name__':'dsh_catalog_task_start_baseline'}
        exec(compile(baseline_source,'git:'+BASE_COMMIT+':inner_tools.py','exec'),baseline_namespace)
        originals={}
        for name in ('qq_actions','dsh_tools'):
            module=ModuleType(name)
            text=subprocess.check_output(['git','show',BASE_COMMIT+':adapters/astrbot/'+name+'.py'],cwd=ROOT,encoding='utf8')
            exec(compile(text,'git:'+BASE_COMMIT+':'+name+'.py','exec'),module.__dict__);originals[name]=module
        with patch.dict(sys.modules,originals):
            baseline={tool.name:tool for tool in baseline_namespace['features'](self.settings,self.event,self.store.db,self.scope,lambda:True,evidence=[])}
        self.assertNotIn('hosted_recall_experience',after)
        self.assertEqual(after['hosted_qq_favorites'].parameters['properties']['operation']['enum'],['prepare','save'])
        self.assertEqual(sum(name=='dsh_delegate' for name in after),1)
        # Real worker recovery/probe acceptance permits analysis ownership in DSH.
        self.assertNotIn('bilibili_analyze',after)
        self.assertIn('hosted_bilibili_image',after)
        self.assertEqual([name for name in after if name.startswith('bilibili_')],[])
        self.assertEqual(set(after)-set(before),{'dsh_delegate'})
        self.assertEqual(set(before)-set(after),{name for name in before if name.startswith('bilibili_')}|{'hosted_recall_experience'})
        self.assertEqual(set(baseline)-set(after),{'hosted_qq_publish','hosted_qq_view'})
        self.assertNotIn('hosted_qq_publish',before);self.assertNotIn('hosted_qq_view',before)
        self.assertEqual(set(after)-set(baseline),set())
        new_group=json.loads(await after['hosted_qq_group_context'].call(self.context,group_id='55555'))
        self.assertEqual(new_group['data']['members'],{self.person:'Owner'})
        self.assertEqual(set(new_group['data']),{'group_id','source','members'})
        self.assertNotIn('fixture deep message',json.dumps(new_group))
        changed={'dsh_delegate'}
        unchanged=(set(baseline)&set(after))-changed
        self.assertTrue(unchanged)
        for name in unchanged:
            self.assertEqual(ToolSet(tools=[baseline[name]]).openai_schema(),ToolSet(tools=[after[name]]).openai_schema(),name)
        encode=lambda tools:json.dumps(ToolSet(tools=list(tools.values())).openai_schema(),ensure_ascii=False,separators=(',',':'))
        size_before=len(encode(baseline));size_after=len(encode(after))
        self.assertLess(size_after,size_before)
        print(json.dumps({'measurement':'this_change_incremental_same_dsh_v2_manifest','baseline_commit':BASE_COMMIT,
                          'before':size_before,'after':size_after,'removed':size_before-size_after,'tools_before':len(baseline),'tools_after':len(after),
                          'unrelated_schemas_identical':sorted(unchanged)},ensure_ascii=False))
        print(json.dumps({'measurement':'configuration_comparison_no_dsh_vs_dsh_not_incremental_change_benefit',
                          'no_dsh_chars':len(encode(before)),'dsh_chars':size_after,'no_dsh_tools':len(before),'dsh_tools':len(after)}))

    async def test_autonomous_main_and_completion_actual_provider_schemas_and_worker_boundary(self):
        self.enable_dsh()
        identity={'id':'cycle','kind':'research'}
        self.extras['_hosted_internal_work']=identity
        self.extras['_hosted_main_activation']=True
        autonomous=inner_tools.features(self.settings,self.event,self.store.db,self.scope,lambda:True,identity=identity)
        runner=ToolLoopAgentRunner()
        await runner.reset(NS(provider_config={}),ProviderRequest(func_tool=ToolSet(tools=autonomous)),self.context,NS(),NS())
        names=runner._func_tool_for_provider().names()
        self.assertIn('dsh_delegate',names);self.assertNotIn('hosted_recall_experience',names)
        self.assertNotIn('get_group_message_history',names)
        favorite=runner.req.func_tool.get_tool('hosted_qq_favorites')
        self.assertEqual(favorite.parameters['properties']['operation']['enum'],['prepare','save'])
        self.assertNotIn('bilibili_search',names)
        self.assertNotIn('bilibili_analyze',names)
        self.assertNotIn('hosted_qq_publish',names);self.assertNotIn('hosted_qq_view',names)
        self.extras.pop('_hosted_main_activation')
        worker=inner_tools.features(self.settings,self.event,self.store.db,self.scope,lambda:True,identity=identity)
        self.assertNotIn('dsh_delegate',{tool.name for tool in worker})
        self.assertIn('hosted_recall_experience',{tool.name for tool in worker})
        self.assertEqual(sum(tool.name.startswith('bilibili_') for tool in worker),35)
        self.extras.pop('_hosted_internal_work');self.extras['_hosted_tool_completion']={'id':'original'}
        completion=self.features()
        self.assertNotIn('dsh_delegate',completion)
        self.assertNotIn('hosted_recall_experience',completion);self.assertNotIn('bilibili_analyze',completion)
        self.assertEqual(completion['hosted_qq_favorites'].parameters['properties']['operation']['enum'],['prepare','save'])


if __name__=='__main__':unittest.main()
