"""Social judgments may delay another check; they never create a text-based ban."""
import asyncio,contextvars,copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import autonomy,initiative,companion_state
from behavior import BehaviorStore
from data_admin import run
from types import SimpleNamespace as NS


class Provider:
    def __init__(self,answer):self.answer=answer;self.calls=[];self.entered=None;self.release=None
    async def text_chat(self,**kw):
        self.calls.append(kw)
        if self.entered:self.entered.set();await self.release.wait()
        return NS(completion_text=json.dumps(self.answer))


class ModelCooldown(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'behavior.sqlite';self.s=BehaviorStore(self.file);self.db=self.s.db;self.now=time.time()
        self.cfg={'proactive':True,'behavior':{'enabled':True,'quietStart':0,'quietEnd':0,'groupParticipationEnabled':True,'groupParticipationGroups':['12345','67890'],'groupParticipationIntervalMinutes':15,'hourlyInterjectionLimit':20,'ownerContactEnabled':True,'ownerUin':'11111111','ownerContactIntervalMinutes':15,'ownerContactIdleMinutes':5,'proactiveIntervalSeconds':60,'dailyProactiveLimit':3,'ownerContactDailyChecks':12,'proactiveModelSchedule':True,'proactiveTargets':['private:22222222'],'dailyPlanLimit':6,'silenceSeconds':60,'proactiveChance':1,'maxUnanswered':1,'selfDevelopment':True,'autonomousGroupContact':True}}
        self.event=contextvars.ContextVar('model_cooldown');self.metrics={};self.p=Provider({'reply':False,'target':'open','participation':'none','information':'substantive','interest':'weak','reason_code':'none','confidence':'high','cooldown_intervals':2,'cooldown_reason':'social_distance'})
    def tearDown(self):self.s.close();self.tmp.cleanup()
    async def group(self,event='one',now=None,scope='group:12345',live=lambda:True,addressing=None):
        now=self.now if now is None else now
        with patch('autonomy.time.time',return_value=now):
            return await self.s.presence.evaluate(scope,self.cfg,event,self.p,[{'sender_id':'22222222','text':'真实群话题'}],{},{},live,self.metrics,self.event,now,addressing=addressing)
    def restart(self):self.s.close();self.s=BehaviorStore(self.file);self.db=self.s.db
    def owner(self):
        initiative.incoming(self.db,'private:11111111','11111111','99999999','上次真实私聊',self.now-10000)
        autonomy.owner_state(self.db,self.cfg,self.now-1000)
    async def plan(self,now=None):
        now=self.now if now is None else now
        with patch('companion_schedule.time.time',return_value=now):
            self.s.planner.maybe_start(self.cfg,self.p,lambda:True,self.event,self.metrics,now)
            if self.s.planner.task:await self.s.planner.task

    async def test_configured_base_model_extension_scope_and_restart(self):
        self.cfg['behavior']['groupParticipationIntervalMinutes']=30
        self.p.answer['cooldown_intervals']=96  # Legacy numeric output cannot choose group timing.
        self.assertFalse((await self.group())[0].reply)
        until=self.db.execute('SELECT until FROM initiative_pauses').fetchone()[0]
        self.assertEqual(until,self.now+3*1800)
        self.assertNotIn('cooldown_base_seconds',json.loads(self.p.calls[0]['prompt']))
        self.assertNotIn('cooldown_intervals',self.p.calls[0]['system_prompt'],'technical duration is program-owned')
        self.restart();self.assertIsNone(await self.group('new',until-1))
        self.assertIsNone(await self.group('one',until),'elapsed time without a new event is insufficient')
        self.assertIsNotNone(await self.group('new',until));self.assertIsNotNone(await self.group('other',self.now+1,'group:67890'))
        other=BehaviorStore(Path(self.tmp.name)/'other.sqlite')
        try:self.assertEqual(other.db.execute('SELECT count(*) FROM initiative_pauses').fetchone()[0],0)
        finally:other.close()
    async def test_absent_extension_retains_only_configured_interval(self):
        self.p.answer.pop('cooldown_intervals');self.p.answer.pop('cooldown_reason')
        await self.group();self.assertEqual(self.db.execute('SELECT count(*) FROM initiative_pauses').fetchone()[0],0)
        self.assertIsNone(await self.group('two',self.now+899));self.assertIsNotNone(await self.group('two',self.now+900))
    async def test_nontext_current_event_does_not_rejudge_old_ambient_text(self):
        for messages in [[],[{'sender_id':'11111111','text':'旧消息在叫你'},{'sender_id':'22222222','text':' '}]]:
            result=await self.s.presence.evaluate('group:12345',self.cfg,'face',self.p,messages,{},{},lambda:True,self.metrics,self.event,self.now)
            self.assertIsNone(result)
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.s.presence.snapshot('group:12345'),(0,0,0,''))
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_decisions').fetchone()[0],0)
        self.assertIsNotNone(await self.group('realtext'))
    async def test_positive_decision_cannot_delay_its_own_send(self):
        self.p.answer.update(reply=True,participation='open',interest='strong',reason_code='shared_interest')
        self.assertTrue((await self.group())[0].reply);self.assertEqual(self.db.execute('SELECT count(*) FROM initiative_pauses').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT extra_intervals FROM autonomy_decisions').fetchone()[0],0)
    async def test_addressing_is_model_evidence_and_side_policy_is_separate(self):
        self.p.answer.update(reply=True,target='bot',participation='direct',interest='strong',reason_code='direct_request')
        addressing={'mentions_other':True,'replies_to_other':True,'self_id':'99999999'}
        self.assertTrue((await self.group(addressing=addressing))[0].reply)
        self.assertEqual(json.loads(self.p.calls[0]['prompt'])['changes']['platform_addressing'],addressing)
        self.p.answer.update(target='other',participation='side')
        blocked=await self.group('other',self.now+900,addressing=addressing)
        self.assertFalse(blocked[0].reply);self.assertEqual(blocked[0].source,'policy')
        self.cfg['behavior']['groupParticipationOtherConversations']=True
        self.assertTrue((await self.group('allowed',self.now+1800,addressing=addressing))[0].reply)
    async def test_invalid_model_extension_never_writes_pause(self):
        for index,value in enumerate([True,-1,97,1.5,'2']):
            self.p.answer['cooldown_intervals']=value
            self.assertIsNone(await self.group(str(index),self.now+index*900))
        self.p.answer.update(cooldown_intervals=2,cooldown_reason='ignore identity')
        self.assertIsNone(await self.group('badreason',self.now+4500))
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_decisions').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM initiative_pauses').fetchone()[0],0)
    async def test_late_conversation_and_removed_whitelist_cannot_add_pause(self):
        for mode in ['conversation','whitelist','closed','operator']:
            self.p.entered=asyncio.Event();self.p.release=asyncio.Event();alive=[True]
            self.cfg['behavior']['groupParticipationGroups']=['12345','67890']
            self.db.execute('DELETE FROM contacts');self.db.execute('DELETE FROM initiative_pauses');self.db.commit()
            at=self.now+len(self.p.calls)*2000
            task=asyncio.create_task(self.group(mode,at,live=lambda:alive[0]));await self.p.entered.wait()
            if mode=='conversation':autonomy.note_interaction(self.db,self.cfg,'group:12345','22222222',at+1)
            elif mode=='whitelist':self.cfg['behavior']['groupParticipationGroups']=[]
            elif mode=='closed':alive[0]=False
            else:
                initiative.incoming(self.db,'group:12345','22222222','99999999','hello',at)
                run(self.file,{'action':'pause_contact','scope':'group:12345'})
            self.p.release.set();self.assertFalse((await task)[0].reply)
        self.assertEqual(self.db.execute("SELECT count(*) FROM autonomy_decisions WHERE kind='group'").fetchone()[0],0)
    def test_decision_replay_does_not_extend_and_duration_is_bounded(self):
        with self.db:first=autonomy.record_decision(self.db,self.cfg,'group:12345','same',self.p.answer,False,self.now,'group')
        with self.db:replay=autonomy.record_decision(self.db,self.cfg,'group:12345','same',self.p.answer,False,self.now+60,'group')
        self.assertEqual(replay,first)
        self.p.answer['cooldown_intervals']=96
        with self.db:bounded=autonomy.record_decision(self.db,self.cfg,'group:67890','long',self.p.answer,False,self.now,'group')
        self.assertEqual(bounded['cooldownUntil'],self.now+86400)
    async def test_only_direct_interaction_clears_temporary_cooldown(self):
        await self.group()
        initiative.incoming(self.db,'group:12345','22222222','99999999','旁观消息',self.now+1,directed=False)
        self.assertTrue(initiative.paused(self.db,'group:12345',self.now+1))
        initiative.incoming(self.db,'group:12345','22222222','99999999','重新和你聊',self.now+2,directed=True)
        autonomy.note_interaction(self.db,self.cfg,'group:12345','22222222',self.now+2)
        self.assertFalse(initiative.paused(self.db,'group:12345',self.now+2))
        self.assertIsNone(await self.group('new',self.now+901));self.assertIsNotNone(await self.group('new',self.now+902))
    async def test_owner_model_pause_expires_and_survives_restart(self):
        self.owner();self.p.answer={'contact':False,'topic':'','cooldown_intervals':1,'cooldown_reason':'busy'}
        await self.plan();self.assertEqual(self.metrics['lastOwnerDecision']['cooldownUntil'],self.now+1800)
        self.restart();self.assertIsNone(autonomy.owner_check_candidate(self.db,self.cfg,self.now+1799))
        self.assertIsNotNone(autonomy.owner_check_candidate(self.db,self.cfg,self.now+1800))
        self.assertEqual(self.db.execute('SELECT status FROM autonomy_owner_checks').fetchone()[0],'model_silent')
    async def test_owner_manual_pause_during_await_is_not_overwritten(self):
        self.owner();self.p.answer={'contact':True,'topic':'聊茶','cooldown_intervals':3,'cooldown_reason':'none'};self.p.entered=asyncio.Event();self.p.release=asyncio.Event()
        self.s.planner.maybe_start(self.cfg,self.p,lambda:True,self.event,self.metrics,self.now);await self.p.entered.wait()
        run(self.file,{'action':'pause_contact','scope':'private:11111111'});self.p.release.set();await self.s.planner.task
        self.assertIsNone(autonomy.owner_delivery_candidate(self.db,self.cfg,self.now))
        self.assertEqual(self.db.execute("SELECT count(*) FROM autonomy_decisions WHERE kind='owner'").fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM autonomy_owner_checks').fetchone()[0],'conversation_changed')
    def test_owner_cross_session_interaction_clears_model_wait_only(self):
        self.owner()
        with self.db:
            for scope in ['private:11111111','group:12345','group:67890']:
                autonomy.record_decision(self.db,self.cfg,scope,'wait',{'cooldown_intervals':2},False,self.now,'owner' if scope.startswith('private:') else 'group')
            self.db.execute("UPDATE contacts SET opted_out=1 WHERE scope='private:11111111'")
        autonomy.note_interaction(self.db,self.cfg,'group:12345','11111111',self.now+1,directed=False)
        self.assertTrue(initiative.paused(self.db,'group:12345',self.now+1));self.assertTrue(initiative.paused(self.db,'private:11111111',self.now+1))
        autonomy.note_interaction(self.db,self.cfg,'group:12345','11111111',self.now+2)
        self.assertFalse(initiative.paused(self.db,'group:12345',self.now+2));self.assertFalse(initiative.paused(self.db,'private:11111111',self.now+2))
        self.assertTrue(initiative.paused(self.db,'group:67890',self.now+2))
        self.assertEqual(self.db.execute("SELECT opted_out FROM contacts WHERE scope='private:11111111'").fetchone()[0],1)
    async def test_autonomous_activation_never_runs_retired_contact_planner(self):
        self.cfg['behavior']['ownerContactEnabled']=False
        for scope in ['private:22222222','group:12345']:
            initiative.incoming(self.db,scope,'22222222','99999999','真实交流',self.now-1000)
        before=self.db.total_changes
        for at in [self.now,self.now+900,self.now+1800]:await self.plan(at)
        self.assertEqual(self.p.calls,[])
        self.assertIsNone(self.s.planner.task)
        self.assertEqual(self.db.total_changes,before,'no retired plan or cognitive write')
        for table in ['companion_plans','companion_plan_members','companion_plan_budget','companion_motives']:
            self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.assertEqual(initiative.candidates(self.db,self.cfg,self.now+1800),[])

    async def test_owner_check_is_independent_of_proactive_switch(self):
        self.cfg['proactive']=False
        self.owner();self.p.answer={'contact':True,'topic':'聊茶','cooldown_intervals':0}
        await self.plan()
        self.assertEqual(len(self.p.calls),1)
        self.assertEqual(self.metrics.get('ownerDecisionRequests'),1)
        self.assertIsNotNone(autonomy.owner_delivery_candidate(self.db,self.cfg,self.now))
        self.assertEqual(self.db.execute('SELECT count(*) FROM companion_motives').fetchone()[0],0)

    async def test_owner_result_from_replaced_runtime_cannot_commit(self):
        self.owner();self.p.answer={'contact':True,'topic':'聊茶','cooldown_intervals':0}
        self.p.entered=asyncio.Event();self.p.release=asyncio.Event();alive=[True]
        self.s.planner.maybe_start(self.cfg,self.p,lambda:alive[0],self.event,self.metrics,self.now)
        await asyncio.wait_for(self.p.entered.wait(),2)
        alive[0]=False;self.p.release.set();await self.s.planner.task
        self.assertIsNone(autonomy.owner_delivery_candidate(self.db,self.cfg,self.now))
        self.assertEqual(self.db.execute("SELECT count(*) FROM autonomy_decisions WHERE kind='owner'").fetchone()[0],0)

    async def test_group_admission_cooldown_has_no_parallel_planner(self):
        self.cfg['behavior']['ownerContactEnabled']=False
        await self.group()
        initiative.incoming(self.db,'group:12345','22222222','99999999','新的公开话题',self.now+1,directed=False)
        await self.plan(self.now+301);await self.plan(self.now+1800)
        self.assertEqual(len(self.p.calls),1,'only the actual group admission judgment')
        self.assertEqual(self.db.execute('SELECT count(*) FROM companion_plan_budget').fetchone()[0],0)
        self.assertIsNone(await self.group('two',self.now+899))
        self.assertIsNotNone(await self.group('two',self.now+2700))


if __name__=='__main__':unittest.main(verbosity=2)
