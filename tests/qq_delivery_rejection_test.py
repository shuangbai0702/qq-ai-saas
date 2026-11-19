import ast,asyncio,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'adapters/astrbot'))
import bot_pipeline
class Failure(Exception):
 def __init__(self,result):self.result=result
class Delivery(unittest.IsolatedAsyncioTestCase):
 def test_precise_rejection_and_unknown(self):
  for error in [TimeoutError(),RuntimeError('Ban state forbit write req'),Failure({'status':'failed','retcode':1200,'message':'other error'}),Failure({'status':'ok','retcode':0,'message':'Ban state forbit write req'})]:
   self.assertEqual(bot_pipeline.protocol_send_failure(error)['status'],'unknown')
  f=bot_pipeline.protocol_send_failure(Failure({'status':'failed','retcode':1200,'message':'EventRet: result=-10203 errMsg=Ban state forbit write req'}))
  self.assertEqual(f,{'status':'rejected','reason':'qq_write_restricted','retcode':1200})
 def test_only_confirmed_receipt_clears_failure(self):
  tree=ast.parse((ROOT/'adapters/astrbot/behavior.py').read_text(encoding='utf-8'))
  node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='note_message_receipt')
  extra={'_hosted_send_failure':{'reason':'qq_write_restricted'}};metrics={'a':{'lastDelivery':{'reason':'qq_write_restricted'}}}
  event=NS(get_platform_id=lambda:'a',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.__setitem__(k,v))
  ns={'bot_pipeline':bot_pipeline,'current':lambda e:True,'metrics':metrics,'time':time,'stores':{},'setting':lambda e:None}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'receipt','exec'),ns)
  with self.assertRaises(RuntimeError):ns['note_message_receipt'](event,{},'send_private_msg',{'user_id':1})
  self.assertEqual(metrics['a']['lastDelivery']['reason'],'qq_write_restricted')
  ns['note_message_receipt'](event,{'message_id':123},'send_private_msg',{'user_id':1})
  self.assertIsNone(extra['_hosted_send_failure']);self.assertEqual(metrics['a']['lastDelivery']['status'],'confirmed')
 async def test_boundary_preserves_error_and_fences_late_writes(self):
  tree=ast.parse((ROOT/'adapters/astrbot/behavior.py').read_text(encoding='utf-8'))
  node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='action_original')
  extra={};client=object();lease={};live=[lease];metrics={}
  event=NS(bot=client,get_platform_id=lambda:'a',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.__setitem__(k,v))
  error=Failure({'status':'failed','retcode':1200,'message':'Ban state forbit write req'})
  stale=[False]
  async def native(*a,**kw):
   if stale[0]:live[0]={}
   raise error
  fake=NS(record_diagnostic=lambda *a:None)
  from unittest.mock import patch
  namespace={'current_message_event':NS(get=lambda:event),'setting':lambda e:live[0],'current':lambda e:True,'metrics':metrics,'protocol_action_original':native,'bot_pipeline':bot_pipeline,'time':time}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'boundary','exec'),namespace)
  with patch.dict(sys.modules,{'model_cache':fake}):
   with self.assertRaises(Failure) as caught:await namespace['action_original'](client,'send_private_msg')
   self.assertIs(caught.exception,error);self.assertEqual(metrics['a']['lastDelivery']['reason'],'qq_write_restricted');self.assertNotIn('b',metrics)
   metrics.clear();stale[0]=True
   with self.assertRaises(Failure):await namespace['action_original'](client,'send_private_msg')
   self.assertEqual(metrics,{})
   live[0]=lease;stale[0]=False
   with self.assertRaises(Failure):await namespace['action_original'](client,'get_status')
   self.assertEqual(metrics,{})
if __name__=='__main__':unittest.main()
