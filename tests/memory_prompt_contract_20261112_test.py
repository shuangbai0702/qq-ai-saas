"""Real summary assembly and optional-product acceptance, no provider calls."""
import asyncio,contextvars,copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
import semantic_batch_summary_test as fixture
import summary_material as material
from companion_insights import SEMANTIC_MEMORY_SYSTEM,SYSTEM as INSIGHT_SYSTEM
from memory_operation_audit import Audit
from unittest.mock import patch

from summary_stream_fixture import summary_provider

class MemoryPromptContract(unittest.IsolatedAsyncioTestCase):
 setUp=fixture.SemanticSummary.setUp
 tearDown=fixture.SemanticSummary.tearDown
 record=fixture.SemanticSummary.record
 run_summary=fixture.SemanticSummary.run_summary
 checkpoint=fixture.SemanticSummary.checkpoint
 chat=fixture.SemanticSummary.chat
 async def test_optional_claim_capacity_rejects_product_and_commits_summary(self):
  self.record();self.mode='claims_overflow';await self.run_summary()
  self.assertIsNotNone(self.checkpoint())
  receipt=self.db.execute("SELECT state,counts FROM memory_operation_audits").fetchone()
  self.assertEqual(receipt[0],'committed');self.assertEqual(json.loads(receipt[1])['claims']['rejected'],1)
  self.assertEqual(self.db.execute("SELECT reason FROM memory_operation_audit_details WHERE category='claims'").fetchone()[0],'capacity')
  # Hot inputs remain until the independently authorized Journal replacement.
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],35)
 async def test_actual_plain_prompt_has_one_output_contract_and_clock(self):
  self.record();await self.run_summary();request=self.calls[0];data=json.loads(request['prompt'])
  self.assertNotIn('输出一段',request['system_prompt']);self.assertIn('JSON',request['system_prompt'])
  self.assertGreater(data['current_time']['unix_seconds'],0);self.assertTrue(data['current_time']['timezone'])
  self.assertEqual(data['persona_visibility']['shown_chars'],0)
  self.assertEqual(data['previous_commitments_visibility']['operation_scope'],'only_shown_ids')
  self.assertIn('接受填accepted',request['system_prompt'])
 async def test_persona_and_prior_commitments_partial_view_are_explicit(self):
  self.record();claims=[]
  for i in range(18):
   claims.append({'id':'claim-old-'+str(i),'text':'尚未解决的条件与后续承诺'*12,'kind':'agreement','speaker':{'role':'human','id':'12345678'},'about':'合成事项','sourceRefs':[material.claim_ref(fixture.SCOPE,'old-'+str(i),'user')],'occurredAt':1,'status':'open','stage':'accepted','visibility':fixture.SCOPE,'protected':True})
  self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,'[]',1,2,?)",(fixture.SCOPE,'此前约定',json.dumps(claims)))
  self.db.commit()
  original=self.run_summary
  async def chat(**kw):self.calls.append(copy.deepcopy(kw));return NS(completion_text=json.dumps({'summary':'保留当前条件'}))
  self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':True,'_persona':'角色'*3000},summary_provider(chat),lambda:True,self.metrics,contextvars.ContextVar('prompt',default=None))
  self.assertTrue(self.r.retry());await self.r.task
  data=json.loads(self.calls[0]['prompt']);view=data['previous_commitments_visibility']
  self.assertEqual(view['total'],18);self.assertGreater(view['omitted'],0);self.assertEqual(view['shown'],len(data['previous_commitments']['rows']) if isinstance(data['previous_commitments'],dict) else len(data['previous_commitments']))
  self.assertTrue(data['persona_visibility']['partial']);self.assertEqual(len(data['persona']),5000)
  self.assertIn('不输出旧facts/relations',self.calls[0]['system_prompt'])
 async def test_big_partial_evidence_and_json_contract(self):
  for i in range(3):
   claims=[{'id':'claim-big-'+str(i)+'-'+str(j),'text':'重要约定的条件和当前状态'*18,'kind':'agreement','speaker':{'role':'human','id':'12345678'},'about':'合成事项','sourceRefs':[material.claim_ref(fixture.SCOPE,str(i)+'-'+str(j),'user')],'occurredAt':1,'status':'open','visibility':fixture.SCOPE,'protected':True} for j in range(20)]
   self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,'[]',1,2,?)",(fixture.SCOPE,'合成摘要'+str(i),json.dumps(claims)))
  self.db.commit();await self.run_summary();request=self.calls[0];data=json.loads(request['prompt'])
  view=data['protected_evidence_visibility'];self.assertEqual(view['total'],60);self.assertGreater(view['omitted'],0)
  self.assertNotIn('只输出摘要正文',request['system_prompt']);self.assertIn('summary为合并后的摘要',request['system_prompt'])
  self.assertIn('不要假定事实必定另存',request['system_prompt']);self.assertEqual(self.metrics['bigSummariesSaved'],1)
 async def test_invalid_learning_product_is_rejected_without_summary_failure(self):
  self.record()
  async def chat(**kw):
   self.calls.append(copy.deepcopy(kw));return NS(completion_text=json.dumps({'summary':'保存合法摘要','learning':'格式错误'}))
  self.r.binding=({'memory':True,'memorySummary':True,'styleLearning':True,'naturalConversation':False},summary_provider(chat),lambda:True,self.metrics,contextvars.ContextVar('learning',default=None))
  with patch('social.summary_learning_batches',return_value=[{'scope':'group:12345','samples':[]}]):
   self.assertTrue(self.r.retry());await self.r.task
  self.assertIsNotNone(self.checkpoint())
  self.assertEqual(self.db.execute("SELECT reason FROM memory_operation_audit_details WHERE category='learning'").fetchone()[0],'invalid_summary_style_learning')
 def test_fact_storage_capacity_is_audited_product_rejection(self):
  from companion_insights import snapshot,apply_semantic_memory
  from unittest.mock import Mock
  payload=[{'event':'synthetic','sender_id':'12345678','user':'现在喜欢咖啡','bot':'知道了'}]
  cfg={'memory':True,'memoryAutoCapture':True};before=snapshot(self.db,fixture.SCOPE,{'12345678'},cfg)
  data={'fact_operations':[{'op':'create','scope':fixture.SCOPE,'subject':'12345678','name':'口味','expected_version':None,'value':'咖啡'}]}
  class CapacityDB:
   def execute(inner,sql,*args):
    if sql=='SELECT count(*) FROM facts':return NS(fetchone=lambda:(20000,))
    return self.db.execute(sql,*args)
  audit=Audit(fixture.SCOPE,0,'summary',data,{'12345678'})
  self.assertEqual(apply_semantic_memory(CapacityDB(),self.store.memory,fixture.SCOPE,cfg,payload,data,before,audit=audit),0)
  self.assertEqual(audit.details[('facts',0)]['reason'],'storage_capacity')
  self.assertEqual(self.db.execute('SELECT count(*) FROM facts').fetchone()[0],0)
 def test_semantic_contract_uses_versioned_primary_protocol(self):
  self.assertIn('expected_version',SEMANTIC_MEMORY_SYSTEM);self.assertIn('旧facts/relations仅保留输入兼容',SEMANTIC_MEMORY_SYSTEM)
  self.assertNotIn('\"facts\":[',INSIGHT_SYSTEM);self.assertNotIn('\"relations\":[',INSIGHT_SYSTEM)
 def test_rejected_claims_receipt_never_contains_original_text(self):
  audit=Audit(fixture.SCOPE,0,'summary',{})
  self.assertEqual(material.semantic_claims_product([{'text':'不得进入审计的原文'}],audit=audit),[])
  self.assertEqual(audit.details[('claims',0)]['reason'],'invalid_claim')
  self.assertNotIn('不得进入审计的原文',str(audit.details))

if __name__=='__main__':unittest.main()
