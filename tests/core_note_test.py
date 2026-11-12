"""One bounded AI-editable region, existing reflection/context and real SQLite."""
import asyncio,contextvars,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from bot_pipeline import conversation_context
from memory_reflection import invalidate
from data_admin import run

class CoreNoteTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.ref=self.store.reflection;self.db=self.store.db;self.scope='private:12345678';self.var=contextvars.ContextVar('core',default=None)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def patch(self,text,events=None,prior=None):
  payload=[{'event':'e','sender_id':'12345678','user':'叫我小林','bot':'好','at':time.time()}]
  data={'core_note':{'text':text,'events':['e'] if events is None else events}}
  with self.db:return self.ref.update_core_note(data,self.ref.core_note() if prior is None else prior,payload,[],self.scope)
 def rounds(self):
  from memory_reflection import DEFAULT_SUMMARY_ROUNDS
  from context_protocol import event_message
  from summary_material import snapshots
  for i in range(DEFAULT_SUMMARY_ROUNDS):
   # A source-bound optional note requires the actual offered material;
   # metadata-only rows now intentionally use the missing-original path.
   message=event_message({'scope':self.scope,'sender_id':'12345678','event_id':str(i)},'请叫我小林')
   self.ref.record(self.scope,'12345678',str(i),'请叫我小林','好的',time.time(),
       materials=snapshots([message,{'role':'assistant','content':'好的'}],self.scope))
 async def drain(self):
  for _ in range(12):
   task=self.ref.task
   if task:await task
   await asyncio.sleep(0)
   if self.ref.task is task:break
 def test_single_region_length_and_no_unknown_evidence(self):
  self.assertEqual(self.patch('甲'*300),1);self.assertEqual(self.patch('乙'*301),0)
  self.assertEqual(self.ref.core_note()['text'],'甲'*300)
  self.assertEqual(self.patch('不应保存',events=['invented']),0)
  self.assertEqual(self.patch('不应保存',events=[]),0)
  self.assertEqual(self.patch('sk-'+'a'*16),0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_core_notes').fetchone()[0],1)
  with self.assertRaises(sqlite3.IntegrityError):self.db.execute("INSERT INTO memory_core_notes(slot,text,scopes,people,sources,updated,schema_version,claims) VALUES(2,'bad','[]','[]','[]',0,1,NULL)")
 def test_replace_clear_restart_and_compare_before(self):
  self.patch('称呼小林');prior=self.ref.core_note();self.patch('改称林哥')
  self.assertEqual(self.patch('迟到旧结果',prior=prior),0)
  self.assertEqual(self.db.execute('SELECT text FROM memory_core_notes WHERE slot=1').fetchone()[0],'改称林哥')
  self.assertEqual(self.patch(''),1);self.assertEqual(self.ref.core_note()['text'],'')
  self.patch('持久背景');self.db.commit();other=sqlite3.connect(Path(self.tmp.name)/'behavior.sqlite');self.assertEqual(other.execute('SELECT text FROM memory_core_notes').fetchone()[0],'持久背景');other.close()
 def test_context_fixed_cross_scope_and_memory_switch(self):
  self.patch('阿梁喜欢可爱的猫')
  context=conversation_context(self.store,{'memory':True},self.scope,'12345678','无关查询',time.time())
  self.assertEqual(context['persona_core_note']['text'],'阿梁喜欢可爱的猫')
  # Current same-bot internal visibility is distinct from public disclosure.
  # Preserve the original source; another bot's store remains isolated below.
  group_note=self.ref.core_note('group:55555','23456789')
  self.assertEqual(group_note['text'],'阿梁喜欢可爱的猫')
  self.assertEqual(group_note['source_scopes'],[self.scope])
  self.assertEqual(self.ref.core_note('self','0')['text'],'阿梁喜欢可爱的猫')
  self.assertEqual(self.ref.core_note('invalid','23456789')['text'],'')
  self.assertEqual(self.ref.core_note(self.scope,'12345678')['text'],'阿梁喜欢可爱的猫')
  self.assertEqual(conversation_context(self.store,{'memory':False},self.scope,'12345678','',time.time())['persona_core_note']['text'],'')
  with tempfile.TemporaryDirectory() as d:
   other=BehaviorStore(Path(d)/'behavior.sqlite');self.assertEqual(other.reflection.core_note()['text'],'');other.close()
 def test_forgetting_clears_derived_region(self):
  self.patch('小林的长期称呼')
  with self.db:invalidate(self.db,self.scope,'12345678')
  self.assertEqual(self.ref.core_note()['text'],'')
 async def test_same_summary_request_updates_region_without_other_insights(self):
  self.rounds();calls=[];metrics={};cfg={'memory':True,'memorySummary':True,'summaryRounds':30,'naturalConversation':False,'_persona':'固定角色不变'}
  async def chat(**kw):
   calls.append(kw);self.assertIn('core_note',json.loads(kw['prompt']))
   return NS(completion_text=json.dumps({'summary':'真实小摘要','core_note':{'text':'12345678希望称呼小林','events':['0']}}))
  self.ref.maybe_start(self.scope,cfg,NS(text_chat=chat),lambda:True,metrics,self.var);await self.drain()
  self.assertEqual(len(calls),1);self.assertEqual(metrics['coreNoteUpdates'],1);self.assertEqual(self.ref.core_note()['text'],'12345678希望称呼小林');self.assertEqual(cfg['_persona'],'固定角色不变')
 async def test_big_compression_can_update_same_region(self):
  # Same-scope smalls form a legal merge group; cross-scope smalls are never
  # merged together, so the fixture keeps one audience.
  ids=[]
  for index in range(3):
   ident=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.scope,'长期背景','["event"]',time.time()+index)).lastrowid;ids.append(ident);self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(ident,));self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(ident,self.scope))
  self.db.commit();calls=[]
  async def chat(**kw):calls.append(kw);return NS(completion_text=json.dumps({'summary':'合并后的大摘要','core_note':{'text':'一段核心背景','summaries':ids}}))
  self.ref.maybe_start(self.scope,{'memory':True,'memorySummary':True},NS(text_chat=chat),lambda:True,{},self.var);await self.drain()
  self.assertEqual(len(calls),1);self.assertEqual(self.ref.core_note()['text'],'一段核心背景');self.assertEqual(self.db.execute("SELECT count(*) FROM memory_summary_levels WHERE kind='big'").fetchone()[0],1)
 async def test_failed_or_late_reflection_cannot_update_region(self):
  self.patch('原背景');self.rounds();entered=asyncio.Event();release=asyncio.Event()
  async def chat(**kw):entered.set();await release.wait();return NS(completion_text=json.dumps({'summary':'旧摘要','core_note':{'text':'迟到内容','events':['0']}}))
  self.ref.maybe_start(self.scope,{'memory':True,'memorySummary':True},NS(text_chat=chat),lambda:True,{},self.var);await asyncio.wait_for(entered.wait(),5)
  with self.db:invalidate(self.db,self.scope,'12345678')
  release.set();await self.drain();self.assertEqual(self.ref.core_note()['text'],'');self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
 def test_forget_summary_also_clears_derived_text(self):
  self.patch('来源于聊天的背景');at=time.time();ident=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.scope,'摘要','["e"]',at)).lastrowid;self.db.commit()
  result=run(Path(self.tmp.name)/'behavior.sqlite',{'action':'forget_summary','scope':self.scope,'id':ident,'expectedUpdated':at},connection=self.db)
  self.assertTrue(result['ok']);self.assertEqual(self.ref.core_note()['text'],'')

if __name__=='__main__':unittest.main()
