"""Batch retirement preserves nonmembers and removes retired image storage."""
import copy,hashlib,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import journal_storage
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from readonly_journal import ReadonlyJournal,Prepared,semantic_retire_projection,ContextPrefixViolation

def event_key(scope,event):return hashlib.sha256(model_json([scope,event]).encode()).hexdigest()
def message(event,scope='group:12345',image=None):
    parts=[{'type':'text','text':SOURCE+model_json({'scope':scope,'sender_id':'12345678','event_id':event})+'\noriginal '+event}]
    if image:parts.append({'type':'image_url','image_url':{'url':image}})
    return {'role':'user','content':parts}

class SemanticJournalTests(unittest.TestCase):
    def setUp(self):
        self.module=patch.dict(sys.modules,{'semantic_batch':SimpleNamespace(event_key=event_key)});self.module.start()
        self.db=sqlite3.connect(':memory:');self.j=ReadonlyJournal(self.db,'bot_shared')
        self.system={'role':'system','content':'tenant authority'}
        self.image='data:image/png;base64,'+'A'*9000
        self.old=message('old',image=self.image);self.new=message('new')
        self.tool={'role':'tool','tool_call_id':'exact','content':'tool evidence'}
        self.base=[self.system,self.old,{'role':'assistant','content':'old answer'},self.tool,self.new,{'role':'assistant','content':'unknown work'}]
        self.p=Prepared(1,None,copy.deepcopy(self.base),copy.deepcopy(self.base),{'old':'stale original'},1,1,'initial')
        self.j.serial=1;self.assertTrue(self.j._persist(self.p));self.j.last=self.p
        self.permit={'kind':'summary_checkpoint','id':1,'scope':'bot_shared','epoch':0,'requestId':'request','semanticBatch':True,'batchDigest':'b'*64,'batchMembers':[event_key('bot_shared',model_json(['group:12345','old']))],'batchCutoff':0}
        self.authority=SimpleNamespace(db=self.db,settle_context_reconstruction=lambda *a,**kw:True,checkpoint_issue=lambda *a,**kw:None)
    def tearDown(self):self.db.close();self.module.stop()
    def test_projection_uses_members_not_cutoff_and_keeps_other_scope(self):
        other=message('old',scope='group:67890')
        projected=semantic_retire_projection(self.base+[other],self.permit)
        self.assertEqual(projected,[self.system,self.new,self.base[-1],other])
        self.assertEqual(self.j.last.base,self.base)
    def test_immediate_retirement_no_coverage_no_archive_no_original_ids(self):
        self.assertTrue(self.j.retire_semantic_batch(self.permit,self.authority,{'memory_foundation':{'conversation_summaries':[{'text':'semantic summary'}]}}))
        self.assertNotIn('original old',model_json(self.j.last.wire))
        self.assertNotIn('stale original',model_json(self.j.last.state))
        self.assertIn('unknown work',model_json(self.j.last.wire))
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],0)
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('memory_checkpoint_archives',tables)
        row=self.db.execute('SELECT retired_events,retained_events FROM memory_checkpoint_retirements').fetchone()
        self.assertNotIn('group:',model_json(row));self.assertIn('batchDigest',row[0])
        loaded=ReadonlyJournal(self.db,'bot_shared');self.assertEqual(loaded.last.wire,self.j.last.wire)
    def test_failed_transaction_keeps_old_snapshot_and_image(self):
        before=self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
        self.authority.settle_context_reconstruction=lambda *a,**kw:False
        self.assertFalse(self.j.retire_semantic_batch(self.permit,self.authority,{}))
        self.assertEqual(self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],1)
    def test_live_candidate_not_rewritten(self):
        self.j._staged=1
        self.assertFalse(self.j.retire_semantic_batch(self.permit,self.authority,{}))
    def test_next_strict_event_needs_no_second_permit_or_retired_identity(self):
        from context_transport import EVENT_CONTEXT_CURRENT
        self.j.last.authority={'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'event':'old'},'currentGuidance':'old identity','meaning':'old'})}
        self.assertTrue(self.j.retire_semantic_batch(self.permit,self.authority,{}))
        loaded=ReadonlyJournal(self.db,'bot_shared')
        self.assertNotIn('old identity',model_json(loaded.last.authority))
        packet={'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'event':'new'},'currentGuidance':'current identity','meaning':'new event'})}
        candidate=loaded.prepare(loaded.last.base+[packet],None,strict=True)
        self.assertIsNone(candidate.reconstruction_permit)
        self.assertIn('current identity',model_json(candidate.wire))
    def test_retired_members_fence_stale_sdk_history_and_record_completion(self):
        self.db.execute('CREATE TABLE memory_semantic_batches(checkpoint_id PRIMARY KEY,scope,digest,members,state,completed)')
        self.db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,NULL)',(1,'bot_shared',self.permit['batchDigest'],model_json(self.permit['batchMembers']),'committed'));self.db.commit()
        self.assertTrue(self.j.retire_semantic_batch(self.permit,self.authority,{}))
        state,completed=self.db.execute('SELECT state,completed FROM memory_semantic_batches').fetchone()
        self.assertEqual(state,'retired');self.assertGreater(completed,0)
        # A fresh guard sees exactly the old native SDK window after restart.
        loaded=ReadonlyJournal(self.db,'bot_shared')
        packet={'role':'user','content':READONLY_CURRENT+'\n{}'}
        prepared=loaded.prepare(copy.deepcopy(self.base)+[packet],None,reconstruction_scope='bot_shared')
        self.assertNotIn('original old',model_json(prepared.base))
        self.assertIn('unknown work',model_json(prepared.base))
    def test_nonmember_drop_is_rejected_without_coverage(self):
        with self.assertRaises(ContextPrefixViolation):self.j._validate_partial_retirement(self.permit,self.authority,'bot_shared',self.p,[self.system])
    def test_retirement_capacity_counts_only_surviving_images(self):
        with patch('readonly_journal.MAX_PERSISTED_JOURNAL_BYTES',5000):
            self.assertTrue(self.j.retire_semantic_batch(self.permit,self.authority,{}))
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],0)
    def test_gc_preserves_archive_and_other_scope_references(self):
        payload=self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
        self.db.execute('CREATE TABLE memory_checkpoint_archives(scope,version,payload)')
        self.db.execute('INSERT INTO memory_checkpoint_archives VALUES(?,?,?)',(self.j.scope_key,2,payload));self.db.commit()
        other=ReadonlyJournal(self.db,'other');self.assertTrue(other._persist(self.p))
        self.assertTrue(self.j.retire_semantic_batch(self.permit,self.authority,{}))
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],2)

if __name__=='__main__':unittest.main()
