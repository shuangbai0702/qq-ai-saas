import asyncio,contextvars,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
from behavior import BehaviorStore
from participation import Participation
import companion_state as cs,initiative
from semantic_rank import recall

class CompanionState(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.s=BehaviorStore(self.path);self.db=self.s.db;self.now=time.time()
  self.cfg={'proactive':True,'systemPrompt':'喜欢喝茶。','groups':[],'behavior':{'selfDevelopment':True,'autonomousGroupContact':True,'proactiveModelSchedule':False,'memorySemantic':True,'memorySummary':True,'silenceSeconds':60,'proactiveIntervalSeconds':60,'proactiveChance':1,'quietStart':0,'quietEnd':0}}
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def seen(self,scope='group:77777',subject='12345',text='你好，谢谢你',directed=True,now=None,event='one'):
  now=self.now-400 if now is None else now
  cs.observe(self.db,self.cfg,scope,subject,text,event,now,directed)
  initiative.incoming(self.db,scope,subject,'99999',text,now,directed)
 def test_choose_real_interacting_member_and_persist(self):
  self.seen();items=initiative.candidates(self.db,self.cfg,self.now)
  self.assertEqual(items[0]['contact_subject'],'12345');self.assertTrue(initiative.reserve(self.db,self.cfg,items[0],self.now))
  self.s.close();self.s=BehaviorStore(self.path);self.db=self.s.db
  self.assertEqual(initiative.candidates(self.db,self.cfg,self.now+90),[])
  self.assertEqual(self.db.execute('SELECT unanswered FROM companion_members').fetchone()[0],1)
 def test_no_stranger_no_cross_group_no_private_leak(self):
  self.seen(directed=False);self.assertEqual(initiative.candidates(self.db,self.cfg,self.now),[])
  cs.outgoing(self.db,self.cfg,'private:12345','12345','我喜欢乌龙茶','bot1',self.now)
  self.assertNotIn('乌龙茶',json.dumps(cs.context(self.db,self.cfg,'group:other',self.now),ensure_ascii=False))
  self.assertNotIn('private:12345',cs.targets(self.db,self.cfg,self.now))
 def test_optout_pause_scope_and_revocation(self):
  self.seen();item=initiative.candidates(self.db,self.cfg,self.now)[0]
  self.seen(text='不要艾特我',now=self.now,event='two')
  self.assertFalse(initiative.reserve(self.db,self.cfg,item,self.now));self.assertEqual(initiative.candidates(self.db,self.cfg,self.now+90),[])
  self.seen(text='可以主动找我',now=self.now+100,event='three');self.assertTrue(initiative.candidates(self.db,self.cfg,self.now+200))
  self.seen(text='晚安',now=self.now+210,event='four');self.assertFalse(cs.select_member(self.db,self.cfg,'group:77777',self.now+300))
 def test_whitelist_disabled_and_daily_budget(self):
  self.seen();self.cfg['groups']=['88888'];self.assertEqual(initiative.candidates(self.db,self.cfg,self.now),[])
  self.cfg['groups']=[];self.cfg['behavior']['autonomousGroupContact']=False;self.assertEqual(initiative.candidates(self.db,self.cfg,self.now),[])
  self.cfg['behavior']['autonomousGroupContact']=True;self.cfg['behavior']['dailyProactiveLimit']=0;self.assertEqual(initiative.candidates(self.db,self.cfg,self.now),[])
 def test_mood_decay_dedup_and_restart(self):
  self.seen();a=cs.context(self.db,self.cfg,'group:77777',self.now)
  self.seen();b=cs.context(self.db,self.cfg,'group:77777',self.now)
  self.assertEqual(a['mood'],b['mood']);self.assertGreater(a['mood']['valence'],0)
  self.assertLess(cs.context(self.db,self.cfg,'group:77777',self.now+86400)['mood']['valence'],a['mood']['valence'])
  self.s.close();self.s=BehaviorStore(self.path);self.db=self.s.db;self.assertEqual(cs.context(self.db,self.cfg,'group:77777',self.now)['mood'],a['mood'])
 def test_own_preferences_and_motives_are_evidenced_and_scoped(self):
  self.seen(text='我喜欢吃火锅');ctx=cs.context(self.db,self.cfg,'group:77777',self.now)
  self.assertFalse(any(x['topic']=='火锅' for x in ctx['interests']))
  cs.outgoing(self.db,self.cfg,'group:77777','12345','我喜欢乌龙茶。下次再聊茶叶烘焙。','reply1',self.now)
  ctx=cs.context(self.db,self.cfg,'group:77777',self.now)
  self.assertTrue(any(x['topic']=='乌龙茶' for x in ctx['interests']));self.assertTrue(ctx['unfinished_topics'])
  self.assertFalse(cs.context(self.db,self.cfg,'group:88888',self.now)['unfinished_topics'])
 def test_emotion_targeting_repair_and_neutral_release(self):
  import emotion_state as es
  es.observe(self.db,'g','我真笨，余额不足',self.now,False);self.assertEqual(es.context(self.db,'g',self.now)['kind'],'calm')
  es.observe(self.db,'g','你真蠢',self.now,True);self.assertEqual(es.context(self.db,'g',self.now)['kind'],'angry')
  es.observe(self.db,'g','谢谢你',self.now+1,False);self.assertEqual(es.context(self.db,'g',self.now+1)['kind'],'angry')
  es.observe(self.db,'g','抱歉，刚才不是说你',self.now+2,True);self.assertEqual(es.context(self.db,'g',self.now+2)['kind'],'calm')
  es.observe(self.db,'g','闭嘴',self.now+3,True)
  for i in range(4):es.observe(self.db,'g','我们接着聊',self.now+4+i,True)
  self.assertEqual(es.context(self.db,'g',self.now+8)['kind'],'calm')
 def test_attention_interest_acknowledgement_and_fatigue(self):
  p=Participation();p.outgoing('g','白饭很好吃',self.now)
  b={'attentionEnabled':True,'fatigueEnabled':True,'fatigueMaxReplies':2,'_interests':[{'topic':'茶叶','source':'persona'}]}
  self.assertTrue(p.allow('g','然后呢',b,now=self.now+1));self.assertTrue(p.allow('g','茶叶怎么挑',b,now=self.now+1));self.assertFalse(p.allow('g','明天爬山',b,now=self.now+1))
  p.outgoing('g','继续',self.now+2);self.assertFalse(p.allow('g','茶叶怎么挑',b,now=self.now+3));self.assertTrue(p.allow('g','茶叶怎么挑',b,directed=True,now=self.now+3))
 async def test_remote_semantic_selects_real_ids(self):
  self.s.observe('private:12345','12345','记住：饮料=乌龙茶','a',self.now)
  async def chat(**kw):return NS(completion_text='{"facts":[0],"summaries":[]}')
  metrics={};facts,notes=await recall(self.s,'private:12345','12345','平时喝什么',self.cfg['behavior'],NS(text_chat=chat),lambda:True,contextvars.ContextVar('event',default=None),metrics)
  self.assertEqual(facts[0]['value'],'乌龙茶');self.assertEqual(facts[0]['recall_reason'],'remote_semantic_rank');self.assertEqual(metrics['semanticRerankSuccesses'],1)
 async def test_semantic_forgetting_during_request_cannot_return_old_facts(self):
  self.s.observe('private:12345','12345','记住：饮料=乌龙茶','a',self.now)
  entered=asyncio.Event();release=asyncio.Event()
  async def chat(**kw):entered.set();await release.wait();return NS(completion_text='{"facts":[0],"summaries":[]}')
  task=asyncio.create_task(recall(self.s,'private:12345','12345','饮料',self.cfg['behavior'],NS(text_chat=chat),lambda:True,contextvars.ContextVar('event',default=None),{}))
  await entered.wait();self.s.observe('private:12345','12345','忘记：饮料','b',self.now+1);release.set();self.assertEqual(await task,([],[]))
 async def test_semantic_invalid_response_cools_down_and_scope_does_not_leak(self):
  self.s.observe('private:12345','12345','记住：饮料=乌龙茶','a',self.now);calls=[]
  async def chat(**kw):calls.append(kw);return NS(completion_text='{"facts":[999],"summaries":[]}')
  args=(self.s,'private:12345','12345','饮料',self.cfg['behavior'],NS(text_chat=chat),lambda:True,contextvars.ContextVar('event',default=None),{})
  await recall(*args);await recall(*args);self.assertEqual(len(calls),1)
  self.assertEqual(await recall(self.s,'group:77777','12345','饮料',self.cfg['behavior'],NS(text_chat=chat),lambda:True,args[-2],{}),([],[]))

if __name__=='__main__':unittest.main()
