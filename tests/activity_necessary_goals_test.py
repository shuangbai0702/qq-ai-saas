"""Only present goal definitions enter activation input."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from activity_context import necessary_goals,visible_goals
class CurrentGoals(unittest.TestCase):
 def value(self):
  return {'goals':[{'id':'active','status':'active','revision':9,'title':'当前目标','success_condition':'当前合法条件','progress':'HISTORICAL_PROGRESS','source':{'scope':'private:12345','constraints':'用户限制'}},{'id':'closed','status':'completed','title':'HISTORICAL_GOAL'},{'id':'abandoned','status':'abandoned','title':'HISTORICAL_GOAL'},{'id':'removed','terminalRecord':True}], 'actions':[{'id':'running','goal_id':'active','status':'executing','result':{'unfinished':['HISTORICAL_ACTION']}}], 'effects':[{'id':'pending','work_id':'running','state':'unknown','result':'HISTORICAL_RESULT'}]}
 def test_only_current_goal_definition_is_sent(self):
  raw=self.value();before=copy.deepcopy(raw);v=necessary_goals(raw)
  self.assertEqual(raw,before);self.assertEqual(necessary_goals(v),v)
  self.assertEqual(v,{'goals':[{'id':'active','revision':9,'title':'当前目标','success_condition':'当前合法条件','source':{'constraints':'用户限制'}}]})
  self.assertNotIn('HISTORICAL',json.dumps(v));self.assertNotIn('actions',v);self.assertNotIn('effects',v)
 def test_no_current_goals_means_only_empty_goal_list(self):
  self.assertEqual(necessary_goals({'goals':self.value()['goals'][1:]}),{'goals':[]})
 def test_latest_revision_replaces_current_definition_without_delta(self):
  raw=self.value();raw['goals'][0].update(revision=10,success_condition='新条件')
  value=necessary_goals(raw);self.assertEqual(value['goals'][0]['revision'],10)
  self.assertEqual(value['goals'][0]['success_condition'],'新条件');self.assertEqual(set(value),{'goals'})
 def test_scope_filter_precedes_projection(self):
  raw=self.value();raw['goals'].append({'id':'foreign','status':'active','source':{'scope':'group:999'}})
  self.assertNotIn('foreign',[g['id']for g in necessary_goals(visible_goals(raw,'private:12345'))['goals']])
if __name__=='__main__':unittest.main()
