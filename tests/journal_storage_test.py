import copy,hashlib,json,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import journal_storage
from readonly_journal import ReadonlyJournal,Prepared,ContextPrefixViolation
from context_protocol import model_json

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.j=ReadonlyJournal(self.db,'bot_shared')
        self.media='data:image/gif;base64,'+'AbCd/+'*2000
        self.a={'role':'system','content':'固定\n规则'}
        self.b={'content':[{'type':'image_url','image_url':{'url':self.media,'detail':'high'}}],'role':'user'}
        # Interleaved readonly packets and replaced native views mean base is
        # neither a prefix nor an ordered subsequence of committed wire.
        self.p=Prepared(1,None,[self.b,self.a,{'role':'user','content':'native-only'}],
            [self.a,{'role':'user','content':'readonly packet'},self.b],{},1,1,'initial')
        self.j.serial=1
    def tearDown(self):self.db.close()
    def save(self):self.assertTrue(self.j._persist(self.p))
    def test_exact_nonprefix_base_wire_and_original_media_restored(self):
        before=copy.deepcopy(self.p);self.save()
        version,payload=self.db.execute('SELECT version,payload FROM fishbot_readonly_journals').fetchone()
        self.assertEqual(version,2);self.assertNotIn(self.media,payload)
        stored=json.loads(payload)['value']
        self.assertEqual(stored['base'][:2],[{'wire':2},{'wire':0}])
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],1)
        loaded=ReadonlyJournal(self.db,'bot_shared')
        self.assertIsNone(loaded.recovery_error)
        self.assertEqual(model_json(loaded.last.wire),model_json(before.wire))
        self.assertEqual(model_json(loaded.last.base),model_json(before.base))
        self.assertEqual(self.p,before)
        loaded.last.base[0]['content'].append({'type':'text','text':'new'})
        self.assertEqual(loaded.last.wire,before.wire)
    def test_json_object_key_order_is_not_equivalent_for_reference(self):
        self.p.base=[{'content':'固定\n规则','role':'system'}]
        self.save();stored=json.loads(self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0])
        self.assertIn('inline',stored['value']['base'][0])
        self.assertEqual(model_json(ReadonlyJournal(self.db,'bot_shared').last.base),model_json(self.p.base))
    def test_v1_reads_without_rewriting(self):
        value={k:getattr(self.p,k) for k in ('base','wire','state','turns','at','group_turn','base_chars','detail','usage','authority','retirement')}
        value={'scope':self.j.scope_key,**value};payload=journal_storage.dumps(value)
        self.db.execute('INSERT INTO fishbot_readonly_journals VALUES(?,1,?,?,?,0)',(self.j.scope_key,'ready',payload,hashlib.sha256(payload.encode()).hexdigest()));self.db.commit()
        loaded=ReadonlyJournal(self.db,'bot_shared');self.assertIsNone(loaded.recovery_error)
        self.assertEqual(model_json(loaded.last.wire),model_json(self.p.wire))
        self.assertEqual(self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],payload)
    def test_missing_corrupt_and_wrong_scope_blobs_fail_closed(self):
        for operation in ('missing','corrupt','scope'):
            self.save()
            if operation=='missing':self.db.execute('DELETE FROM fishbot_readonly_journal_blobs')
            elif operation=='corrupt':self.db.execute("UPDATE fishbot_readonly_journal_blobs SET data=x'00'")
            else:self.db.execute("UPDATE fishbot_readonly_journal_blobs SET scope='another-bot'")
            self.db.commit()
            self.assertEqual(ReadonlyJournal(self.db,'bot_shared').recovery_error,'journal_snapshot_invalid')
            self.db.execute('DELETE FROM fishbot_readonly_journal_blobs');self.db.commit()
    def test_quota_includes_blobs_and_refusal_preserves_existing(self):
        self.save();before=self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
        self.p.wire.append({'role':'user','content':'data:image/png;base64,'+'X'*9000})
        size=self.db.execute('SELECT sum(length(data)) FROM fishbot_readonly_journal_blobs').fetchone()[0]
        with patch('readonly_journal.MAX_PERSISTED_JOURNAL_BYTES',size+len(before.encode())+100):
            self.assertFalse(self.j._persist(self.p))
        self.assertEqual(self.db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],1)
    def test_snapshot_failure_rolls_back_blob_insertion(self):
        self.db.execute("CREATE TRIGGER reject_snapshot BEFORE INSERT ON fishbot_readonly_journals BEGIN SELECT RAISE(ABORT,'simulated disk write failure'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.j._persist(self.p)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journals').fetchone()[0],0)
    def test_stage_hash_detects_inline_and_blob_string_mutation(self):
        self.assertTrue(self.j.stage_candidate(self.p))
        self.p.wire[2]['content'][0]['image_url']['url']+='A'
        with self.assertRaises(ContextPrefixViolation):self.j.stage_candidate(self.p)
    def test_blob_lifetime_no_gc_and_scope_isolation(self):
        self.save();other=ReadonlyJournal(self.db,'other_scope')
        self.assertTrue(other._persist(self.p))
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],2)
        self.p.base=[self.a];self.p.wire=[self.a];self.save()
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],2)
        self.assertEqual(model_json(ReadonlyJournal(self.db,'other_scope').last.wire[2]),model_json(self.b))

if __name__=='__main__':unittest.main()
