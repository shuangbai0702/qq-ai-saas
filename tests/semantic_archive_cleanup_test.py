"""Retired batch copies leave archives atomically; other evidence survives."""
import copy,hashlib,json,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import journal_storage as storage
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from semantic_batch import event_key
from context_transport import EVENT_CONTEXT_CURRENT
from context_foundation import FOUNDATION_CURRENT


class SemanticArchiveCleanup(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.scope='group:12345';self.key=model_json(self.scope)
        self.db.executescript('''CREATE TABLE memory_semantic_batches(checkpoint_id PRIMARY KEY,scope,members,state);
          CREATE TABLE fishbot_readonly_journal_blobs(scope,sha,data,PRIMARY KEY(scope,sha));
          CREATE TABLE fishbot_readonly_journals(scope,version,payload);
          CREATE TABLE memory_checkpoint_archives(checkpoint_id PRIMARY KEY,scope,version,payload,sha);
          CREATE TABLE fishbot_operator_session_archives(authorization PRIMARY KEY,scope,archive,sha,new_session_state);
          CREATE TRIGGER operator_session_archive_immutable BEFORE UPDATE OF archive,sha ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'immutable'); END;
          CREATE TRIGGER operator_session_archive_no_delete BEFORE DELETE ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'immutable'); END;''')
        self.db.execute('INSERT INTO memory_semantic_batches VALUES(1,?,?,?)',(self.scope,model_json([event_key(self.scope,'old')]),'retired'))
        self.system={'role':'system','content':'exact System '+('S'*40)}
        def message(event,image):return {'role':'user','content':[{'type':'text','text':SOURCE+model_json({'scope':self.scope,'sender_id':'12345678','event_id':event})+'\nraw '+event},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+image*9000}}]}
        self.old=message('old','A');self.new=message('new','B')
        self.state={'recent_messages':[{'event_id':'old','scope':self.scope,'text':'retired row'},{'event_id':'new','scope':self.scope,'text':'uncompressed row'}],'facts':[{'event':'old','value':'semantic product'}]}
        self.value={'scope':self.key,'base':[self.system,self.old,{'role':'assistant','content':'old answer'},self.new],
          'wire':[self.system,{'role':'user','content':READONLY_CURRENT+'\n'+model_json(self.state)},self.old,{'role':'assistant','content':'old answer'},self.new], 'state':self.state}
        self.payload=self.encode(self.value)
        self.db.execute('INSERT INTO memory_checkpoint_archives VALUES(1,?,2,?,?)',(self.key,self.payload,self.sha(self.payload)))
        archive={'journal':{'scope':self.key,'version':2,'payload':self.payload,'sha':self.sha(self.payload)},'evidence':{
          'memory_round_material':{'columns':['scope','event','material'],'rows':[[self.scope,'old','retired material'],[self.scope,'new','new material'],['group:67890','old','other group']]},
          'fishbot_system_scope_contract':{'columns':['scope','system'],'rows':[[self.scope,'exact contract']]}}}
        self.archive=model_json(archive)
        self.db.execute("INSERT INTO fishbot_operator_session_archives VALUES('grant',?,?,?,'consumed')",(self.key,self.archive,self.sha(self.archive)))
        active=copy.deepcopy(self.value);active['base']=[self.system,self.new];active['wire']=active['base'];active['state']={}
        self.db.execute('INSERT INTO fishbot_readonly_journals VALUES(?,2,?)',(self.key,self.encode(active)))
        self.db.commit()

    def tearDown(self):self.db.close()
    def sha(self,text):return hashlib.sha256(text.encode()).hexdigest()
    def encode(self,value):
        payload,blobs=storage.encode(value)
        for digest,raw in blobs.items():self.db.execute('INSERT OR IGNORE INTO fishbot_readonly_journal_blobs VALUES(?,?,?)',(self.key,digest,raw))
        return payload
    def decode(self,payload):return storage.decode(payload,self.key,lambda scope,digest:self.db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(scope,digest)).fetchone()[0])

    def test_exact_cleanup_preserves_system_nonmembers_products_and_restores_guards(self):
        with self.db:self.assertEqual(storage.retire_archived_members(self.db,1),2)
        payload,sha=self.db.execute('SELECT payload,sha FROM memory_checkpoint_archives').fetchone()
        value=self.decode(payload);self.assertEqual(self.sha(payload),sha)
        self.assertEqual(value['base'],[self.system,self.new]);self.assertEqual(value['state']['facts'],self.state['facts'])
        self.assertNotIn('retired row',model_json(value));self.assertIn('uncompressed row',model_json(value))
        encoded,sha=self.db.execute('SELECT archive,sha FROM fishbot_operator_session_archives').fetchone();archive=json.loads(encoded)
        self.assertEqual(self.sha(encoded),sha)
        self.assertEqual(archive['evidence']['memory_round_material']['rows'],[[self.scope,'new','new material'],['group:67890','old','other group']])
        self.assertEqual(archive['evidence']['fishbot_system_scope_contract']['rows'],[[self.scope,'exact contract']])
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],1)
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute("UPDATE fishbot_operator_session_archives SET archive='bad'")
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute('DELETE FROM fishbot_operator_session_archives')
        self.db.rollback()
        with self.db:self.assertEqual(storage.retire_archived_members(self.db,1),0)

    def test_unretired_batch_never_changes_archive(self):
        self.db.execute("UPDATE memory_semantic_batches SET state='committed'")
        self.assertEqual(storage.retire_archived_members(self.db,1),0)
        self.assertEqual(self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0],self.archive)

    def test_failure_rolls_back_payload_blob_and_trigger_changes(self):
        with patch('journal_storage.prune_unreferenced_blobs',side_effect=RuntimeError('forced GC failure')):
            with self.assertRaises(RuntimeError):storage.retire_archived_members(self.db,1)
        self.assertEqual(self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0],self.archive)
        self.assertEqual(self.db.execute('SELECT payload FROM memory_checkpoint_archives').fetchone()[0],self.payload)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0],2)
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute("UPDATE fishbot_operator_session_archives SET archive='bad'")

    def test_identityless_delta_and_other_derived_packets_are_removed_whole(self):
        value=copy.deepcopy(self.value)
        developer={'role':'developer','content':READONLY_CURRENT+'\nkeep exact developer instruction'}
        foreign=copy.deepcopy(self.new);foreign['content'][0]['text']=foreign['content'][0]['text'].replace(self.scope,'group:67890')
        packets=[{'role':'user','content':prefix+'\n'+model_json({'readonlyChanges':[['set',['recent_messages',0,'text'],'ambiguous old raw text']]})} for prefix in (READONLY_CURRENT,EVENT_CONTEXT_CURRENT,FOUNDATION_CURRENT)]
        for field in ('base','wire'):value[field]=[self.system,developer,*packets,self.old,{'role':'assistant','content':'old answer'},self.new,foreign]
        payload=self.encode(value)
        self.db.execute('UPDATE memory_checkpoint_archives SET payload=?,sha=?',(payload,self.sha(payload)))
        with self.db:self.assertEqual(storage.retire_archived_members(self.db,1),2)
        saved=self.decode(self.db.execute('SELECT payload FROM memory_checkpoint_archives').fetchone()[0])
        for field in ('base','wire'):self.assertEqual(saved[field],[self.system,developer,self.new,foreign])
        self.assertNotIn('ambiguous old raw text',model_json(saved))
        self.assertEqual(saved['state']['recent_messages'],[self.state['recent_messages'][1]])
        self.assertEqual(saved['state']['facts'],self.state['facts'])

    def test_unrelated_scope_archive_is_unchanged(self):
        foreign=copy.deepcopy(self.value);foreign_key=model_json('group:67890');foreign['scope']=foreign_key
        for field in ('base','wire'):
            foreign[field]=[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'readonlyChanges':[['set',['recent_messages',0,'text'],'foreign raw']]})}]
        foreign['state']={}
        payload=model_json(foreign)
        self.db.execute('INSERT INTO memory_checkpoint_archives VALUES(2,?,1,?,?)',(foreign_key,payload,self.sha(payload)))
        with self.db:storage.retire_archived_members(self.db,1)
        self.assertEqual(self.db.execute('SELECT payload,sha FROM memory_checkpoint_archives WHERE checkpoint_id=2').fetchone(),(payload,self.sha(payload)))

    def test_legacy_inline_snapshot_becomes_clean_version_two(self):
        payload=model_json(self.value)
        self.db.execute('UPDATE memory_checkpoint_archives SET version=1,payload=?,sha=?',(payload,self.sha(payload)))
        with self.db:storage.retire_archived_members(self.db,1)
        version,payload=self.db.execute('SELECT version,payload FROM memory_checkpoint_archives').fetchone()
        self.assertEqual(version,2);self.assertEqual(self.decode(payload)['base'],[self.system,self.new])


if __name__=='__main__':unittest.main()
