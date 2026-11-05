import asyncio,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from model_cache import routing_key,decision_scope
class Tests(unittest.TestCase):
 def test_stable_scoped_hint_and_unsupported_provider_unchanged(self):
  lease={'tenantId':'tenant','botId':'bot','revision':1};scope=NS(scope='private:123');extra={'_hosted_lease':lease,'_hosted_pipeline':scope};event=NS(get_extra=lambda k,d=None:extra.get(k,d));provider=NS(provider_config={'api_base':'https://aiapi.codes/v1'})
  first=routing_key(event,provider,'gpt-6.1-sol');self.assertTrue(first.startswith('fb-v1-'));self.assertNotIn('123',first)
  lease['revision']=99;self.assertEqual(first,routing_key(event,provider,'gpt-6.1-sol'))
  scope.scope='group:123';self.assertNotEqual(first,routing_key(event,provider,'gpt-6.1-sol'))
  scope.scope='private:123';lease['tenantId']='other';self.assertNotEqual(first,routing_key(event,provider,'gpt-6.1-sol'))
  self.assertIsNone(routing_key(event,provider,'deepseek-v4.1'))
  provider.provider_config['api_base']='https://unknown.example/v1';self.assertIsNone(routing_key(event,provider,'gpt-6.1-sol'))
  self.assertIsNone(routing_key(None,provider,'gpt-6.1-sol'))
class DecisionTests(unittest.IsolatedAsyncioTestCase):
 async def test_decision_scope_is_provider_tenant_and_phase_bound(self):
  provider=NS(provider_config={'api_base':'https://aiapi.codes/v1'});other=NS(provider_config=provider.provider_config)
  lease={'tenantId':'tenant','botId':'bot'}
  self.assertIsNone(routing_key(None,provider,'gpt-6-luna'))
  with decision_scope(provider,lease,'planning'):
   first=routing_key(None,provider,'gpt-6-luna');self.assertTrue(first.startswith('fb-v1-'))
   self.assertIsNone(routing_key(None,other,'gpt-6-luna'))
   with decision_scope(provider,lease,'orientation'):self.assertNotEqual(first,routing_key(None,provider,'gpt-6-luna'))
   with decision_scope(provider,dict(lease,tenantId='other'),'planning'):self.assertNotEqual(first,routing_key(None,provider,'gpt-6-luna'))
   self.assertEqual(first,routing_key(None,provider,'gpt-6-luna'))
  self.assertIsNone(routing_key(None,provider,'gpt-6-luna'))

 async def test_cancel_and_concurrent_tasks_never_transfer_scope(self):
  provider=NS(provider_config={'api_base':'https://aiapi.codes/v1'});ready=asyncio.Event();release=asyncio.Event();observed=[]
  async def worker():
   try:
    with decision_scope(provider,{'tenantId':'a','botId':'a'},'planning'):
     observed.append(routing_key(None,provider,'gpt-6-luna'));ready.set();await release.wait()
   finally:observed.append(routing_key(None,provider,'gpt-6-luna'))
  task=asyncio.create_task(worker());await ready.wait()
  self.assertIsNone(routing_key(None,provider,'gpt-6-luna'))
  task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertIsNone(observed[-1]);self.assertIsNotNone(observed[0])
  with decision_scope(provider,{'tenantId':'a','botId':'a'},'unsupported'):self.assertIsNone(routing_key(None,provider,'gpt-6-luna'))

if __name__=='__main__':unittest.main()
