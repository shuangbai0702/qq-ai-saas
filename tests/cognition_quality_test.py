"""Semantic choices use existing writers; programs enforce identity and CAS."""
import contextvars,hashlib,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
import agreement_state as agreements,companion_state as companion,social
from companion_insights import snapshot,apply_semantic_memory
from memory_operation_audit import Audit
from autonomous_runtime import observation_metadata

SCOPE='private:12345678'
CFG={'memory':True,'memoryAutoCapture':True,'selfDevelopment':True,'ownerUin':'12345678'}


class Quality(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db;self.now=time.time()
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def seed(self,count=1):
  for i in range(count):
   self.db.execute('INSERT INTO memory_agreements(id,topic,detail,speaker,participants,source_scope,source_event,audiences,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
    (str(i),'群聊边界'+str(i),'群聊须私聊授权','12345678',json.dumps(['12345678','bot']),SCOPE,'old'+str(i),json.dumps([SCOPE]),'accepted' if i<5 else 'proposed',1,1))
  self.db.commit()
 def retire(self,row):return {'id':row['id'],'expected_revision':row['revision'],'op':'retire','reason':'已合并进同义有效约定'}
 def apply(self,data,before=None):
  before=before or snapshot(self.db,SCOPE,{'12345678'},CFG);audit=Audit(SCOPE,0,'small',data,{'12345678'})
  payload=[{'event':'new','sender_id':'12345678','user':'保留需要的群聊边界','bot':'好'}]
  with self.db:apply_semantic_memory(self.db,self.store.memory,SCOPE,CFG,payload,data,before,self.now,audit)
  return audit
 def test_legacy_slots_converge_without_fabricating_execution_or_changing_audience(self):
  self.seed(32);before=snapshot(self.db,SCOPE,{'12345678'},CFG);original=self.db.execute('SELECT id,participants,audiences,source_scope,source_event,due FROM memory_agreements ORDER BY id').fetchall()
  rows=before['memory_state']['agreements'];data={'agreement_operations':[self.retire(r) for r in rows if int(r['id'])>=5]}
  audit=self.apply(data,before)
  self.assertEqual(self.db.execute("SELECT count(*) FROM memory_agreements WHERE status IN ('proposed','accepted','uncertain')").fetchone()[0],5)
  self.assertEqual(self.db.execute("SELECT count(*) FROM memory_agreements WHERE status='fulfilled'").fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT id,participants,audiences,source_scope,source_event,due FROM memory_agreements ORDER BY id').fetchall(),original)
  self.assertEqual(audit.counts['agreements']['accepted'],27)
  self.assertEqual(self.db.execute("SELECT status FROM memory_agreements WHERE id='0'").fetchone()[0],'accepted')
 def test_bound_task_stale_and_unoffered_edits_are_rejected(self):
  self.seed(2);before=snapshot(self.db,SCOPE,{'12345678'},CFG);rows=before['memory_state']['agreements']
  self.db.execute("UPDATE memory_agreements SET handoff_id='real-task' WHERE id='0'");self.db.commit()
  audit=self.apply({'agreement_operations':[self.retire(rows[0]),{**self.retire(rows[1]),'expected_revision':99},{**self.retire(rows[1]),'id':'foreign'}]},before)
  self.assertEqual(audit.counts['agreements']['rejected'],3)
  self.assertEqual(self.db.execute("SELECT count(*) FROM memory_agreements WHERE status='accepted'").fetchone()[0],2)
 def test_revision_race_rolls_back_all_semantic_changes(self):
  self.seed(2);before=snapshot(self.db,SCOPE,{'12345678'},CFG)
  self.db.execute("UPDATE memory_agreements SET revision=revision+1 WHERE id='1'");self.db.commit()
  with self.assertRaisesRegex(ValueError,'semantic_agreement_snapshot_changed'):
   self.apply({'agreement_operations':[self.retire(r) for r in before['memory_state']['agreements']]},before)
  self.assertEqual(self.db.execute("SELECT status,revision FROM memory_agreements WHERE id='0'").fetchone(),('accepted',1))
 def test_agreement_replacement_cannot_rename_identity_or_change_real_terms(self):
  self.seed();before=snapshot(self.db,SCOPE,{'12345678'},CFG)
  operation={'id':'0','expected_revision':1,'op':'replace','reason':'合并同义表述','detail':'群内回复仍需主人私聊授权'}
  audit=self.apply({'agreement_operations':[{**operation,'topic':'新事项'}]},before)
  self.assertEqual(audit.counts['agreements']['rejected'],1)
  self.apply({'agreement_operations':[operation]},before)
  self.assertEqual(self.db.execute('SELECT topic,detail,status,revision,participants,audiences FROM memory_agreements').fetchone(),('群聊边界0',operation['detail'],'accepted',2,json.dumps(['12345678','bot']),json.dumps([SCOPE])))
 def test_retired_agreement_closes_its_claim_without_guessing_same_text_elsewhere(self):
  self.seed();self.apply({'agreement_operations':[self.retire(agreements.review_view(self.db)[0])]})
  ref={'scope':SCOPE,'event':'semantic:'+hashlib.sha256(b'old0').hexdigest(),'role':'user'}
  claim={'id':'claim-one','kind':'agreement','about':'群聊边界0','text':'群聊须私聊授权','status':'open','protected':True,'sourceRefs':[ref],'speaker':{'role':'human','id':'12345678'},'visibility':SCOPE}
  other={**claim,'id':'other-scope','sourceRefs':[{**ref,'scope':'private:87654321'}]}
  other_speaker={**claim,'id':'other-speaker','speaker':{'role':'bot','id':''}}
  self.assertEqual(agreements.retired_claims(self.db,[claim,other,other_speaker]),{'claim-one'})
  self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'既有约定','[]',1,json.dumps([claim,other])))
  from summary_material import settle_commitments,protected_claim
  settle_commitments(self.db,SCOPE,[],[claim,other],retired={'claim-one'})
  stored=json.loads(self.db.execute('SELECT claims FROM memory_summaries').fetchone()[0])
  self.assertFalse(protected_claim(stored[0]));self.assertEqual(stored[0]['status'],'expired')
  self.assertEqual(stored[1],other)
 def test_shared_retired_claim_identity_survives_raw_source_purge(self):
  from context_protocol import model_json,scoped_event_hash
  from semantic_batch import semantic_claims
  event=model_json([SCOPE,'old0'])
  self.seed();self.db.execute("UPDATE memory_agreements SET source_scope='bot_shared',source_event=?",(event,))
  self.db.commit();self.apply({'agreement_operations':[self.retire(agreements.review_view(self.db)[0])]})
  claim={'id':'shared-claim','kind':'agreement','about':'群聊边界0','speaker':{'role':'human','id':'12345678'},'visibility':'bot_shared','sourceRefs':[{'scope':'bot_shared','event':event,'role':'user'}]}
  normalized=semantic_claims([claim])
  self.assertEqual(agreements.retired_claims(self.db,[claim]),{'shared-claim'})
  self.assertEqual(agreements.retired_claims(self.db,normalized),{'shared-claim'})
  agreements.rebind_agreement_source_events(self.db,[self.db.execute('SELECT rowid FROM memory_agreements').fetchone()[0]],'semantic:'+scoped_event_hash(SCOPE,'old0'))
  self.assertEqual(agreements.retired_claims(self.db,normalized),{'shared-claim'})
  wrong=semantic_claims([{**claim,'id':'different-source','sourceRefs':[{'scope':'bot_shared','event':model_json(['private:87654321','old0']),'role':'user'}]}])
  self.assertEqual(agreements.retired_claims(self.db,wrong),set())
 def test_interest_weight_is_semantic_and_zero_does_not_occupy_a_slot(self):
  self.db.execute("INSERT INTO companion_interests VALUES(?,?,?,0,?,'proof','autonomous_choice')",('self','光学设计',4,self.now-90*86400))
  self.db.execute("INSERT INTO companion_interests VALUES(?,?,?,0,?,'proof','autonomous_choice')",('self','已放下的话题',0,self.now))
  before=snapshot(self.db,SCOPE,{'12345678'},CFG)
  self.assertEqual([(x['topic'],x['heat']) for x in companion.context(self.db,{},SCOPE,self.now)['interests']],[('光学设计',4)])
  row=next(r for r in before['memory_state']['products']['interest'] if r['target']['topic']=='光学设计')
  self.apply({'memory_operations':[{'kind':'interest','op':'replace','target':row['target'],'expected_version':row['version'],'value':{'weight':0}}]},before)
  self.assertEqual(companion.context(self.db,{},SCOPE,self.now)['interests'],[])
  self.assertIsNotNone(companion.read_product(self.db,'interest',row['target']))
  self.db.execute("UPDATE companion_interests SET weight=7.8,updated=? WHERE topic='光学设计'",(self.now-90*86400,))
  companion.note_interest(self.db,'self','光学设计',self.now,'different-proof','conversation_heat')
  self.assertEqual(companion.read_product(self.db,'interest',row['target'])['weight'],8,'new evidence must not lower a model-reviewed weight to the old cap of four')
 def test_motive_provenance_is_visible_without_raw_locator_and_cas_still_matches(self):
  evidence=companion.motive_record('private-raw-event','model_plan')
  self.db.execute('INSERT INTO companion_motives VALUES(?,?,?,?,?,?,?)',(SCOPE,'12345678','稍后问作业',self.now,self.now+100,'open',evidence))
  before=snapshot(self.db,SCOPE,{'12345678'},CFG);row=before['memory_state']['products']['motive'][0]
  self.assertEqual(row['value']['evidence'],{'author':'model_plan','acceptance':'none'})
  self.assertNotIn('private-raw-event',json.dumps(row))
  self.apply({'memory_operations':[{'kind':'motive','op':'replace','target':row['target'],'expected_version':row['version'],'value':{'status':'abandoned','due':self.now,'expires':self.now+100}}]},before)
  self.assertEqual(companion.context(self.db,{},SCOPE,self.now)['unfinished_topics'],[])
 def test_expression_foundation_uses_value_and_honors_user_blocks(self):
  for i in range(25):self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',('group:12345','普通词'+str(i),'泛用','[]',.1,self.now+i))
  self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',('group:12345','有节目效果','遇到意外展开','[]',4,1))
  self.assertIn('有节目效果',[r['expression'] for r in social.foundation_view(self.db)])
  self.db.execute("INSERT INTO social_pattern_blocks VALUES('group:12345','有节目效果')")
  self.assertNotIn('有节目效果',[r['expression'] for r in social.foundation_view(self.db)])
 def test_model_expression_edit_wins_over_same_batch_automatic_feedback(self):
  from companion_insights import apply
  self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',(SCOPE,'有节目效果','意外展开','[]',3,1))
  self.db.execute('INSERT INTO dialogue_feedback(scope,event,at,state) VALUES(?,?,?,?)',(SCOPE,'original',1,'unknown'))
  before=snapshot(self.db,SCOPE,{'12345678'},CFG)
  row=before['memory_state']['products']['expression'][0]
  data={'memory_operations':[{'kind':'expression','op':'replace','target':row['target'],'expected_version':row['version'],'value':{'weight':0,'situation':'暂不使用'}}],
        'effects':[{'event':'original','reaction_event':'reaction','result':'engaged','quote':'这个说法挺有意思'}]}
  payload=[{'event':'original','sender_id':'12345678','user':'说点什么','bot':'有节目效果'},
           {'event':'reaction','sender_id':'12345678','user':'这个说法挺有意思','bot':'好'}]
  with self.db:
   apply_semantic_memory(self.db,self.store.memory,SCOPE,CFG,payload,data,before,self.now)
   apply(self.db,self.store.memory,SCOPE,CFG,payload,data,before,self.now)
  self.assertEqual(self.db.execute('SELECT weight FROM social_patterns').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT state FROM dialogue_feedback').fetchone()[0],'engaged','real feedback remains recorded')
  self.assertEqual(self.db.execute('SELECT count(*) FROM expression_reinforcement').fetchone()[0],0)
 def test_observer_keeps_version_and_real_outcome_without_prose(self):
  value={'at':123,'eventId':'one','scope':SCOPE,'text':'旧原话','result':{'status':'partial','protocolAttempts':2,'protocolConfirmed':1,'response':'旧回复','toolResults':[{'text':'工具原文'}]}}
  metadata=observation_metadata(value)
  self.assertEqual(metadata,{'at':123,'eventId':'one','scope':SCOPE,'result':{'status':'partial','protocolAttempts':2,'protocolConfirmed':1}})
  self.assertEqual(value['text'],'旧原话','read projection must not mutate an in-flight event')


class QualityCompression(unittest.IsolatedAsyncioTestCase):
 async def test_product_revision_conflict_retries_with_original_compression_deadline(self):
  import semantic_batch_summary_test as fixture
  from summary_stream_fixture import summary_provider
  from unittest.mock import patch
  fixture.SemanticSummary.setUp(self)
  try:
   fixture.SemanticSummary.record(self)
   self.r.binding=({**CFG,'memorySummary':True},summary_provider(lambda_chat),lambda:True,self.metrics,contextvars.ContextVar('quality-cas',default=None))
   with patch('memory_products.apply',side_effect=ValueError('semantic_product_snapshot_changed')):
    self.assertTrue(self.r.retry());await self.r.task
   self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
   self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
   self.assertGreater(self.db.execute('SELECT deadline FROM memory_reflection_retry').fetchone()[0],0)
   failures=self.db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind LIKE 'failure_%'").fetchall()
   self.assertTrue(any(json.loads(row[0]).get('reason')=='semantic_product_snapshot_changed' for row in failures))
  finally:fixture.SemanticSummary.tearDown(self)
 async def test_real_summary_request_applies_semantic_retirement_once_at_30_rounds(self):
  import semantic_batch_summary_test as fixture
  from summary_stream_fixture import summary_provider
  fixture.SemanticSummary.setUp(self)
  try:
   Quality.seed(self,6);fixture.SemanticSummary.record(self,29)
   async def chat(**kw):
    self.calls.append(kw)
    from memory_model_view import expand
    prompt=expand(json.loads(kw['prompt']));rows=prompt['existing_memory']['agreements']
    self.assertEqual(len(rows),6);self.assertIn('agreement_operations',kw['system_prompt'])
    self.assertIn('不按天机械扣分',kw['system_prompt'])
    data={'summary':'原有群聊边界保持，重复提议已合并。','agreement_operations':[{'id':'5','expected_revision':1,'op':'retire','reason':'与既有约定同义重复'}]}
    return NS(completion_text=json.dumps(data))
   self.r.binding=({**CFG,'memorySummary':True},summary_provider(chat),lambda:True,self.metrics,contextvars.ContextVar('quality',default=None))
   self.assertFalse(self.r.retry());self.assertEqual(self.calls,[])
   from summary_material_test import message
   import summary_material
   materials=summary_material.snapshots([message('29'),{'role':'assistant','content':'完整真实回复'}],SCOPE)
   self.r.record(SCOPE,'12345678','29','看这个','完整真实回复',now=134,materials=materials)
   self.assertTrue(self.r.retry());await self.r.task
   self.assertEqual(len(self.calls),1)
   self.assertEqual(self.db.execute("SELECT status FROM memory_agreements WHERE id='5'").fetchone()[0],'expired')
   self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone()[0],30)
   counts=json.loads(self.db.execute("SELECT counts FROM memory_operation_audits WHERE state='committed'").fetchone()[0])
   self.assertEqual(counts['agreements']['accepted'],1)
  finally:fixture.SemanticSummary.tearDown(self)


