"""Person demand packets use committed wire and never filter real messages."""
import json,sys,tempfile,unittest,copy
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parents[1]/'references/astrbot')]
from behavior import BehaviorStore
from relationships import observe_message,active_people
from person_memory import additions,visible,PERSON_CURRENT
from context_foundation import FOUNDATION_CURRENT
from context_protocol import SOURCE,model_json

def incoming(person,event='1'):
 return {'role':'user','content':SOURCE+model_json({'scope':'group:123456','sender_id':person,'event_id':event,'at':10})+'\nhello'}
class Demand(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
  self.addCleanup(self.tmp.cleanup);self.addCleanup(self.store.close)
 def test_recent_six_real_times_and_media_speaker(self):
  for i in range(8):observe_message(self.db,'group:123456',str(100000+i),100+i)
  observe_message(self.db,'group:123456','100000',1) # old replay cannot bump
  observe_message(self.db,'self','999999',1000) # service activation is not a speaker
  self.assertEqual(active_people(self.db),[str(i) for i in range(100007,100001,-1)])
  self.assertEqual(self.db.execute('SELECT semantic_updated FROM relations WHERE subject=?',('100007',)).fetchone()[0],0)
  service=incoming('100007');service['content']=service['content'].replace('"event_id":"1"','"kind":"platform_service_not_user","event_id":"1"')
  self.assertEqual(additions(self.db,[],[service]),[])
 def test_once_from_committed_wire_and_revision_delta(self):
  p='100007';observe_message(self.db,'group:123456',p,100)
  self.store.memory.remember('group:123456',p,'喜好','围棋','e1',100);self.db.commit()
  base=[{'role':'system','content':'immutable'},incoming('100000')];before=copy.deepcopy(base)
  first=additions(self.db,base,[incoming(p)])
  self.assertEqual(len(first),1);self.assertEqual(base,before)
  self.assertEqual(additions(self.db,base,[incoming(p)]),first,'failed candidate establishes no visibility')
  wire=base+first+[incoming(p)]
  self.assertEqual(additions(self.db,wire,[incoming(p,'2')]),[])
  self.store.memory.remember('group:123456',p,'喜好','象棋','e2',200);self.db.commit()
  patch=additions(self.db,wire,[incoming(p,'3')]);data=json.loads(patch[0]['content'].split('\n',1)[1])
  self.assertIn('changes',data);self.assertNotIn('facts',data)
  self.assertEqual(visible(wire+patch)[p]['facts'][0]['value'],'象棋')
  self.assertNotIn('affinity',patch[0]['content'],'unchanged relationship fields must not replay')
  self.assertEqual(additions(self.db,wire+patch,[incoming(p,'4')]),[])
 def test_baseline_visibility_and_identical_text_distinct_people(self):
  for p in ('100001','100002'):observe_message(self.db,'group:123456',p,100)
  from person_memory import profile,canonical
  prof=canonical(profile(self.db,'group:123456','100001'))
  basis=[{'role':'user','content':FOUNDATION_CURRENT+'\n'+model_json({'facts':prof['facts'],'relations':[prof['relation']]})}]
  self.assertEqual(additions(self.db,basis,[incoming('100001')]),[])
  self.assertEqual(len(additions(self.db,basis,[incoming('100002')])),1)
  self.assertEqual(len(additions(self.db,[],[incoming('100001'),incoming('100002')])),2)
 def test_shared_baseline_uses_selected_people_not_current_private_author_only(self):
  from context_foundation import read
  self.store.shared_context=True
  for p in ('100001','100002'):
   observe_message(self.db,'private:'+p,p,100)
   self.store.memory.remember('private:'+p,p,'喜好','围棋','e'+p,100)
  self.db.commit()
  basis=read(self.store,{'memory':True,'memorySummary':False},'private:100001','100001')
  self.assertEqual({f['subject'] for f in basis['facts']},{'100001','100002'})
 def test_committed_prefix_survives_storage_restart_and_scope_switch(self):
  from readonly_journal import ReadonlyJournal
  from readonly_journal_test import packet
  p='100007';observe_message(self.db,'group:123456',p,100)
  self.store.memory.remember('group:123456',p,'喜好','围棋','e1',100);self.db.commit()
  journal=ReadonlyJournal(db=self.db,scope_key=model_json('bot_shared'))
  base=[{'role':'system','content':'unchanged-system'},incoming(p)]
  prepared=journal.prepare(base+[packet({'state':{},'memory_foundation':{'facts':[]}})],('bot',1),now=1)
  self.assertTrue(journal.commit(prepared));prefix=copy.deepcopy(prepared.wire)
  restored=ReadonlyJournal(db=self.db,scope_key=model_json('bot_shared'))
  following=incoming(p,'2');following['content']=following['content'].replace('group:123456','private:100007')
  candidate=restored.prepare(base+[following,packet({'state':{},'memory_foundation':{'facts':[]}})],('bot',1),now=2)
  self.assertEqual(candidate.wire[:len(prefix)],prefix)
  self.assertTrue(restored.commit(candidate))
  self.assertEqual(additions(self.db,restored.last.wire,[incoming(p,'3')]),[])
if __name__=='__main__':unittest.main()
