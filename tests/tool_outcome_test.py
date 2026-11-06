import sys,json,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from tool_outcome import project
class Outcomes(unittest.TestCase):
 def test_no_prose_can_prove_writes(self):
  for text in ['已发送成功','confirmed','{"message":"success"}']:
   self.assertEqual(project(text,write=True)['receipt']['status'],'unknown')
 def test_pending_and_read_are_not_confirmed_effects(self):
  pending=project({'execution':'pending','receipt':{'status':'not_applicable'},'actionId':'same'})
  self.assertEqual(pending['execution'],'pending');self.assertEqual(pending['actionId'],'same')
  self.assertEqual(project('read result')['receipt']['status'],'not_applicable')
 def test_receipt_does_not_erase_execution(self):
  for execution in ['pending','canceled','failed','completed']:
   for receipt in ['confirmed','rejected','partial','unknown','not_applicable']:
    got=project({'execution':execution,'receipt':{'status':receipt},'evidence':['actual']})
    self.assertEqual(got['execution'],execution);self.assertEqual(got['receipt']['status'],receipt);self.assertEqual(got['evidence'],['actual'])
if __name__=='__main__':unittest.main()
