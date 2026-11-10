"""Explicit administrator loss authorization; actual Provider wire and rollback."""
import contextvars,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot'),str(ROOT/'tests')]
from context_protocol import event_message,model_json
import summary_material,semantic_batch,semantic_collection


class DiscardTests(unittest.IsolatedAsyncioTestCase):
    from semantic_provider_wire_test import SemanticProviderWire as Fixture
    asyncSetUp=Fixture.asyncSetUp
    asyncTearDown=Fixture.asyncTearDown

    def prepare(self):
        db=self.store.db;r=self.store.reflection;r.shared_context=True;r.checkpoint_callback=None
        for i in range(2):
            msg=event_message({'scope':'private:12345678','sender_id':'12345678','event_id':str(i),'at':100+i},'actual complete text')
            material=summary_material.snapshots([msg,{'role':'assistant','content':'sent'}],'bot_shared')
            r.record('bot_shared','12345678',material[0]['event'],'display','sent',now=100+i,materials=material)
        db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('group:55555','87654321','lost','ACTUAL_REMAINING_FRAGMENT','',50))
        db.commit()
        r.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('discard_test',default=None))
        return r,db

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

    async def test_ordinary_admin_force_does_not_discard(self):
        r,db=self.prepare();r.force_compress_once(allow_partial=True,administrator='admin',request_id='strict')
        result=await r.task;self.assertFalse(result['ok']);self.assertEqual(self.requests,[])
        self.assertIsNone(db.execute('SELECT 1 FROM memory_semantic_batches').fetchone())

    async def test_loss_requires_administrator_and_failed_model_keeps_everything(self):
        r,db=self.prepare()
        with self.assertRaises(PermissionError):r.force_compress_once(discard_unavailable=True)
        async def fail(**kwargs):raise RuntimeError('upstream unavailable')
        r.binding=(self.cfg,NS(text_chat=fail),lambda:True,self.metrics,contextvars.ContextVar('discard_fail',default=None))
        r.force_compress_once(allow_partial=True,administrator='admin',request_id='fail',discard_unavailable=True)
        self.assertFalse((await r.task)['ok'])
        self.assertIsNotNone(db.execute("SELECT 1 FROM memory_pending WHERE scope='group:55555'").fetchone())
        self.assertIsNone(db.execute('SELECT 1 FROM memory_semantic_batches').fetchone())

    async def test_new_raw_content_invalidates_loss_authorization_snapshot(self):
        r,db=self.prepare()
        async def changed(**kwargs):
            db.execute("UPDATE memory_pending SET question='newly recovered actual content' WHERE scope='group:55555'");db.commit()
            return NS(completion_text='{"summary":"actual summary"}')
        r.binding=(self.cfg,NS(text_chat=changed),lambda:True,self.metrics,contextvars.ContextVar('discard_cas',default=None))
        r.force_compress_once(allow_partial=True,administrator='admin',request_id='changed',discard_unavailable=True)
        result=await r.task;self.assertFalse(result['ok']);self.assertEqual(result['reason'],'semantic_discard_material_changed')
        self.assertIsNone(db.execute('SELECT 1 FROM memory_semantic_batches').fetchone())


if __name__=='__main__':unittest.main()
