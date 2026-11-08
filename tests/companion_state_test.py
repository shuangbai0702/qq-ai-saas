import asyncio,contextvars,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
from behavior import BehaviorStore
from participation import Participation
import companion_state as cs,initiative,autonomy
from semantic_context import recall

class CompanionState(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.s=BehaviorStore(self.path);self.db=self.s.db;self.now=time.time()
  self.cfg={'proactive':True,'systemPrompt':'喜欢喝茶。','groups':[],'behavior':{'selfDevelopment':True,'autonomousGroupContact':True,'groupParticipationEnabled':True,'groupParticipationGroups':['77777'],'proactiveModelSchedule':False,'memorySemantic':True,'memorySummary':True,'silenceSeconds':60,'proactiveIntervalSeconds':60,'proactiveChance':1,'quietStart':0,'quietEnd':0}}
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def seen(self,scope='group:77777',subject='12345',text='你好，谢谢你',directed=True,now=None,event='one'):
  now=self.now-400 if now is None else now
  cs.observe(self.db,self.cfg,scope,subject,text,event,now,directed)
  initiative.incoming(self.db,scope,subject,'99999',text,now,directed)
 def test_choose_real_interacting_member_and_persist(self):
  self.seen();items=initiative.candidates(self.db,self.cfg,self.now)
  self.assertEqual(items[0]['contact_subject'],'12345');self.assertTrue(initiative.reserve(self.db,self.cfg,items[0],self.now))
  self.s.close();self.s=BehaviorStore(self.path);self.db=self.s.db
  self.assertEqual(self.db.execute('SELECT unanswered FROM companion_members').fetchone()[0],1)
  # The common default now stops after four unanswered attempts. Restart must
  # retain the first attempt, while allowing the remaining three at interval.
  for index in range(1,4):
   at=self.now+90*index;items=initiative.candidates(self.db,self.cfg,at)
   self.assertEqual(items[0]['contact_subject'],'12345');self.assertTrue(initiative.reserve(self.db,self.cfg,items[0],at))
  self.assertEqual(self.db.execute('SELECT unanswered FROM companion_members').fetchone()[0],4)
  self.assertEqual(initiative.candidates(self.db,self.cfg,self.now+450),[])
 def test_no_stranger_no_cross_group_no_private_leak(self):
  self.seen(directed=False);self.assertEqual(initiative.candidates(self.db,self.cfg,self.now),[])
  cs.outgoing(self.db,self.cfg,'private:12345','12345','我喜欢乌龙茶','bot1',self.now)
  self.assertNotIn('乌龙茶',json.dumps(cs.context(self.db,self.cfg,'group:other',self.now),ensure_ascii=False))
  self.assertNotIn('private:12345',cs.targets(self.db,self.cfg,self.now))
 def test_own_research_interest_has_explicit_bot_attribution(self):
  self.db.execute("INSERT INTO companion_interests VALUES('self',?,?,?,?,?,'autonomous_choice')",('光学设计',1,1,self.now,'research-work'))
  view=cs.context(self.db,self.cfg,'private:12345',self.now)
  interest=next(x for x in view['interests'] if x['topic']=='光学设计')
  self.assertEqual(interest['attribution'],'bot_self_interest_not_user_statement')
  self.assertEqual(interest['source_scope'],'self')
 def test_chat_is_model_evidence_and_cooldown_is_scope_local(self):
  self.seen();item=initiative.candidates(self.db,self.cfg,self.now)[0]
  self.seen(text='不要艾特我',now=self.now,event='two')
  self.assertFalse(initiative.reserve(self.db,self.cfg,item,self.now));self.assertTrue(initiative.candidates(self.db,self.cfg,self.now+90))
  self.assertEqual(self.db.execute('SELECT refused,pause FROM companion_members').fetchone(),(0,0))
  with self.db:autonomy.record_decision(self.db,self.cfg,'group:77777','cooldown',{'cooldown_intervals':1,'cooldown_reason':'social_distance'},False,self.now,'group')
  self.assertEqual(initiative.candidates(self.db,self.cfg,self.now+90),[])
  self.seen(text='可以主动找我',now=self.now+100,event='three');self.assertTrue(initiative.candidates(self.db,self.cfg,self.now+200))
  self.seen(text='晚安',now=self.now+210,event='four');self.assertTrue(cs.select_member(self.db,self.cfg,'group:77777',self.now+300))
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
 def test_bootstrap_uses_only_successful_replied_history_and_never_overwrites_refusal(self):
  self.db.execute("DELETE FROM companion_migrations WHERE name='replied_members_v1'")
  self.s.reflection.record('group:77777','12345','real1','一起喝茶','喝茶好呀',self.now-100)
  self.db.execute("INSERT INTO companion_members(scope,subject,seen,directed,refused) VALUES('group:77777','12345',?,?,1)",(self.now-80,self.now-80));self.db.commit()
  self.s.reflection.record('group:77777','23456','real2','聊茶叶','好呀',self.now-50)
  cs.bootstrap_members(self.db)
  self.assertEqual(self.db.execute("SELECT refused FROM companion_members WHERE subject='12345'").fetchone()[0],1)
  self.assertEqual(self.db.execute("SELECT directed FROM companion_members WHERE subject='23456'").fetchone()[0],self.now-50)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_members').fetchone()[0],2)
  cs.bootstrap_members(self.db);self.assertEqual(self.db.execute('SELECT count(*) FROM companion_members').fetchone()[0],2)
 def test_attention_interest_acknowledgement_and_fatigue(self):
  p=Participation();p.outgoing('g','白饭很好吃',self.now)
  b={'attentionEnabled':True,'fatigueEnabled':True,'fatigueMaxReplies':2,'_interests':[{'topic':'茶叶','source':'persona'}]}
  self.assertTrue(p.allow('g','然后呢',b,now=self.now+1));self.assertTrue(p.allow('g','茶叶怎么挑',b,now=self.now+1));self.assertTrue(p.allow('g','明天爬山',b,now=self.now+1))
  p.outgoing('g','继续',self.now+2);self.assertFalse(p.allow('g','茶叶怎么挑',b,now=self.now+3));self.assertTrue(p.allow('g','茶叶怎么挑',b,directed=True,now=self.now+3))
 def test_followup_keeps_both_sides_topic_and_recent_speaker(self):
  p=Participation();b={'attentionEnabled':True,'fatigueEnabled':True,'fatigueMaxReplies':3}
  p.outgoing('g','可以的',self.now,subject='12345',request='乌龙茶适合冷泡吗')
  self.assertIsNone(p.blocked_reason('g','乌龙茶放多久',b,now=self.now+1,subject='12345'))
  self.assertTrue(p.continuing('g','那要多久呢',subject='12345',now=self.now+2))
  self.assertIsNone(p.blocked_reason('g','那要多久呢',b,now=self.now+2,subject='12345'))
  self.assertIsNone(p.blocked_reason('g','那要多久呢',b,now=self.now+2,subject='54321'))
  self.assertFalse(p.continuing('other','那要多久呢',subject='12345',now=self.now+2))
  self.assertFalse(p.continuing('g','那要多久呢',subject='12345',now=self.now+120))
  self.assertIsNone(p.blocked_reason('g','明天爬山去吗',b,now=self.now+2,subject='12345'))
  self.assertFalse(p.continuing('g','继续聊',subject='12345',now=self.now+2,seconds=0))
  p.outgoing('g','还可以',self.now+3,subject='12345');p.outgoing('g','嗯',self.now+4,subject='12345')
  self.assertEqual(p.blocked_reason('g','那要多久呢',b,now=self.now+5,subject='12345'),'fatigue')
 def test_semantic_candidates_include_different_wording_without_an_extra_request(self):
  self.s.observe('private:12345','12345','记住：偏好饮料=乌龙茶','a',self.now)
  metrics={};facts,notes=recall(self.s,'private:12345','12345','口渴时想喝点什么',self.cfg['behavior'],lambda:True,metrics)
  self.assertEqual(facts[0]['value'],'乌龙茶');self.assertEqual(facts[0]['semantic_mode'],'model_semantic_candidate');self.assertEqual(metrics['semanticContextTurns'],1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_embedding_budget').fetchone()[0],0)
 def test_semantic_forgetting_and_stopped_work_return_no_old_facts(self):
  self.s.observe('private:12345','12345','记住：饮料=乌龙茶','a',self.now)
  self.s.observe('private:12345','12345','忘记：饮料','b',self.now+1)
  self.assertEqual(recall(self.s,'private:12345','12345','饮料',self.cfg['behavior'],lambda:True,{}),([],[]))
  self.assertEqual(recall(self.s,'private:12345','12345','饮料',self.cfg['behavior'],lambda:False,{}),([],[]))
 def test_semantic_context_follows_same_person_but_does_not_fill_other_people(self):
  self.s.observe('private:12345','12345','记住：饮料=乌龙茶','a',self.now)
  same=recall(self.s,'group:77777','12345','饮料',self.cfg['behavior'],lambda:True,{})[0]
  self.assertEqual(same[0]['value'],'乌龙茶');self.assertEqual(same[0]['source_scope'],'private:12345')
  private=recall(self.s,'private:12345','12345','饮料',self.cfg['behavior'],lambda:True,{})[0]
  self.assertEqual(private[0]['value'],'乌龙茶');self.assertEqual(private[0]['source_scope'],'private:12345')
  self.assertEqual(recall(self.s,'group:77777','54321','饮料',self.cfg['behavior'],lambda:True,{}),([],[]))
  self.assertEqual(recall(self.s,'private:12345','54321','饮料',self.cfg['behavior'],lambda:True,{}),([],[]))

if __name__=='__main__':unittest.main()
