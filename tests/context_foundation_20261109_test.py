import copy,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from context_foundation import FOUNDATION_CURRENT,read
from context_transport import request_context_view
from context_protocol import READONLY_CURRENT,model_json
from readonly_journal import ReadonlyJournal
from decision_history import apply_changes

def foundation(version=1):
 return {'conversation_summaries':[{'id':1,'summary':'旧大摘要完整依据','level':'big'},{'id':2,'summary':'小摘要完整依据','level':'small'}], 'facts':[{'subject':'12345678','name':'偏好','value':'红茶' if version==1 else '咖啡','version':version,'source_scope':'private:12345678'}]}

def messages(base,state):
 system,history=request_context_view('固定角色',base[1:],'new','policy','rules',model_json(state))
 return [{'role':'system','content':system}]+history

class FoundationWire(unittest.TestCase):
 def test_final_message_order_and_single_copy(self):
  j=ReadonlyJournal();state={'memory_foundation':foundation(),'active_agreements':[]}
  p=j.prepare(messages([{'role':'system','content':''},{'role':'user','content':'热区消息'}],state),'stamp')
  regions=[json.loads(m['content'].split('\n',1)[1])['region'] for m in p.wire if str(m.get('content','')).startswith(FOUNDATION_CURRENT+'\n')]
  self.assertEqual(regions,['big_summary','small_summaries','long_term_facts'])
  self.assertEqual([m['role'] for m in p.wire[:4]],['system','user','user','user'])
  encoded=model_json(p.wire)
  for text in ('旧大摘要完整依据','小摘要完整依据','红茶'):self.assertEqual(encoded.count(text),1)
  self.assertNotIn('memory_foundation',next(m['content'] for m in p.wire if str(m.get('content','')).startswith(READONLY_CURRENT+'\n')))
  self.assertEqual(p.wire[4]['content'],'热区消息')
 def test_ordinary_update_waits_but_cancellation_is_immediate(self):
  j=ReadonlyJournal();raw=[{'role':'system','content':'fixed'},{'role':'user','content':'first'}]
  original={'memory_foundation':foundation(),'active_agreements':[{'id':1,'status':'accepted'}]}
  first=j.prepare(messages(raw,original),'stamp');self.assertTrue(j.commit(first))
  before=copy.deepcopy(j.last.wire)
  newer={'memory_foundation':foundation(2),'active_agreements':[{'id':1,'status':'canceled','revision':2}]}
  p=j.prepare(messages(raw+[{'role':'assistant','content':'answer'},{'role':'user','content':'next'}],newer),'stamp')
  self.assertEqual(p.wire[:len(before)],before)
  self.assertEqual(p.state['memory_foundation'],foundation())
  added=model_json(p.wire[len(before):]);self.assertNotIn('咖啡',added);self.assertIn('canceled',added)
  self.assertNotIn('旧大摘要完整依据',added)
 def test_legacy_layout_is_not_upgraded_without_checkpoint(self):
  j=ReadonlyJournal();raw=[{'role':'system','content':'fixed'},{'role':'user','content':'first'}]
  old={'state':{'facts':[{'value':'legacy fact'}],'conversation_summaries':[]}}
  first=j.prepare(messages(raw,old),'stamp');j.commit(first);prefix=model_json(first.wire)
  p=j.prepare(messages(raw+[{'role':'user','content':'next'}],{'memory_foundation':foundation(),'state':{}}),'stamp')
  self.assertEqual(model_json(p.wire[:len(first.wire)]),prefix)
  self.assertNotIn('memory_foundation',p.state)
  self.assertFalse(any(str(m.get('content','')).startswith(FOUNDATION_CURRENT+'\n') for m in p.wire))
 def test_storage_roundtrip_protects_media_and_dictionary_order(self):
  with tempfile.TemporaryDirectory() as root:
   db=sqlite3.connect(Path(root)/'j.sqlite');j=ReadonlyJournal(db,'bot_shared')
   image={'role':'user','content':[{'type':'image_url','image_url':{'detail':'high','url':'data:image/gif;base64,'+'ABCD'*9000}}]}
   p=j.prepare(messages([{'role':'system','content':'fixed'},image],{'memory_foundation':foundation()}),'stamp')
   before=model_json(p.wire);self.assertTrue(j.commit(p));db.close()
   db=sqlite3.connect(Path(root)/'j.sqlite');restored=ReadonlyJournal(db,'bot_shared')
   self.assertEqual(model_json(restored.last.wire),before);db.close()

 def test_legacy_multiple_bigs_do_not_block_append_or_publish_empty_basis(self):
  from behavior import BehaviorStore
  with tempfile.TemporaryDirectory() as root:
   store=BehaviorStore(Path(root)/'j.sqlite');store.shared_context=True
   j=ReadonlyJournal(store.db,'bot_shared');raw=[{'role':'system','content':'fixed'},{'role':'user','content':'first'}]
   old={'state':{'facts':[{'value':'old fact'}],'conversation_summaries':[{'id':1,'summary':'old big'}]}}
   p=j.prepare(messages(raw,old),store.reflection.reconstruction_stamp('bot_shared'));self.assertTrue(j.commit(p))
   prefix=model_json(p.wire)
   for scope in ('self','private:12345678'):
    identifier=store.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,0)',(scope,'legacy input','[]')).lastrowid
    store.db.execute("INSERT INTO memory_summary_levels VALUES(?,'big')",(identifier,))
   store.db.commit()
   # Simulate restart: no cached Journal object yet. Read metadata without
   # clearing history or inventing a legal checkpoint.
   candidate=read(store,{'memory':True,'memorySummary':True},'private:12345678','12345678')
   self.assertEqual(candidate['conversation_summaries'],old['state']['conversation_summaries'])
   next_state={'memory_foundation':candidate,'state':{}}
   q=j.prepare(messages(raw+[{'role':'user','content':'next'}],next_state),store.reflection.reconstruction_stamp('bot_shared'))
   self.assertEqual(model_json(q.wire[:len(p.wire)]),prefix)
   self.assertNotIn('memory_foundation',q.state)
   self.assertEqual(store.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
   store.close()

if __name__=='__main__':unittest.main()
