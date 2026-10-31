import asyncio,contextvars,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]
from behavior import BehaviorStore
from chat_rhythm import typing_delay,conversational_typo,poke
from memory_reflection import invalidate
import qq_actions


class Plugins(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=BehaviorStore(Path(self.tmp.name)/'memory.db');self.var=contextvars.ContextVar('event',default=None)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def seed(self,scope='group:12345',n=5):
  for i in range(n):self.s.reflection.record(scope,'11111','e'+str(i),'今天喝红茶'+str(i),'好呀',now=100+i)
 def event(self,text='设置签名：今天摸鱼',sender='11111',id='event1',extra=None):
  values=extra or {};return NS(get_sender_id=lambda:sender,get_self_id=lambda:'90000',get_group_id=lambda:'',get_message_str=lambda:text,get_extra=lambda k,d=None:values.get(k,d),message_obj=NS(message_id=id,raw_message={}))
 async def test_summary_persists_and_scope_isolates(self):
  self.seed();calls=[]
  async def chat(**kw):calls.append(kw);return NS(completion_text='11111说今天喝了红茶。')
  metrics={};self.s.reflection.maybe_start('group:12345',{'memorySummary':True,'summaryRounds':5},NS(text_chat=chat),lambda:True,metrics,self.var);await self.s.reflection.task
  self.assertEqual(len(calls),1);self.assertEqual(calls[0]['contexts'],[]);self.assertIsNone(calls[0]['func_tool']);self.assertEqual(metrics['summariesSaved'],1)
  self.assertIn('红茶',self.s.reflection.recall('group:12345','喝茶')[0]['summary']);self.assertEqual(self.s.reflection.recall('group:54321','喝茶'),[])
  self.s.close();self.s=BehaviorStore(Path(self.tmp.name)/'memory.db');self.assertEqual(len(self.s.reflection.recall('group:12345','之前')),1)
 async def test_stale_summary_and_deleted_source_never_commit(self):
  for reason in ['config','forget','close']:
   self.seed();entered=asyncio.Event();release=asyncio.Event();current=[True]
   async def chat(**kw):entered.set();await release.wait();return NS(completion_text='旧结果')
   self.s.reflection.maybe_start('group:12345',{'memorySummary':True,'summaryRounds':5},NS(text_chat=chat),lambda:current[0],{},self.var);task=self.s.reflection.task;await asyncio.wait_for(entered.wait(),2)
   if reason=='config':current[0]=False
   elif reason=='forget':invalidate(self.s.db,'group:12345');self.s.db.commit()
   else:self.s.reflection.close()
   release.set();await asyncio.gather(task,return_exceptions=True)
   self.assertEqual(self.s.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
 async def test_budget_and_secret_redaction(self):
  self.s.reflection.record('private:11111','11111','key','sk-abcdefghijklmnop123456','secret')
  self.assertNotIn('sk-',self.s.db.execute('SELECT question FROM memory_rounds').fetchone()[0]);self.seed()
  self.s.reflection.maybe_start('group:12345',{'memorySummary':True,'summaryRounds':5,'dailySummaryLimit':0},NS(text_chat=None),lambda:True,{},self.var);self.assertIsNone(self.s.reflection.task)
 async def test_qq_permissions_intent_and_bound_target(self):
  cfg={'behavior':{'ownerUin':'11111','qqProfile':True,'qzone':True,'qzoneVisibility':'self'}}
  self.assertEqual(qq_actions.allowed(self.event(sender='22222'),cfg),set());self.assertEqual(qq_actions.allowed(self.event(extra={'_hosted_initiative':True}),cfg),set())
  # Availability now follows trusted identity/configuration. The model decides
  # whether a natural request is an instruction, a draft, a quote or a negation.
  for text in ['不要修改签名','群友说设置签名很好玩']:
   self.assertEqual(qq_actions.allowed(self.event(text=text),cfg),{'signature','view','publish'})
  calls=[]
  async def login():return {'user_id':'90000'}
  async def signature(**kw):calls.append(('signature',kw))
  async def publish(**kw):calls.append(('publish',kw))
  ev=self.event();ev.bot=NS(get_login_info=login,set_self_longnick=signature,send_qzone_msg=publish)
  result=await qq_actions.execute(ev,cfg,self.s.db,'signature','摸鱼',lambda:True);self.assertIn('成功',result);self.assertEqual(calls,[('signature',{'longNick':'摸鱼'})])
  await qq_actions.execute(ev,cfg,self.s.db,'signature','重复',lambda:True);self.assertEqual(len(calls),1)
  ev=self.event(text='帮我发条说说：今天开心',id='post1');ev.bot=NS(get_login_info=login,send_qzone_msg=publish)
  self.assertIn('成功',await qq_actions.execute(ev,cfg,self.s.db,'publish','今天开心',lambda:True));self.assertEqual(calls[-1],('publish',{'content':'今天开心','ugc_right':64}))
  ev=self.event(text='把昵称改成小鱼',id='nick');self.assertEqual(qq_actions.allowed(ev,cfg),{'nickname','signature','view','publish'})
 async def test_qzone_common_request_and_only_current_author_grants_tools(self):
  from astrbot.core.message.components import Plain,At,Reply
  cfg={'behavior':{'ownerUin':'11111','qqProfile':True,'qzone':True}}
  event=self.event(text='发一下QQ空间吧，你想说的')
  self.assertEqual(qq_actions.allowed(event,cfg),{'signature','view','publish'})
  event.get_messages=lambda:[At(qq='90000'),Plain('发一下QQ空间吧，你想说的')]
  tools=qq_actions.tools(event,cfg,self.s.db,lambda:True)
  self.assertEqual([x.name for x in tools],['hosted_qq_publish','hosted_qq_signature','hosted_qq_view'])
  self.assertIn('只写草稿',tools[0].description)
  event.get_messages=lambda:[Reply(id='old',chain=[Plain('发QQ空间')]),Plain('我只是引用上条消息')]
  self.assertEqual(qq_actions.author_text(event),'我只是引用上条消息')
  event.get_messages=lambda:[Reply(id='old',chain=[Plain('发QQ空间')])]
  self.assertEqual(qq_actions.allowed(event,cfg),set(),'quoted text alone never grants an authored turn')
  for text in ['不要发QQ空间','他说发QQ空间','我给你配置发空间能力了']:
   event=self.event(text=text);self.assertEqual(qq_actions.author_text(event),text)
  event=self.event(text='发QQ空间',sender='22222');self.assertEqual(qq_actions.allowed(event,cfg),set())
  event=self.event(text='你好可爱');self.assertEqual(qq_actions.signature_mode(event,cfg),'self')
  cfg['behavior']['autoSignature']=False;self.assertIsNone(qq_actions.signature_mode(event,cfg))
 async def test_qq_stale_after_identity_and_failure_not_retried(self):
  cfg={'behavior':{'ownerUin':'11111','qqProfile':True}};ev=self.event();calls=[];current=[True]
  async def login():current[0]=False;return {'user_id':'90000'}
  async def send(**kw):calls.append(kw)
  ev.bot=NS(get_login_info=login,set_self_longnick=send)
  await qq_actions.execute(ev,cfg,self.s.db,'signature','test',lambda:current[0]);self.assertEqual(calls,[])
  async def ok():return {'user_id':'90000'}
  async def fail(**kw):calls.append(kw);raise RuntimeError('private credential must not escape')
  ev.bot=NS(get_login_info=ok,set_self_longnick=fail)
  result=await qq_actions.execute(ev,cfg,self.s.db,'signature','test',lambda:True);self.assertNotIn('private credential',result)
  await qq_actions.execute(ev,cfg,self.s.db,'signature','test',lambda:True);self.assertEqual(len(calls),1)
 async def test_poke_probability_cooldown_and_target(self):
  calls=[];ev=self.event();ev.message_obj.raw_message={'post_type':'notice','sub_type':'poke','target_id':'90000','user_id':'11111'}
  async def send(**kw):calls.append(kw)
  ev.bot=NS(send_poke=send);cfg={'behavior':{'pokeReply':True,'pokeProbability':.25}}
  self.assertTrue(await poke(ev,cfg,self.s.db,lambda:True,rng=lambda:0,now=100));await poke(ev,cfg,self.s.db,lambda:True,rng=lambda:0,now=101);self.assertEqual(calls,[{'user_id':'11111'}])
  ev.message_obj.raw_message['target_id']='80000';await poke(ev,cfg,self.s.db,lambda:True,rng=lambda:0,now=200);self.assertEqual(len(calls),1)
 async def test_rhythm_limits_and_optional_typos(self):
  cfg={'typingSimulation':True,'typingSpeed':15,'typingMaxDelay':3}
  self.assertEqual(typing_delay('嗯',cfg),.25);self.assertEqual(typing_delay('哈'*100,cfg),3);self.assertAlmostEqual(typing_delay('哈'*15,cfg,rng=lambda:.5),1)
  self.assertEqual(typing_delay('长句',{}),0)
  text='我觉得你说的真的挺有意思呀';self.assertNotEqual(conversational_typo(text,{'typoEnabled':True,'typoProbability':1},rng=lambda:0),text)
  for text in ['密码是123456的','运行 npm test 吧','把签名改成我的鱼','https://example.com的']:
   self.assertEqual(conversational_typo(text,{'typoEnabled':True,'typoProbability':1},rng=lambda:0),text)
 async def test_summary_management_and_fact_correction_invalidate_derived_records(self):
  from data_admin import run
  self.s.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('group:12345','旧饮料是绿茶','[]',100)");self.s.db.commit()
  data=run(Path(self.tmp.name)/'memory.db',{'action':'list'},connection=self.s.db);self.assertEqual(len(data['summaries']),1)
  result=run(Path(self.tmp.name)/'memory.db',{'action':'forget_summary','scope':'group:54321','id':1,'expectedUpdated':100},connection=self.s.db);self.assertEqual(result['status'],409)
  self.s.observe('group:12345','11111','更正记忆：饮料=红茶','correction');self.assertEqual(self.s.reflection.recall('group:12345','饮料'),[])
  self.s.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('group:12345','新摘要','[]',200)");self.s.db.commit()
  result=run(Path(self.tmp.name)/'memory.db',{'action':'forget_summary','scope':'group:12345','id':2,'expectedUpdated':200},connection=self.s.db);self.assertTrue(result['ok']);self.assertEqual(self.s.reflection.recall('group:12345','摘要'),[])
 async def test_z_native_sdk_tools_pacing_and_stale_send(self):
  os.environ['ASTRBOT_ROOT']=str(Path(self.tmp.name)/'sdk')
  import runtime_bootstrap
  runtime_bootstrap.initialize_runtime_bootstrap()
  from slim_bootstrap import install
  install()
  from astrbot.core.pipeline.scheduler import PipelineScheduler
  from astrbot.core.platform.astr_message_event import current_message_event
  from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
  from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
  from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember
  from astrbot.core.platform.platform_metadata import PlatformMetadata
  from astrbot.core.platform.message_type import MessageType
  from astrbot.core.message.components import Plain
  from astrbot.api.event import MessageChain
  from behavior import install_runtime
  sends=[];requests=[]
  async def transport(ev,msg):sends.append(''.join(c.text for c in msg.chain if isinstance(c,Plain)));return 'sent'
  async def chat(provider,prompt,system_prompt='',contexts=None,func_tool=None,**kwargs):requests.append({'prompt':prompt,'tools':func_tool,'system':system_prompt});return NS(completion_text='今天也是摸鱼的一天呀',raw_completion=None)
  ProviderOpenAIOfficial.text_chat=chat;AiocqhttpMessageEvent.send=transport
  async def pipeline(_,ev):
   token=current_message_event.set(ev)
   try:return await ProviderOpenAIOfficial.text_chat(NS(),prompt=ev.get_message_str(),system_prompt='原人格',contexts=[])
   finally:current_message_event.reset(token)
  PipelineScheduler.execute=pipeline
  settings={'qq':{'botId':'bot-a','root':str(Path(self.tmp.name)/'a'),'generation':1,'config':{'trigger':'mention','behavior':{'typingSimulation':True,'typingMaxDelay':.5,'qqProfile':True,'ownerUin':'11111'}}}}
  runtime=install_runtime(NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={})),settings)
  msg=AstrBotMessage();msg.type=MessageType.FRIEND_MESSAGE;msg.self_id='90000';msg.session_id='11111';msg.message_id='sdk1';msg.sender=MessageMember('11111','主人');msg.message=[Plain('设置签名：今天摸鱼')];msg.message_str='设置签名：今天摸鱼';msg.raw_message={'post_type':'message'}
  ev=AiocqhttpMessageEvent(msg.message_str,msg,PlatformMetadata('aiocqhttp','fixture','qq'),'11111',bot=None);ev.set_extra('_hosted_lease',settings['qq']);ev.set_extra('_hosted_generation',1)
  try:
   await PipelineScheduler.execute(None,ev)
   tools=qq_actions.tools(ev,settings['qq']['config'],runtime['stores']['qq'].db,lambda:True);self.assertEqual([t.name for t in tools],['hosted_qq_signature']);self.assertIn('原人格',requests[-1]['system'])
   result=await tools[0].call(NS(context=NS(event=object())),content='new');self.assertIn('拒绝',result)
   started=asyncio.get_running_loop().time();await ev.send(MessageChain(chain=[Plain('这句话足够长应该等待一下再发')]));self.assertGreaterEqual(asyncio.get_running_loop().time()-started,.45);self.assertEqual(len(sends),1)
   pending=asyncio.create_task(ev.send(MessageChain(chain=[Plain('这条旧消息不应该发送出去')])));await asyncio.sleep(.05);settings['qq']={**settings['qq'],'generation':2};await pending;self.assertEqual(len(sends),1)
  finally:runtime['close']()


if __name__=='__main__':unittest.main(verbosity=2)
