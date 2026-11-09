import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from execution_memory import context_projection

class ExecutionProjection(unittest.TestCase):
 def test_completed_prose_once_in_foundation_but_unknown_retains_material(self):
  done={'actionId':'a','execution':'completed','receipt':{'status':'confirmed'},'summary':'完整观察结果','coverage':{'audioAnalyzed':False}}
  uncertain={'actionId':'b','execution':'completed','receipt':{'status':'unknown'},'summary':'仍需确认的完整结果','errorCode':'ACK_UNKNOWN'}
  original={'sampledAt':1,'experiences':[done,uncertain]};before=copy.deepcopy(original);basis={}
  hot=context_projection(original,basis)
  self.assertEqual(original,before)
  self.assertEqual(basis['execution_experience']['experiences'],[done])
  self.assertNotIn('summary',hot['experiences'][0]);self.assertEqual(hot['experiences'][0]['receipt'],done['receipt'])
  self.assertEqual(hot['experiences'][0]['coverage'],done['coverage'])
  self.assertEqual(hot['experiences'][1],uncertain);self.assertNotIn('sampledAt',hot)
  self.assertEqual(context_projection({**original,'sampledAt':2},basis),hot)
 def test_new_completion_does_not_rewrite_frozen_checkpoint(self):
  basis={'execution_experience':{'experiences':[{'actionId':'old','summary':'保留原始整理'}]}};before=copy.deepcopy(basis)
  hot=context_projection({'experiences':[{'actionId':'new','execution':'completed','receipt':{'status':'confirmed'},'summary':'新观察'}]},basis)
  self.assertEqual(basis,before);self.assertEqual(hot['experiences'][0]['actionId'],'new')
  self.assertEqual(hot['experiences'][0]['execution'],'completed')
 def test_pending_failed_and_unavailable_are_not_silenced(self):
  for state in ('pending','failed','unknown','partial'):
   row={'actionId':'x','execution':state,'receipt':{'status':'unknown'},'summary':'必要细节','errorCode':'REAL_ERROR'}
   self.assertEqual(context_projection({'experiences':[row]}, {})['experiences'],[row])
  self.assertEqual(context_projection({'status':'unavailable','reason':'read_failed'},{}),{'status':'unavailable','reason':'read_failed'})

if __name__=='__main__':unittest.main()
