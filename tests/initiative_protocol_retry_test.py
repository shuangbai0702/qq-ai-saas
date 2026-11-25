import asyncio,contextvars,json,sys,types,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import initiative,bilibili_tools,model_cache
from aiocqhttp.exceptions import ApiNotAvailable


class InitiativeProtocolRetryTest(unittest.IsolatedAsyncioTestCase):
 async def run_sweep(self,*,replace=False,late=False):
  clock=[0];ticks=[0];calls=[];recorded=[];metrics={}
  def lease(uin):return {'config':{'proactive':True},'expectedUin':uin,'generation':1}
  settings={'a':lease('91000001'),'b':lease('91000002')}
  old=settings['a']
  async def unavailable(**kwargs):
   calls.append(clock[0])
   if late:settings['a']=lease('91000001')
   raise ApiNotAvailable
  bots={'a':NS(get_status=AsyncMock(side_effect=unavailable)),'b':NS(get_status=AsyncMock(return_value={'online':False}))}
  platforms=[NS(meta=lambda i=i:NS(id=i),bot=bots[i]) for i in settings]
  core=NS(provider_manager=NS(inst_map={'provider-a':object(),'provider-b':object()}),event_bus=NS(stopped_platforms=set()),platform_manager=NS(get_insts=lambda:platforms))
  stores={i:NS(active=0,planner=NS(maybe_start=lambda *a,**k:None),presence=NS(pending=lambda *a,i=i:{'payload':json.dumps({'post_type':'message','message_type':'group','sub_type':'normal','self_id':settings[i]['expectedUin'],'group_id':777777,'user_id':12345678,'message_id':1,'message':[]})})) for i in settings}
  real_sleep=asyncio.sleep
  async def tick(_):
   ticks[0]+=1;clock[0]+=5
   if replace and ticks[0]==8:settings['a']=lease('91000001')
   if ticks[0]>80:raise asyncio.CancelledError
   await real_sleep(0)
  modules={}
  for name,values in {
   'astrbot.core.platform.astrbot_message':{'AstrBotMessage':NS,'MessageMember':NS,'Group':NS},
   'astrbot.core.platform.message_type':{'MessageType':NS()},
   'astrbot.core.message.components':{'Plain':NS},
   'astrbot.core.platform.astr_message_event':{'current_message_event':contextvars.ContextVar('test_event',default=None)},
  }.items():
   m=types.ModuleType(name);m.__dict__.update(values);modules[name]=m
  with patch.dict(sys.modules,modules),patch.object(initiative.asyncio,'sleep',tick),patch.object(initiative,'time',NS(monotonic=lambda:clock[0],time=lambda:clock[0])),patch.object(initiative,'resume_scope_input',AsyncMock(return_value=False)),patch.object(bilibili_tools,'resume_completion',AsyncMock(return_value=False)),patch.object(model_cache,'record_execution_error',lambda s,e,**kw:recorded.append(type(e).__name__)):
   task=initiative.start(core,settings,lambda i,s:stores[i],metrics)
   with self.assertRaises(asyncio.CancelledError):await task
  return calls,bots,recorded,metrics,old

 async def test_protocol_outage_backs_off_without_blocking_other_bots(self):
  calls,bots,recorded,metrics,_=await self.run_sweep()
  self.assertEqual(calls,[5,35,95,215]);self.assertEqual(len(recorded),4)
  self.assertEqual(bots['b'].get_status.await_count,80)
  self.assertEqual(metrics['a']['proactiveProtocolFailure']['action'],'get_status')
  self.assertEqual(bots['a'].get_status.await_args.kwargs,{'self_id':'91000001'})

 async def test_new_generation_does_not_inherit_old_protocol_backoff(self):
  calls,*_=await self.run_sweep(replace=True)
  self.assertIn(40,calls)

 async def test_late_old_owner_error_cannot_poison_new_owner(self):
  calls,bots,recorded,metrics,old=await self.run_sweep(late=True)
  self.assertEqual(len(calls),80);self.assertEqual(recorded,[]);self.assertEqual(metrics.get('a'),{})


if __name__=='__main__':unittest.main()
