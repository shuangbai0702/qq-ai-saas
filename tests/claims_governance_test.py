"""Current Claim identity, capacity and source-ownership contracts."""
import json,sqlite3,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from summary_material import fit_claims,protected_claim,inherit_claims,govern_claims,claim_view,bound_claim_views

def claim(i,**extra):
 return dict(id=f'{i:016x}',text=f'evidence {i}',kind='correction',speaker={'role':'human','id':'123456'},sourceRefs=[{'scope':'private:123456','event':str(i),'role':'user'}],occurredAt=i,status='applied',visibility='private:123456',protected=True,**extra)

class Governance(unittest.TestCase):
 def db(self):
  db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  db.executescript('CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,claims TEXT); CREATE TABLE memory_core_notes(slot INTEGER PRIMARY KEY,claims TEXT);')
  return db
 def test_latest_thirty_ignore_protection_and_copy_time(self):
  items=[claim(i) for i in range(50)];items[0].update(updatedAt=100,revision=2)
  kept,dropped=fit_claims(items+items)
  self.assertEqual(len(kept),30);self.assertIn(items[0]['id'],{c['id'] for c in kept});self.assertEqual(len(dropped),20)
  self.assertEqual(kept[0]['updatedAt'],100)
 def test_terminal_flags_never_protect_and_unknown_stays_unknown(self):
  for state in ('done','resolved','abandoned','superseded','canceled','expired'):
   c=claim(1);c['status']=state;self.assertFalse(protected_claim(c))
  c=claim(2);c['status']='unknown';self.assertTrue(protected_claim(c));self.assertEqual(c['status'],'unknown')
 def test_inherit_real_source_not_array_tail(self):
  c=claim(1);c['parents']=[{'summary':9999,'claim':c['id']}]
  merged=inherit_claims([(3,json.dumps([c])),(8,json.dumps([c])),(2,json.dumps([c]))])
  self.assertEqual(merged[0]['parents'],[{'summary':8,'claim':c['id']}])
 def test_family_capacity_single_storage_and_other_scope_untouched(self):
  db=self.db();scope='private:123456'
  for start in (0,20,40):db.execute('INSERT INTO memory_summaries(scope,claims) VALUES(?,?)',(scope,json.dumps([claim(i) for i in range(start,start+20)])))
  db.execute('INSERT INTO memory_core_notes VALUES(1,?)',(json.dumps([claim(59)]),))
  db.execute('INSERT INTO memory_summaries(scope,claims) VALUES(?,?)',('private:999999',json.dumps([claim(999)])))
  govern_claims(db,scope)
  rows=[c for raw, in db.execute('SELECT claims FROM memory_summaries WHERE scope=?',(scope,)) for c in json.loads(raw or '[]')]
  self.assertEqual(len(rows),30);self.assertEqual(len({c['id'] for c in rows}),30);self.assertTrue(all(c['occurredAt']>=30 for c in rows))
  self.assertIsNone(db.execute('SELECT claims FROM memory_core_notes').fetchone()[0]);self.assertIsNotNone(db.execute("SELECT claims FROM memory_summaries WHERE scope='private:999999'").fetchone()[0])
 def test_views_deduplicate_by_id_not_text(self):
  a,b=claim(1),claim(2);b['text']=a['text']
  rows=[{'protected_claims':[a,b]},{'protected_claims':[a]}]
  bound_claim_views(rows,budget=10**9)
  self.assertEqual(sum(len(r.get('protected_claims',[])) for r in rows),2)
  self.assertNotIn('parents',claim_view(a))
if __name__=='__main__':unittest.main()
