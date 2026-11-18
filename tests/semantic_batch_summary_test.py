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

from summary_stream_fixture import summary_provider

class SemanticSummary(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.r=self.store.reflection;self.db=self.store.db;self.calls=[];self.mode='ok';self.live=True;self.metrics={}
  self.r.shared_context=False  # This fixture deliberately records private local rounds.
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def record(self,count=30,image=False):
  for i in range(count):
   event=str(i);m=summary_material.snapshots([message(event,image=image),{'role':'assistant','content':'完整真实回复'}],SCOPE)
   self.r.record(SCOPE,'12345678',event,'看这个','完整真实回复',now=100+i,materials=m)
 async def chat(self,**kw):
  self.calls.append(copy.deepcopy(kw))
  if self.mode=='failed':raise RuntimeError('provider unavailable')
  if self.mode=='truncated':return NS(completion_text='{"summary":"截断"}',raw_completion={'choices':[{'finish_reason':'length'}]})
  if self.mode=='stale':self.live=False
  if self.mode=='oversized':return NS(completion_text=json.dumps({'summary':'记'*801}))
  if self.mode=='claims_overflow':return NS(completion_text=json.dumps({'summary':'选择的事实','evidence':[{'event':str(i%30),'who':'user','quote':'看这个'} for i in range(97)]}))
  return NS(completion_text=json.dumps({'summary':'记住人物关系和重要经历，普通寒暄自主遗忘。'}))
 async def run_summary(self):
  self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':False},summary_provider(self.chat),lambda:self.live,self.metrics,contextvars.ContextVar('semantic_test',default=None))
  if not self.r.retry():return False
  await self.r.task;return True
 def checkpoint(self):return self.db.execute('SELECT id,round_count,state,pending_events FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1').fetchone()
 async def test_29_does_not_trigger_30_submits_whole_batch_without_coverage(self):
  self.record(29);self.assertFalse(await self.run_summary());self.r.binding=None
  m=summary_material.snapshots([message('29'),{'role':'assistant','content':'完整真实回复'}],SCOPE)
  self.r.record(SCOPE,'12345678','29','看这个','完整真实回复',now=134,materials=m)
  self.assertTrue(await self.run_summary());self.assertEqual(len(self.calls),1)
  cp=self.checkpoint();self.assertEqual(cp[1:],(30,'available',None));self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)
  members=json.loads(self.db.execute('SELECT members FROM memory_semantic_batches').fetchone()[0]);self.assertEqual(len(members),30);self.assertTrue(all(len(x)==64 for x in members))
  permit=self.r.reserve_context_reconstruction(SCOPE,'real-rebuild');self.assertTrue(permit['semanticBatch'])
  self.assertNotIn('retire_media_events',self.calls[0]['system_prompt'])
 async def test_all_pending_above_600_and_all_images_in_one_model_request(self):
  self.record(30,image=True)
  for i in range(601):
   event='passive-'+str(i);m=summary_material.snapshots([message(event,image=i==600)],SCOPE)[0]
   self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,'12345678',event,'看这个','',120+i/10000))
   self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,event,'12345678',json.dumps(m)))
  self.db.commit();await self.run_summary();self.assertIsNotNone(self.checkpoint());self.assertEqual(len(self.calls),1)
  prompt=json.loads(self.calls[0]['prompt']);self.assertEqual(len(prompt['turns']),631);self.assertEqual(len(prompt['observed_requests']),631)
  self.assertEqual(self.calls[0]['image_urls'],['data:image/png;base64,YQ=='])
  self.assertEqual(len(prompt['images'][0]['events']),31)
 async def test_failed_truncated_and_stale_do_not_commit_or_delete(self):
  for mode in ('failed','truncated','stale','oversized'):
   with self.subTest(mode=mode):
    self.r.binding=None
    if not self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0]:self.record(30,image=True)
    self.mode=mode;self.live=True;self.db.execute('DELETE FROM memory_reflection_retry');self.db.commit();await self.run_summary()
    self.assertIsNone(self.checkpoint());self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],30);self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
 async def test_unrecoverable_material_is_declared_and_does_not_block_legal_summary(self):
  self.record();self.db.execute("DELETE FROM memory_round_material WHERE event='4'");self.db.commit();await self.run_summary()
  self.assertEqual(self.checkpoint()[1:],(30,'available',None));self.assertEqual(len(self.calls),1)
  prompt=json.loads(self.calls[0]['prompt'])
  self.assertEqual(prompt['incomplete_material'][0]['event'],'4');self.assertEqual(prompt['incomplete_material'][0]['missing'],'full_original')
  self.assertIn('missing部分未提供',self.calls[0]['system_prompt'])
  self.assertEqual(self.metrics.get('summaryFailures',0),0)
  # Commit is not Journal replacement: all remaining hot material is protected.
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],29)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
 async def test_concurrent_material_change_rejects_commit(self):
  self.record();original=self.chat
  async def changed(**kw):
   reply=await original(**kw);row=self.db.execute("SELECT material FROM memory_round_material WHERE event='0'").fetchone();m=json.loads(row[0]);m['reply']='迟到的新回复';self.db.execute("UPDATE memory_round_material SET material=? WHERE event='0'",(json.dumps(m),));self.db.commit();return reply
  self.chat=changed;await self.run_summary();self.assertIsNone(self.checkpoint());self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
 async def test_core_note_exposes_no_sources_and_update_does_not_accumulate(self):
  self.db.execute("INSERT INTO memory_core_notes(slot,text,scopes,people,sources,updated,schema_version,claims) VALUES(1,'提炼事实','[\"private:12345678\"]','[\"12345678\"]','[\"old-original-id\"]',1,1,NULL)");self.db.commit()
  prior=self.r.core_note();self.assertNotIn('sources',prior)
  changed=self.r.update_core_note({'core_note':{'text':'新的提炼事实','events':['new'],'summaries':[]}},prior,[{'event':'new','sender_id':'12345678'}],[],SCOPE)
  self.assertEqual(changed,1);self.assertEqual(self.db.execute('SELECT sources FROM memory_core_notes').fetchone()[0],'[]')
 async def test_merge_waits_for_committed_batch_journal_retirement(self):
  self.record();await self.run_summary();self.r.binding=None
  checkpoint=self.checkpoint()[0]
  summary=self.db.execute('SELECT summary_id FROM memory_reconstruction_checkpoints WHERE id=?',(checkpoint,)).fetchone()[0]
  for event,raw in self.db.execute('SELECT event,material FROM memory_round_material').fetchall():
   self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',(SCOPE,event,'12345678',summary_material.coverage_digest(json.loads(raw)),summary))
  # An additional covered input must also survive until real retirement.
  legacy=summary_material.snapshots([message('legacy'),{'role':'assistant','content':'完整真实回复'}],SCOPE)[0]
  self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,'legacy','12345678',json.dumps(legacy)))
  self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,'12345678','legacy','看这个','完整真实回复',90))
  self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',(SCOPE,'legacy','12345678',summary_material.coverage_digest(legacy),summary))
  for i in range(2):
   sid=self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,'[]',1)",(SCOPE,'此前提炼记忆'+str(i))).lastrowid
   self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(sid,))
  self.db.commit()
  originals={table:self.db.execute('SELECT * FROM '+table+' WHERE scope=? AND event<>? ORDER BY event',(SCOPE,'legacy')).fetchall() for table in ('memory_rounds','memory_round_material','memory_pending')}
  self.mode='failed';await self.run_summary()
  self.assertEqual(len(self.calls),1,'unretired batch recovery precedes another merge request')
  self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_round_material WHERE event=?',('legacy',)).fetchone())
  self.mode='ok';self.db.execute("DELETE FROM memory_reflection_retry WHERE scope='self'");self.db.commit();await self.run_summary()
  self.assertEqual(len(self.calls),1)
  self.assertEqual(self.metrics.get('bigSummariesSaved',0),0)
  self.assertEqual(self.db.execute('SELECT state FROM memory_semantic_batches WHERE checkpoint_id=?',(checkpoint,)).fetchone()[0],'committed')
  self.assertEqual(self.checkpoint()[2],'available')
  for table,before in originals.items():
   self.assertEqual(self.db.execute('SELECT * FROM '+table+' WHERE scope=? AND event<>? ORDER BY event',(SCOPE,'legacy')).fetchall(),before,table)
  self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_round_material WHERE event=?',('legacy',)).fetchone())
  self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_pending WHERE event=?',('legacy',)).fetchone())

if __name__=='__main__':unittest.main()
