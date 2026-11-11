import asyncio,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from model_probe_admin import manage

class ProbeTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.db=sqlite3.connect(':memory:');self.db.execute('CREATE TABLE pipeline_events(scope TEXT)');self.db.execute("INSERT INTO pipeline_events VALUES('private:12345678')")
  self.st=NS(db=self.db,shared_context=True)
  self.care=NS(busy=lambda st:False,track=lambda st,t:None)
  self.data={'action':'admin_model_probe','requestId':'11111111-1111-4111-8111-111111111111','administrator':'session-admin','scope':'private:12345678'}
  self.calls=0;self.gate=asyncio.Event()
 async def asyncTearDown(self):self.db.close()
 async def run_probe(self,data):self.calls+=1;await self.gate.wait();return {'qqSend':False}
 async def test_exact_admin_command_and_known_scope(self):
  for change in [{'administrator':''},{'scope':'private:87654321'},{'is_admin':True},{'scope':'invalid'}]:
   self.assertEqual(manage(self.st,{**self.data,**change},self.care,self.run_probe)['status'],400)
  self.assertEqual(self.calls,0)
 async def test_once_and_status_without_reexecution(self):
  self.assertEqual(manage(self.st,self.data,self.care,self.run_probe)['state'],'running')
  self.assertEqual(manage(self.st,self.data,self.care,self.run_probe)['state'],'running')
  other={**self.data,'requestId':'22222222-2222-4222-8222-222222222222'}
  self.assertEqual(manage(self.st,other,self.care,self.run_probe)['status'],409)
  self.gate.set();await self.st._model_probe_task
  status=manage(self.st,{'action':'admin_model_probe_status','requestId':self.data['requestId']},self.care,self.run_probe)
  self.assertEqual(status['state'],'completed');self.assertEqual(self.calls,1)
  self.assertFalse(status['qqSend']);self.assertFalse(status['businessRound']);self.assertFalse(status['toolExecution'])
 async def test_failure_is_not_success_and_busy_refuses(self):
  self.care.busy=lambda st:True
  self.assertEqual(manage(self.st,self.data,self.care,self.run_probe)['status'],409)
  self.care.busy=lambda st:False
  async def fail(data):raise RuntimeError('private exception must not escape')
  manage(self.st,self.data,self.care,fail);await self.st._model_probe_task
  result=manage(self.st,{'action':'admin_model_probe_status','requestId':self.data['requestId']},self.care,fail)
  self.assertEqual(result['state'],'failed');self.assertEqual(result['reason'],'RuntimeError');self.assertNotIn('private',str(result))

 async def test_cancel_before_start_is_terminal(self):
  manage(self.st,self.data,self.care,self.run_probe)
  self.st._model_probe_task.cancel()
  try:await self.st._model_probe_task
  except asyncio.CancelledError:pass
  await asyncio.sleep(0)
  self.assertEqual(self.st._model_probe_jobs[self.data['requestId']]['state'],'cancelled')
  self.assertEqual(self.calls,0)

if __name__=='__main__':unittest.main()
