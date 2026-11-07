import asyncio,json,sys,unittest,tempfile
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from model_cache import routing_key,decision_scope,usage_evidence,record_diagnostic
class Tests(unittest.TestCase):
 def test_absent_cache_usage_is_not_zero_and_explicit_zero_is_preserved(self):
  for raw in (None,{'usage':{'prompt_tokens':10}},{'usage':{'prompt_tokens_details':{'cached_tokens':None}}}):
   result=usage_evidence(NS(raw_completion=raw));self.assertIsNone(result['cachedTokens']);self.assertFalse(result['cacheUsageReported'])
  for cached in (0,8):
   result=usage_evidence(NS(raw_completion=NS(id='id',usage=NS(prompt_tokens=10,completion_tokens=2,prompt_tokens_details=NS(cached_tokens=cached)))))
   self.assertEqual(result['cachedTokens'],cached);self.assertTrue(result['cacheUsageReported'])
 def test_stable_scoped_hint_and_unsupported_provider_unchanged(self):
  lease={'tenantId':'tenant','botId':'bot','revision':1};scope=NS(scope='private:123');extra={'_hosted_lease':lease,'_hosted_pipeline':scope};event=NS(get_extra=lambda k,d=None:extra.get(k,d));provider=NS(provider_config={'api_base':'https://aiapi.codes/v1'})
  first=routing_key(event,provider,'gpt-6.1-sol');self.assertTrue(first.startswith('fb-v1-'));self.assertNotIn('123',first)
  lease['revision']=99;self.assertEqual(first,routing_key(event,provider,'gpt-6.1-sol'))
  scope.scope='group:123';self.assertNotEqual(first,routing_key(event,provider,'gpt-6.1-sol'))
  scope.scope='private:123';lease['tenantId']='other';self.assertNotEqual(first,routing_key(event,provider,'gpt-6.1-sol'))
  self.assertIsNone(routing_key(event,provider,'deepseek-v4.1'))
  provider.provider_config['api_base']='https://unknown.example/v1';self.assertIsNone(routing_key(event,provider,'gpt-6.1-sol'))
  self.assertIsNone(routing_key(None,provider,'gpt-6.1-sol'))
