"""Compression boundary validation, retry identity and source-scope admission."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from summary_response import summary_model_output,decode_summary_object,SummaryValidationError


def response(text,finish='stop'):
 return NS(completion_text=text,raw_completion={'choices':[{'finish_reason':finish}]})


class Validation(unittest.IsolatedAsyncioTestCase):
 async def test_structural_failures_feedback_exact_path_and_keep_input(self):
  for invalid,path in [('{bad','$'),('[]','$'),('{}','$.summary'),('{"summary":12}','$.summary'),('{"summary":"a","summary":"b"}','$'),('{"summary":"x","weight":NaN}','$'),('{"summary":"x","weight":1e999}','$'),('['*1100,'$')]:
   with self.subTest(invalid=invalid[:40]):
    calls=[];metrics={};request={'prompt':'unchanged sources and cognitive versions','contexts':[{'role':'user','content':'original'}],'system_prompt':'contract','func_tool':['same']}
    before=copy.deepcopy(request)
    async def stream(**kw):
     calls.append(copy.deepcopy(kw));kw['contexts'][0]['content']='provider mutated request'
     yield response(invalid if len(calls)==1 else '{"summary":"valid"}')
    result=await summary_model_output(NS(text_chat_stream=stream),request,lambda:True,metrics,800)
    self.assertEqual(result[2],'valid');self.assertEqual(len(calls),2)
    self.assertEqual(request,before)
    for key in ('prompt','contexts','func_tool'):self.assertEqual(calls[0][key],calls[1][key])
    self.assertIn(path,calls[1]['system_prompt']);self.assertEqual(metrics['summaryValidationErrors'][0]['path'],path)
 async def test_safe_whole_json_fence_and_bom_need_no_retry(self):
  for text in ('\ufeff{"summary":"valid"}','```json\n{"summary":"valid"}\n```'):
   calls=[]
   async def stream(**kw):calls.append(kw);yield response(text)
   result=await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},800)
   self.assertEqual(result[2],'valid');self.assertEqual(len(calls),1)
 async def test_invalid_finish_and_structure_never_submit_or_retry_forever(self):
  for text,finish in [('{"summary":"looks complete"}','length'),('{"summary":"x"}','tool_calls'),('plain prose','stop')]:
   calls=[]
   async def stream(**kw):calls.append(kw);yield response(text,finish)
   with self.assertRaises(SummaryValidationError):await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},800)
   self.assertEqual(len(calls),2)
 async def test_business_rejection_not_delegated_to_model(self):
  for reason in ('semantic_product_snapshot_changed','semantic_review_operation_not_offered'):
   calls=[]
   async def stream(**kw):calls.append(kw);yield response('{"summary":"valid"}')
   def reject(*args):raise ValueError(reason)
   with self.assertRaisesRegex(ValueError,reason):await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},800,on_decode=reject)
   self.assertEqual(len(calls),1)
 async def test_page_structure_retries_but_identity_rejection_does_not(self):
  from memory_review import validate_page
  page={'fact_versions':[],'relation_versions':[],'products':{},'scope':'bot_shared'}
  calls=[]
  async def stream(**kw):
   calls.append(kw)
   yield response('{"fact_operations":[12]}' if len(calls)==1 else '{"fact_operations":[]}')
  result=await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},None,on_decode=lambda data,diagnostics:validate_page(data,page))
  self.assertEqual(len(calls),2);self.assertEqual(result[1],{'fact_operations':[]})
  self.assertIn('$.fact_operations[0]',calls[1]['system_prompt'])
 async def test_failed_dispatch_does_not_count_or_send_a_model_request(self):
  from summary_response import SummaryPersistenceError
  calls=[];metrics={}
  async def stream(**kw):calls.append(kw);yield response('{"summary":"valid"}')
  def unavailable():raise SummaryPersistenceError('summary_request_owner_changed')
  with self.assertRaises(SummaryPersistenceError):await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,metrics,800,on_attempt=unavailable)
  self.assertEqual(calls,[]);self.assertEqual(metrics.get('summaryRequests',0),0)
 async def test_optional_product_error_not_a_second_summary_request(self):
  calls=[]
  async def stream(**kw):calls.append(kw);yield response('{"summary":"valid","fact_operations":"bad"}')
  result=await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},800)
  self.assertEqual(result[2],'valid');self.assertEqual(len(calls),1)


class ProductScope(unittest.TestCase):
 def test_shared_scope_create_only_actual_material_scope_or_self(self):
  from behavior import BehaviorStore
  from companion_insights import snapshot,apply_semantic_memory
  from memory_operation_audit import Audit
  with tempfile.TemporaryDirectory() as temp:
   store=BehaviorStore(Path(temp)/'behavior.sqlite');db=store.db
   try:
    cfg={'selfDevelopment':True};person='12345678';allowed='private:'+person;other='private:87654321'
    payload=[{'event':json.dumps([allowed,'real-message']),'sender_id':person,'user':'explicit evidence','bot':'reply'}]
    for target_scope,expected in [(other,0),(allowed,1),('self',1)]:
     before=snapshot(db,'bot_shared',{person},cfg)
     data={'memory_operations':[{'kind':'interest','op':'create','target':{'scope':target_scope,'topic':'real topic'},'expected_version':None,'value':{'weight':1}}]}
     audit=Audit('bot_shared',0,'small',data,{person})
     count=apply_semantic_memory(db,store.memory,'bot_shared',cfg,payload,data,before,now=100,audit=audit)
     self.assertEqual(count,expected)
     if not expected:self.assertEqual(audit.details[('products',0)]['reason'],'scope_not_offered')
    self.assertIsNone(db.execute('SELECT 1 FROM companion_interests WHERE scope=?',(other,)).fetchone())
   finally:store.close()

if __name__=='__main__':unittest.main()
