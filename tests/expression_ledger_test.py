"""Use/recognition ledger separation for learned expressions (D6.6)."""
import sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore

class ExpressionLedger(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.s=BehaviorStore(self.path);self.db=self.s.db;self.now=time.time()
  self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',('group:1','有点意思','轻松聊天','[]',2,self.now));self.db.commit()
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def weight(self):return self.db.execute("SELECT weight FROM social_patterns WHERE expression='有点意思'").fetchone()[0]
 def test_self_use_records_frequency_without_rewarding_itself(self):
  social=self.s.social;before=self.weight()
  social.expression_sent('group:1','这句有点意思',now=self.now)
  self.assertEqual(self.weight(),before,'self-use must not add positive evaluation weight')
  usage=self.db.execute("SELECT at FROM expression_usage WHERE expression='有点意思' AND scope='group:1'").fetchone()
  self.assertIsNotNone(usage,'the use frequency is still recorded')
  from social import reinforce_expression
  reinforce_expression(self.db,'group:1','有点意思','positive','reaction-1',self.now+60)
  self.assertGreater(self.weight(),before,'real positive feedback evidence still moves the weight')
 def test_use_is_not_an_automatic_reinforcement_kind(self):
  self.s.social.expression_sent('group:1','有点意思',now=self.now)
  rows=self.db.execute("SELECT kind FROM expression_reinforcement WHERE expression='有点意思'").fetchall()
  self.assertFalse(any(r[0]=='use' for r in rows),'use is no longer an automatic reinforcement kind')
 def test_repeated_self_use_within_interval_neither_adds_weight_nor_flags(self):
  social=self.s.social;before=self.weight()
  social.expression_sent('group:1','有点意思',now=self.now)
  social.expression_sent('group:1','又有点意思了',now=self.now+30)
  self.assertEqual(self.weight(),before)
  self.assertEqual(self.db.execute("SELECT count(*) FROM expression_usage WHERE expression='有点意思' AND scope='group:1'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
