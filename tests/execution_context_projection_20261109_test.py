import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from execution_memory import context_projection
from context_foundation import execution_view
from decision_history import apply_changes

class ExecutionProjection(unittest.TestCase):
 def test_complete_current_view_and_exact_basis_overlay(self):
  done={'actionId':'a','execution':'completed','receipt':{'status':'confirmed'},'summary':'完整观察结果','coverage':{'audioAnalyzed':False},'future':{'keep':True}}
  uncertain={'actionId':'b','execution':'completed','receipt':{'status':'unknown'},'summary':'仍需确认的完整结果','errorCode':'ACK_UNKNOWN'}
  original={'sampledAt':1,'experiences':[done,uncertain]};before=copy.deepcopy(original);basis={}
  hot=context_projection(original,basis)
  self.assertEqual(original,before);self.assertEqual(hot,{'experiences':[done,uncertain]})
  state=execution_view({'memory_foundation':basis,'execution_experience':hot})
  self.assertEqual(apply_changes(basis['execution_experience'],state['execution_experience']['changes']),hot)
  self.assertNotIn('完整观察结果',json.dumps(state['execution_experience'],ensure_ascii=False))
  self.assertEqual(context_projection({**original,'sampledAt':2},basis),hot)
 def test_new_completion_correction_and_removal_do_not_rewrite_basis(self):
  basis={'execution_experience':{'experiences':[{'actionId':'old','summary':'保留原始整理'}],'obsolete':'旧值'}};before=copy.deepcopy(basis)
  current={'experiences':[{'actionId':'new','execution':'completed','receipt':{'status':'confirmed'},'summary':'新增完整经历','future':None}]}
  hot=context_projection(current,basis)
  self.assertEqual(basis,before);self.assertEqual(hot,current)
  state=execution_view({'memory_foundation':basis,'execution_experience':hot})
  self.assertEqual(apply_changes(before['execution_experience'],state['execution_experience']['changes']),current)
  # Existing transport may choose an equivalent root replacement when shorter.
  self.assertNotIn('obsolete',apply_changes(before['execution_experience'],state['execution_experience']['changes']))
 def test_pending_failed_unavailable_and_missing_legacy_basis(self):
  for status in ('pending','failed','unknown','partial'):
   current={'experiences':[{'actionId':'x','execution':status,'receipt':{'status':'unknown'},'summary':'必要细节','errorCode':'REAL_ERROR'}]}
   hot=context_projection(current,{})
   self.assertEqual(hot,current)
   self.assertEqual(execution_view({'execution_experience':hot}),{'execution_experience':current})
  current={'status':'unavailable','reason':'read_failed'}
  self.assertEqual(context_projection(current,{}),current)
  basis={'execution_experience':{'experiences':[{'summary':'旧经历'}]}}
  self.assertEqual(apply_changes(basis['execution_experience'],execution_view({'memory_foundation':basis,'execution_experience':current})['execution_experience']['changes']),current)

