"""Admin exceptions keep real round counts and all retirement proof fences."""
import asyncio,contextvars,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from summary_material_test import message
import summary_material as material

class AdminCheckpoint(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.r=self.store.reflection;self.db=self.store.db;self.calls=[];self.current=True;self.mode='ok'
        self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':False},NS(text_chat=self.chat),lambda:self.current,{},contextvars.ContextVar('admin_checkpoint',default=None))
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def chat(self,**kw):
        self.calls.append(json.loads(kw['prompt']))
        if self.mode=='fail':raise RuntimeError('provider unavailable')
        if self.mode=='stale':self.current=False
        return NS(completion_text='持续记忆' if '最多1500' in kw['system_prompt'] else json.dumps({'summary':'真实经历'}))
    def record(self,start,count):
        for i in range(start,start+count):
            event=str(i);m=material.snapshots([message(event)],'bot_shared')
            self.r.record('bot_shared','12345678',event,'看这个','已回复',materials=m)
    async def force(self):
        result=self.r.force_compress_once(allow_partial=True,administrator='admin-1',request_id='maintenance-'+str(len(self.calls)))
        self.assertTrue(result['started'],result);return await self.r.task
    async def test_auto_and_unauthorized_calls_cannot_waive_35(self):
        self.record(0,30);self.assertFalse(self.r.retry())
        self.assertFalse(self.r.force_compress_once()['started'])
        with self.assertRaises(PermissionError):self.r.force_compress_once(allow_partial=True)
        self.assertEqual(self.calls,[]);self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
    async def test_admin_partial_mints_actual_count_once_and_consumes_once(self):
        self.record(0,30);result=await self.force();self.assertTrue(result['ok'],result)
        cp=self.db.execute('SELECT id,round_count,state FROM memory_reconstruction_checkpoints').fetchone()
        self.assertEqual(cp[1:],(30,'available'))
        self.assertEqual(self.db.execute('SELECT checkpoint_id,administrator FROM memory_checkpoint_admin_overrides').fetchone(),(cp[0],'admin-1'))
        permit=self.r.reserve_context_reconstruction('bot_shared','provider-request');self.assertIsNotNone(permit)
        self.assertTrue(self.r.settle_context_reconstruction(permit,True))
        self.assertIsNone(self.r.reserve_context_reconstruction('bot_shared','replay'))
        self.assertFalse(self.r.force_compress_once(allow_partial=True,administrator='admin-1',request_id='maintenance-0')['started'])
        self.record(30,34);self.assertFalse(self.r.retry());self.record(64,1);self.assertTrue(self.r.retry());await self.r.task
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints ORDER BY id').fetchall(),[(30,),(35,)])
    async def test_unregistered_partial_checkpoint_stays_unusable(self):
        self.record(0,2);await self.force()
        self.db.execute('DELETE FROM memory_checkpoint_admin_overrides');self.db.commit()
        self.assertIsNone(self.r.reserve_context_reconstruction('bot_shared','forged'))
    async def test_failure_keeps_pending_material_and_watermark(self):
        self.record(0,30);self.mode='fail';result=await self.force();self.assertFalse(result['ok'])
        self.assertEqual(self.db.execute('SELECT summary_watermark FROM memory_reflection_progress').fetchone(),None)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_checkpoint_admin_overrides').fetchone()[0],0)
    async def test_configuration_change_never_publishes(self):
        self.record(0,30);self.mode='stale';result=await self.force();self.assertFalse(result['ok'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
    async def test_one_manual_batch_does_not_drain_more_rounds(self):
        self.record(0,70);await self.force();await asyncio.sleep(0)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchall(),[(35,)])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
    async def test_third_small_rollup_preserves_partial_authorization(self):
        for i in range(3):self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('private:99999','其他私聊','[]',0)")
        self.db.commit()
        for start in (0,10,20):
            self.record(start,10);result=await self.force();self.assertTrue(result['ok'],result)
        self.assertEqual([x['level'] for x in self.r.foundation('bot_shared')],['big'])
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints ORDER BY id').fetchall(),[(10,)]*3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_checkpoint_admin_overrides').fetchone()[0],3)
        self.assertIsNotNone(self.r.reserve_context_reconstruction('bot_shared','after-merge'))
    async def test_zero_rounds_and_other_scopes_cannot_mint(self):
        self.assertFalse(self.r.force_compress_once(allow_partial=True,administrator='admin',request_id='empty')['started'])
        self.record(0,3)
        self.assertFalse(self.r.force_compress_once('private:12345678',allow_partial=True,administrator='admin',request_id='wrong')['started'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
    async def test_epoch_change_invalidates_admin_permission(self):
        self.record(0,3);await self.force()
        self.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1');self.db.commit()
        self.assertIsNone(self.r.reserve_context_reconstruction('bot_shared','wrong-epoch'))
    async def test_full_batch_request_is_also_durable_and_idempotent(self):
        self.record(0,70);await self.force()
        self.assertFalse(self.r.force_compress_once(allow_partial=True,administrator='admin-1',request_id='maintenance-0')['started'])
        self.assertEqual(len(self.calls),1)

if __name__=='__main__':unittest.main()