class QualityProviderWire(unittest.IsolatedAsyncioTestCase):
 async def test_review_decisions_cross_real_sdk_http_and_checkpoint_without_mainline_repetition(self):
  import httpx,semantic_provider_wire_test as fixture
  from unittest.mock import patch
  from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
  from memory_model_view import expand
  f=fixture.SemanticProviderWire('test_summary_http_then_checkpoint_retires_originals_before_next_http')
  await f.asyncSetUp()
  try:
   f.db=f.store.db;Quality.seed(f,6);f.populate();requests=[]
   async def handler(request):
    body=json.loads(request.content);requests.append(body)
    if len(requests)==1:
     texts=[m['content'] if isinstance(m.get('content'),str) else '\n'.join(p.get('text','') for p in m.get('content',[]) if p.get('type')=='text') for m in body['messages']]
     prompt=next(expand(json.loads(text)) for text in texts if text.startswith('{') and 'existing_memory' in text)
     self.assertIn('agreement_operations','\n'.join(texts));self.assertIn('不按天机械扣分','\n'.join(texts))
     row=next(r for r in prompt['existing_memory']['agreements'] if r['id']=='5')
     self.assertEqual(row['participants'],['12345678','bot']);self.assertEqual(row['audiences'],[SCOPE])
     output={'summary':fixture.SUMMARY,'agreement_operations':[{'id':row['id'],'expected_revision':row['revision'],'op':'retire','reason':'同义提议合并，原承诺保留'}]}
    else:output={'summary':'下一轮回复'}
    content=json.dumps(output,ensure_ascii=False)
    if body.get('stream'):
     chunk={'id':'quality-wire','object':'chat.completion.chunk','created':1,'model':'fixture','choices':[{'index':0,'delta':{'role':'assistant','content':content},'finish_reason':'stop'}]}
     return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
    return httpx.Response(200,json={'id':'quality-wire','model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}]})
   await f.provider.client.close()
   with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda provider,config:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
    f.provider=ProviderOpenAIOfficial({'id':'quality-wire','type':'openai_chat_completion','model':'fixture','key':['fixture'],'api_base':'https://fixture.invalid/v1'}, {})
   await f.summarize()
   self.assertEqual(len(requests),1)
   self.assertEqual(f.db.execute("SELECT status,revision FROM memory_agreements WHERE id='5'").fetchone(),('expired',2))
   self.assertEqual(f.db.execute("SELECT count(*) FROM memory_agreements WHERE status='accepted'").fetchone()[0],5)
   self.assertEqual(f.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
   self.assertEqual(f.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],0)
   await f.provider.text_chat(contexts=f.journal.last.wire,prompt=None,func_tool=None)
   wire=json.dumps(requests[1]['messages'],ensure_ascii=False)
   self.assertIn(fixture.SUMMARY,wire);self.assertNotIn('agreement_operations',wire)
   for original in f.originals+f.events+[f.image]:self.assertNotIn(original,wire)
   self.assertIn(f.system,requests[1]['messages']);self.assertIn(f.new,requests[1]['messages'])
  finally:await f.asyncTearDown()


class ContextDelta(unittest.TestCase):
 def test_short_parent_and_shared_record_changes_never_replay_unchanged_fields(self):
  from decision_history import changes,apply_changes
  for before,after in [({'deep':{'unchanged':'yes','x':0}},{'deep':{'unchanged':'yes','x':1}}),
                       ([{'id':'first','body':'keep'},{'id':'second','body':'keep','state':0}], [{'id':'second','body':'keep','state':1},{'id':'third','body':'new'}])]:
   delta=changes(before,after)
   self.assertEqual(apply_changes(before,delta),after)
   self.assertNotIn('keep',json.dumps(delta));self.assertNotIn('unchanged',json.dumps(delta))
 def test_event_delta_retains_exact_prefix_and_omits_unchanged_small_fields(self):
  from context_transport import EVENT_CONTEXT_CURRENT
  from context_protocol import model_json
  from decision_history import apply_changes
  from readonly_journal import ReadonlyJournal
  before={'authority':{'owner':True,'same':'keep'},'policy':'stable','currentGuidance':'same-guidance','meaning':'platform event data'}
  after={**before,'authority':{'owner':False,'same':'keep'}}
  packet=lambda value:{'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json(value)}
  native=[{'role':'system','content':'platform'},{'role':'user','content':'hello'}]
  journal=ReadonlyJournal();first=journal.prepare(native+[packet(before)],'one',now=1,strict=True,compact_policy=True)
  self.assertTrue(journal.commit(first))
  next_native=native+[{'role':'assistant','content':'reply'},{'role':'user','content':'next'}]
  second=journal.prepare(next_native+[packet(after)],'one',now=2,strict=True,compact_policy=True)
  self.assertEqual(second.wire[:len(first.wire)],first.wire)
  update=second.wire[-1]['content'];self.assertNotIn('keep',update);self.assertNotIn('stable',update)
  self.assertEqual(apply_changes(before,json.loads(update.split('\n',1)[1])['eventChanges']),after)
  self.assertTrue(journal.commit(second))
  same=journal.prepare(next_native+[packet(after)],'one',now=3,strict=True,compact_policy=True)
  self.assertEqual(same.wire,second.wire)


async def lambda_chat(**kwargs):return NS(completion_text=json.dumps({'summary':'真实经历已整理。'}))


if __name__=='__main__':unittest.main()
