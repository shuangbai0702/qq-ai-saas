import asyncio,contextvars,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from model_probe_admin import manage,request

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

 async def test_request_uses_shared_contract_before_provider_hooks(self):
  import model_cache
  from shared_context import JOURNAL_SCOPE
  from system_contract import SystemContract,system_vector_sha
  self.db.executescript('''CREATE TABLE fishbot_shared_context(id INTEGER,state TEXT);
   INSERT INTO fishbot_shared_context VALUES(1,'active');
   CREATE TABLE fishbot_readonly_journals(scope TEXT,state TEXT);
   INSERT INTO fishbot_readonly_journals VALUES('"bot_shared"','ready');''')
  contract=SystemContract(self.db)
  wire=[{'role':'system','content':'committed shared system'}]
  old=[{'role':'system','content':'retired physical scope system'}]
  for scope,messages in [(JOURNAL_SCOPE,wire),(self.data['scope'],old)]:
   self.db.execute('INSERT INTO fishbot_system_scope_contract VALUES(?,?,?)',(scope,system_vector_sha(messages),1))
  self.db.commit()
  before=self.db.execute('SELECT * FROM fishbot_system_scope_contract ORDER BY scope').fetchall()
  lease={'config':{'systemPrompt':'committed shared system'}}
  extras={};event=NS(set_extra=lambda k,v:extras.__setitem__(k,v),get_extra=lambda k:extras.get(k),get_self_id=lambda:'12345678')
  current=contextvars.ContextVar('probe_test_event')
  self.st.active=0;self.st.slot_changed=asyncio.Event();self.st.model_journals={JOURNAL_SCOPE:NS(last=NS(wire=wire))}
  async def chat(**kw):
   self.assertIs(current.get(),event)
   token=model_cache.begin_attempt(event,provider,system_contract=contract)
   try:
    attempt=model_cache._attempt.get()
    self.assertEqual(attempt['scope'],JOURNAL_SCOPE)
    self.assertEqual(extras['_hosted_pipeline'].scope,self.data['scope'])
    contract.reserve(wire,None,attempt['attemptId'],2,scope=attempt['scope'])
   finally:model_cache._attempt.reset(token)
   return NS(completion_text='OK',raw_completion={'usage':{'prompt_tokens':1,'completion_tokens':1}})
  provider=NS(text_chat=chat)
  brain=NS(settings={'bot':lease},make_event=lambda *args:event,prepare_context=lambda *args:{'identity':{}},provider=lambda id:provider)
  with patch.dict(sys.modules,{'astrbot.core.platform.astr_message_event':NS(current_message_event=current)}):
   result=await request(brain,'bot',self.st,lease,self.data)
  self.assertTrue(result['journalCommitted']);self.assertFalse(result['qqSend'])
  self.assertEqual(self.st.active,0);self.assertTrue(self.st.slot_changed.is_set())
  self.assertEqual(before,self.db.execute('SELECT * FROM fishbot_system_scope_contract ORDER BY scope').fetchall())
  self.assertIsNone(current.get(None))

if __name__=='__main__':unittest.main()
