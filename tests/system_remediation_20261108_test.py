"""Durability and judgment lifecycle regressions; no network or model calls."""
import asyncio,contextvars,copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]
from readonly_journal import ReadonlyJournal,ContextPrefixViolation
from context_protocol import READONLY_CURRENT,model_json
from behavior import BehaviorStore
from bot_pipeline import decision_memory,decision_version

class Durability(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.scope='private:12345'
        self.j=ReadonlyJournal(self.db,self.scope)
        self.base=[{'role':'system','content':'immutable'},{'role':'user','content':'question'}]
        self.packet={'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'facts':[]},'recent_messages':[]})}
        self.first=self.j.prepare(self.base+[self.packet],'one');self.assertTrue(self.j.commit(self.first))
    def tearDown(self):self.db.close()
    def candidate(self):return self.j.prepare(self.base+[{'role':'assistant','content':'answer'},{'role':'user','content':'next'},self.packet],'one')
    def test_new_chat_guard_cannot_rewrite_existing_system_before_checkpoint(self):
        from context_protocol import persona_prompt,CHAT_SEMANTIC_GUARD
        updated=copy.deepcopy(self.base)
        updated[0]['content']=persona_prompt('new role config',images=True,chat=True)
        p=self.j.prepare(updated+[{'role':'assistant','content':'answer'},{'role':'user','content':'next'},self.packet],'one')
        self.assertEqual(p.wire[:len(self.first.wire)],self.first.wire)
        self.assertNotIn(CHAT_SEMANTIC_GUARD,p.wire[0]['content'])
    def test_chat_wakeups_use_durable_scope_without_minting_business_rounds(self):
        from model_cache import is_foreground_event
        for trigger in ('scheduled_contact','owner_contact','tool_continuation'):
            with self.subTest(trigger=trigger):
                extras={'_hosted_pipeline':NS(source=trigger)}
                event=NS(get_extra=lambda key,default=None:extras.get(key,default))
                self.assertTrue(is_foreground_event(event))
                db=sqlite3.connect(':memory:')
                try:
                    j=ReadonlyJournal(db,'private:98765')
                    initial=self.base+[self.packet]
                    p=j.prepare(initial,'timer');self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p))
                    restarted=ReadonlyJournal(db,'private:98765')
                    continued=restarted.prepare(self.base+[{'role':'assistant','content':'proactive reply'},{'role':'user','content':'real user follows'},self.packet],'message')
                    self.assertEqual(continued.wire[:len(p.wire)],p.wire)
                    self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_rounds'").fetchone())
                finally:db.close()
    def test_success_and_unknown_accept_exact_durable_stage_without_second_write(self):
        for method in ('commit','retain_submitted'):
            with self.subTest(method=method):
                p=self.candidate();self.assertTrue(self.j.stage_candidate(p))
                persisted=self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
                with patch.object(self.j,'_persist',side_effect=sqlite3.OperationalError('disk full')):
                    self.assertTrue(getattr(self.j,method)(p,*(['request_submission_unknown'] if method=='retain_submitted' else [])))
                self.assertEqual(ReadonlyJournal(self.db,self.scope).last.wire,p.wire)
                self.assertEqual(self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],persisted)
                self.assertEqual(p.wire[:len(self.first.wire)],self.first.wire)
    def test_post_stage_mutation_is_never_accepted(self):
        p=self.candidate();self.assertTrue(self.j.stage_candidate(p));p.wire[0]['content']='mutated'
        with self.assertRaises(ContextPrefixViolation):self.j.stage_candidate(p)
        with self.assertRaises(ContextPrefixViolation):self.j.commit(p)
        self.assertEqual(ReadonlyJournal(self.db,self.scope).last.wire[0],self.base[0])
    def test_abort_restore_failure_keeps_memory_and_restart_on_same_durable_candidate(self):
        for failure in (False,sqlite3.OperationalError('disk full')):
            p=self.candidate();self.assertTrue(self.j.stage_candidate(p))
            kwargs={'side_effect':failure} if isinstance(failure,Exception) else {'return_value':False}
            with patch.object(self.j,'_persist',**kwargs):self.assertFalse(self.j.abort_candidate(p))
            self.assertEqual(self.j.last.wire,ReadonlyJournal(self.db,self.scope).last.wire)
            self.assertEqual(self.j.last.wire,p.wire)
    def test_separate_scope_connections_share_one_atomic_capacity_bound(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        barrier=threading.Barrier(2)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'journals.sqlite'
            db=sqlite3.connect(path);ReadonlyJournal(db,'private:11111');db.close()
            sample=self.j.prepare(self.base+[self.packet],'one')
            limit=len(self.j._snapshot(sample).encode())+200
            def stage(scope):
                db=sqlite3.connect(path,timeout=5)
                try:
                    j=ReadonlyJournal(db,scope);p=j.prepare(self.base+[self.packet],'one')
                    barrier.wait(timeout=3);return j.stage_candidate(p)
                finally:db.close()
            with patch('readonly_journal.MAX_PERSISTED_JOURNAL_BYTES',limit),ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(stage,scope) for scope in ('private:11111','private:22222')]
                self.assertEqual(sorted(f.result(timeout=8) for f in futures),[False,True])
            db=sqlite3.connect(path)
            try:self.assertLessEqual(db.execute('SELECT sum(length(cast(payload AS BLOB))) FROM fishbot_readonly_journals').fetchone()[0],limit)
            finally:db.close()

    def test_successful_unsent_abort_restores_previous_stage(self):
        p=self.candidate();self.assertTrue(self.j.stage_candidate(p));self.assertTrue(self.j.abort_candidate(p))
        self.assertEqual(self.j.last.wire,self.first.wire)
        self.assertEqual(ReadonlyJournal(self.db,self.scope).last.wire,self.first.wire)

class DecisionLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite');self.db=self.store.db;self.now=time.time()
        self.cfg={'behavior':{'groupParticipationEnabled':True,'groupParticipationGroups':['12345'],'groupParticipationIntervalMinutes':10,'hourlyInterjectionLimit':20,'quietStart':0,'quietEnd':0}}
        self.var=contextvars.ContextVar('decision-test',default=None)
    def tearDown(self):self.store.close();self.temp.cleanup()
    async def test_activity_diagnostics_are_owned_and_late_tasks_release_busy(self):
        from runtime_care import RuntimeCare
        other=BehaviorStore(Path(self.temp.name)/'other'/'behavior.sqlite')
        try:
            other.active=1
            care=RuntimeCare({'owned':self.store,'other':other});care.stamp(self.store.root)
            row=json.loads((self.store.root/'memory-activity.json').read_text())
            self.assertFalse(row['active']);self.assertEqual(row['work']['active'],0)
            release=asyncio.Event();task=asyncio.create_task(release.wait());self.store.planner.task=task
            care.track(self.store,task)
            row=json.loads((self.store.root/'memory-activity.json').read_text());self.assertEqual(row['work']['background'],1)
            release.set();await task;await asyncio.sleep(0)
            row=json.loads((self.store.root/'memory-activity.json').read_text());self.assertFalse(row['active'])
            self.assertEqual(row['work']['background'],0)
            care.close()
        finally:other.close()
    def defer(self,event,at):
        raw={'post_type':'message','message_type':'group','group_id':'12345','message_id':event,'user_id':'11111','self_id':'99999'}
        self.assertTrue(self.store.presence.defer('group:12345',self.cfg,event,raw,'11111','99999','real input',at))
    async def test_judging_older_event_cannot_consume_newer_already_deferred_input(self):
        self.defer('new',self.now-1)
        async def chat(**kwargs):return NS(completion_text=json.dumps({'reply':False,'target':'other','participation':'none','information':'none','interest':'none','reason_code':'other_conversation','confidence':'high'}))
        provider=NS(text_chat=chat)
        await self.store.presence.evaluate('group:12345',self.cfg,'old',provider,[{'sender_id':'11111','text':'old'}],{},{},lambda:True,{},self.var,now=self.now)
        row=self.db.execute('SELECT event FROM autonomy_group_inbox').fetchone();self.assertEqual(row[0],'new')
        self.assertEqual(self.store.presence.pending(self.cfg,provider,lambda:True,self.now+601)['event'],'new')
    async def test_memory_change_during_decision_discards_late_result_and_payload_has_time(self):
        captured=[]
        async def chat(**kwargs):
            captured.append(kwargs)
            self.db.execute("INSERT INTO memory_blocks(scope,subject,name,revision) VALUES('group:12345','11111','drink',1)");self.db.commit()
            return NS(completion_text=json.dumps({'reply':True,'target':'bot','participation':'direct','information':'substantive','interest':'strong','reason_code':'direct_request','confidence':'high'}))
        result=await self.store.presence.evaluate('group:12345',self.cfg,'one',NS(text_chat=chat),[{'sender_id':'11111','text':'hello','at':self.now-1}],{},{},lambda:True,{},self.var,now=self.now)
        self.assertFalse(result[0].reply);self.assertEqual(result[0].source,'stale')
        payload=json.loads(captured[0]['prompt'])
        self.assertIn('current_time',payload);self.assertEqual(payload['messages'][0]['at'],self.now-1)
        self.assertIn('active_agreements',payload['memory'])
        self.assertTrue(captured[0]['system_prompt'].startswith('[机器人配置]'))
    def test_private_fact_is_internally_readable_by_same_person_judgment(self):
        self.store.observe('private:11111','11111','记住：饮料=绿茶','private-secret',self.now)
        group=decision_memory(self.db,self.cfg,'group:12345','11111',self.now)['facts']
        self.assertEqual(group[0]['value'],'绿茶');self.assertEqual(group[0]['source_scope'],'private:11111')
        own=decision_memory(self.db,self.cfg,'private:11111','11111',self.now)['facts']
        self.assertEqual(own[0]['value'],'绿茶')
        self.assertEqual(decision_memory(self.db,self.cfg,'private:22222','22222',self.now)['facts'],[])

    def test_expired_inbox_does_not_permanently_exclude_scope(self):
        self.defer('expired',self.now-86401)
        self.assertIsNone(self.store.presence.pending(self.cfg,object(),lambda:True,self.now))
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_group_inbox').fetchone()[0],0)
    def test_memory_revision_fences_pending_judgment(self):
        before=decision_version(self.db,'group:12345')
        self.db.execute("INSERT INTO memory_blocks(scope,subject,name,revision) VALUES('group:12345','11111','drink',1)")
        self.assertNotEqual(before,decision_version(self.db,'group:12345'))
        self.assertEqual(decision_memory(self.db,self.cfg,'group:67890','11111',self.now)['facts'],[])

if __name__=='__main__':unittest.main(verbosity=2)