class DiagnosticTests(unittest.TestCase):
 def test_append_only_final_wire_allows_only_exact_prefix_plus_new_suffix(self):
  import model_cache
  provider=NS();rows=[];lease={'tenantId':'tenant-guard','botId':'bot-guard','root':'unused'}
  token=model_cache.begin_attempt(None,provider)
  try:
   candidate=[{'role':'system','content':'fixed'},{'role':'user','content':'old exact message'}]
   self.assertTrue(model_cache.expect_append_only_messages(candidate))
   with patch.object(model_cache,'record_diagnostic',side_effect=lambda owner,row:rows.append(row)):
    valid={'messages':candidate+[{'role':'user','content':'new message'}]}
    model_cache.observe_wire_request(NS(content=json.dumps(valid,ensure_ascii=False).encode()))
    self.assertEqual(rows[-1]['appendOnlyStatus'],'verified')
    changed={'messages':[candidate[0],{'role':'user','content':'rewritten old message'}]}
    with self.assertRaises(model_cache.AppendOnlyWireViolation):
     model_cache.observe_wire_request(NS(content=json.dumps(changed,ensure_ascii=False).encode()))
    self.assertEqual(rows[-1]['appendOnlyStatus'],'blocked')
    for private in ('old exact message','rewritten old message','new message'):
     self.assertNotIn(private,json.dumps(rows))
  finally:model_cache.reset_attempt(token)

 def test_system_contract_binding_is_not_applied_to_background_or_internal_calls(self):
  import model_cache
  provider=NS();guard=object();lease={'tenantId':'tenant','botId':'bot'}
  model_cache.bind_system_contract(provider,guard,lease)
  try:
   token=model_cache.begin_attempt(None,provider)
   try:self.assertIsNone(model_cache._attempt.get()['systemContract'])
   finally:model_cache.reset_attempt(token)
   for source,internal,expected in [('autonomous',True,None),('message',False,guard)]:
    extra={'_hosted_pipeline':NS(source=source),'_hosted_internal_work':internal}
    event=NS(get_extra=lambda key,default=None:extra.get(key,default))
    token=model_cache.begin_attempt(event,provider)
    try:self.assertIs(model_cache._attempt.get()['systemContract'],expected)
    finally:model_cache.reset_attempt(token)
  finally:model_cache.unbind_system_contract(provider)

 def test_final_wire_sees_sdk_tools_and_overrides_without_changing_request(self):
  import model_cache,copy
  provider=NS();rows=[];lease={'tenantId':'tenant-wire','botId':'bot-wire','root':'unused'}
  event=NS(get_extra=lambda key,default=None:lease if key=='_hosted_lease' else NS(scope='group:55555',event_id='event-wire') if key=='_hosted_pipeline' else default)
  token=model_cache.begin_attempt(event,provider)
  try:
   with patch.object(model_cache,'record_diagnostic',side_effect=lambda owner,row:rows.append(row)):
    prepared={'messages':[{'role':'user','content':'private message'}],'model':'model'}
    model_cache.observe_payload(prepared,'append')
    actual={**prepared,'tools':[{'type':'function','function':{'name':'actual_tool','parameters':{'type':'object'}}}],'tool_choice':'auto','reasoning_effort':'medium','prompt_cache_key':'private-routing'}
    request=NS(content=json.dumps(actual).encode());raw=request.content
    model_cache.observe_wire_request(request)
    wire=rows[-1];self.assertEqual(wire['kind'],'wire_request');self.assertEqual(wire['wireIndex'],1)
    self.assertEqual(wire['toolCount'],1);self.assertGreater(wire['toolsChars'],0);self.assertEqual(wire['mediaChars'],0)
    self.assertNotEqual(wire['toolsSha'],rows[0]['toolsSha']);self.assertEqual(request.content,raw)
    model_cache.observe_wire_request(request);same=rows[-1]
    self.assertEqual(same['prefixMessages'],1);self.assertEqual(same['wireIndex'],2)
    changed=copy.deepcopy(actual);changed['tools'][0]['function']['name']='different_tool'
    changed['reasoning_effort']='high';model_cache.observe_wire_request(NS(content=json.dumps(changed).encode()))
    self.assertEqual(rows[-1]['prefixMessages'],1)
    self.assertNotEqual(rows[-1]['toolsSha'],wire['toolsSha']);self.assertNotEqual(rows[-1]['parametersSha'],wire['parametersSha'])
    for private in ('private message','actual_tool','private-routing','different_tool'):self.assertNotIn(private,json.dumps(rows))
    count=len(rows)
    for content in [b'not json',b'{"input":"not chat"}']:
     model_cache.observe_wire_request(NS(content=content))
    self.assertEqual(len(rows),count)
    model_cache.observe_wire_request(NS(content=b'x'*(4*1024*1024+1)))
    self.assertEqual(rows[-1]['diagnosticDetail'],'wire_body_limit');self.assertNotIn('messagesSha',rows[-1])
  finally:model_cache.reset_attempt(token)
  with patch.object(model_cache,'record_diagnostic') as record:
   model_cache.observe_wire_request(NS(content=b'{"messages":[]}'));record.assert_not_called()

 def test_private_bounded_metadata_does_not_include_prompt_credentials_or_other_bot(self):
  with tempfile.TemporaryDirectory() as root:
   lease={'root':root,'botId':'bot-a','tenantId':'tenant-a'}
   self.assertTrue(record_diagnostic(lease,{'kind':'usage','phase':'planning','inputTokens':100,'cachedTokens':None,'systemSha':'hash','apiKey':'secret','prompt':'private text','body':{'key':'secret'},'botId':'other'}))
   path=Path(root)/'model-diagnostics.jsonl';row=json.loads(path.read_text(encoding='utf-8'))
   self.assertEqual(row['botId'],'bot-a');self.assertIsNone(row['cachedTokens'])
   for key in ('apiKey','prompt','body'):self.assertNotIn(key,row)
   path.write_text('x'*(256*1024))
   self.assertTrue(record_diagnostic(lease,{'kind':'context','resetReason':'idle_boundary'}))
   self.assertLess(path.stat().st_size,1024);self.assertTrue(path.with_name('model-diagnostics.previous.jsonl').exists())
   self.assertFalse(record_diagnostic({'root':root,'botId':'other'},{}))
 def test_diagnostic_io_failure_never_breaks_the_call(self):
  with tempfile.TemporaryDirectory() as root,patch('model_cache.os.open',side_effect=PermissionError):
   self.assertFalse(record_diagnostic({'root':root,'botId':'a','tenantId':'t'},{'kind':'usage'}))

