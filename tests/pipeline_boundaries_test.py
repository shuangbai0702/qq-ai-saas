"""Native adapter boundaries with deterministic receipts and failure injection."""
import asyncio,copy,json,os,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
temp=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=temp.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.event import MessageChain
from astrbot.core.message.components import Plain,Image
from behavior import install_runtime
import autonomy,initiative


async def run():
 failures=[];receipts=[];execute_calls=[]
 async def native_execute(self,event):execute_calls.append(event)
 async def transport(event,message):
  st=runtime['stores']['qq'];work=event.get_extra('_hosted_pipeline')
  st.pipeline.sending(work);st.pipeline.confirmed(work);receipts.append(message)
  if event.get_extra('_interrupt') and len(receipts)==1:
   autonomy.note_interaction(st.db,event.get_extra('_hosted_lease')['config'],'group:55555','22222222',time.time())
  return 'confirmed'
 PipelineScheduler.execute=native_execute;AiocqhttpMessageEvent.send=transport
 settings={};core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}))
 runtime=install_runtime(core,settings)
 def fresh(group='55555',message='boundary',text='真实输入',**behavior):
  runtime['release']('qq')
  config={'proactive':False,'behavior':{'enabled':True,'memory':False,'memeEnabled':False,'qqFaces':False,'splitMessages':True,'sendIntervalMs':0,'typingSimulation':False,'styleLearning':False,'fatigueEnabled':False,'quietStart':0,'quietEnd':0,'groupParticipationEnabled':True,'groupParticipationGroups':['55555'],**behavior}}
  lease={'root':str(Path(temp.name)/message),'botId':'logical-bot','generation':1,'config':config};settings['qq']=lease
  extra={'_hosted_lease':lease,'_hosted_generation':1}
  event=NS(message_obj=NS(message_id=message,timestamp=time.time(),raw_message={}),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:group,get_sender_id=lambda:'12345678',get_sender_name=lambda:'真实发送者',get_message_str=lambda:text,get_messages=lambda:[Plain(text)],get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}))
  runtime['manage']('qq',{'action':'list'});st=runtime['stores']['qq']
  scope='group:'+group if group else 'private:12345678'
  initiative.incoming(st.db,scope,'12345678','90000',text,time.time()-1)
  work=st.pipeline.bind(event,lease)
  return event,extra,st,work
 async def check(name,fn):
  try:await fn();print('PASS '+name)
  except AssertionError as error:failures.append(name+': '+str(error));print('FAIL '+failures[-1])
 async def semantic_bubbles():
  receipts.clear();event,extra,st,work=fresh(message='semantic')
  now=time.time();st.db.execute("INSERT OR REPLACE INTO autonomy_groups(scope,checked,version,event) VALUES('group:55555',?,0,'semantic')",(now,));st.db.commit()
  extra['_hosted_group_decision']={'scope':'group:55555','checked':now,'version':0}
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('第一段。\n第二段。\n第三段。'),Image.fromBytes(b'fixture')]))
  st.pipeline.finish(work,{})
  assert len(receipts)==3,'own first receipt invalidated the rest of this admitted output'
  assert not any(isinstance(c,Image) for msg in receipts[:-1] for c in msg.chain)
  assert sum(isinstance(c,Image) for msg in receipts for c in msg.chain)==1,'last-bubble image must arrive once'
  assert work.status=='sent' and work.confirmed_messages==3
 async def interruption_still_fences():
  receipts.clear();event,extra,st,work=fresh(message='interrupted')
  now=time.time();st.db.execute("INSERT OR REPLACE INTO autonomy_groups(scope,checked,version,event) VALUES('group:55555',?,0,'interrupted')",(now,));st.db.commit()
  extra.update({'_hosted_group_decision':{'scope':'group:55555','checked':now,'version':0},'_interrupt':True})
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('已送出。\n不应送出的旧后续。')]))
  st.pipeline.finish(work,{})
  assert len(receipts)==1 and work.status=='partial','new interaction during dispatch must still invalidate remaining bubbles'
 async def context_failure_releases_activity():
  event,extra,st,work=fresh(group='',message='context-failure')
  # execute owns bind; use a new protocol ID, not the prepared fixture work.
  event.message_obj.message_id='context-failure-actual';extra.pop('_hosted_pipeline',None)
  before=len(execute_calls)
  with patch.object(st.social,'context',side_effect=OSError('controlled catalog failure')):
   try:await PipelineScheduler.execute(None,event)
   except OSError:pass
   else:raise AssertionError('fixture did not reach the context boundary')
  assert st.active==0,'context assembly failure leaked the per-bot busy slot'
  event.message_obj.message_id='next-actual';extra.pop('_hosted_pipeline',None)
  await PipelineScheduler.execute(None,event)
  assert len(execute_calls)==before+1 and st.active==0,'next message must remain serviceable after the failure'
 async def combined_silent_work_refunds_selected_reservation():
  event,extra,st,work=fresh(message='combined',burstDebounceMs=100)
  event.message_obj.message_id='combined-first';extra.pop('_hosted_pipeline',None)
  selected=copy.copy(event);selected_extra={'_hosted_lease':settings['qq'],'_hosted_generation':1}
  selected.message_obj=NS(message_id='combined-last',timestamp=time.time(),raw_message={})
  selected.get_extra=lambda k,d=None:selected_extra.get(k,d)
  selected.set_extra=lambda k,v:selected_extra.update({k:v})
  selected.get_message_str=lambda:'合并后实际选中的消息'
  selected.get_messages=lambda:[Plain('合并后实际选中的消息')]
  async def chat(**kwargs):return NS(completion_text=json.dumps({'reply':True,'target':'open','participation':'open','information':'substantive','interest':'strong','reason_code':'shared_interest','confidence':'high'}))
  core.provider_manager.inst_map['provider-qq']=NS(text_chat=chat)
  entered,release=asyncio.Event(),asyncio.Event();original_sleep=asyncio.sleep
  async def held_sleep(seconds):
   if seconds==.1:entered.set();await release.wait()
   else:await original_sleep(seconds)
  try:
   with patch('behavior.asyncio.sleep',held_sleep):
    first=asyncio.create_task(PipelineScheduler.execute(None,event))
    await asyncio.wait_for(entered.wait(),3)
    await PipelineScheduler.execute(None,selected);release.set();await first
   rows=st.db.execute('SELECT state FROM autonomy_sends').fetchall()
   assert rows==[('released',)],'combined silent turn leaked the selected event reservation: '+repr(rows)
   assert st.active==0
  finally:release.set();core.provider_manager.inst_map.clear()
 try:
  await check('semantic multi-bubble receipt and last image',semantic_bubbles)
  await check('new interaction still invalidates stale output',interruption_still_fences)
  await check('context assembly always returns its active slot',context_failure_releases_activity)
  await check('combined silent work refunds the selected reservation',combined_silent_work_refunds_selected_reservation)
 finally:runtime['close']()
 if failures:raise AssertionError('\n'.join(failures))

asyncio.run(run());temp.cleanup()
