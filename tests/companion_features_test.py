import asyncio,contextvars,json,sqlite3,sys,time,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
from companion_schedule import Planner
from participation import Participation
import initiative,qq_actions

class Companion(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');initiative.initialize(self.db);self.p=Planner(self.db);self.var=contextvars.ContextVar('event',default=None)
  self.now=time.time();initiative.incoming(self.db,'private:12345','12345','99999','喜欢喝茶',self.now-400)
  self.cfg={'proactive':True,'behavior':{'memory':False,'proactiveTargets':['private:12345'],'proactiveModelSchedule':True,'quietStart':0,'quietEnd':0,'proactiveIntervalSeconds':60,'silenceSeconds':60,'proactiveChance':1}}
  self.db.execute('CREATE TABLE deliveries(scope TEXT,day TEXT,count INTEGER,last_sent REAL,PRIMARY KEY(scope,day))')
 def tearDown(self):self.p.close();self.db.close()
 async def test_model_plan_and_due(self):
  async def chat(**kw):return NS(completion_text=json.dumps({'contact':True,'delay_minutes':30,'topic':'最近喝的茶'}))
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);await self.p.task
  plan=self.p.plan('private:12345',self.now-400);self.assertTrue(plan['allowed'])
  self.assertEqual(initiative.candidates(self.db,self.cfg,plan['due']-1),[])
  item=initiative.candidates(self.db,self.cfg,plan['due']+1)[0];self.assertEqual(item['topic'],'最近喝的茶')
  initiative.incoming(self.db,'private:12345','12345','99999','新的消息',self.now)
  self.assertEqual(initiative.candidates(self.db,self.cfg,plan['due']+1),[])
 async def test_plan_receives_configured_persona(self):
  captured=[]
  async def chat(**kw):captured.append(kw);return NS(completion_text='{"contact":false,"delay_minutes":30,"topic":""}')
  self.cfg['systemPrompt']='角色有稳定的个人兴趣。'
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);await self.p.task
  self.assertEqual(json.loads(captured[0]['prompt'])['persona'],self.cfg['systemPrompt'])
 async def test_unlimited_planning_does_not_inherit_old_six_checks_cap(self):
  day=int((self.now+8*3600)//86400);self.db.execute('INSERT INTO companion_plan_budget VALUES(?,100)',(day,));calls=[]
  async def chat(**kw):calls.append(kw);return NS(completion_text='{"contact":false,"delay_minutes":30,"topic":""}')
  self.cfg['behavior']['dailyPlanLimit']=-1
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);await self.p.task
  self.assertEqual(len(calls),1);self.assertEqual(self.db.execute('SELECT count FROM companion_plan_budget WHERE day=?',(day,)).fetchone()[0],101)
 async def test_actual_daily_send_cap_prevents_wasted_planning(self):
  day=datetime.fromtimestamp(self.now,timezone(timedelta(hours=8))).date().isoformat()
  self.db.execute('INSERT INTO deliveries VALUES(?,?,10,?)',('private:12345',day,self.now-300))
  async def chat(**kw):raise AssertionError('No remaining send budget')
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now)
  self.assertIsNone(self.p.task);self.assertEqual(self.db.execute('SELECT count(*) FROM companion_plan_budget').fetchone()[0],0)
 async def test_planning_budget_rolls_over_at_beijing_midnight(self):
  before=datetime(2026,11,1,15,59,30,tzinfo=timezone.utc).timestamp();after=before+60
  old_day=int((before+8*3600)//86400);new_day=int((after+8*3600)//86400)
  self.assertNotEqual(old_day,new_day)
  self.cfg['behavior']['dailyPlanLimit']=1;self.db.execute('INSERT INTO companion_plan_budget VALUES(?,1)',(old_day,))
  initiative.incoming(self.db,'private:12345','12345','99999','边界前的真实互动',before-400)
  async def chat(**kw):return NS(completion_text='{"contact":false,"delay_minutes":30,"topic":""}')
  provider=NS(text_chat=chat);self.p.maybe_start(self.cfg,provider,lambda:True,self.var,{},before);self.assertIsNone(self.p.task)
  self.p.maybe_start(self.cfg,provider,lambda:True,self.var,{},after);await self.p.task
  self.assertEqual(self.db.execute('SELECT count FROM companion_plan_budget WHERE day=?',(new_day,)).fetchone()[0],1)
 async def test_authorized_plan_target_is_not_hidden_by_other_contacts(self):
  for i in range(80):initiative.incoming(self.db,'private:'+str(50000000+i),str(50000000+i),'99999','普通聊天',self.now-350)
  async def chat(**kw):return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"继续聊茶"}')
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now)
  self.assertIsNotNone(self.p.task);await self.p.task
  self.assertIsNotNone(self.p.plan('private:12345',self.now-400))
 async def test_stale_plan_not_saved(self):
  entered=asyncio.Event();release=asyncio.Event()
  async def chat(**kw):entered.set();await release.wait();return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"茶"}')
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);await entered.wait()
  initiative.incoming(self.db,'private:12345','12345','99999','晚安',self.now)
  release.set();await self.p.task;self.assertIsNone(self.p.plan('private:12345',self.now-400))
 async def test_invalid_plan_pauses_anchor(self):
  async def chat(**kw):return NS(completion_text='invalid')
  self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:True,self.var,{},self.now);await self.p.task
  self.assertFalse(self.p.plan('private:12345',self.now-400)['allowed'])
 async def test_signature_owner_mood_budget_and_dedup(self):
  calls=[]
  async def identity():return {'user_id':99999}
  async def signature(**kw):calls.append(kw)
  event=NS(get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_extra=lambda key,default=None:default,get_message_str=lambda:'你好可爱',message_obj=NS(message_id='one'),bot=NS(get_login_info=identity,set_self_longnick=signature))
  cfg={'behavior':{'ownerUin':'12345','qqProfile':True,'autoSignature':True,'dailyAutoSignatureLimit':1}}
  self.assertIn('signature',qq_actions.allowed(event,cfg));self.assertTrue(qq_actions.signature_hint(event,cfg))
  result=await qq_actions.execute(event,cfg,self.db,'signature','今天被夸啦',lambda:True);self.assertIn('成功',result);self.assertEqual(len(calls),1)
  event.message_obj.message_id='two';self.assertIn('额度',await qq_actions.execute(event,cfg,self.db,'signature','新的签名',lambda:True));self.assertEqual(len(calls),1)
  event.get_sender_id=lambda:'67890';self.assertEqual(qq_actions.allowed(event,cfg),set())
  event.get_sender_id=lambda:'12345';event.get_message_str=lambda:'设置签名：主人指定'
  self.assertIn('成功',await qq_actions.execute(event,cfg,self.db,'signature','主人指定',lambda:True));self.assertEqual(len(calls),2)
 async def test_profile_text_controls_are_removed_before_limits_and_dedup(self):
  calls=[]
  async def identity():return {'user_id':99999}
  async def action(**kw):calls.append(kw)
  event=NS(get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_extra=lambda k,d=None:d,get_message_str=lambda:'你好可爱',message_obj=NS(message_id='clean'),bot=NS(get_login_info=identity,set_self_longnick=action,set_qq_profile=action,send_qzone_msg=action))
  cfg={'behavior':{'ownerUin':'12345','qqProfile':True,'autoSignature':True,'qzone':True}}
  value='被夸可爱了，今天尾巴翘得高高的[QQ_FACE:4]'
  self.assertIn('成功',await qq_actions.execute(event,cfg,self.db,'signature',value,lambda:True))
  self.assertEqual(calls[-1],{'longNick':'被夸可爱了，今天尾巴翘得高高的'})
  self.assertEqual(self.db.execute('SELECT value FROM qq_signature_history').fetchone()[0],qq_actions.plain_text(value))
  event.message_obj.message_id='dedup'
  self.assertIn('不重复',await qq_actions.execute(event,cfg,self.db,'signature',qq_actions.plain_text(value),lambda:True));self.assertEqual(len(calls),1)
  event.message_obj.message_id='only-controls'
  self.assertIn('没有可用文本',await qq_actions.execute(event,cfg,self.db,'signature','[QQ_FACE:4][NO_REPLY]',lambda:True));self.assertEqual(len(calls),1)
  event.get_message_str=lambda:'设置昵称';event.message_obj.message_id='nickname'
  self.assertIn('成功',await qq_actions.execute(event,cfg,self.db,'nickname','肥鱼[MEME:hug]🐟',lambda:True));self.assertEqual(calls[-1],{'nickname':'肥鱼🐟'})
  event.get_message_str=lambda:'发条说说';event.message_obj.message_id='publish-clean'
  self.assertIn('成功',await qq_actions.execute(event,cfg,self.db,'publish','今天[IMAGE:photo]很开心[QQ_FACE:4',lambda:True));self.assertEqual(calls[-1]['content'],'今天很开心')
  self.assertEqual(qq_actions.plain_text('保留[周末] 😊 [版本:4]'),'保留[周末] 😊 [版本:4]')
  self.assertEqual(qq_actions.plain_text('好[qq_face:4]\n明天见'),'好\n明天见')
  event.get_message_str=lambda:'你好可爱';event.message_obj.message_id='over-limit'
  self.assertIn('最多40字',await qq_actions.execute(event,cfg,self.db,'signature','鱼'*41+'[QQ_FACE:4]',lambda:True))
 def test_attention_and_fatigue_bypass(self):
  p=Participation();cfg={'attentionEnabled':True,'fatigueEnabled':True,'fatigueMaxReplies':2}
  self.assertTrue(p.allow('g','喝茶',cfg,now=1000));p.outgoing('g','今天喝茶',1000)
  self.assertTrue(p.allow('g','喝茶好呀',cfg,now=1001));self.assertTrue(p.allow('g','明天爬山',cfg,now=1001)) # attention supplies context; fatigue below remains enforced
  p.outgoing('g','喝茶',1002);self.assertFalse(p.allow('g','喝茶',cfg,now=1003));self.assertTrue(p.allow('g','爬山',cfg,directed=True,now=1003))
  self.assertTrue(p.allow('other','爬山',cfg,now=1003));self.assertTrue(p.allow('g','爬山',cfg,now=1400))
 async def test_disabled_or_stale_planner_does_not_request(self):
  provider=NS(text_chat=None)
  self.cfg['behavior']['enabled']=False
  self.p.maybe_start(self.cfg,provider,lambda:True,self.var,{},self.now);self.assertIsNone(self.p.task)
  self.cfg['behavior']['enabled']=True
  self.p.maybe_start(self.cfg,provider,lambda:False,self.var,{},self.now);self.assertIsNone(self.p.task)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_plan_budget').fetchone()[0],0)
 async def test_closed_or_replaced_plan_never_saves(self):
  for close in (False,True):
   entered=asyncio.Event();release=asyncio.Event();lease=[True]
   async def chat(**kw):
    entered.set()
    try:await release.wait()
    except asyncio.CancelledError:pass
    return NS(completion_text='{"contact":true,"delay_minutes":30,"topic":"茶"}')
   self.p=Planner(self.db)
   self.p.maybe_start(self.cfg,NS(text_chat=chat),lambda:lease[0],self.var,{},self.now);await entered.wait()
   if close:self.p.close()
   else:lease[0]=False
   release.set();await self.p.task
   self.assertEqual(self.db.execute('SELECT count(*) FROM companion_plans').fetchone()[0],0)
 async def test_profile_publish_and_stale_receipt(self):
  calls=[];lease=[True]
  async def identity():return {'user_id':99999}
  async def action(**kw):calls.append(kw)
  event=NS(get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_extra=lambda key,default=None:default,get_message_str=lambda:'把昵称改成小鱼',message_obj=NS(message_id='profile'),bot=NS(get_login_info=identity,set_qq_profile=action,send_qzone_msg=action))
  cfg={'behavior':{'ownerUin':'12345','qqProfile':True,'qzone':True,'qzoneVisibility':'self'}}
  self.assertIn('成功',await qq_actions.execute(event,cfg,self.db,'nickname','小鱼',lambda:lease[0]));self.assertEqual(calls[-1],{'nickname':'小鱼'})
  event.get_message_str=lambda:'发条说说：今天喝茶';event.message_obj.message_id='publish'
  self.assertIn('成功',await qq_actions.execute(event,cfg,self.db,'publish','今天喝茶',lambda:lease[0]));self.assertEqual(calls[-1],{'content':'今天喝茶','ugc_right':64})
  self.assertIn('不会重复',await qq_actions.execute(event,cfg,self.db,'publish','今天喝茶',lambda:lease[0]));self.assertEqual(len(calls),2)
  async def stale(**kw):lease[0]=False
  event.bot.send_qzone_msg=stale;event.message_obj.message_id='stale'
  self.assertIn('原操作已获协议回执并记录；配置已变化',await qq_actions.execute(event,cfg,self.db,'publish','明天喝茶',lambda:lease[0]))
  self.assertEqual(self.db.execute('SELECT status FROM qq_action_attempts WHERE event=?',('stale',)).fetchone()[0],'confirmed')
 async def test_qzone_fixed_origin_account_and_timeout(self):
  from unittest.mock import patch
  requests=[]
  class Content:
   async def read(self,n):return b'{"code":0,"msglist":[{"tid":"a","content":"tea"}]}'
  class Response:
   status=200;content=Content()
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  class Client:
   def __init__(self,**kwargs):self.kwargs=kwargs
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   def get(self,url,**kwargs):requests.append((url,kwargs));return Response()
  async def cookies(**kw):return {'cookies':'uin=o99999; p_skey=fixture'}
  event=NS(bot=NS(get_cookies=cookies),get_self_id=lambda:'99999')
  with patch('aiohttp.ClientSession',Client):
   result=json.loads(await qq_actions.own_feed(event,lambda:True));self.assertEqual(result['posts'][0]['text'],'tea')
   self.assertEqual(requests[0][0],'https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6');self.assertFalse(requests[0][1]['allow_redirects'])
   async def jsonp_read(self,n):return b'_Callback({"code":0,"msglist":[{"tid":"a","content":"tea"}]});'
   Content.read=jsonp_read
   result=json.loads(await qq_actions.own_feed(event,lambda:True));self.assertEqual(result['posts'][0]['text'],'tea')
   event.get_self_id=lambda:'88888'
   with self.assertRaisesRegex(RuntimeError,'account mismatch'):await qq_actions.own_feed(event,lambda:True)
   self.assertEqual(len(requests),2)
  async def timeout(**kw):raise asyncio.TimeoutError()
  event.bot.get_cookies=timeout
  with self.assertRaisesRegex(qq_actions.QzoneUnavailable,'未请求空间内容'):await qq_actions.own_feed(event,lambda:True)

if __name__=='__main__':unittest.main()
