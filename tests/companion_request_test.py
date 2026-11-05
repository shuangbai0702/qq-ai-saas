"""Real legacy planner request projection, without model or QQ side effects."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from companion_schedule import decision_request

class Requests(unittest.TestCase):
    def test_fresh_candidates_cannot_churn_trusted_prefix(self):
        source={'owner_id':'12345','persona':'固定人格','companion':{'energy':1},'eligible_members':[{'subject':'11111'}],'owner_memory':{'summaries':['真实摘要']},'current_time':1,'future_field':{'keep':[1,2]}}
        before=copy.deepcopy(source);first=decision_request(source,'固定规则')
        changed=copy.deepcopy(source);changed.update(companion={'energy':2},eligible_members=[],current_time=2)
        second=decision_request(changed,'固定规则')
        self.assertEqual(first['system_prompt'],second['system_prompt'])
        stable=json.loads(first['system_prompt'].rsplit('\n',1)[1])
        self.assertEqual({**stable,**json.loads(first['prompt'])},source)
        self.assertEqual({**stable,**json.loads(second['prompt'])},changed)
        self.assertEqual(source,before)
        changed['owner_id']='54321'
        self.assertNotEqual(decision_request(changed,'固定规则')['system_prompt'],first['system_prompt'])
        changed['owner_id']='12345';changed['persona']='新人格'
        self.assertNotEqual(decision_request(changed,'固定规则')['system_prompt'],first['system_prompt'])

if __name__=='__main__':unittest.main()
