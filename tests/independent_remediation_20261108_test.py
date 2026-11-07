"""Independent-replay regressions for the 20261108 remediation work packages.

Each test replays a defect the independent plan reproduced against production
behavior and asserts the fixed contract. No network, no models, no QQ.
"""
import json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from memory_scope import pending_messages,memory_audience,summary_rows,retained_context_visible
from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
from context_protocol import READONLY_CURRENT,model_json
import agreement_state


def packet(state):
    return {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':state,'recent_messages':[]})}


class LicenseSafety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.db=self.store.db;self.r=self.store.reflection;self.scope='private:12345678'
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def seed_checkpoint(self,state='available',request_id=None):
        summary_id=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version) VALUES(?,?,?,?,2)',(self.scope,'真实35轮摘要','[]',time.time())).lastrowid
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(summary_id,))
        self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(summary_id,self.scope))
        self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,request_id,created,summary_id) VALUES(?,35,35,0,?,?,?,?)",
                        (self.scope,state,request_id,time.time(),summary_id))
        self.db.commit();return summary_id

    def test_dead_process_reserved_permit_is_never_reissued(self):
        # The independent replay re-signed a dead request's reserved license.
        # R1: a reserved permit whose owner is gone stays conservatively
        # consumed — a journal row without the candidate is not proof the
        # request was never submitted.
        self.seed_checkpoint(state='reserved',request_id='99999@123.0@dead')
        with patch('system_contract.abandoned_request',return_value=True):
            license=self.r.reserve_context_reconstruction(self.scope,'new-live-request',summary_ids=[])
        self.assertIsNone(license,'an orphaned reserved permit must never be re-issued')
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints").fetchone()[0],'reserved')

    def test_stage_transaction_consumes_the_license_for_good(self):
        journal=ReadonlyJournal(self.db,self.scope)
        base=[{'role':'system','content':'old'},{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'facts':[]},'recent_messages':[]})}]
        first=journal.prepare(base,'one');self.assertTrue(journal.commit(first))
        summary_id=self.seed_checkpoint()
        journal.retire_committed(first,'fixture_retire')
        candidate=journal.prepare(base,'two',
                                  retirement=self.r.reconstruction_stamp(self.scope),
                                  reconstruction_authority=self.r,reconstruction_scope=self.scope)
        self.assertIsNotNone(candidate.reconstruction_permit,'a real 35-round checkpoint licenses the rebuild')
        self.assertTrue(journal.stage_candidate(candidate))
        self.assertTrue(candidate.reconstruction_consumed)
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints WHERE id=?",(candidate.reconstruction_permit['id'],)).fetchone()[0],'consumed')
        # Whatever happens now — abort, settle attempts, a fresh reserve — the
        # license never returns to available for another baseline.
        journal.abort_candidate(candidate)
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints WHERE id=?",(candidate.reconstruction_permit['id'],)).fetchone()[0],'consumed')
        self.assertFalse(self.r.settle_context_reconstruction(candidate.reconstruction_permit,False),'a consumed permit is not settleable again')
        self.assertIsNone(self.r.reserve_context_reconstruction(self.scope,'again',summary_ids=[]))
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints WHERE summary_id=?",(summary_id,)).fetchone()[0],'consumed')

    def test_stage_refusal_keeps_the_license_available(self):
        journal=ReadonlyJournal(self.db,self.scope)
        base=[{'role':'system','content':'old'},{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'facts':[]},'recent_messages':[]})}]
        first=journal.prepare(base,'one');self.assertTrue(journal.commit(first))
        self.seed_checkpoint()
        journal.retire_committed(first,'fixture_retire')
        candidate=journal.prepare(base,'two',retirement=self.r.reconstruction_stamp(self.scope),
                                  reconstruction_authority=self.r,reconstruction_scope=self.scope)
        with patch.object(journal,'_persist',return_value=False):
            self.assertFalse(journal.stage_candidate(candidate))
        self.assertFalse(candidate.reconstruction_consumed)
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints").fetchone()[0],'reserved')
        # The refused stage is proof nothing was submitted: settle(False)
        # returns the license to available for the scope's own retry.
        self.assertTrue(self.r.settle_context_reconstruction(candidate.reconstruction_permit,False))
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints").fetchone()[0],'available')

    def test_system_contract_is_per_scope(self):
        from system_contract import SystemContract,SystemContractViolation
        guard=SystemContract(self.db)
        now=time.time()
        first=guard.reserve([{'role':'system','content':'OLD'}],None,'a1',now,scope='group:1')
        self.assertTrue(first['bootstrap'])
        permit_id='save-permit-1'
        reserve=guard.reserve([{'role':'system','content':'NEW'}],permit_id,'a2',now,scope='group:1')
        self.assertTrue(reserve['changed'])
        # Scope 2 still runs the committed OLD vector with no permit of its
        # own: the global guard used to block it with a violation here.
        other=guard.reserve([{'role':'system','content':'OLD'}],None,'b1',now,scope='group:2')
        self.assertTrue(other['bootstrap']);self.assertFalse(other['changed'])
        # Scope 2 adopts the NEW vector through its own trusted transition.
        reserve2=guard.reserve([{'role':'system','content':'NEW'}],permit_id,'b2',now,scope='group:2')
        self.assertTrue(reserve2['changed'])
        self.assertTrue(guard.settle(reserve,True,now));self.assertTrue(guard.settle(reserve2,True,now))
        self.assertEqual(self.db.execute("SELECT system_sha FROM fishbot_system_scope_contract WHERE scope='group:1'").fetchone()[0],
                         self.db.execute("SELECT system_sha FROM fishbot_system_scope_contract WHERE scope='group:2'").fetchone()[0])
        # Same scope after its own transition: the committed vector is NEW, so
        # sending OLD again is an unauthorized change.
        with self.assertRaises(SystemContractViolation):
            guard.reserve([{'role':'system','content':'OLD'}],None,'a3',now,scope='group:1')
        with self.assertRaises(SystemContractViolation):
            guard.reserve([{'role':'system','content':'CHANGED'}],None,'a4',now,scope='group:1')

    def test_scope_transition_is_authorized_once_per_scope(self):
        from system_contract import SystemContract,SystemContractViolation
        guard=SystemContract(self.db);now=time.time()
        guard.reserve([{'role':'system','content':'OLD'}],None,'a1',now,scope='group:1')
        permit='permit-x'
        first=guard.reserve([{'role':'system','content':'NEW'}],permit,'a2',now,scope='group:1')
        with self.assertRaises(SystemContractViolation):
            # The same scope cannot run a second conflicting transition while
            # one is in flight.
            guard.reserve([{'role':'system','content':'OTHER'}],permit,'a3',now,scope='group:1')
        self.assertTrue(guard.settle(first,True,now))
        with self.assertRaises(SystemContractViolation):
            # A consumed trusted token cannot authorize yet another change.
            guard.reserve([{'role':'system','content':'CHANGED'}],permit,'a4',now,scope='group:1')


class MaterialLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.db=self.store.db;self.r=self.store.reflection
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def test_unsummarized_rounds_survive_the_global_quota(self):
        # The independent replay: 32 scopes x 34 rounds lost 88 rows before
        # any scope reached 35. Quota pressure now only reclaims rounds a
        # completed summary already processed.
        for s in range(32):
            person=str(20000000+s)
            for i in range(34):self.r.record('private:'+person,person,str(i),'q','a',time.time()+s*34+i)
        for s in range(32):
            self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds WHERE scope=?',('private:'+str(20000000+s),)).fetchone()[0],34)
        # A scope whose batch completed may have its processed rows reclaimed.
        done='private:20000000'
        summary_id=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(done,'摘要','[]',time.time())).lastrowid
        for event in [str(i) for i in range(34)]:
            self.db.execute('INSERT INTO memory_summary_coverage(scope,event,subject,digest,summary_id) VALUES(?,?,?,?,?)',(done,event,'20000000','x',summary_id))
        self.db.execute('INSERT OR REPLACE INTO memory_reflection_progress(scope,summary_watermark,insight_watermark) VALUES(?,?,?)',(done,34,34));self.db.commit()
        for i in range(1000):self.r.record('private:30000000','30000000',str(i),'q','a',time.time())
        remaining=self.db.execute('SELECT count(*) FROM memory_rounds WHERE scope=?',(done,)).fetchone()[0]
        self.assertLess(remaining,34,'processed covered rounds are reclaimable under quota pressure')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds WHERE scope=?',('private:20000001',)).fetchone()[0],34)

    def test_pending_quota_never_deletes_unanswered_evidence(self):
        person='12345678';scope='private:'+person
        for i in range(1000):self.r.record(scope,person,'round-'+str(i),'q','a',time.time())
        for i in range(40):self.r.incoming(scope,person,'unanswered-'+str(i),'还没有回复的问题',time.time()+i)
        self.r.prune_pending()
        remaining=self.db.execute("SELECT count(*) FROM memory_pending WHERE scope=? AND event LIKE 'unanswered-%'",(scope,)).fetchone()[0]
        self.assertEqual(remaining,40,'an unanswered pending row is the only copy of that evidence')

    def test_summary_quota_spares_claims_and_coverage(self):
        scope='private:12345678'
        victims=[]
        for i in range(1005):
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,'普通摘要'+str(i),'[]',time.time()+i)).lastrowid
            victims.append(identifier)
        protected=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',
                                  (scope,'带约定证据','[]',time.time()-9999,json.dumps([{'id':'aabbccdd11223344','text':'周六八点东门见','kind':'agreement','speaker':{'role':'human','id':'12345678'},'sourceRefs':[{'event':'e1','role':'user'}],'visibility':scope,'status':'open','protected':True}],ensure_ascii=False))).lastrowid
        self.db.execute('INSERT INTO memory_summary_levels VALUES(?,?)',(protected,'small'))
        self.db.commit()
        from memory_reflection import Reflection
        Reflection.__init__(self.r,self.db)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_summaries WHERE id=?',(protected,)).fetchone(),'a claims-bearing summary survives the count quota')

    def test_one_work_one_round_with_complete_material(self):
        from context_protocol import SOURCE
        scope='group:55555';person='12345678'
        self.r.incoming(scope,person,'e1','今晚吃什么',time.time())
        # The Work-level finalization aggregates all confirmed bubbles BEFORE
        # the single record call; at the store, the (scope,event) identity is
        # one round, and a repeated submission can never duplicate or mutate it.
        native=SOURCE+json.dumps({'scope':scope,'sender_id':person,'event_id':'e1'})+'\n今晚吃什么'
        self.r.record(scope,person,'e1','今晚吃什么','第一段：举双鳍赞成\n第二段：再给我一碗',time.time(),materials=[{'version':1,'event':'e1','subject':person,'scope':scope,'text':native,'reply':'第一段：举双鳍赞成\n第二段：再给我一碗','native_reply':None,'images':[],'complete':True,'digest':'a'*64}])
        self.r.record(scope,person,'e1','今晚吃什么','迟到的另一段',time.time()+1)
        rows=self.db.execute('SELECT count(*),answer FROM memory_rounds WHERE scope=? AND event=?',(scope,'e1')).fetchone()
        self.assertEqual(rows[0],1)
        self.assertIn('第二段：再给我一碗',rows[1],'the finalized aggregate reaches the round material')
        material=self.db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(scope,'e1')).fetchone()
        self.assertIsNotNone(material,'the complete tool-loop material is retained for the summary batch')


class AudienceContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.db=self.store.db;self.r=self.store.reflection
        self.person='12345678';self.private='private:'+self.person;self.group='group:55555';self.other='group:66666'
        self.now=time.time()
        self.r.incoming(self.private,self.person,'secret-event','SYNTHETIC_PRIVATE_SECRET',self.now)
        self.r.record(self.private,self.person,'secret-event','SYNTHETIC_PRIVATE_SECRET','SYNTHETIC_ACK',self.now+1)
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.private,'SYNTHETIC_PRIVATE_SUMMARY','["secret-event"]',self.now)).lastrowid
        self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(identifier,self.person));self.db.commit()
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def test_private_pending_never_enters_a_group_request(self):
        self.assertEqual(pending_messages(self.db,self.group,self.person,self.now+2),[])
        # The private chat itself keeps its own rows (scope-local view).
        self.assertEqual(len(pending_messages(self.db,self.private,self.person,self.now+2,cross_scope=False)),1)

    def test_private_summary_never_enters_group_recall(self):
        rows=summary_rows(self.db,self.group,self.person)
        self.assertFalse(any(row[0]=='SYNTHETIC_PRIVATE_SUMMARY' or 'SYNTHETIC_PRIVATE' in row[1] for row in rows))
        self.assertFalse(memory_audience({'scope':self.group,'subject':self.person},{'source_scope':self.private}))
        self.assertTrue(memory_audience({'scope':self.private,'subject':self.person},{'source_scope':self.private}))

    def test_group_material_reaches_participant_private_but_not_other_groups(self):
        gid=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.group,'群里的共同经历','[]',self.now)).lastrowid
        self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(gid,self.person));self.db.commit()
        self.assertTrue(any(row[0]==gid for row in summary_rows(self.db,self.private,self.person)))
        self.assertFalse(any(row[0]==gid for row in summary_rows(self.db,self.other,self.person)),'another group is a different audience')
        self.r.incoming(self.group,self.person,'group-event','群里的消息',self.now)
        self.assertEqual(len(pending_messages(self.db,self.other,self.person,self.now+2)),0)
        # The participant's private chat cross-session view carries the group
        # message they really sent (single-person audience), not other scopes'
        # private rows; the scope-local view keeps their own private row.
        self.assertEqual([r['source_event'] for r in pending_messages(self.db,self.private,self.person,self.now+2)],['group-event'])
        self.assertEqual([r['source_event'] for r in pending_messages(self.db,self.private,self.person,self.now+2,cross_scope=False)],['secret-event'])

    def test_retained_wire_gate_blocks_private_entries(self):
        entry={'kind':'recent_uncompressed_message','source_scope':self.private,'sender_id':self.person,'source_event':'secret-event','at':self.now,'user':'SYNTHETIC_PRIVATE_SECRET','bot':'SYNTHETIC_ACK'}
        poisoned=[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'recent_cross_session_messages':[entry]}})}]
        self.assertFalse(retained_context_visible(self.db,poisoned,self.group,'768687099'))
        clean_group=[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'recent_cross_session_messages':[{**entry,'source_scope':self.group,'source_event':'group-event'}]}})}]
        self.assertTrue(retained_context_visible(self.db,clean_group,self.group,'768687099'))
        # A private summary row inside a group wire is equally forbidden.
        summary_entry={'id':1,'summary':'SYNTHETIC_PRIVATE_SUMMARY','source_scope':self.private,'source_events':[],'at':self.now}
        private_leak=[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'conversation_summaries':[summary_entry]}})}]
        self.assertFalse(retained_context_visible(self.db,private_leak,self.group,'768687099'))


