"""Offline behavioral regressions; REVIEW_SOURCE can select the frozen preimage."""
import json,os,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(os.environ.get('REVIEW_SOURCE',ROOT))/'adapters/astrbot'))
from behavior import BehaviorStore
from companion_insights import apply,snapshot
from memory_scope import core_note_visible,summary_item
from system_contract import SystemContract,SystemContractViolation
import summary_material as material

SCOPE='private:12345678';PERSON='12345678'

class ReviewMemory(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite');self.db=self.store.db
 def tearDown(self):self.store.close();self.temp.cleanup()
 def correct(self,quote,new,before=None):
  before=before or snapshot(self.db,SCOPE,{PERSON})
  data={'corrections':[{'event':'correction','name':'称呼','oldValue':'甲','newValue':new,'quote':quote}]}
  return apply(self.db,self.store.memory,SCOPE,{'memory':True,'memoryAutoCapture':True},[{'event':'correction','sender_id':PERSON,'user':quote,'bot':'好'}],data,before,now=100)
 def fact(self):return self.db.execute('SELECT value FROM facts WHERE active=1 ORDER BY version DESC').fetchone()[0]
 def test_correction_cannot_invent_replacement_or_block_same_value(self):
  self.store.observe(SCOPE,PERSON,'记住：称呼=甲','old',now=1)
  self.correct('我不叫甲，叫我乙','伪造的新值');self.assertEqual(self.fact(),'甲')
  self.correct('我叫甲','甲');self.assertEqual(self.fact(),'甲')
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_blocks').fetchone()[0],0)
 def test_late_correction_rejects_same_value_new_version(self):
  self.store.observe(SCOPE,PERSON,'记住：称呼=甲','old',now=1);before=snapshot(self.db,SCOPE,{PERSON})
  self.store.observe(SCOPE,PERSON,'记住：称呼=丙','new1',now=2)
  self.store.observe(SCOPE,PERSON,'记住：称呼=甲','new2',now=3)
  self.correct('我不叫甲，叫我乙','乙',before);self.assertEqual(self.fact(),'甲')
 def test_real_correction_of_same_person_group_fact_is_kept(self):
  self.store.observe('group:55555',PERSON,'记住：称呼=甲','old',now=1)
  self.correct('我不叫甲，叫我乙','乙');self.assertEqual(self.fact(),'乙')
 def test_quote_and_hypothetical_cannot_authorize_correction(self):
  self.store.observe(SCOPE,PERSON,'记住：称呼=甲','old',now=1)
  for quote in ('如果我不叫甲，叫我乙会怎样','他说“我不叫甲，叫我乙”'):
   self.correct(quote,'乙');self.assertEqual(self.fact(),'甲')
 def test_first_person_prose_is_not_a_correction_relation(self):
  self.store.observe(SCOPE,PERSON,'记住：称呼=甲','old',now=1)
  self.correct('我今天和甲乙一起出去玩','乙');self.assertEqual(self.fact(),'甲')
  self.correct('我今天和甲一起出去玩',None);self.assertEqual(self.fact(),'甲')
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_blocks').fetchone()[0],0)
 def add_summary(self,scope,text='摘要',people=()):
  identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,'["old"]',time.time())).lastrowid
  self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
  for person in people:self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(identifier,person))
  self.db.commit();return identifier
 def test_rolling_excludes_unrelated_group_but_preserves_participation(self):
  other=self.add_summary('group:55555','他群资料')
  related=self.add_summary('group:66666','本人参与的群资料',[PERSON])
  self.assertEqual(self.store.reflection.rolling('group:77777'),[])
  self.assertEqual({x['id'] for x in self.store.reflection.rolling(SCOPE)},{related})
  self.assertEqual({x['id'] for x in self.store.reflection.rolling('group:55555')},{other})
 def test_unknown_and_self_core_note_not_public(self):
  for scopes in ([],['*'],['self']):
   self.assertFalse(core_note_visible(scopes,'group:55555'))
   self.assertFalse(core_note_visible(scopes,SCOPE))
   self.assertTrue(core_note_visible(scopes,'self'))
   self.assertTrue(core_note_visible(scopes,SCOPE,PERSON))
 def test_protected_claim_reaches_model_and_fences_inflight_update(self):
  identifier=self.add_summary(SCOPE,'概略摘要')
  claim={'id':'1234567890abcdef','text':'答应明天提醒，不是已经提醒', 'kind':'agreement','speaker':{'role':'human','id':PERSON},'sourceRefs':[{'scope':SCOPE,'event':'old','role':'user','digest':''}],'visibility':SCOPE,'protected':True}
  dependency=self.store.reflection.context_dependency([identifier],SCOPE)
  self.db.execute('UPDATE memory_summaries SET schema_version=2,claims=? WHERE id=?',(json.dumps([claim]),identifier));self.db.commit()
  visible=self.store.reflection.rolling(SCOPE)[0]
  self.assertEqual(visible.get('protected_claims'),[claim])
  self.assertNotEqual(dependency,self.store.reflection.context_dependency([identifier],SCOPE))
 def test_long_protected_claim_not_falsely_marked_retained(self):
  text='约定：'+('必要条件不能丢；'*80)+'仅限明天本人确认后执行'
  claim={'id':material.claim_identity(SCOPE,'old','agreement',text),'text':text,'kind':'agreement','speaker':{'role':'human','id':PERSON},'about':'','sourceRefs':[material.claim_ref(SCOPE,'old','user','a'*64)],'occurredAt':1,'status':'open','visibility':SCOPE,'protected':True}
  self.assertFalse(material.valid_claim(claim),'oversized protected evidence cannot pass the shape gate')
  claims=[claim]
  kept,dropped=material.fit_claims(claims)
  self.assertIn(claims[0]['id'],dropped)
  self.assertEqual(material.uncarried_events(claims,{c['id'] for c in kept}),{'old'})
 def test_claim_json_budget_includes_commas(self):
  claim={'id':'1234567890abcdef','text':'约定','kind':'agreement','speaker':{'role':'human'},'sourceRefs':[{'scope':SCOPE,'event':'old','role':'user'}],'visibility':SCOPE}
  claims=[{**claim,'id':str(i)*16} for i in range(3)]
  budget=2+sum(len(json.dumps(c,ensure_ascii=False,separators=(',',':')).encode()) for c in claims)
  kept,_=material.fit_claims(claims,budget=budget)
  self.assertLessEqual(len(json.dumps(kept,ensure_ascii=False,separators=(',',':')).encode()),budget)
 def test_failed_explicit_restore_does_not_release_forget(self):
  self.store.observe(SCOPE,PERSON,'记住：称呼=甲','old',now=1)
  self.store.observe(SCOPE,PERSON,'忘记：称呼','forget',now=2)
  self.db.executemany('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)',[(SCOPE,PERSON,'field'+str(i),'value','event',3,1) for i in range(200)])
  status=self.store.memory.remember(SCOPE,PERSON,'称呼','乙','restore',4)
  self.assertIn('额度已满',status)
  self.assertEqual(self.db.execute('SELECT block_all FROM memory_blocks WHERE name=?',('称呼',)).fetchone(),(1,))
 def test_forbidden_committed_memory_blocks_without_rewriting_prefix(self):
  from memory_scope import retained_context_visible
  from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
  from context_protocol import READONLY_CURRENT
  identifier=self.add_summary('group:55555','不属于此人的群资料')
  base=[{'role':'system','content':'fixed'},{'role':'user','content':'real question'}]
  packet=lambda state:{'role':'user','content':READONLY_CURRENT+'\n'+json.dumps(state)}
  old_state={'state':{'conversation_summaries':[{'id':identifier,'source_scope':'group:55555','summary':'不属于此人的群资料','recall_reason':'rolling_background'}]}}
  journal=ReadonlyJournal();first=journal.prepare(base+[packet(old_state)],1);self.assertTrue(journal.commit(first))
  before=json.dumps(journal.last.wire)
  with self.assertRaises(ContextReconstructionDenied):
   journal.prepare(base+[packet({'state':{'conversation_summaries':[]}})],1,retained_validator=lambda wire:retained_context_visible(self.db,wire,SCOPE))
  self.assertEqual(json.dumps(journal.last.wire),before)
 def test_cross_scope_summary_recall_follows_audience_contract(self):
  from memory_scope import retained_context_visible
  from context_protocol import READONLY_CURRENT
  # R3: one group's audience is that group. Neither a private chat's summary
  # nor another group's summary may appear inside group:66666's request —
  # whatever recall reason labelled it. Authorized cross-scope continuity
  # rides the bounded fact/agreement projections instead (R5).
  for origin in ('private:'+PERSON,'group:55555'):
   data={'state':{'conversation_summaries':[{'source_scope':origin,'summary':'本人约定','recall_reason':'scoped_bm25'}]}}
   wire=[{'role':'user','content':READONLY_CURRENT+'\n'+json.dumps(data)}]
   self.assertFalse(retained_context_visible(self.db,wire,'group:66666'))
  # The participant's own private chat remains a single-person audience for
  # a group they really took part in.
  data={'state':{'conversation_summaries':[{'source_scope':'group:55555','summary':'本人约定','recall_reason':'scoped_bm25'}]}}
  wire=[{'role':'user','content':READONLY_CURRENT+'\n'+json.dumps(data)}]
  self.assertTrue(retained_context_visible(self.db,wire,'private:'+PERSON))

