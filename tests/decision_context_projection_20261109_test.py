import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from context_transport import decision_request

class DecisionProjection(unittest.TestCase):
 def payload(self):
  message={'source_event':'same-event','sender_id':'12345678','text':'足够长的真实群聊原文。'*30}
  return {'persona':'fixed','owner_id':'12345678','scope':'group:55555','messages':[message],
          'memory':{'recent_cross_session_messages':[{'source_scope':'group:55555',**message}]}}
 def test_exact_same_event_body_is_present_once_and_reference_resolves(self):
  value=self.payload();before=copy.deepcopy(value);request=decision_request(value,'duty');view=json.loads(request['prompt'])
  self.assertEqual(value,before);row=view['memory']['recent_cross_session_messages'][0]
  self.assertEqual(row['text_ref'],'/messages/0/text');self.assertNotIn('text',row)
  self.assertEqual(view['messages'][0]['text'],value['messages'][0]['text'])
  self.assertLess(len(request['prompt']),len(json.dumps({k:v for k,v in value.items() if k not in ('persona','owner_id')},ensure_ascii=False)))
 def test_scope_author_content_and_event_are_not_interchangeable(self):
  for key,replacement in [('source_scope','group:66666'),('sender_id','22222222'),('source_event','new'),('text','另一段真实内容'*30)]:
   value=self.payload();value['memory']['recent_cross_session_messages'][0][key]=replacement
   row=json.loads(decision_request(value,'duty')['prompt'])['memory']['recent_cross_session_messages'][0]
   self.assertIn('text',row);self.assertNotIn('text_ref',row)
 def test_short_or_unsourced_messages_are_not_replaced(self):
  for change in ('short','no_id'):
   value=self.payload()
   for row in (value['messages'][0],value['memory']['recent_cross_session_messages'][0]):
    if change=='short':row['text']='你好'
    else:row.pop('source_event')
   self.assertIn('text',json.loads(decision_request(value,'duty')['prompt'])['memory']['recent_cross_session_messages'][0])
 def test_existing_receipt_text_reference_is_not_broken(self):
  value=self.payload();value['memory']['cross_scope_deliveries']=[{'confirmed_text_ref':['recent_cross_session_messages',0,'text']}]
  view=json.loads(decision_request(value,'duty')['prompt'])
  self.assertEqual(view['memory']['recent_cross_session_messages'][0]['text'],value['messages'][0]['text'])

if __name__=='__main__':unittest.main()
