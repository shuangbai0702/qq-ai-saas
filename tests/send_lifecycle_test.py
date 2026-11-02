"""Native SDK send wrapper with a controlled protocol-await barrier (no QQ I/O)."""
import asyncio,os,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
temp=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=temp.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.event import MessageChain
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import current_message_event
from behavior import install_runtime

async def run():
 entered,release=asyncio.Event(),asyncio.Event()
 captured=[]
 async def transport(self,message):
  if self.get_extra('_partial_fixture',False):
   st=runtime['stores']['qq'];work=self.get_extra('_hosted_pipeline');st.pipeline.sending(work)
   if work.attempted==2:raise asyncio.TimeoutError()
   st.pipeline.confirmed(work);return 'committed'
  captured.append(message);entered.set();await release.wait();return 'committed'
 AiocqhttpMessageEvent.send=transport
 settings={};core=SimpleNamespace(event_bus=SimpleNamespace(stopped_platforms=set()))
 runtime=install_runtime(core,settings)
 try:
  for replace in (False,True):
   entered.clear();release.clear()
   settings['qq']={'root':temp.name,'botId':'bot-A','generation':1,'config':{}}
   extras={'_hosted_generation':1,'_hosted_lease':settings['qq']}
   event=SimpleNamespace(message_obj=SimpleNamespace(message_id='send-fixture'),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:'',get_sender_id=lambda:'12345678',get_message_str=lambda:'你好',get_extra=lambda key,default=None:extras.get(key,default),set_extra=lambda key,value:extras.update({key:value}))
   pending=asyncio.create_task(AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('正常回复')])))
   await asyncio.wait_for(entered.wait(),5);settings.pop('qq');runtime['release']('qq')
   if replace:settings['qq']={'root':temp.name,'botId':'bot-A','generation':2,'config':{}}
   release.set();assert await pending=='committed'
   assert runtime['stores']=={},'late send recreated released SQLite store'
   assert runtime['metrics']=={},'late send wrote into next generation metrics'
  # Same generation, new configuration: the old model result must not reach
  # the protocol at all. Removal/re-addition must not revive the old event.
  for replacement in ({'generation':1,'revision':2},{'generation':2,'revision':1},None):
   entered.clear();release.clear()
   old={'root':temp.name,'botId':'bot-A','generation':1,'config':{}}
   settings['qq']=old;extras={'_hosted_generation':1,'_hosted_lease':old}
   event=SimpleNamespace(get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_extra=lambda key,default=None:extras.get(key,default))
   settings.pop('qq')
   if replacement:settings['qq']={**old,**replacement}
   await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('必须丢弃的旧回复')]))
   assert not entered.is_set(),'stale reply reached protocol before commit'
  # A response may contain several SDK Plain components. The configured limit
  # applies to their combined text, rather than resetting for each component.
  release.set();old={'root':temp.name,'botId':'bot-A','generation':3,'config':{'behavior':{'maxReplyChars':80}}};settings['qq']=old
  extra={'_hosted_generation':3,'_hosted_lease':old}
  event=SimpleNamespace(message_obj=SimpleNamespace(message_id='send-fixture'),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:'',get_sender_id=lambda:'12345678',get_message_str=lambda:'你好',get_extra=lambda key,default=None:extra.get(key,default),set_extra=lambda key,value:extra.update({key:value}))
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('甲'*50),Plain('乙'*50),Plain('丙'*50)]))
  assert ''.join(x.text for x in captured[-1].chain if isinstance(x,Plain))=='甲'*50+'乙'*30,'reply limit reset per component'
  # A memory deletion while a real protocol call is in flight must not put
  # its old question/answer back into the new cross-session pending cache.
  entered.clear();release.clear();st=runtime['stores']['qq']
  extra['_hosted_memory_epoch']=st.reflection.epoch()
  pending=asyncio.create_task(AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('这份旧材料不能重新入库')])));
  await asyncio.wait_for(entered.wait(),5)
  from memory_reflection import invalidate
  invalidate(st.db,'private:12345678','12345678');st.db.commit();release.set();await pending
  assert st.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0]==0,'late protocol completion restored forgotten raw memory'
  # Native send_message awaits media conversion before OneBot dispatch. Hold
  # that exact boundary and prove config replacement suppresses the request.
  calls=[]
  async def delivered(**kwargs):calls.append(kwargs)
  async def parse(message):entered.set();await release.wait();return [{'type':'text','data':{'text':'converted'}}]
  old_parse=AiocqhttpMessageEvent._parse_onebot_json
  AiocqhttpMessageEvent._parse_onebot_json=staticmethod(parse)
  try:
   for stale in (False,True,'optout'):
    entered.clear();release.clear();old={'root':temp.name,'botId':'bot-A','generation':1,'config':{}};settings['qq']=old
    extra={'_hosted_lease':old}
    event=SimpleNamespace(message_obj=SimpleNamespace(message_id='send-fixture'),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:'',get_sender_id=lambda:'12345678',get_extra=lambda key,default=None:extra.get(key,default),set_extra=lambda key,value:extra.update({key:value}))
    if stale=='optout':
     runtime['manage']('qq',{'action':'list'})
     st=runtime['stores']['qq'];st.db.execute("INSERT OR REPLACE INTO contacts(scope,subject,self_id,last_user) VALUES('private:12345678','12345678','90000',1000)");st.db.commit()
     extra.update({'_hosted_initiative':True,'_hosted_last_user':1000})
    token=current_message_event.set(event)
    try:pending=asyncio.create_task(AiocqhttpMessageEvent.send_message(SimpleNamespace(send_private_msg=delivered),MessageChain(chain=[Plain('转换后发送')]),session_id='12345678'))
    finally:current_message_event.reset(token)
    await asyncio.wait_for(entered.wait(),5)
    if stale is True:settings['qq']={**old,'config':{'changed':True}}
    if stale=='optout':runtime['manage']('qq',{'action':'pause_contact','scope':'private:12345678'})
    before=len(calls);release.set();await pending
    assert len(calls)==before+(0 if stale else 1),'late media conversion crossed configuration lease'
    assert extra.get('_hosted_protocol_blocked',False)==bool(stale),'blocked dispatch is visible to the outer send recorder'
  finally:AiocqhttpMessageEvent._parse_onebot_json=old_parse
  # First bubble confirmed, second uncertain: preserve actual interaction
  # timing without recording an unsent suffix as a completed conversation.
  cfg={'behavior':{'splitMessages':True,'typingSimulation':False,'sendIntervalMs':0,'memory':True,'qqFaces':False}}
  lease={'root':temp.name,'botId':'bot-A','generation':1,'config':cfg};settings['qq']=lease
  extra={'_hosted_lease':lease,'_hosted_generation':1,'_partial_fixture':True}
  event=SimpleNamespace(message_obj=SimpleNamespace(message_id='partial-fixture'),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:'55555',get_sender_id=lambda:'12345678',get_message_str=lambda:'继续聊天',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}))
  runtime['manage']('qq',{'action':'list'});st=runtime['stores']['qq']
  import initiative
  before=time.time();initiative.incoming(st.db,'group:55555','12345678','90000','继续聊天',before-1)
  work=st.pipeline.bind(event,lease)
  try:await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('第一条已经到达。\n\n第二条回执超时。')]))
  except asyncio.TimeoutError:pass
  else:raise AssertionError('Second send must remain uncertain')
  st.pipeline.finish(work,runtime['metrics'].setdefault('qq',{}));assert work.status=='partial' and work.confirmed_messages==1
  last_bot,last_output=st.db.execute("SELECT last_bot,last_output FROM contacts WHERE scope='group:55555'").fetchone()
  assert last_bot>=before and last_output=='第一条已经到达。','partial receipt lost actual timing or invented unsent text'
  assert st.presence.snapshot('group:55555')[1]>=before,'partial reply did not refresh local interaction clock'
  assert st.db.execute("SELECT count(*) FROM memory_pending WHERE event='partial-fixture'").fetchone()[0]==0,'failed suffix became completed reply memory'
  # A delivered error notice is not a completed model reply. Keep its actual
  # protocol receipt while exposing the real failure to the common ledger.
  extra={'_hosted_lease':lease,'_hosted_generation':1,'_partial_fixture':True,'_hosted_model_error':True}
  event=SimpleNamespace(message_obj=SimpleNamespace(message_id='model-error-fixture'),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:'',get_sender_id=lambda:'12345678',get_message_str=lambda:'普通消息',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}))
  work=st.pipeline.bind(event,lease)
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('raw upstream failure must remain private')]))
  st.pipeline.finish(work,runtime['metrics'].setdefault('qq',{}))
  assert work.confirmed==1 and work.status=='error' and work.reason=='model_error'
  assert st.db.execute("SELECT count(*) FROM memory_pending WHERE event='model-error-fixture'").fetchone()[0]==0
  print('PASS model failure with delivered sanitized notice remains error and preserves actual receipt')
  print('PASS protocol send late completion after stop and replacement; no store resurrection')
 finally:runtime['close']()
asyncio.run(run());temp.cleanup()
