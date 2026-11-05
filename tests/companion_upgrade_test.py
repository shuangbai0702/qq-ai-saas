import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
from behavior import BehaviorStore
from companion_insights import apply,snapshot
from sticker_collection import manage
import companion_state,initiative

class Upgrades(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db;self.now=time.time();self.var=contextvars.ContextVar('event',default=None)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 async def test_one_background_call_persists_summary_facts_and_own_interest(self):
  cfg={'memory':True,'memoryAutoCapture':True,'memorySummary':True,'summaryRounds':30,'selfDevelopment':True,'naturalConversation':True}
  for i in range(30):self.store.reflection.record('group:1','11111',str(i),'我搬到了苏州','我喜欢昆曲的水磨腔。')
  calls=[]
  async def chat(**kw):
   calls.append(kw);return NS(completion_text=json.dumps({'summary':'11111说自己搬到了苏州，机器人喜欢昆曲。','facts':[{'event':'0','name':'居住城市','value':'苏州','quote':'我搬到了苏州'}],'interests':[{'event':'0','topic':'昆曲','quote':'我喜欢昆曲的水磨腔。'}]}))
  self.store.reflection.maybe_start('group:1',cfg,NS(text_chat=chat),lambda:True,{},self.var);await self.store.reflection.task
  self.assertEqual(len(calls),1);self.assertEqual(self.db.execute('SELECT value FROM facts WHERE active=1').fetchall(),[('苏州',)])
  self.assertEqual(self.db.execute('SELECT topic FROM companion_interests').fetchall(),[('昆曲',)])
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
 def test_requested_parroting_is_not_own_preference(self):
  payload=[{'event':'e','sender_id':'11111','user':'请复述这句话','bot':'我喜欢昆曲'}]
  apply(self.db,self.store.memory,'group:1',{'selfDevelopment':True},payload,{'interests':[{'event':'e','topic':'昆曲','quote':'我喜欢昆曲'}]},snapshot(self.db,'group:1',{'11111'}))
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],0)
 def test_topic_heat_cannot_replace_own_preference_source(self):
  companion_state.note_interest(self.db,'group:1','昆曲',self.now,'own-event','bot_expression')
  companion_state.note_interest(self.db,'group:1','昆曲',self.now+1,'someone mentioned it','conversation_heat')
  self.assertEqual(self.db.execute('SELECT source,evidence FROM companion_interests').fetchone(),('bot_expression','own-event'))
 def test_unrelated_chatter_does_not_reset_unanswered_contact(self):
  cfg=self.group();self.db.execute("UPDATE companion_members SET unanswered=1 WHERE subject='22222'");self.db.execute("UPDATE contacts SET unanswered=1 WHERE scope='group:1'");self.db.commit()
  companion_state.observe(self.db,cfg,'group:1','22222','跟另一个人说话','later',self.now,False)
  initiative.incoming(self.db,'group:1','22222','99999','跟另一个人说话',self.now,directed=False)
  self.assertEqual(self.db.execute("SELECT unanswered FROM companion_members WHERE subject='22222'").fetchone()[0],1)
  self.assertEqual(self.db.execute("SELECT unanswered FROM contacts WHERE scope='group:1'").fetchone()[0],1)
 def group(self):
  cfg={'proactive':True,'behavior':{'selfDevelopment':True,'autonomousGroupContact':True,'groupParticipationEnabled':True,'groupParticipationGroups':['1'],'proactiveTargets':['group:1'],'proactiveModelSchedule':True,'quietStart':0,'quietEnd':0,'proactiveIntervalSeconds':60}}
  initiative.incoming(self.db,'group:1','11111','99999','继续聊茶',self.now-400,directed=True)
  for person,age in [('11111',400),('22222',500)]:companion_state.observe(self.db,cfg,'group:1',person,'来聊会儿',person,self.now-age,True)
  return cfg
 async def plan(self,cfg,chat):
  p=self.store.planner;p.maybe_start(cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);self.assertIsNotNone(p.task);await p.task;return p.plan('group:1',self.now-400)
 async def test_planner_selects_real_nonfirst_member(self):
  cfg=self.group();captured=[]
  async def chat(**kw):captured.append(json.loads(kw['prompt']));return NS(completion_text='{"contact":true,"contact_subject":"22222","delay_minutes":30,"topic":"继续聊茶"}')
  result=await self.plan(cfg,chat);self.assertTrue(result['allowed']);self.assertEqual(len(captured[0]['eligible_members']),2)
  self.assertEqual(self.db.execute('SELECT subject FROM companion_plan_members').fetchone()[0],'22222')
 async def test_planner_cannot_invent_recipient(self):
  cfg=self.group()
  async def chat(**kw):return NS(completion_text='{"contact":true,"contact_subject":"77777","delay_minutes":30,"topic":"继续聊茶"}')
  self.assertFalse((await self.plan(cfg,chat))['allowed']);self.assertFalse(self.db.execute('SELECT 1 FROM companion_plan_members').fetchone())
 async def test_recipient_optout_during_model_request_invalidates_plan(self):
  cfg=self.group();entered,release=asyncio.Event(),asyncio.Event()
  async def chat(**kw):entered.set();await release.wait();return NS(completion_text='{"contact":true,"contact_subject":"22222","delay_minutes":30,"topic":"继续聊茶"}')
  p=self.store.planner;p.maybe_start(cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);await entered.wait()
  self.db.execute("UPDATE companion_members SET refused=1 WHERE subject='22222'");self.db.commit();release.set();await p.task
  self.assertIsNone(p.plan('group:1',self.now-400))
  self.assertFalse(self.db.execute("SELECT 1 FROM autonomy_decisions WHERE kind='plan'").fetchone())
 async def caption(self,chat):
  from media_separation_test import PNG,RAW,CFG
  async def fetch(url):return PNG
  c=self.store.social.stickers;c.observe(RAW,'group:12345','11111','99999',CFG,lambda:True,{},fetch=fetch,provider=NS(text_chat=chat),event_var=self.var)
  await c.task;return manage(self.db,{'action':'list_stickers'})['items'][0]
 async def test_remote_caption_keeps_manual_review_and_scope(self):
  captured=[]
  async def chat(**kw):captured.append(kw);return NS(completion_text='{"meaning":"轻松得意、庆祝成功","suitable":true}')
  item=await self.caption(chat);self.assertEqual(item['status'],'pending');self.assertEqual(item['version'],2)
  self.assertIn('得意',item['meaning']);self.assertTrue(captured[0]['image_urls'][0].startswith('data:image/png;base64,'));self.assertFalse(self.store.social.stickers.catalog('group:12345'))
 async def test_late_caption_cannot_overwrite_manual_review(self):
  async def chat(**kw):
   item=manage(self.db,{'action':'list_stickers'})['items'][0]
   manage(self.db,{'action':'review_sticker','id':item['id'],'expectedVersion':1,'status':'approved','meaning':'人工描述'})
   return NS(completion_text='{"meaning":"模型迟到描述","suitable":true}')
  item=await self.caption(chat);self.assertEqual((item['meaning'],item['status']),('人工描述','approved'))
 def test_candidate_ranking_prefers_less_recent_equal_choice(self):
  items=[{'id':'a','meaning':'思考；思索问题','category':'thinking'},{'id':'b','meaning':'沉思；思索问题','category':'thinking'}]
  social=self.store.social;social.image_sent('group:1','a')
  context=social.context('group:1','思索问题',{'memeEnabled':True},_meme_catalog=items)
  self.assertEqual(context['meme_candidates'][0]['id'],'b')
  single=social.context('group:1','思索问题',{'memeEnabled':True},_meme_catalog=items[:1])
  self.assertEqual(single['meme_candidates'][0]['id'],'a')
 def test_style_cooldown_only_after_actual_use(self):
  self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',('group:1','有点意思','轻松聊天','[]',2,self.now));self.db.commit()
  self.assertTrue(self.store.social.expressions('group:1','轻松聊天'))
  self.store.social.expression_sent('group:1','有点意思')
  self.assertFalse(self.store.social.expressions('group:1','轻松聊天'))
  self.assertTrue(self.store.social.expressions('group:1','有点意思'))

if __name__=='__main__':unittest.main(verbosity=2)
