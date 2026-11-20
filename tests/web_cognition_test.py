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
  result=run(self.path,{'action':'edit_fact','actorId':'test-web-user','scope':'private:12345678','subject':'12345678','name':'饮料','value':'咖啡','expectedVersion':1},connection=self.db)
  self.assertTrue(result['ok']);self.assertEqual({t:self.db.execute('SELECT * FROM '+t).fetchall() for t in tables},before)
 def test_fact_projection_uses_only_real_user_exact_identity_and_keeps_dynamic_history(self):
  from context_foundation import arrange
  from hosted_memory import user_edits,project_user_edits
  from person_memory import profile
  from types import SimpleNamespace
  self.store.memory.remember('private:12345678','12345678','饮料','红茶','source',1)
  foundation={'facts':profile(self.db,'private:12345678','12345678')['facts']}
  state={'memory_foundation':foundation}
  dynamic={'role':'user','content':'历史消息说红茶，不是基础配置'}
  messages=arrange([{'role':'system','content':'system'}],state)+[dynamic]
  snapshot=SimpleNamespace(base=copy.deepcopy(messages),wire=copy.deepcopy(messages),state=state)
  body={'action':'edit_fact','actorId':'real-user','scope':'private:12345678','subject':'12345678','name':'饮料','value':'咖啡','expectedVersion':1}
  self.assertEqual(run(self.path,{**body,'actorId':''},connection=self.db)['status'],403)
  self.assertTrue(run(self.path,body,connection=self.db)['ok'])
  edits=user_edits(self.db);candidate=project_user_edits(snapshot,edits)
  self.assertEqual(snapshot.wire,messages);self.assertEqual(candidate.wire[-1],dynamic)
  self.assertEqual(candidate.state['memory_foundation']['facts'][0]['value'],'咖啡')
  self.assertEqual(sum(a!=b for a,b in zip(candidate.wire,messages)),1)
  self.assertNotIn('actorId',json.dumps(candidate.wire))
  forged=copy.deepcopy(edits);forged[0]['fact']['value']='伪造'
  with self.assertRaises(ContextPrefixViolation):project_user_edits(snapshot,forged)
  self.assertEqual(project_user_edits(candidate,edits).wire,candidate.wire)
  self.assertEqual(self.db.execute('SELECT value,version FROM facts WHERE active=1').fetchall(),[('咖啡',2)])
 def test_relation_projection_only_exact_foundation_and_user_version(self):
  from context_foundation import arrange
  from person_memory import relation_view
  from relationships import user_version,user_edits,project_user_edits
  from types import SimpleNamespace
  self.db.execute("INSERT INTO relations(scope,subject,affinity,last_seen,trust,impression,semantic_updated) VALUES('private:12345678','12345678',5,1,.2,'旧印象',1)");self.db.commit()
  from relationships import state as relation_state
  before=relation_state(self.db,'12345678')
  state={'memory_foundation':{'relations':[relation_view(self.db,'12345678')]}}
  dynamic={'role':'user','content':'过去说过旧印象，这是动态历史'}
  messages=arrange([{'role':'system','content':'system'}],state)+[dynamic]
  snapshot=SimpleNamespace(base=copy.deepcopy(messages),wire=copy.deepcopy(messages),state=state)
  body={'action':'edit_relation','actorId':'real-user','scope':before['scope'],'subject':before['subject'],'expectedVersion':user_version(before),'affinity':20,'trust':.8,'impression':'新印象'}
  self.assertTrue(run(self.path,body,connection=self.db)['ok'])
  self.db.execute('PRAGMA query_only=ON');edits=user_edits(self.db);self.db.execute('PRAGMA query_only=OFF')
  candidate=project_user_edits(snapshot,edits)
  self.assertEqual(candidate.wire[-1],dynamic);self.assertEqual(snapshot.wire,messages)
  self.assertEqual(sum(a!=b for a,b in zip(candidate.wire,messages)),1)
  self.assertEqual(candidate.state['memory_foundation']['relations'][0]['impression'],'新印象')
  self.assertNotIn('actorId',json.dumps(candidate.wire))
  self.assertEqual(project_user_edits(candidate,edits).wire,candidate.wire)
  forged=copy.deepcopy(edits);forged[0]['relation']['impression']='伪造'
  with self.assertRaises(ContextPrefixViolation):project_user_edits(snapshot,forged)
  self.db.execute('UPDATE relations SET semantic_updated=semantic_updated+1');self.db.commit()
  self.assertEqual(user_edits(self.db),[],'later compression revokes old edit projection authority')

 def test_forgetting_one_fact_does_not_clear_other_cognition_or_compression_material(self):
  from context_foundation import arrange
  from person_memory import profile
  from hosted_memory import user_edits,project_user_edits
  from types import SimpleNamespace
  self.store.memory.remember('private:12345678','12345678','饮料','红茶','source',1)
  self.store.memory.remember('private:12345678','12345678','城市','成都','source-2',1)
  self.store.reflection.record('bot_shared','12345678','real-event','未压缩输入','真实回复',now=10)
  self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('bot_shared','原摘要','[]',1)");self.db.commit()
  tables=('memory_core_notes','memory_summaries','memory_pending','memory_rounds','memory_round_material','memory_reconstruction_checkpoints')
  before={t:self.db.execute('SELECT * FROM '+t).fetchall() for t in tables}
  state={'memory_foundation':{'facts':profile(self.db,'private:12345678','12345678')['facts']}}
  dynamic={'role':'user','content':'历史中的红茶不得改写'}
  messages=arrange([],state)+[dynamic];snapshot=SimpleNamespace(base=messages,wire=messages,state=state)
  self.assertTrue(run(self.path,{'action':'forget_fact','actorId':'user','scope':'private:12345678','subject':'12345678','name':'饮料','expectedVersion':1},connection=self.db)['ok'])
  self.assertEqual({t:self.db.execute('SELECT * FROM '+t).fetchall() for t in tables},before)
  candidate=project_user_edits(snapshot,user_edits(self.db))
  self.assertEqual(candidate.wire[-1],dynamic)
  self.assertEqual([r['name'] for r in candidate.state['memory_foundation']['facts']],['城市'])
  self.assertEqual([r['name'] for r in profile(self.db,'private:12345678','12345678')['facts']],['城市'])

 def test_expression_user_edit_disable_and_clear_patch_only_owned_foundation(self):
  import social
  from context_foundation import arrange
  from types import SimpleNamespace
  self.db.execute("INSERT INTO social_patterns VALUES('private:12345678','好耶','喝茶时','[]',1,1)");self.db.commit()
  state={'memory_foundation':{'expression_hints':social.foundation_view(self.db)}}
  dynamic={'role':'assistant','content':'以前说过好耶和喝茶时，这是动态历史'}
  messages=arrange([],state)+[dynamic];snapshot=SimpleNamespace(base=messages,wire=messages,state=state)
  listed=run(self.path,{'action':'list'},self.db);row=listed['patterns'][0]
  body={'action':'edit_pattern','actorId':'user','scope':row['scope'],'expression':row['expression'],'expectedEpoch':listed['epoch'],'expectedVersion':row['version'],'situation':'朋友分享茶叶时','weight':1}
  self.assertTrue(run(self.path,body,self.db)['ok'])
  edited=social.project_user_edits(snapshot,social.user_edits(self.db))
  self.assertEqual(edited.wire[-1],dynamic);self.assertEqual(snapshot.wire,messages)
  self.assertEqual(edited.state['memory_foundation']['expression_hints'][0]['situation'],'朋友分享茶叶时')
  self.assertNotIn('actorId',json.dumps(edited.wire))
  listed=run(self.path,{'action':'list'},self.db);row=listed['patterns'][0]
  self.assertTrue(run(self.path,{'action':'set_pattern','actorId':'user','scope':row['scope'],'expression':row['expression'],'expectedEpoch':listed['epoch'],'expectedUpdated':row['updated'],'disabled':True},self.db)['ok'])
  disabled=social.project_user_edits(edited,social.user_edits(self.db))
  self.assertEqual(disabled.state['memory_foundation']['expression_hints'],[]);self.assertEqual(disabled.wire[-1],dynamic)
  self.assertEqual(social.foundation_view(self.db),[])
  stale=run(self.path,{'action':'clear_learning','actorId':'user','expectedEpoch':listed['epoch'],'scope':row['scope']},self.db)
  self.assertEqual(stale['status'],409)
  self.assertEqual(self.db.execute('SELECT count(*) FROM social_patterns').fetchone()[0],1)
  self.assertTrue(run(self.path,{'action':'clear_learning','actorId':'user','expectedEpoch':run(self.path,{'action':'list'},self.db)['epoch'],'scope':row['scope']},self.db)['ok'])
  cleared=social.project_user_edits(snapshot,social.user_edits(self.db))
  self.assertEqual(cleared.state['memory_foundation']['expression_hints'],[])
  self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
  self.assertEqual(social.project_user_edits(snapshot,social.user_edits(self.db)).wire,cleared.wire)
  self.db.execute('PRAGMA query_only=ON');social.foundation_view(self.db);social.user_edits(self.db);self.db.execute('PRAGMA query_only=OFF')

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
