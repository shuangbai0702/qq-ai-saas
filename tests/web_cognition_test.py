"""User-only Core Note edits: exact CAS and selective Journal projection."""
import copy,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from contextlib import closing
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
from behavior import BehaviorStore
from context_protocol import READONLY_CURRENT,model_json
from data_admin import run
from readonly_journal import ReadonlyJournal,ContextPrefixViolation
from web_cognition import note,fingerprint,read,project

class WebCognition(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.store=BehaviorStore(self.path);self.db=self.store.db
  self.db.execute("INSERT INTO memory_core_notes(slot,text,scopes,people,sources,updated,source_verification) VALUES(1,'old note','[\"private:12345678\"]','[\"12345678\"]','[]',1,'references_matched')");self.db.commit()
  self.before=note(self.db)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def edit(self,text,**extra):return run(self.path,{'action':'edit_core_note','actorId':'real-user','text':text,'expectedVersion':fingerprint(note(self.db)),**extra},connection=self.db)
 def test_user_only_cas_and_restart_without_a_second_ledger(self):
  version=fingerprint(self.before)
  self.assertEqual(self.edit('new',actorId='')['status'],403)
  self.assertEqual(note(self.db),self.before)
  self.assertTrue(self.edit('new')['ok']);self.assertEqual(self.edit('stale',expectedVersion=version)['status'],409)
  self.assertEqual(note(self.db)['text'],'new');self.assertEqual(read(self.db)['proof']['actorId'],'real-user')
  self.assertTrue(self.edit('newer')['ok']);self.assertIn(version,read(self.db)['proof']['origins'])
  with closing(sqlite3.connect(self.path)) as reopened:self.assertEqual(read(reopened),read(self.db))
 def test_user_text_does_not_inherit_old_ai_evidence(self):
  self.db.execute("UPDATE memory_core_notes SET sources='[\"old-event\"]',claims='[{\"id\":\"old-claim\"}]'");self.db.commit()
  self.assertTrue(self.edit('a direct correction')['ok'])
  self.assertEqual(tuple(self.db.execute('SELECT sources,claims FROM memory_core_notes').fetchone()),('[]','[]'))
 def test_legacy_visibility_annotation_is_not_another_authoritative_version(self):
  journal,_,_,first=self.initial()
  first.state['foundation_core_note']['legacy_unverified']=True
  import context_foundation
  for message in first.wire:
   if message.get('content','').startswith(context_foundation.FOUNDATION_CURRENT+'\n'):
    packet=json.loads(message['content'].split('\n',1)[1])
    if packet.get('region')=='core_note':
     packet['core_note']['legacy_unverified']=True
     from memory_model_view import memory_json
     message['content']=context_foundation.FOUNDATION_CURRENT+'\n'+memory_json(packet)
  self.assertTrue(self.edit('user correction')['ok'])
  projected=project(first,read(self.db))
  self.assertEqual(projected.state['foundation_core_note']['text'],'user correction')
 def test_fact_edit_preserves_unrelated_cognition_and_uncompressed_material(self):
  self.store.memory.remember('private:12345678','12345678','饮料','红茶','source',1)
  self.store.reflection.record('bot_shared','12345678','real-event','尚未压缩的输入','真实回复',now=10)
  self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('bot_shared','原有历史摘要','[]',1)");self.db.commit()
  tables=('memory_core_notes','memory_summaries','memory_pending','memory_rounds','memory_round_material','memory_reflection_control','memory_reconstruction_checkpoints')
  before={t:self.db.execute('SELECT * FROM '+t).fetchall() for t in tables}
  result=run(self.path,{'action':'edit_fact','scope':'private:12345678','subject':'12345678','name':'饮料','value':'咖啡','expectedVersion':1},connection=self.db)
  self.assertTrue(result['ok']);self.assertEqual({t:self.db.execute('SELECT * FROM '+t).fetchall() for t in tables},before)
  self.assertEqual(self.db.execute('SELECT value,version FROM facts WHERE active=1').fetchall(),[('咖啡',2)])
 def initial(self):
  journal=ReadonlyJournal(self.db,'bot_shared')
  messages=[{'role':'system','content':'fixed system'},{'role':'user','content':'original input'},{'role':'assistant','content':'original answer'}]
  state={'memory_foundation':{'facts':[],'conversation_summaries':[],'core_note':self.before}}
  packet={'role':'user','content':READONLY_CURRENT+'\n'+model_json(state)}
  first=journal.prepare(messages+[packet],1);self.assertTrue(journal.commit(first))
  return journal,messages,packet,first
 def test_journal_final_http_wire_changes_only_authorized_core_region(self):
  import httpx
  journal,messages,packet,first=self.initial();saved=copy.deepcopy(first.wire)
  self.assertTrue(self.edit('web correction')['ok']);change=read(self.db)
  candidate=journal.prepare(messages+[{'role':'user','content':'next input'},packet],2,reconstruction_scope='bot_shared',cognition_edit=change)
  self.assertIsNone(candidate.reconstruction_permit);self.assertEqual(candidate.turns,first.turns+1)
  difference=[(a,b) for a,b in zip(saved,candidate.wire) if a!=b];self.assertEqual(len(difference),1)
  self.assertIn('web correction',difference[0][1]['content']);self.assertEqual(first.wire,saved)
  self.assertTrue(journal.stage_candidate(candidate))
  request=httpx.Request('POST','https://fixture.invalid/chat/completions',json={'messages':candidate.wire})
  wire=json.loads(request.content)['messages'];self.assertEqual(wire,candidate.wire)
  self.assertNotIn('old note',json.dumps(wire));self.assertEqual(json.dumps(wire).count('web correction'),1)
  self.assertTrue(journal.commit(candidate));restored=ReadonlyJournal(self.db,'bot_shared');self.assertEqual(restored.last.wire,wire)
  self.assertEqual(restored.last.state['foundation_core_note']['text'],'web correction')
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
 def test_first_user_note_adds_only_previously_empty_base_region(self):
  self.db.execute("UPDATE memory_core_notes SET text='',scopes='[]'");self.db.commit();self.before=note(self.db)
  journal,messages,packet,first=self.initial();before=copy.deepcopy(first.wire)
  self.assertTrue(self.edit('first user note')['ok']);change=read(self.db)
  candidate=journal.prepare(project(first,change).base+[{'role':'user','content':'next'},packet],2,cognition_edit=change)
  self.assertEqual(first.wire,before);self.assertEqual(json.dumps(candidate.wire).count('first user note'),1)
  self.assertIsNone(candidate.reconstruction_permit);self.assertTrue(journal.commit(candidate))
  self.assertEqual(self.store.reflection.core_note('bot_shared')['text'],'first user note')
 def test_user_replacement_of_legacy_hidden_note_becomes_visible_without_old_attribution(self):
  self.db.execute("UPDATE memory_core_notes SET scopes='[]'");self.db.commit()
  self.before=self.store.reflection.core_note('bot_shared')
  self.assertEqual(self.before['text'],'')
  journal,messages,packet,first=self.initial()
  self.assertTrue(self.edit('owner supplied new note')['ok'])
  actual=note(self.db)
  self.assertEqual(actual['source_scopes'],['bot_shared']);self.assertEqual(actual['subjects'],[])
  candidate=journal.prepare(project(first,read(self.db)).base+[{'role':'user','content':'next'},packet],2,cognition_edit=read(self.db))
  self.assertEqual(json.dumps(candidate.wire).count('owner supplied new note'),1)
  self.assertIsNone(candidate.reconstruction_permit)
  self.assertEqual(self.store.reflection.core_note('bot_shared')['text'],'owner supplied new note')
 def test_unknown_or_forged_region_is_not_editable(self):
  journal,_,_,first=self.initial();self.assertTrue(self.edit('new')['ok']);change=read(self.db)
  forged=copy.deepcopy(change);forged['note']['text']='forged'
  with self.assertRaises(ContextPrefixViolation):project(first,forged)
  first.state['foundation_core_note']['text']='unproven'
  with self.assertRaises(ContextPrefixViolation):project(first,change)
 def test_failed_layout_preflight_rolls_back_whole_user_edit(self):
  journal,_,_,first=self.initial()
  candidate=copy.deepcopy(first);candidate.wire=[m for m in candidate.wire if 'old note' not in m.get('content','')];journal.serial=candidate.serial
  self.assertTrue(journal.commit(candidate))
  result=self.edit('cannot locate')
  self.assertEqual(result['status'],409);self.assertEqual(note(self.db),self.before)

if __name__=='__main__':unittest.main()