class AgreementProjection(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.db=self.store.db;self.owner='768687099';self.private='private:'+self.owner;self.group='group:963105233'
        self.cfg={'ownerUin':self.owner,'groups':['963105233']}
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def test_private_instruction_reaches_group_as_current_state(self):
        # The 02:25 contract: "在群里被@时不要回复" said in private must be a
        # current, trusted commitment inside the group decision — without
        # exposing the private chat's verbatim text.
        row,error=agreement_state.apply_update(self.db,'propose','主人在群里艾特时不回复',
            speaker=self.owner,participants=[self.owner,'bot'],source_scope=self.private,source_event='e-0225',
            audiences=[self.group],detail='主人一会去群里艾特，不要回复',cfg=self.cfg)
        self.assertEqual(error,'')
        row2,error2=agreement_state.apply_update(self.db,'accept','主人在群里艾特时不回复',speaker='bot',
            participants=['bot'],source_scope=row['source_scope'],source_event=row['source_event'],cfg=self.cfg)
        self.assertEqual(error2,'')
        visible=agreement_state.context_view(self.db,self.group)
        self.assertEqual([r['status'] for r in visible],['accepted'])
        self.assertEqual(visible[0]['topic'],'主人在群里艾特时不回复')
        blob=json.dumps(visible,ensure_ascii=False)
        self.assertNotIn('用户说在群里别回',blob,'the cross-scope view carries the bounded state, never private verbatim text')
        self.assertNotIn('主人一会去群里艾特',blob)
        stamp=agreement_state.agreements_stamp(self.db,self.group)
        row3,_=agreement_state.apply_update(self.db,'cancel','主人在群里艾特时不回复',speaker=self.owner,
            participants=[self.owner],source_scope=row['source_scope'],source_event=row['source_event'],cfg=self.cfg)
        self.assertEqual(row3['status'],'canceled')
        self.assertNotEqual(agreement_state.agreements_stamp(self.db,self.group),stamp,'a mid-Work revision invalidates the stale candidate via the context fence')

    def test_non_owner_cannot_bind_arbitrary_group_audiences(self):
        member='11111111'
        row,error=agreement_state.apply_update(self.db,'propose','普通群友的约定',speaker=member,
            participants=[member,'bot'],source_scope=self.group,source_event='e-1',
            audiences=[self.private],cfg=self.cfg)
        self.assertEqual(error,'')
        self.assertEqual(row['audiences'],[self.group],'only the current scope and participant privates are available to non-owners')

    def test_claims_promotion_keeps_source_scope_only(self):
        claims=[{'id':'aabbccdd11223344','text':'周六八点东门见','kind':'agreement','speaker':{'role':'human','id':self.owner},
                 'about':'周六八点东门见','sourceRefs':[{'scope':self.private,'event':'e-9','role':'user'}],
                 'occurredAt':time.time(),'status':'open','stage':'accepted','visibility':self.private,'protected':True}]
        promoted=agreement_state.promote_from_claims(self.db,self.private,claims)
        self.assertEqual(promoted,1)
        self.assertEqual([r['id'] for r in agreement_state.visible(self.db,self.private)],[agreement_state.agreement_identity(self.private,'e-9','周六八点东门见')])
        self.assertEqual(agreement_state.visible(self.db,self.group),[],'legacy claims never gain audiences in bulk')


class PrivateFreshness(unittest.TestCase):
    def test_private_review_triggers_only_on_real_new_messages(self):
        from group_freshness import GroupFreshness,review_system
        import asyncio
        f=GroupFreshness();scope='private:12345678'
        f.observe(scope,'e1','12345678','第一句',now=time.monotonic())
        cursor=f.cursor(scope)
        calls=[]
        async def call(payload):
            calls.append(payload);return json.dumps({'action':'keep','reason':'still fits'})
        async def run():
            # No new messages since the Work started: keep without any model call.
            keep=await f.check(scope,cursor,time.monotonic(),'e1','草稿','',call,lambda:True)
            self.assertEqual((keep['action'],keep['checked']),('keep',False));self.assertEqual(calls,[])
            started=time.monotonic()
            f.observe(scope,'e2','12345678','我改主意了，不要发了',now=time.monotonic())
            reviewed=await f.check(scope,cursor,started,'e1','草稿','',call,lambda:True)
            self.assertEqual(reviewed['checked'],True);self.assertEqual(len(calls),1)
            self.assertIn('latest_messages',calls[0])
        asyncio.new_event_loop().run_until_complete(run())
        private_prompt=review_system('人格','12345678','99999',group=False)
        self.assertIn('私聊发送前复核器',private_prompt)
        self.assertNotIn('群聊发送前复核器',private_prompt)


if __name__=='__main__':unittest.main()