class ProviderWire(unittest.IsolatedAsyncioTestCase):
 async def test_final_sdk_http_dedup_updates_tools_and_immutable_prefix(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ASTRBOT_ROOT':folder}):
   import runtime_bootstrap
   runtime_bootstrap.initialize_runtime_bootstrap()
   from slim_bootstrap import install
   install()
   import httpx
   from behavior import install_runtime
   from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
   from context_protocol import READONLY_CURRENT,model_json
   from readonly_journal import ReadonlyJournal
   from context_foundation import FOUNDATION_CURRENT
   captured=[]
   async def handler(request):
    captured.append(json.loads(request.content))
    return httpx.Response(200,json={'id':'fixture','model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':'答复'},'finish_reason':'stop'}]})
   with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda self,c:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
    runtime=install_runtime(NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={})),{})
    provider=ProviderOpenAIOfficial({'id':'fixture','type':'openai_chat_completion','model':'fixture','key':['fixture'],'api_base':'https://fixture.invalid/v1'}, {})
    try:
     journal=ReadonlyJournal();basis={};native=[{'role':'user','content':'第一问'}]
     done={'actionId':'a','execution':'completed','receipt':{'status':'confirmed'},'summary':'UNIQUE_COMPLETE_PROSE_'+'完整经历'*300,'future':{'keep':True}}
     current={'experiences':[done],'visibility':'private'};original=copy.deepcopy(current)
     def effective_wire(messages):
      readonly={};foundation={}
      for message in messages:
       content=message.get('content')
       text=content if isinstance(content,str) else '\n'.join(p.get('text','') for p in content or [] if isinstance(p,dict))
       if text.startswith(FOUNDATION_CURRENT+'\n'):
        data=json.loads(text.split('\n',1)[1])
        if 'execution_experience' in data:foundation=data['execution_experience']
       elif text.startswith(READONLY_CURRENT+'\n'):
        data=json.loads(text.split('\n',1)[1])
        readonly=apply_changes(readonly,data['readonlyChanges']) if 'readonlyChanges' in data else data
      experience=readonly['execution_experience']
      return apply_changes(foundation,experience['changes']) if experience.get('basis')=='memory_foundation.execution_experience' else experience
     async def submit(value):
      hot=context_projection(value,basis)
      packet={'role':'user','content':READONLY_CURRENT+'\n'+model_json({'memory_foundation':basis,'execution_experience':hot})}
      prepared=journal.prepare(native+[packet],'same',now=100+len(captured))
      self.assertIsNotNone(prepared)
      await provider.text_chat(contexts=prepared.wire,prompt=None,func_tool=None)
      self.assertTrue(journal.commit(prepared))
      return captured[-1]['messages']
     first=await submit(current)
     self.assertEqual(effective_wire(first),current)
     self.assertEqual(json.dumps(first,ensure_ascii=False).count(done['summary']),1)
     self.assertEqual(journal.last.state['execution_experience']['changes'],[])
     committed=copy.deepcopy(journal.last.wire)
     native.extend([{'role':'assistant','content':'答复'},{'role':'user','content':'继续'}])
     second=await submit(current)
     self.assertEqual(effective_wire(second),current)
     self.assertEqual(second[:len(first)],first)
     self.assertEqual(json.dumps(second,ensure_ascii=False).count(done['summary']),1)
     self.assertEqual(second[:len(committed)],committed)
     updated=copy.deepcopy(current);updated['experiences'][0]['receipt']['status']='unknown';updated['experiences'][0]['summary']='CORRECTED_COMPLETE_PROSE';updated['experiences'].append({'actionId':'b','execution':'completed','receipt':{'status':'confirmed'},'summary':'NEW_COMPLETE_PROSE','future':[1,None]})
     # Tool continuation has no new human turn; actual call/result adjacency stays.
     native.extend([{'role':'assistant','tool_calls':[{'id':'call-1','type':'function','function':{'name':'fixture','arguments':'{}'}}]},{'role':'tool','tool_call_id':'call-1','content':'真实回执'}])
     third=await submit(updated)
     self.assertEqual(effective_wire(third),updated)
     self.assertEqual(third[:len(second)],second)
     encoded=json.dumps(third,ensure_ascii=False)
     self.assertIn('CORRECTED_COMPLETE_PROSE',encoded);self.assertIn('NEW_COMPLETE_PROSE',encoded)
     call=next(i for i,m in enumerate(third) if m.get('tool_calls'))
     self.assertEqual(third[call+1]['tool_call_id'],'call-1')
     effective=apply_changes(basis['execution_experience'],journal.last.state['execution_experience']['changes'])
     self.assertEqual(effective,updated);self.assertEqual(current,original)
     native.extend([{'role':'assistant','content':'工具结果已知'},{'role':'user','content':'再问'}])
     fourth=await submit({'status':'unavailable','reason':'read_failed'})
     self.assertEqual(effective_wire(fourth),{'status':'unavailable','reason':'read_failed'})
     self.assertEqual(fourth[:len(third)],third)
     self.assertEqual(apply_changes(basis['execution_experience'],journal.last.state['execution_experience']['changes']),{'status':'unavailable','reason':'read_failed'})
     native.extend([{'role':'assistant','content':'读取失败不代表没有经历'},{'role':'user','content':'重试读取'}])
     fifth=await submit(current)
     self.assertEqual(effective_wire(fifth),current)
     self.assertEqual(fifth[:len(fourth)],fourth)
    finally:
     await provider.client.close();runtime['close']()

if __name__=='__main__':unittest.main()
