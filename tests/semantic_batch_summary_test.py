"""The user's whole-batch lossy semantic compression contract."""
import asyncio,contextvars,copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from summary_material_test import message,SCOPE
import summary_material
import semantic_batch

class SemanticSummary(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.r=self.store.reflection;self.db=self.store.db;self.calls=[];self.mode='ok';self.live=True;self.metrics={}
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def record(self,count=35,image=False):
  for i in range(count):
   event=str(i);m=summary_material.snapshots([message(event,image=image),{'role':'assistant','content':'完整真实回复'}],SCOPE)
   self.r.record(SCOPE,'12345678',event,'看这个','完整真实回复',now=100+i,materials=m)
 async def chat(self,**kw):
  self.calls.append(copy.deepcopy(kw))
  if self.mode=='failed':raise RuntimeError('provider unavailable')
  if self.mode=='truncated':return NS(completion_text='{"summary":"截断"}',raw_completion={'choices':[{'finish_reason':'length'}]})
  if self.mode=='stale':self.live=False
  if self.mode=='oversized':return NS(completion_text=json.dumps({'summary':'记'*801}))
  if self.mode=='claims_overflow':return NS(completion_text=json.dumps({'summary':'选择的事实','evidence':[{'event':str(i%35),'who':'user','quote':'看这个'} for i in range(97)]}))
  return NS(completion_text=json.dumps({'summary':'记住人物关系和重要经历，普通寒暄自主遗忘。'}))
 async def run_summary(self):
  self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':False},NS(text_chat=self.chat),lambda:self.live,self.metrics,contextvars.ContextVar('semantic_test',default=None))
  if not self.r.retry():return False
  await self.r.task;return True
 def checkpoint(self):return self.db.execute('SELECT id,round_count,state,pending_events FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1').fetchone()
 async def test_34_does_not_trigger_35_submits_whole_batch_without_coverage(self):
  self.record(34);self.assertFalse(await self.run_summary());self.r.binding=None
  m=summary_material.snapshots([message('34'),{'role':'assistant','content':'完整真实回复'}],SCOPE)
  self.r.record(SCOPE,'12345678','34','看这个','完整真实回复',now=134,materials=m)
  self.assertTrue(await self.run_summary());self.assertEqual(len(self.calls),1)
  cp=self.checkpoint();self.assertEqual(cp[1:],(35,'available',None));self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)
  members=json.loads(self.db.execute('SELECT members FROM memory_semantic_batches').fetchone()[0]);self.assertEqual(len(members),35);self.assertTrue(all(len(x)==64 for x in members))
  permit=self.r.reserve_context_reconstruction(SCOPE,'real-rebuild');self.assertTrue(permit['semanticBatch'])
  self.assertNotIn('retire_media_events',self.calls[0]['system_prompt'])
 async def test_all_pending_above_600_and_all_images_in_one_model_request(self):
  self.record(35,image=True)
  for i in range(601):
   event='passive-'+str(i);m=summary_material.snapshots([message(event,image=i==600)],SCOPE)[0]
   self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,'12345678',event,'看这个','',120+i/10000))
   self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,event,'12345678',json.dumps(m)))
  self.db.commit();await self.run_summary();self.assertIsNotNone(self.checkpoint());self.assertEqual(len(self.calls),1)
  prompt=json.loads(self.calls[0]['prompt']);self.assertEqual(len(prompt['turns']),636);self.assertEqual(len(prompt['observed_requests']),636)
  self.assertEqual(self.calls[0]['image_urls'],['data:image/png;base64,YQ=='])
  self.assertEqual(len(prompt['images'][0]['events']),36)
 async def test_failed_truncated_and_stale_do_not_commit_or_delete(self):
  for mode in ('failed','truncated','stale','oversized','claims_overflow'):
   with self.subTest(mode=mode):
    self.r.binding=None
    if not self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0]:self.record(35,image=True)
    self.mode=mode;self.live=True;self.db.execute('DELETE FROM memory_reflection_retry');self.db.commit();await self.run_summary()
    self.assertIsNone(self.checkpoint());self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],35);self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
 async def test_missing_or_incomplete_material_reports_failure_before_model(self):
  self.record();self.db.execute("DELETE FROM memory_round_material WHERE event='4'");self.db.commit();await self.run_summary()
  self.assertIsNone(self.checkpoint());self.assertEqual(self.calls,[]);self.assertEqual(self.metrics['summaryFailures'],1)
 async def test_concurrent_material_change_rejects_commit(self):
  self.record();original=self.chat
  async def changed(**kw):
   reply=await original(**kw);row=self.db.execute("SELECT material FROM memory_round_material WHERE event='0'").fetchone();m=json.loads(row[0]);m['reply']='迟到的新回复';self.db.execute("UPDATE memory_round_material SET material=? WHERE event='0'",(json.dumps(m),));self.db.commit();return reply
  self.chat=changed;await self.run_summary();self.assertIsNone(self.checkpoint());self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
 async def test_core_note_exposes_no_sources_and_update_does_not_accumulate(self):
  self.db.execute("INSERT INTO memory_core_notes VALUES(1,'提炼事实','[\"private:12345678\"]','[\"12345678\"]','[\"old-original-id\"]',1,1,NULL)");self.db.commit()
  prior=self.r.core_note();self.assertNotIn('sources',prior)
  changed=self.r.update_core_note({'core_note':{'text':'新的提炼事实','events':['new'],'summaries':[]}},prior,[{'event':'new','sender_id':'12345678'}],[],SCOPE)
  self.assertEqual(changed,1);self.assertEqual(self.db.execute('SELECT sources FROM memory_core_notes').fetchone()[0],'[]')

if __name__=='__main__':unittest.main()
