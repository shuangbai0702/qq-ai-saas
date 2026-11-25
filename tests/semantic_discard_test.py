"""Shared recovery at legal checkpoints; actual Provider wire and rollback."""
import contextvars,hashlib,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot'),str(ROOT/'tests')]
from context_protocol import event_message,model_json
import summary_material,semantic_batch,semantic_collection


from summary_stream_fixture import summary_provider

class DiscardTests(unittest.IsolatedAsyncioTestCase):
    from semantic_provider_wire_test import SemanticProviderWire as Fixture
    asyncSetUp=Fixture.asyncSetUp
    asyncTearDown=Fixture.asyncTearDown

    def prepare(self,count=2):
        db=self.store.db;r=self.store.reflection;r.shared_context=True;r.checkpoint_callback=None
        for i in range(count):
            msg=event_message({'scope':'private:12345678','sender_id':'12345678','event_id':str(i),'at':100+i},'actual complete text')
            material=summary_material.snapshots([msg,{'role':'assistant','content':'sent'}],'bot_shared')
            r.record('bot_shared','12345678',material[0]['event'],'display','sent',now=100+i,materials=material)
        db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('group:55555','87654321','lost','ACTUAL_REMAINING_FRAGMENT','',50))
        db.commit()
        r.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('discard_test',default=None))
        return r,db

    def blocked(self,db,event,status='blocked_recovery',scope='group:66666',at=40):
        value={'post_type':'message','message_type':'group','group_id':int(scope.split(':')[1]),
               'user_id':87654321,'message_id':event,'message':[{'type':'text','data':{'text':'BLOCKED_ORIGINAL_'+event}}]}
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        db.execute('INSERT INTO pipeline_work_inputs(scope,event,subject,payload,payload_sha,status,observed_at,created,identity) VALUES(?,?,?,?,?,?,?,?,?)',
                   (scope,event,'87654321',raw,hashlib.sha256(raw.encode()).hexdigest(),status,at,at,'{}'))

    async def test_blocked_originals_join_30_round_wire_then_retire_without_replaying_unknown(self):
        r,db=self.prepare(30)
        self.blocked(db,'recovery');self.blocked(db,'unknown','blocked_unknown');self.blocked(db,'future',at=500)
        db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,attempted,confirmed) VALUES('group:66666','87654321','unknown',40,'foreground','send','unknown',1,0)")
        db.commit();receipt=db.execute("SELECT * FROM pipeline_events WHERE event='unknown'").fetchall()
        self.assertTrue(r.retry());await r.task
        self.assertEqual(len(self.requests),1)
        wire=model_json(self.requests[0]['messages'])
        self.assertEqual(wire.count('BLOCKED_ORIGINAL_recovery'),1);self.assertEqual(wire.count('BLOCKED_ORIGINAL_unknown'),1)
        self.assertNotIn('BLOCKED_ORIGINAL_future',wire)
        self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone(),(30,))
        checkpoint=db.execute('SELECT checkpoint_id FROM memory_semantic_batches').fetchone()[0]
        self.assertIsNotNone(db.execute("SELECT payload FROM pipeline_work_inputs WHERE event='recovery'").fetchone()[0])
        with db:
            db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=?",(checkpoint,))
            semantic_batch.purge(db,checkpoint)
        self.assertEqual(db.execute("SELECT status,payload FROM pipeline_work_inputs WHERE event='recovery'").fetchone(),('settled_retired_observation',None))
        self.assertEqual(db.execute("SELECT status,payload FROM pipeline_work_inputs WHERE event='unknown'").fetchone(),('blocked_unknown',None))
        self.assertEqual(db.execute("SELECT * FROM pipeline_events WHERE event='unknown'").fetchall(),receipt)
        self.assertIsNotNone(db.execute("SELECT payload FROM pipeline_work_inputs WHERE event='future'").fetchone()[0])

    async def test_blocked_input_with_live_scope_owner_is_not_retirement_evidence(self):
        r,db=self.prepare(30);self.blocked(db,'live')
        db.execute("INSERT INTO pipeline_scope_work(scope,event) VALUES('group:66666','active-work')");db.commit()
        self.assertTrue(r.retry());await r.task
        self.assertEqual(len(self.requests),1);self.assertNotIn('BLOCKED_ORIGINAL_live',model_json(self.requests))
        self.assertIsNotNone(db.execute("SELECT payload FROM pipeline_work_inputs WHERE event='live'").fetchone()[0])

    async def test_damaged_blocked_original_is_audited_as_missing_without_stalling_summary(self):
        r,db=self.prepare(30);self.blocked(db,'broken')
        db.execute("UPDATE pipeline_work_inputs SET payload='not-json' WHERE event='broken'");db.commit()
        self.assertTrue(r.retry());await r.task
        self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone(),(30,))
        self.assertIn('full_original',model_json(self.requests));self.assertNotIn('not-json',model_json(self.requests))

    async def test_resolving_failure_retains_content_free_cause(self):
        r,db=self.prepare()
        detail={'reason':'semantic_discard_material_changed','type':'RuntimeError'}
        r.checkpoint_issue('bot_shared',0,'failure_summary',detail)
        r.checkpoint_issue('bot_shared',0,'failure_summary',{},resolved=True)
        status,raw=db.execute("SELECT status,detail FROM memory_checkpoint_issues WHERE kind='failure_summary'").fetchone()
        self.assertEqual(status,'resolved');self.assertEqual(json.loads(raw),detail)
        changes=db.total_changes
        r.checkpoint_issue('bot_shared',0,'failure_summary',{},resolved=True)
        self.assertEqual(db.total_changes,changes,'Repeated recovery must not churn the resolved diagnostic')

    async def test_admin_offers_real_fragments_once_and_records_honest_loss(self):
        r,db=self.prepare()
        started=r.force_compress_once(allow_partial=True,administrator='admin',request_id='grant',discard_unavailable=True)
        self.assertTrue(started['started']);result=await r.task;self.assertTrue(result['ok'],result)
        self.assertEqual(len(self.requests),1)
        wire=model_json(self.requests[0]['messages'])
        self.assertIn('ACTUAL_REMAINING_FRAGMENT',wire);self.assertIn('full_original',wire)
        self.assertIsNone(db.execute("SELECT 1 FROM memory_round_material WHERE scope='group:55555' AND event='lost'").fetchone())
        self.assertEqual(db.execute('SELECT missing_count,incomplete_count,conflict_count FROM memory_semantic_discard_audit').fetchone(),(1,0,0))
        cp,members=db.execute('SELECT checkpoint_id,members FROM memory_semantic_batches').fetchone()
        self.assertIn(semantic_batch.event_key('bot_shared',model_json(['group:55555','lost'])),json.loads(members))
        self.assertIsNotNone(db.execute("SELECT 1 FROM memory_pending WHERE scope='group:55555'").fetchone(),'do not delete before Journal replacement')
        db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=?",(cp,));db.commit()
        with db:semantic_batch.purge(db,cp)
        self.assertIsNone(db.execute("SELECT 1 FROM memory_pending WHERE scope='group:55555'").fetchone())

    async def test_ordinary_admin_force_uses_shared_material_recovery(self):
        r,db=self.prepare();r.force_compress_once(allow_partial=True,administrator='admin',request_id='strict')
        result=await r.task;self.assertTrue(result['ok'],result);self.assertEqual(len(self.requests),1)
        self.assertIn('ACTUAL_REMAINING_FRAGMENT',model_json(self.requests[0]['messages']))
        self.assertEqual(result['discarded']['full_original'],1)

    async def test_normal_30_round_checkpoint_recovers_without_admin_or_extra_call(self):
        r,db=self.prepare(30);self.assertTrue(r.retry());await r.task
        self.assertEqual(len(self.requests),1)
        wire=model_json(self.requests[0]['messages'])
        self.assertIn('ACTUAL_REMAINING_FRAGMENT',wire);self.assertIn('full_original',wire)
        self.assertEqual(wire.count('existing_memory是本次开始时平台提供的长期记忆快照'),1)
        self.assertIn('missing部分未提供',wire);self.assertNotIn('管理员已授权',wire)
        self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone(),(30,))
        self.assertEqual(db.execute('SELECT administrator,missing_count FROM memory_semantic_discard_audit').fetchone(),('automatic:30_round_checkpoint',1))
        self.assertIsNotNone(db.execute("SELECT 1 FROM memory_pending WHERE scope='group:55555'").fetchone(),'keep material until Journal replacement')

    async def test_normal_recovery_does_not_bypass_30_round_boundary(self):
        r,db=self.prepare(29);self.assertFalse(r.retry());self.assertEqual(self.requests,[])
        self.assertIsNone(db.execute('SELECT 1 FROM memory_semantic_batches').fetchone())

    async def test_orphan_pool_is_model_input_then_whole_batch_purge_protects_future(self):
        r,db=self.prepare()
        db.execute("INSERT INTO social_samples(scope,event,subject,text,created) VALUES('group:55555','orphan','87654321','ORPHAN_REAL_TEXT',50)")
        db.execute("INSERT INTO social_samples(scope,event,subject,text,created) VALUES('group:55555','future','87654321','NEW_BATCH_KEEP',500)")
        db.commit();r.force_compress_once(allow_partial=True,administrator='admin',request_id='orphan')
        outcome=await r.task;self.assertTrue(outcome['ok'],outcome)
        wire=model_json(self.requests[0]['messages']);self.assertIn('ORPHAN_REAL_TEXT',wire);self.assertNotIn('NEW_BATCH_KEEP',wire)
        checkpoint=outcome['checkpoint']['id']
        with db:
            db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=?",(checkpoint,))
            semantic_batch.purge(db,checkpoint)
        self.assertIsNone(db.execute("SELECT 1 FROM social_samples WHERE event='orphan'").fetchone())
        self.assertIsNotNone(db.execute("SELECT 1 FROM social_samples WHERE event='future'").fetchone())

    async def test_partial_force_requires_administrator_and_failure_records_loss_before_retirement(self):
        r,db=self.prepare()
        with self.assertRaises(PermissionError):r.force_compress_once(discard_unavailable=True)
        async def fail(**kwargs):raise RuntimeError('upstream unavailable')
        r.binding=(self.cfg,summary_provider(fail),lambda:True,self.metrics,contextvars.ContextVar('discard_fail',default=None))
        r.force_compress_once(allow_partial=True,administrator='admin',request_id='fail',discard_unavailable=True)
        result=await r.task;self.assertTrue(result['ok']);self.assertTrue(result['lossy'])
        self.assertIsNotNone(db.execute("SELECT 1 FROM memory_pending WHERE scope='group:55555'").fetchone())
        self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone(),('committed',))
        self.assertIsNotNone(db.execute("SELECT 1 FROM memory_checkpoint_issues WHERE kind='compression_degradation'").fetchone())

    async def test_new_raw_content_invalidates_loss_authorization_snapshot(self):
        r,db=self.prepare()
        async def changed(**kwargs):
            db.execute("UPDATE memory_pending SET question='newly recovered actual content' WHERE scope='group:55555'");db.commit()
            return NS(completion_text='{"summary":"actual summary"}')
        r.binding=(self.cfg,summary_provider(changed),lambda:True,self.metrics,contextvars.ContextVar('discard_cas',default=None))
        r.force_compress_once(allow_partial=True,administrator='admin',request_id='changed',discard_unavailable=True)
        result=await r.task;self.assertFalse(result['ok']);self.assertEqual(result['reason'],'semantic_discard_material_changed')
        self.assertIsNone(db.execute('SELECT 1 FROM memory_semantic_batches').fetchone())


if __name__=='__main__':unittest.main()
