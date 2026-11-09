"""Independent real entry gates and resumed work, using only local providers."""
import asyncio,contextvars,copy,itertools,json,sys,tempfile,time,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from autonomous_runtime import AutonomousRuntime
import autonomy,initiative


class Mechanisms(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'bot.sqlite');self.db=self.store.db
        self.now=datetime(2026,11,9,12,tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.event=contextvars.ContextVar('independent_mechanisms',default=None)
        self.cfg={'proactive':False,'behavior':{'memory':False,'ownerUin':'12345678','ownerContactEnabled':False,'groupParticipationEnabled':False,'groupParticipationGroups':['55555'],'quietStart':0,'quietEnd':0,'proactiveIntervalSeconds':0,'silenceSeconds':0,'proactiveModelSchedule':True}}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def contact(self,cfg):
        initiative.incoming(self.db,'private:12345678','12345678','99999999','真实私聊',self.now-7200)
        autonomy.owner_state(self.db,cfg,self.now-3600)
    def runtime(self,cfg):
        s={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'config':cfg}
        rt=AutonomousRuntime(NS(event_bus=NS(stopped_platforms=set())),{'bot':s},lambda *_:self.store,lambda _:None,{},None,None)
        rt.policies['bot']={**{k:s[k] for k in ('tenantId','botId','generation','revision')},'epoch':1,'cycleId':'cycle','config':{'mode':'active','rollout':'live'},'lease':s,'until':self.now*1000+14000,'_permit_deadline':time.monotonic()+14}
        work={**{k:s[k] for k in ('tenantId','botId','generation','revision')},'epoch':1,'cycleId':'cycle','principal':'autonomous-service','expiresAt':self.now*1000+10000}
        return rt,work
    async def test_eight_switch_combinations_use_independent_real_entry_gates(self):
        for proactive,group,owner in itertools.product((False,True),repeat=3):
            with self.subTest(proactive=proactive,group=group,owner=owner):
                cfg=copy.deepcopy(self.cfg);cfg['proactive']=proactive;cfg['behavior'].update(groupParticipationEnabled=group,ownerContactEnabled=owner)
                self.db.execute('DELETE FROM autonomy_groups');self.db.execute('DELETE FROM autonomy_owner');self.db.execute('DELETE FROM autonomy_owner_checks');self.db.execute('DELETE FROM contacts');self.db.commit()
                self.contact(cfg);calls=[]
                async def chat(**kw):
                    calls.append(kw)
                    return NS(completion_text=json.dumps({'contact':True,'topic':'真实共同话题','reply':True,'target':'open','participation':'open','information':'substantive','interest':'strong','reason_code':'shared_interest','confidence':'high'}))
                with patch('time.time',return_value=self.now):
                    self.store.planner.task=None
                    self.store.planner.maybe_start(cfg,NS(text_chat=chat),lambda:True,self.event,{},self.now)
                    if self.store.planner.task:await self.store.planner.task
                    self.assertEqual(len(calls),int(owner))
                    owner_item=autonomy.owner_delivery_candidate(self.db,cfg,self.now)
                    self.assertEqual(bool(owner_item),owner)
                    if owner_item:
                        self.assertTrue(autonomy.owner_delivery_current(self.db,cfg,owner_item,self.now))
                        self.assertFalse(autonomy.owner_delivery_current(self.db,cfg,{**owner_item,'scope':'private:22222222'},self.now))
                        self.assertFalse(autonomy.owner_delivery_current(self.db,cfg,{**owner_item,'owner_version':owner_item['owner_version']+1},self.now))
                        self.assertEqual(initiative.candidates(self.db,cfg,self.now)[0],owner_item)
                        self.assertTrue(initiative.reserve(self.db,cfg,owner_item,self.now))
                    calls.clear()
                    verdict=await self.store.presence.evaluate('group:55555',cfg,'event-'+str((proactive,group,owner)),NS(text_chat=chat),[{'sender_id':'22222222','text':'真实公开话题'}],{},{},lambda:True,{},self.event,self.now)
                    self.assertEqual(len(calls),int(group));self.assertEqual(verdict is not None,group)
                    rt,work=self.runtime(cfg)
                    self.assertEqual(rt.validation_error('bot',{**work,'_model_origin':True}),'' if proactive else 'autonomy_disabled')
                    self.assertEqual(rt.validation_error('bot',{**work,'_model_origin':False}),'')
    async def test_disabled_autonomous_research_never_calls_model_and_keeps_other_state(self):
        self.cfg['behavior'].update(ownerContactEnabled=True,groupParticipationEnabled=True)
        self.contact(self.cfg);before=self.db.execute('SELECT * FROM autonomy_owner').fetchall();rt,work=self.runtime(self.cfg)
        with patch('time.time',return_value=self.now):
            result=await rt.execute('bot',{'work':work,'action':{'kind':'research','objective':'pending research','requestSource':{'origin':'model'}}})
            self.assertEqual(result['usage']['modelCalls'],0);self.assertEqual(result['status'],'blocked')
            self.assertEqual(self.db.execute('SELECT * FROM autonomy_owner').fetchall(),before)
            self.assertTrue(autonomy.group_allowed(self.cfg,'group:55555'))
            self.cfg['proactive']=True
            self.assertEqual(rt.validation_error('bot',{**work,'_model_origin':True}),'')
    async def test_busy_plan_preflight_and_disable_during_model_fence(self):
        self.cfg['proactive']=True;self.cfg['behavior'].update(proactiveTargets=['private:22222222'],silenceSeconds=1800)
        initiative.incoming(self.db,'private:22222222','22222222','99999999','真实消息',self.now-400)
        calls=[]
        async def chat(**kw):calls.append(kw);return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"真实话题"}')
        self.store.planner.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.event,{},self.now)
        self.assertIsNone(self.store.planner.task);self.assertEqual(calls,[])
        self.db.execute('UPDATE contacts SET last_user=?',(self.now-4000,));self.db.commit()
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(**kw):calls.append(kw);entered.set();await release.wait();return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"真实话题"}')
        with patch('time.time',return_value=self.now):
            self.store.planner.maybe_start(self.cfg,NS(text_chat=delayed),lambda:True,self.event,{},self.now)
            await asyncio.wait_for(entered.wait(),2);self.cfg['proactive']=False;release.set();await self.store.planner.task
        self.assertIsNone(self.store.planner.plan('private:22222222',self.now-4000))
        self.assertEqual(len(calls),1)
    async def test_disable_during_real_tool_loop_blocks_continuation_but_user_task_still_runs(self):
        from native_internal import RunControl,run
        from astrbot.core.agent.tool import FunctionTool
        from astrbot.core.provider.entities import LLMResponse,TokenUsage
        self.cfg['proactive']=True;rt,work=self.runtime(self.cfg)
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        class Read(FunctionTool):
            async def call(self,context):entered.set();await release.wait();return {'source':'actual-local-read','value':7}
        class Provider:
            provider_config={'id':'independent-fixture','max_context_tokens':0}
            async def text_chat(self,**kw):
                calls.append(kw)
                return LLMResponse(role='assistant',tools_call_name=['read'],tools_call_args=[{}],tools_call_ids=['read-1'],usage=TokenUsage(input_other=10,output=10))
        with patch('time.time',return_value=self.now):
            control=RunControl(4,3,24000,10,lambda:rt.valid('bot',{**work,'_model_origin':True}))
            task=asyncio.create_task(run(Provider(),control,'research','system',tools=[Read(name='read',description='read only',parameters={'type':'object','properties':{}})]))
            await asyncio.wait_for(entered.wait(),2);self.cfg['proactive']=False;release.set();result=await task
            self.assertEqual(result['status'],'canceled');self.assertEqual(len(calls),1)
            self.assertEqual(result['usage']['modelCalls'],1);self.assertEqual(result['toolResults'][0]['tool'],'read')
            class UserProvider:
                provider_config={'id':'user-reminder-fixture','max_context_tokens':0}
                async def text_chat(self,**kw):return LLMResponse(role='assistant',completion_text='authorized reminder',usage=TokenUsage(input_other=10,output=10))
            user=await run(UserProvider(),RunControl(2,0,24000,10,lambda:rt.valid('bot',{**work,'_model_origin':False})),'authorized task','system')
            self.assertEqual(user['status'],'completed');self.assertEqual(user['usage']['modelCalls'],1)
    def test_contract_defaults_and_normal_reply_do_not_spend_proactive_budget(self):
        cfg={'behavior':{'groupParticipationGroups':['55555']}}
        self.assertTrue(autonomy.group_allowed(cfg,'group:55555'));self.assertFalse(autonomy.group_allowed({'behavior':{}},'group:55555'))
        for hour,expected in [(0,False),(1,True),(6,True),(7,False),(23,False)]:
            at=datetime(2026,11,9,hour,tzinfo=timezone(timedelta(hours=8))).timestamp()
            self.assertEqual(initiative.quiet(cfg,at),expected)
        initiative.incoming(self.db,'private:12345678','12345678','99999999','普通入站',self.now)
        initiative.outgoing(self.db,'private:12345678','普通回复',self.now+1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_sends').fetchone()[0],0)


if __name__=='__main__':unittest.main()