class DecisionTests(unittest.IsolatedAsyncioTestCase):
 async def test_request_phase_does_not_change_shared_routing(self):
  import model_cache
  provider=NS(provider_config={'api_base':'https://aiapi.codes/v1'});lease={'tenantId':'tenant','botId':'bot'};keys=[]
  for phase in ['orientation','planning']:
   with decision_scope(provider,lease,'deliberation','work-1',phase):
    keys.append(routing_key(None,provider,'gpt-6-luna'));token=model_cache.begin_attempt(None,provider)
    try:self.assertEqual(model_cache._attempt.get()['phase'],phase);self.assertEqual(model_cache._attempt.get()['workId'],'work-1')
    finally:model_cache.reset_attempt(token)
  self.assertEqual(keys[0],keys[1])
 async def test_decision_scope_is_provider_tenant_and_phase_bound(self):
  provider=NS(provider_config={'api_base':'https://aiapi.codes/v1'});other=NS(provider_config=provider.provider_config)
  lease={'tenantId':'tenant','botId':'bot'}
  self.assertIsNone(routing_key(None,provider,'gpt-6-luna'))
  with decision_scope(provider,lease,'planning'):
   first=routing_key(None,provider,'gpt-6-luna');self.assertTrue(first.startswith('fb-v1-'))
   self.assertIsNone(routing_key(None,other,'gpt-6-luna'))
   with decision_scope(provider,lease,'orientation'):self.assertNotEqual(first,routing_key(None,provider,'gpt-6-luna'))
   with decision_scope(provider,lease,'group_freshness'):self.assertNotEqual(first,routing_key(None,provider,'gpt-6-luna'))
   with decision_scope(provider,lease,'deliberation'):
    shared=routing_key(None,provider,'gpt-6-luna');self.assertIsNotNone(shared);self.assertNotEqual(first,shared)
    with decision_scope(provider,dict(lease,botId='other'),'deliberation'):self.assertNotEqual(shared,routing_key(None,provider,'gpt-6-luna'))
    with decision_scope(other,lease,'deliberation'):self.assertIsNone(routing_key(None,provider,'gpt-6-luna'))
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

class ResponseShapeTests(unittest.TestCase):
 def _raw(self,identifier='resp_1',content='在的',reasoning=None,finish='stop'):
  return NS(id=identifier,usage=NS(prompt_tokens=10,completion_tokens=4,prompt_tokens_details=NS(cached_tokens=0)),
            choices=[NS(finish_reason=finish,message=NS(content=content,reasoning_content=reasoning))])
 def test_visible_content_and_reasoning_are_measured_apart(self):
  result=usage_evidence(NS(raw_completion=self._raw()))
  self.assertEqual(result['contentChars'],2);self.assertIsNone(result['reasoningChars'])
  self.assertFalse(result['reasoningReported']);self.assertEqual(result['finishReason'],'stop')
  self.assertEqual(result['responseIdShape'],'other')
  leaked=usage_evidence(NS(raw_completion=self._raw(content='',reasoning='I am currently focused on',finish='length')))
  self.assertEqual(leaked['contentChars'],0);self.assertEqual(leaked['reasoningChars'],len('I am currently focused on'))
  self.assertTrue(leaked['reasoningReported']);self.assertEqual(leaked['finishReason'],'length')
  # An explicit empty reasoning string is a provider statement, absence is not.
  empty=usage_evidence(NS(raw_completion=self._raw(reasoning='')))
  self.assertEqual(empty['reasoningChars'],0);self.assertTrue(empty['reasoningReported'])
 def test_upstream_id_shape_separates_provider_channels(self):
  self.assertEqual(usage_evidence(NS(raw_completion=self._raw(identifier='gen-1793966358-cCBDnju')))['responseIdShape'],'gen-prefixed')
  self.assertEqual(usage_evidence(NS(raw_completion=self._raw(identifier='01a1b0c29ac87702870bad')))['responseIdShape'],'other')
 def test_missing_or_odd_payload_never_raises(self):
  for raw in (None,{},NS(choices=None),NS(choices=[]),NS(choices=[NS()]),{'id':'x','choices':[{'message':{'content':None}}]}):
   result=usage_evidence(NS(raw_completion=raw))
   self.assertIn('contentChars',result);self.assertIn('responseIdShape',result)
 def test_new_fields_survive_the_diagnostic_whitelist(self):
  with tempfile.TemporaryDirectory() as root:
   lease={'root':root,'botId':'bot','tenantId':'tenant'}
   row=usage_evidence(NS(raw_completion=self._raw(reasoning='thinking text')))
   self.assertTrue(record_diagnostic(lease,{'kind':'usage',**row}))
   stored=json.loads((Path(root)/'model-diagnostics.jsonl').read_text(encoding='utf-8'))
   for key in ('contentChars','reasoningChars','reasoningReported','finishReason','responseIdShape'):
    self.assertIn(key,stored)
   self.assertNotIn('thinking text',json.dumps(stored))
if __name__=='__main__':unittest.main()
