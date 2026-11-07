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
from astrbot.core.message.components import Plain,Image,Record
from behavior import install_runtime
import autonomy,initiative


async def run():
 failures=[];receipts=[];execute_calls=[]
 async def native_execute(self,event):execute_calls.append(event)
 async def transport(event,message):
  st=runtime['stores']['qq'];work=event.get_extra('_hosted_pipeline')
  st.pipeline.sending(work)
  if event.get_extra('_voice_fail') and any(isinstance(c,Record) for c in message.chain):raise TimeoutError('unknown voice receipt')
  st.pipeline.confirmed(work);receipts.append(message)
  if event.get_extra('_replace_lease') and len(receipts)==1:settings['qq']=dict(settings['qq'])
  if event.get_extra('_interrupt') and len(receipts)==1:
   autonomy.note_interaction(st.db,event.get_extra('_hosted_lease')['config'],'group:55555','22222222',time.time())
  if event.get_extra('_reset_memory') and len(receipts)==1:
   st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit()
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
 def voice_profile(event):
  import hashlib
  lease=event.get_extra('_hosted_lease');lease['tenantId']='tenant-a';lease['config']['systemPrompt']='voice-persona'
  root=Path(lease['root']);(root/'reply-voice').mkdir(parents=True,exist_ok=True);data=b'fixture-mp3';(root/'reply-voice/short.mp3').write_bytes(data)
  (root/'reply-voice.json').write_text(json.dumps({'version':1,'botId':lease['botId'],'tenantId':lease['tenantId'],'promptSha256':hashlib.sha256(b'voice-persona').hexdigest(),'default':'short','bodyLengths':[2,3,5],'prefix':'！？','suffix':'？！','assets':{'short':{'file':'short.mp3','sha256':hashlib.sha256(data).hexdigest()}}}))
 async def voice_bubbles():
  receipts.clear();event,extra,st,work=fresh(message='voice-five');voice_profile(event)
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('！？奶娃？！\n\n！？牛批？！\n\n！？丢人不？！\n\n！？给我看看啦？！\n\n[VOICE:short]')]))
  st.pipeline.finish(work,{})
  assert len(receipts)==5 and work.confirmed_messages==5 and work.status=='sent'
  assert [c.text for m in receipts[:-1] for c in m.chain if isinstance(c,Plain)]==['！？奶娃？！','！？牛批？！','！？丢人不？！','！？给我看看啦？！']
  assert len(receipts[-1].chain)==1 and isinstance(receipts[-1].chain[0],Record)
 async def voice_stale():
  receipts.clear();event,extra,st,work=fresh(message='voice-stale');voice_profile(event);extra['_replace_lease']=True
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('！？奶娃？！\n\n[VOICE:short]')]))
  assert len(receipts)==1 and not any(isinstance(c,Record) for m in receipts for c in m.chain)
 async def voice_unknown():
  receipts.clear();event,extra,st,work=fresh(message='voice-unknown');voice_profile(event);extra['_voice_fail']=True
  try:await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('！？奶娃？！\n\n[VOICE:short]')]))
  except TimeoutError:work.stop('TimeoutError','error')
  else:raise AssertionError('unknown receipt was swallowed')
  st.pipeline.finish(work,{})
  assert len(receipts)==1 and work.status=='partial' and work.attempted==2 and work.confirmed==1
 async def semantic_bubbles():
  receipts.clear();event,extra,st,work=fresh(message='semantic')
  now=time.time();st.db.execute("INSERT OR REPLACE INTO autonomy_groups(scope,checked,version,event) VALUES('group:55555',?,0,'semantic')",(now,));st.db.commit()
  extra['_hosted_group_decision']={'scope':'group:55555','checked':now,'version':0}
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('第一段。[QQ_BREAK]第二段。[QQ_BREAK]第三段。'),Image.fromBytes(b'fixture')]))
  st.pipeline.finish(work,{})
  assert len(receipts)==3,'own first receipt invalidated the rest of this admitted output'
  assert not any(isinstance(c,Image) for msg in receipts[:-1] for c in msg.chain)
  assert sum(isinstance(c,Image) for msg in receipts for c in msg.chain)==1,'last-bubble image must arrive once'
  assert work.status=='sent' and work.confirmed_messages==3
 async def interruption_still_fences():
  receipts.clear();event,extra,st,work=fresh(message='interrupted')
  now=time.time();st.db.execute("INSERT OR REPLACE INTO autonomy_groups(scope,checked,version,event) VALUES('group:55555',?,0,'interrupted')",(now,));st.db.commit()
  extra.update({'_hosted_group_decision':{'scope':'group:55555','checked':now,'version':0},'_interrupt':True})
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('已送出。[QQ_BREAK]不应送出的旧后续。')]))
  st.pipeline.finish(work,{})
  assert len(receipts)==1 and work.status=='partial','new interaction during dispatch must still invalidate remaining bubbles'
  assert extra.get('_hosted_freshness_history')=='已送出。','native history must keep only the confirmed prefix even without a freshness rewrite'
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
 async def reset_fences_old_context_output():
  receipts.clear();event,extra,st,work=fresh(message='memory-reset-between-bubbles')
  extra.update({'_hosted_memory_epoch':st.reflection.epoch(),'_reset_memory':True})
  await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('已确认。[QQ_BREAK]旧记忆不应继续发。')]))
  st.pipeline.finish(work,{})
  assert len(receipts)==1 and work.status=='partial' and work.confirmed==1,'reset lost a receipt or allowed stale follow-up'
  for completion in (False,True):
   receipts.clear();event,extra,st,work=fresh(message='memory-reset-before-send-'+str(completion))
   extra['_hosted_memory_epoch']=st.reflection.epoch()
   if completion:extra['_hosted_tool_completion']={'fixture':True}
   st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit()
   await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('不应使用已撤销的记忆。')]))
   assert receipts==[],'completion bypassed the old-memory output fence'
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
  entered,release=asyncio.Event(),asyncio.Event();original_wait=asyncio.wait_for
  async def held_wait(awaitable,timeout):
   if timeout==.1:
    entered.set();await release.wait()
    awaitable.close()
    raise asyncio.TimeoutError
   return await original_wait(awaitable,timeout)
  try:
   with patch('behavior.asyncio.wait_for',held_wait):
    first=asyncio.create_task(PipelineScheduler.execute(None,event))
    await asyncio.wait_for(entered.wait(),3)
    await PipelineScheduler.execute(None,selected);release.set();await first
   rows=st.db.execute('SELECT state FROM autonomy_sends').fetchall()
   assert rows==[('released',)],'combined silent turn leaked the selected event reservation: '+repr(rows)
   assert st.active==0
  finally:release.set();core.provider_manager.inst_map.clear()
 async def stale_native_history_cannot_commit_after_forget_or_rebind():
  from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import InternalAgentSubStage
  from astrbot.core.agent.message import Message
  from astrbot.core.provider.entities import LLMResponse
  saved=[]
  async def persist(*args,**kwargs):saved.append(kwargs)
  saver=NS(conv_manager=NS(update_conversation=persist))
  for mode in ('current','forget','rebind'):
   event,extra,st,work=fresh(group='',message='native-history-'+mode)
   event.unified_msg_origin='qq:FriendMessage:12345678'
   extra['_hosted_memory_epoch']=st.reflection.epoch()
   if mode=='forget':st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit()
   if mode=='rebind':settings['qq']=dict(settings['qq'])
   before=len(saved)
   await InternalAgentSubStage._save_to_history(saver,event,NS(conversation=NS(cid='fixture')),LLMResponse(role='assistant',completion_text='原生记录'),[Message(role='user',content='原问题'),Message(role='assistant',content='原生记录')],None)
   assert len(saved)==before+int(mode=='current'),'stale native history committed after '+mode
 try:
  await check('four text bubbles and one voice share a confirmed output plan',voice_bubbles)
  await check('configuration replacement fences the trailing voice',voice_stale)
  await check('unknown voice receipt stays partial and is not resent',voice_unknown)
  await check('semantic multi-bubble receipt and last image',semantic_bubbles)
  await check('new interaction still invalidates stale output',interruption_still_fences)
  await check('context assembly always returns its active slot',context_failure_releases_activity)
  await check('memory reset fences old output and preserves dispatched receipts',reset_fences_old_context_output)
  await check('combined silent work refunds the selected reservation',combined_silent_work_refunds_selected_reservation)
  await check('native history commits only to the current lease and memory epoch',stale_native_history_cannot_commit_after_forget_or_rebind)
 finally:runtime['close']()
 if failures:raise AssertionError('\n'.join(failures))

asyncio.run(run());temp.cleanup()
