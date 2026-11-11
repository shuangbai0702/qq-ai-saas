"""Hosted native declaration and effect fences; no real transport/provider."""
import asyncio,copy,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from astrbot.core.tools.message_tools import SendMessageToUserTool
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
import tenant_tools

class Event:
 unified_msg_origin='bot:FriendMessage:42'
 def get_platform_id(self):return 'bot'
 def get_extra(self,key,*args):
  return {'_hosted_lease':{'generation':1,'revision':1},'_hosted_internal_work':True,'_hosted_tool_completion':True}.get(key)

class HostedSend(unittest.IsolatedAsyncioTestCase):
 def test_nonstandard_same_name_fails_before_old_declaration_can_escape(self):
  for schema in ({'type':'object','properties':{}}, {'type':'object','properties':{'messages':[]}}):
   tool=SimpleNamespace(name='send_message_to_user',description='old path/cross-session capability',parameters=schema)
   before=copy.deepcopy(tool.__dict__)
   with self.assertRaisesRegex(ValueError,'hosted_native_send_schema_mismatch'):
    tenant_tools.hosted_send_definition(tool)
   self.assertEqual(tool.__dict__,before)
 async def test_http_wire_contains_hosted_declaration_from_real_runner(self):
  import httpx
  from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
  from native_internal import RunControl,run
  tenant_tools.install({'bot':{'generation':1,'revision':1}})
  requests=[]
  async def handler(request):
   self.assertEqual(request.url.host,'fixture.invalid')
   requests.append(json.loads(request.content))
   return httpx.Response(200,json={'id':'hosted-send-wire','model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':'fixture complete'},'finish_reason':'stop'}]})
  with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda provider,config:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
   provider=ProviderOpenAIOfficial({'id':'hosted-send-wire','type':'openai_chat_completion','model':'fixture','key':['fixture'],'api_base':'https://fixture.invalid/v1'}, {})
  try:
   result=await run(provider,RunControl(model_calls=2,tool_calls=1,tokens=24000,seconds=10,current=lambda:True),'inspect fixture declaration','synthetic persona',tools=[SendMessageToUserTool()],context=SimpleNamespace(event=Event()))
   self.assertEqual(result['status'],'completed')
   self.assertEqual(len(requests),1)
   function=next(t['function'] for t in requests[0]['tools'] if t['function']['name']=='send_message_to_user')
   parameters=function['parameters'];component=parameters['properties']['messages']['items']
   self.assertNotIn('path',component['properties'])
   self.assertFalse(component['additionalProperties'])
   self.assertIn('不支持跨会话',parameters['properties']['session']['description'])
   self.assertIn('unknown',requests[0]['messages'][0]['content'])
   self.assertIn('当前会话',function['description'])
   output=ROOT/'.local/prompt-implementation-20261112/hosted-send-wire-B.json'
   output.parent.mkdir(parents=True,exist_ok=True)
   output.write_text(json.dumps(requests[0],ensure_ascii=False,indent=2),encoding='utf-8')
  finally:await provider.client.close()
 async def test_real_sdk_schema_is_copied_before_runner_and_guard_retained(self):
  from inner_tools import completion_guard
  event=Event();original=SendMessageToUserTool();schema=copy.deepcopy(original.parameters)
  guarded=completion_guard(original,event)
  request=SimpleNamespace(func_tool=ToolSet(tools=[guarded]))
  seen=[]
  async def reset(self,*,run_context,request):seen.append(request.func_tool.get_tool('send_message_to_user'))
  with patch.object(ToolLoopAgentRunner,'reset',reset):
   tenant_tools._install_tool_catalog(lambda e:True)
   await ToolLoopAgentRunner().reset(run_context=SimpleNamespace(context=SimpleNamespace(event=event)),request=request)
  tool=seen[0]
  self.assertNotIn('path',tool.parameters['properties']['messages']['items']['properties'])
  self.assertNotIn('sandbox',tool.description)
  self.assertEqual(original.parameters,schema)
  self.assertIsNot(tool,guarded)
  result=json.loads(await tool.call(SimpleNamespace(context=SimpleNamespace(event=event)),messages=[{'type':'plain','text':'fixture'}]))
  self.assertEqual(result['code'],'completion_capability_denied')
 async def test_current_session_plain_uses_existing_receipt(self):
  tenant_tools.install({'bot':{'generation':1,'revision':1}})
  event=Event();get_extra=event.get_extra;work=SimpleNamespace(confirmed=0,attempted=0)
  event.get_extra=lambda key,*args:work if key=='_hosted_pipeline' else get_extra(key,*args)
  sent=[]
  async def send(value):sent.append(value);work.attempted+=1;work.confirmed+=1
  event.send=send
  tool=tenant_tools.hosted_send_definition(SendMessageToUserTool())
  result=json.loads(await tool.call(SimpleNamespace(context=SimpleNamespace(event=event)),session=event.unified_msg_origin,messages=[{'type':'plain','text':'fixture'}]))
  self.assertEqual(result['status'],'confirmed');self.assertEqual(len(sent),1)
 async def test_rejected_paths_and_other_sessions_never_send(self):
  event=Event();event.send=lambda value:self.fail('unexpected transport')
  tenant_tools.install({'bot':{'generation':1,'revision':1}})
  context=SimpleNamespace(context=SimpleNamespace(event=event));tool=SendMessageToUserTool()
  for message in [{'type':'image','path':'C:/private.png'},{'type':'image','path':'/sandbox/private.png'}]:
   self.assertIn('local file access denied',await tool.call(context,messages=[message]))
  for target in ['bot:FriendMessage:99','another:FriendMessage:42']:
   self.assertIn('error:',await tool.call(context,messages=[{'type':'plain','text':'fixture'}],session=target))

if __name__=='__main__':unittest.main()