class ReviewSystem(unittest.TestCase):
 def test_conflicting_system_transitions_cannot_both_be_sent(self):
  db=sqlite3.connect(':memory:');self.addCleanup(db.close);contract=SystemContract(db)
  wire=lambda text:[{'role':'system','content':text}]
  contract.reserve(wire('baseline'),None,'initial',1)
  first=contract.reserve(wire('new-a'),'web-a','request-a',2)
  with self.assertRaises(SystemContractViolation):contract.reserve(wire('new-b'),'web-b','request-b',3)
  self.assertTrue(contract.settle(first,False,4))
  second=contract.reserve(wire('new-b'),'web-b','request-b',5)
  self.assertTrue(contract.settle(second,True,6))

class ReviewFavorite(unittest.IsolatedAsyncioTestCase):
 def test_legacy_index_does_not_bypass_semantic_authority(self):
  from qq_favorites import descriptions,catalog
  with tempfile.TemporaryDirectory() as root:
   folder=Path(root)/'qq-favorites';(folder/'images').mkdir(parents=True)
   identifier='qqf_'+'a'*16;file=identifier+'.gif';(folder/'images'/file).write_bytes(b'fixture')
   item={'id':identifier,'file':file,'meaning':'QQ原描述','enabled':True}
   def save():(folder/'index.json').write_text(json.dumps({'account':'90000','enabled':True,'items':[item]}),encoding='utf-8')
   save();self.assertFalse(descriptions(root,'90000')[identifier]['enabled']);self.assertEqual(catalog(root,'90000'),[])
   item['manual']=True;save();self.assertTrue(descriptions(root,'90000')[identifier]['enabled']);self.assertEqual(len(catalog(root,'90000')),1)
   item.pop('manual');item['semantic']={'confidence':'clear'};save();self.assertEqual(len(catalog(root,'90000')),1)
 async def test_prepared_attachment_consumed_still_blocks_repeat(self):
  from qq_favorite_tools import tools
  with tempfile.TemporaryDirectory() as root:
   store=BehaviorStore(Path(root)/'behavior.sqlite')
   try:
    extras={'_hosted_meme_sent':True};calls=[]
    async def login():calls.append('login');return {'user_id':'90000'}
    event=NS(message_obj=NS(),bot=NS(get_login_info=login),get_extra=lambda k,d=None:extras.get(k,d),get_self_id=lambda:'90000',get_group_id=lambda:'',get_sender_id=lambda:PERSON)
    tool=tools(event,{'behavior':{'memeEnabled':True}},store.db,root,lambda:True)[0]
    result=json.loads(await tool.call(NS(context=NS(event=event)),operation='prepare',favorite_handle='qf_'+'1'*20))
    self.assertEqual(result['status'],'already_sent');self.assertEqual(calls,[])
   finally:store.close()

if __name__=='__main__':unittest.main()
