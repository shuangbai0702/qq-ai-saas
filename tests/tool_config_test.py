import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from tool_config import apply_user_tools,tool_hash

class ToolConfigTest(unittest.TestCase):
 def setUp(self):
  self.owner=object()
  self.current={'botId':'a','tenantId':'t','generation':1,'revision':2,'_base_owner':self.owner,'config':{'systemPrompt':'persona','toolPrompts':{'dsh':'old'},'delegatedCapabilities':{'webSearch':True},'behavior':{'memory':True}}}
  self.body={k:copy.deepcopy(self.current[k]) for k in ('botId','tenantId','generation','revision','config')};self.body['revision']=3;self.body['config']['toolPrompts']['dsh']='new';self.body['toolsEdit']={'permitId':'7','target':tool_hash(self.body['config'])}
 def test_only_tool_config_changes_without_mutating_in_flight_snapshot(self):
  before=copy.deepcopy(self.current['config']);result=apply_user_tools(self.current,self.body)
  self.assertEqual(self.current['config'],before);self.assertEqual(result['config']['systemPrompt'],'persona');self.assertEqual(result['config']['behavior'],before['behavior']);self.assertIs(result['_base_owner'],self.owner)
  self.assertEqual(apply_user_tools(result,self.body)['config'],result['config'])
 def test_unrelated_fields_stale_revisions_and_foreign_owners_rejected(self):
  for key,value in [('botId','b'),('tenantId','other'),('generation',2),('revision',1)]:
   body={**self.body,key:value}
   with self.assertRaises(ValueError):apply_user_tools(self.current,body)
  for key,value in [('systemPrompt','changed'),('behavior',{'memory':False})]:
   body=copy.deepcopy(self.body);body['config'][key]=value
   with self.assertRaises(ValueError):apply_user_tools(self.current,body)
 def test_user_cannot_forge_tool_witness_or_modify_permissions_outside_tool_fields(self):
  for edit in ({'permitId':'not-an-audit','target':tool_hash(self.body['config'])},{'permitId':'7','target':'0'*64}):
   with self.assertRaises(ValueError):apply_user_tools(self.current,{**self.body,'toolsEdit':edit})

if __name__=='__main__':unittest.main()
