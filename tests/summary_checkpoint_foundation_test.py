"""Durable third-small staging, same-task rollup and fixed summary readers."""
import asyncio,contextvars,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from summary_material_test import message,SCOPE
import summary_material as material

from summary_stream_fixture import summary_provider

class CheckpointFoundation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db
        self.var=contextvars.ContextVar('checkpoint_test',default=None);self.callbacks=[]
        self.r.checkpoint_callback=self.callbacks.append
        self.mode='ok';self.merge_entered=asyncio.Event();self.merge_release=asyncio.Event();self.tasks=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def chat(self,**kw):
        self.tasks.append(asyncio.current_task())
        if '最多1500' in kw['system_prompt']:
            self.merge_entered.set()
            if self.mode=='blocked':await self.merge_release.wait()
            if self.mode=='fail':raise RuntimeError('merge unavailable')
            return NS(completion_text=json.dumps({'summary':'持续记忆'}))
        return NS(completion_text=json.dumps({'summary':'本批真实经历'}))
    async def batch(self,start):
        for i in range(start,start+35):
            self.r.record(SCOPE,'12345678',str(i),'看这个','已回复',materials=material.snapshots([message(str(i))],SCOPE))
        self.r.maybe_start(SCOPE,{'memory':True,'memorySummary':True},summary_provider(self.chat),lambda:True,{},self.var)
        await self.r.task
        await asyncio.sleep(0)
    async def first_two(self):
        await self.batch(0);await self.batch(35)
        self.assertEqual(len(self.r.foundation(SCOPE)),2)
    async def test_third_rolls_up_in_same_task_and_notifies_after_publish(self):
        await self.first_two();await self.batch(70)
        self.assertIs(self.tasks[-1],self.tasks[-2])
        self.assertEqual([r['level'] for r in self.r.foundation(SCOPE)],['big'])
        self.assertEqual(self.callbacks,[SCOPE]*3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_reconstruction_checkpoints WHERE state='available'").fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
    async def test_waiting_merge_never_publishes_third_baseline(self):
        await self.first_two();before=self.r.foundation(SCOPE);self.mode='blocked'
        task=asyncio.create_task(self.batch(70));await self.merge_entered.wait()
        self.assertEqual(self.r.foundation(SCOPE),before)
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1").fetchone()[0],'pending_merge')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],35)
        self.assertEqual(self.callbacks,[SCOPE]*2)
        self.merge_release.set();await task
        self.assertEqual(self.callbacks,[SCOPE]*3)
    async def test_next_35_still_summarizes_while_previous_merge_is_backing_off(self):
        await self.first_two();self.mode='fail';await self.batch(70)
        await self.batch(105)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],4)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_summary_levels WHERE kind='pending_small'").fetchone()[0],2)
        self.assertEqual(len(self.r.foundation(SCOPE)),2)
        self.mode='ok';self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
        self.assertTrue(self.r.retry());await self.r.task;await asyncio.sleep(0)
        self.assertEqual(sorted(r['level'] for r in self.r.foundation(SCOPE)),['big','small'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_reconstruction_checkpoints WHERE state='available'").fetchone()[0],4)
    async def test_failed_merge_survives_restart_and_releases_once(self):
        await self.first_two();self.mode='fail';await self.batch(70)
        before=self.r.foundation(SCOPE)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],35)
        self.store.close();self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db
        self.assertEqual(self.r.foundation(SCOPE),before)
        self.r.checkpoint_callback=self.callbacks.append;self.mode='ok'
        self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
        self.assertTrue(self.r.maybe_start(SCOPE,{'memory':True,'memorySummary':True},summary_provider(self.chat),lambda:True,{},self.var))
        await self.r.task;await asyncio.sleep(0)
        self.assertEqual([r['level'] for r in self.r.foundation(SCOPE)],['big'])
        self.assertEqual(self.callbacks,[SCOPE]*3)
        self.assertFalse(self.r.retry())
        self.assertEqual(self.callbacks,[SCOPE]*3)
    async def test_protected_claim_conflict_keeps_sources_and_blocks_request(self):
        await self.first_two();before=self.r.foundation(SCOPE)
        self.r.merge_sources=lambda identifiers:([],identifiers)
        await self.batch(70)
        self.assertFalse(self.merge_entered.is_set())
        self.assertEqual(self.r.foundation(SCOPE),before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],35)
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1").fetchone()[0],'pending_merge')
        self.assertEqual(self.callbacks,[SCOPE]*2)

    async def test_merge_stale_epoch_cannot_publish_or_notify(self):
        await self.first_two();before=self.r.foundation(SCOPE);self.mode='blocked'
        task=asyncio.create_task(self.batch(70));await self.merge_entered.wait()
        self.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1');self.db.commit()
        self.merge_release.set();await task
        self.assertEqual(self.r.foundation(SCOPE),before)
        self.assertEqual(self.callbacks,[SCOPE]*2)
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1").fetchone()[0],'pending_merge')

    async def test_legacy_bigs_join_first_shared_checkpoint_without_forging_rounds(self):
        for scope,text in [('self','研究来源'),('private:12345678','人物来源')]:
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,0)',(scope,text,'[]')).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'big')",(identifier,))
            self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,scope))
            self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id) VALUES(?,35,35,0,'pending_coverage',0,?)",(scope,identifier))
        self.db.commit()
        self.assertEqual(self.r.merge_group(),([],None))
        self.r.shared_context=True
        for i in range(35):
            self.r.record('bot_shared','12345678',json.dumps([SCOPE,str(i)],separators=(',',':')),'看这个','已回复',materials=material.snapshots([message(str(i))],'bot_shared'))
        metrics={}
        self.r.maybe_start('bot_shared',{'memory':True,'memorySummary':True},summary_provider(self.chat),lambda:True,metrics,self.var)
        await self.r.task
        self.assertTrue(self.merge_entered.is_set(),(metrics,self.db.execute('SELECT scope,text FROM memory_summaries').fetchall(),self.db.execute('SELECT * FROM memory_reconstruction_checkpoints').fetchall()))
        self.assertEqual([r['level'] for r in self.r.foundation('bot_shared')],['big'])
        self.assertEqual(self.db.execute("SELECT round_count,state FROM memory_reconstruction_checkpoints WHERE scope='bot_shared'").fetchall(),[(35,'available')])
        self.assertEqual(self.db.execute("SELECT state FROM memory_reconstruction_checkpoints WHERE scope<>'bot_shared'").fetchall(),[('invalidated',),('invalidated',)])
        self.assertEqual(self.callbacks,['bot_shared'])

if __name__=='__main__':unittest.main()
