"""Bounded complete old-cognition review and existing retry-slot recovery."""
import asyncio,contextvars,copy,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
import semantic_batch_summary_test as fixture
from memory_review import pages,coverage,combine,validate_page
from companion_insights import semantic_snapshot
from summary_stream_fixture import summary_provider
from memory_scope import fact_rows
from person_memory import expand
from behavior import BehaviorStore


class Review(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  fixture.SemanticSummary.setUp(self)
  self.cfg={'memory':True,'memoryAutoCapture':True,'memorySummary':True,'selfDevelopment':True,'naturalConversation':False}
  for i in range(39):self.store.memory.remember(fixture.SCOPE,'12345678','事实'+str(i),'既有内容'+str(i),'old'+str(i),1)
  self.db.commit();self.failure=False;self.offered=[]
 def tearDown(self):fixture.SemanticSummary.tearDown(self)
 def test_pages_are_complete_bounded_deterministic_and_keep_cas_groups(self):
  state=semantic_snapshot(self.db,fixture.SCOPE,['12345678'],self.cfg)
  split=pages(state,1800)
  self.assertGreater(len(split),1);self.assertEqual(split,pages(state,1800))
  self.assertEqual(sum(coverage(p)['facts'] for p in split),39)
  self.assertEqual({r['name'] for p in split for r in p['facts']},{r['name'] for r in state['facts']})
  self.assertTrue(all(len(json.dumps(p,ensure_ascii=False,separators=(',',':')))<=1800 for p in split))
  with self.assertRaisesRegex(ValueError,'single_candidate'):pages(state,10)
 def test_cross_page_commands_and_conflicting_operations_cannot_guess(self):
  state=semantic_snapshot(self.db,fixture.SCOPE,['12345678'],self.cfg);split=pages(state,1800)
  field=split[-1]['fact_versions'][0]
  op={'op':'delete','scope':fixture.SCOPE,**field,'expected_version':field['version']}
  with self.assertRaisesRegex(ValueError,'not_offered'):validate_page({'fact_operations':[op]},split[0])
  one={'fact_operations':[op]};self.assertEqual(len(combine([one,one])['fact_operations']),1)
  with self.assertRaisesRegex(ValueError,'conflicting'):combine([one,{'fact_operations':[{**op,'op':'replace','value':'different'}]}])
 async def chat(self,**kw):
  prompt=expand(json.loads(kw['prompt']));state=prompt['existing_memory'];self.calls.append(prompt)
  if 'facts' in state:
   # A stopped process can resume paid pages; a provider failure now runs the
   # authorized bounded lossy checkpoint rather than holding history forever.
   if self.failure and len(self.offered)==1:raise asyncio.CancelledError('process interrupted on second page')
   self.offered.append([r['name'] for r in state['facts']])
   output={'fact_operations':[{'op':'replace','scope':fixture.SCOPE,'subject':'12345678','name':v['name'],'expected_version':v['version'],'value':'已经修正'} for v in state['fact_versions']]}
  else:output={'summary':'旧认知已依本批材料核对修订，未确认内容没有推断。'}
  return NS(completion_text=json.dumps(output,ensure_ascii=False))
 async def run_review(self):
  self.r.binding=(self.cfg,summary_provider(self.chat),lambda:True,self.metrics,contextvars.ContextVar('review',default=None))
  with patch('memory_review.pages',side_effect=lambda state:pages(state,1800)):
   self.assertTrue(self.r.retry());await self.r.task
 async def test_all_pages_commit_once_and_interrupted_page_recovers_without_recalling_finished_page(self):
  fixture.SemanticSummary.record(self)
  self.failure=True
  with self.assertRaises(asyncio.CancelledError):await self.run_review()
  self.assertEqual(len(self.offered),1,(self.metrics,self.db.execute('SELECT kind,detail FROM memory_checkpoint_issues').fetchall()))
  self.assertTrue(all(r[1].startswith('既有内容') for r in fact_rows(self.db,fixture.SCOPE,'12345678')))
  saved=json.loads(self.db.execute('SELECT result FROM memory_reflection_retry').fetchone()[0]);self.assertIn('0',saved['parts'])
  first=self.offered[0];self.failure=False
  self.store.close();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
  self.r=self.store.reflection;self.r.shared_context=False;self.db=self.store.db
  self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit();await self.run_review()
  self.assertEqual(sum(names==first for names in self.offered),1)
  self.assertEqual(sum(map(len,self.offered)),39)
  self.assertTrue(all(r[1]=='已经修正' for r in fact_rows(self.db,fixture.SCOPE,'12345678')))
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
  self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone()[0],30)
  counts=json.loads(self.db.execute("SELECT counts FROM memory_operation_audits WHERE state='committed'").fetchone()[0])
  self.assertEqual(counts['facts']['input_coverage']['offered'],39)
  self.assertEqual(counts['facts']['input_coverage']['omitted'],0)
  self.assertEqual(self.metrics['summaryResponsesRecovered'],1)


if __name__=='__main__':unittest.main()
