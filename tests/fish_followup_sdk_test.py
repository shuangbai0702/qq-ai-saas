"""Deterministic follow-up boundary through the actual AstrBot event wrapper."""
import asyncio,json,os,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
tmp=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=tmp.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember,Group
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.message_type import MessageType
from astrbot.core.message.components import Plain,At
from behavior import install_runtime
import behavior,initiative

async def run():
 calls=[]
 async def execute(self,event):calls.append(json.loads(event.get_extra('_hosted_context')))
 PipelineScheduler.execute=execute
 cfg={'trigger':'mention','behavior':{'followupSeconds':120,'followupReplyProbability':.8,'randomReplyProbability':.02,'quietStart':0,'quietEnd':0,'memeEnabled':False}}
 settings={'a':{'botId':'a','generation':1,'root':str(Path(tmp.name)/'a'),'config':cfg}}
 runtime=install_runtime(SimpleNamespace(event_bus=SimpleNamespace(stopped_platforms=set())),settings)
 def event(mention=False):
  msg=AstrBotMessage();msg.type=MessageType.GROUP_MESSAGE;msg.self_id='90000';msg.session_id='12345';msg.message_id=str(time.time_ns());msg.group=Group(group_id='12345');msg.sender=MessageMember('123456','群友');msg.message=[Plain('接着聊')]+([At(qq='90000')] if mention else []);msg.message_str='接着聊';msg.raw_message={}
  e=AiocqhttpMessageEvent('接着聊',msg,PlatformMetadata('aiocqhttp','fixture','a'),'12345',bot=None);e.set_extra('_hosted_lease',settings['a']);e.set_extra('_hosted_generation',1);return e
 old_random=behavior.random.random;old_quiet=initiative.quiet
 try:
  await PipelineScheduler.execute(None,event(True));assert len(calls)==1
  db=runtime['stores']['a'].db;db.execute('UPDATE contacts SET last_bot=?',(time.time(),));db.commit()
  behavior.random.random=lambda:.8
  await PipelineScheduler.execute(None,event());assert len(calls)==1,'80% boundary rejected'
  behavior.random.random=lambda:.79
  await PipelineScheduler.execute(None,event());assert len(calls)==2,'below 80% accepted'
  initiative.quiet=lambda cfg,now:True
  await PipelineScheduler.execute(None,event());assert len(calls)==2,'quiet auto-followup stays silent'
  await PipelineScheduler.execute(None,event(True));assert len(calls)==3,'explicit mention still works'
  assert calls[-1]['identity']['directed_to_bot'] is True
  print('PASS SDK 80% boundary, quiet follow-up suppression and explicit mention')
 finally:behavior.random.random=old_random;initiative.quiet=old_quiet;runtime['close']()
asyncio.run(run());tmp.cleanup()
