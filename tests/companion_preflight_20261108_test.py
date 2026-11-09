"""Actual planner task boundaries: no model calls, QQ sends or paid services."""
import asyncio,contextvars,json,sqlite3,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import autonomy,initiative
from companion_schedule import Planner


class Preflight(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');initiative.initialize(self.db)
        self.db.execute('CREATE TABLE deliveries(scope TEXT,day TEXT,count INTEGER,last_sent REAL,PRIMARY KEY(scope,day))')
        self.db.execute('CREATE TABLE group_interjections(scope TEXT,event TEXT,at REAL)')
        self.planner=Planner(self.db);self.now=time.time();self.event=contextvars.ContextVar('event',default=None)
        initiative.incoming(self.db,'private:12345','12345','99999','想聊茶',self.now-400)
        self.cfg={'proactive':True,'behavior':{'memory':False,'proactiveTargets':['private:12345'],'proactiveModelSchedule':True,'quietStart':0,'quietEnd':0,'dailyPlanLimit':10,'silenceSeconds':300,'proactiveIntervalSeconds':300}}
        self.calls=[];self.reasons=[]
        async def chat(**kwargs):
            self.calls.append(kwargs)
            return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"茶"}')
        self.provider=NS(text_chat=chat)
    def tearDown(self):self.planner.close();self.db.close()
    async def test_new_message_before_task_starts_never_spends_request_budget(self):
        self.planner.maybe_start(self.cfg,self.provider,lambda:True,self.event,{},self.now)
        initiative.incoming(self.db,'private:12345','12345','99999','新消息',self.now)
        with patch('companion_schedule.discarded',side_effect=self.reasons.append):await self.planner.task
        self.assertEqual(self.calls,[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM companion_plan_budget').fetchone()[0],0)
        self.assertEqual(self.reasons,['conversation_changed'])
    async def test_result_invalidated_during_request_has_explicit_disposition(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def chat(**kwargs):
            self.calls.append(kwargs);entered.set();await release.wait()
            return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"茶"}')
        metrics={}
        self.planner.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.event,metrics,self.now)
        with patch('companion_schedule.discarded',side_effect=self.reasons.append):
            await asyncio.wait_for(entered.wait(),2);initiative.incoming(self.db,'private:12345','12345','99999','新消息',self.now)
            release.set();await self.planner.task
        self.assertEqual(len(self.calls),1)
        self.assertIn('lastPlanUsage',metrics)
        self.assertEqual(self.reasons,['conversation_changed'])
        self.assertIsNone(self.planner.plan('private:12345',self.now-400))
        self.assertEqual(self.db.execute('SELECT count FROM companion_plan_budget').fetchone()[0],1)
    async def test_replaced_runtime_before_task_starts_never_calls_provider(self):
        live=[True]
        self.planner.maybe_start(self.cfg,self.provider,lambda:live[0],self.event,{},self.now);live[0]=False
        with patch('companion_schedule.discarded',side_effect=self.reasons.append):await self.planner.task
        self.assertEqual(self.calls,[]);self.assertEqual(self.reasons,['runtime_replaced'])
    async def test_owner_preflight_rechecks_real_interaction_before_charging(self):
        self.cfg['behavior'].update(ownerUin='12345',ownerContactEnabled=True,ownerContactIntervalMinutes=15,ownerContactIdleMinutes=1,proactiveIntervalSeconds=0)
        initiative.incoming(self.db,'private:12345','12345','99999','想聊茶',self.now-4000)
        autonomy.owner_state(self.db,self.cfg,self.now-3600)
        self.planner.maybe_start(self.cfg,self.provider,lambda:True,self.event,{},self.now)
        autonomy.note_interaction(self.db,self.cfg,'private:12345','12345',self.now+1)
        with patch('companion_schedule.discarded',side_effect=self.reasons.append):await self.planner.task
        self.assertEqual(self.calls,[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_owner_budget').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM autonomy_owner_checks').fetchone()[0],'conversation_changed')
    def test_private_window_reads_shared_authority_without_drafts_or_group_displacement(self):
        from shared_context import recent,JOURNAL_SCOPE
        from context_protocol import SOURCE,model_json
        import hashlib
        self.db.execute('CREATE TABLE fishbot_readonly_journals(scope TEXT,payload TEXT,version INTEGER,sha TEXT)')
        def message(scope,event,text):
            return {'role':'user','content':SOURCE+model_json({'scope':scope,'event_id':event,'sender_id':'12345','at':self.now})+'\n'+text}
        messages=[message('private:12345','private','真实共同话题'),{'role':'assistant','content':'这只是模型原稿，不等于发送成功'}]
        messages.extend(message('group:55555',str(i),'群聊'+str(i)) for i in range(20))
        payload=model_json({'scope':model_json(JOURNAL_SCOPE),'base':messages})
        self.db.execute('INSERT INTO fishbot_readonly_journals VALUES(?,?,1,?)',(model_json(JOURNAL_SCOPE),payload,hashlib.sha256(payload.encode()).hexdigest()))
        view=recent(self.db,4,1200,scope='private:12345')
        self.assertEqual(len(view),1);self.assertEqual(view[0]['source_event'],'private')
        self.assertEqual(view[0]['sender_id'],'12345');self.assertEqual(view[0]['text'],'真实共同话题')
        self.assertEqual(self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],payload)

if __name__=='__main__':unittest.main()
