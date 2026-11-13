"""Shared batches retire exact original-scope copies in one transaction."""
import copy,hashlib,json,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import semantic_batch as batch
import journal_storage as storage
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from context_transport import EVENT_CONTEXT_CURRENT
from context_foundation import FOUNDATION_CURRENT

TARGETS=('memory_pending','memory_round_material','memory_pending_rounds','memory_hold_rounds',
         'memory_summary_coverage','memory_summary_unresolved','memory_media_retirement_choices',
         'memory_rounds','memory_message_sources')


class AllScopeCleanup(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');batch.initialize(self.db)
        self.channel='group:12345';self.other='private:12345'
        self.old=model_json([self.channel,'same-id']);self.new=model_json([self.channel,'new'])
        for table in TARGETS:
            self.db.execute('CREATE TABLE '+table+'(scope,event,raw)')
            for scope,event in (('bot_shared',self.old),(self.channel,'same-id'),
                                ('bot_shared',self.new),(self.channel,'new'),(self.other,'same-id')):
                self.db.execute('INSERT INTO '+table+' VALUES(?,?,?)',(scope,event,'raw '+event))
        self.db.executescript('''CREATE TABLE pipeline_scope_work(scope,event);
          CREATE TABLE pipeline_work_inputs(scope,event,status,payload);
          CREATE TABLE memory_summaries(scope,text,sources);
          CREATE TABLE memory_core_notes(note,sources);
          CREATE TABLE memory_summary_people(summary_id,subject);
          CREATE TABLE memory_summary_origins(summary_id,scope);
          CREATE TABLE fishbot_readonly_journals(scope PRIMARY KEY,version,state,payload,sha,updated);
          CREATE TABLE fishbot_readonly_journal_blobs(scope,sha,data,PRIMARY KEY(scope,sha));
          CREATE TABLE memory_checkpoint_archives(checkpoint_id,scope,version,payload,sha);''')
        for scope in ('bot_shared',self.channel,self.other):
            self.db.execute('INSERT INTO memory_summaries VALUES(?,?,?)',(scope,'semantic product','["old source"]'))
        self.db.execute('INSERT INTO memory_core_notes VALUES(?,?)',('semantic core','["old source"]'))
        self.db.execute('INSERT INTO memory_summary_people VALUES(1,12345)')
        self.db.execute('INSERT INTO memory_summary_origins VALUES(1,?)',(self.channel,))
        self.db.execute("INSERT INTO memory_semantic_batches VALUES(1,'bot_shared','digest',?,0,'retired',0,0)",
                        (model_json([batch.event_key('bot_shared',self.old)]),))
        self.system={'role':'system','content':'exact System'}
        def message(scope,event):
            return {'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':'12345','event_id':event})+'\nraw '+event}
        self.messages=[self.system,{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'readonlyChanges':[['set',['x'],'old derived packet']]})},
                       message(self.channel,'same-id'),{'role':'assistant','content':'old answer'},
                       message(self.channel,'new'),message(self.other,'same-id')]
        self.expected=[self.system,*self.messages[-2:]]
        self.key=model_json(self.channel)
        value={'scope':self.key,'base':self.messages,'wire':self.messages,
               'state':{'recent_messages':[{'scope':self.channel,'event_id':'same-id','text':'old'},
                                            {'scope':self.channel,'event_id':'new','text':'new'}],
                        'facts':[{'event':'same-id','value':'semantic product'}]}}
        payload,blobs=storage.encode(value)
        for sha,raw in blobs.items():self.db.execute('INSERT INTO fishbot_readonly_journal_blobs VALUES(?,?,?)',(self.key,sha,raw))
        sha=hashlib.sha256(payload.encode()).hexdigest()
        self.db.execute('INSERT INTO memory_checkpoint_archives VALUES(7,?,2,?,?)',(self.key,payload,sha))
        # Purge follows a licensed Journal replacement. Only recovery copies
        # still contain the retired batch; cleanup has no active-wire authority.
        foundation={'facts':[{'value':'checkpoint-owned fact'}],
                    'conversation_summaries':[{'text':'checkpoint-owned summary'}]}
        packets=[{'role':'user','content':prefix+'\n'+model_json(body)} for prefix,body in (
            (FOUNDATION_CURRENT,foundation),(READONLY_CURRENT,{'readonlyChanges':[]}),
            (EVENT_CONTEXT_CURRENT,{'event':'new'}))]
        self.active_value=copy.deepcopy(value)
        self.active_value.update(base=self.expected,wire=[self.system,*packets,*self.expected[1:]],
                                 retirement='checkpoint:1',revision=19,updated=123.5,
                                 pending_system=None)
        self.active_value['state']['recent_messages']=self.active_value['state']['recent_messages'][1:]
        self.active_value['state']['memory_foundation']=foundation
        active_payload,active_blobs=storage.encode(self.active_value)
        self.assertEqual(active_blobs,{})
        self.db.execute("INSERT INTO fishbot_readonly_journals VALUES(?,2,'ready',?,?,123.5)",
                        (self.key,active_payload,hashlib.sha256(active_payload.encode()).hexdigest()))
        self.db.commit()

    def tearDown(self):self.db.close()

    def test_original_aliases_and_archives_are_cleaned_without_rewriting_active(self):
        committed=self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall()
        with self.db:self.assertTrue(batch.purge(self.db,1))
        for table in TARGETS:
            rows=set(self.db.execute('SELECT scope,event FROM '+table))
            self.assertEqual(rows,{('bot_shared',self.new),(self.channel,'new'),(self.other,'same-id')},table)
        active=storage.read_snapshot(self.db,self.key)
        self.assertEqual(active,self.active_value)
        self.assertEqual(self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall(),committed)
        self.assertEqual(active['state']['recent_messages'],[{'scope':self.channel,'event_id':'new','text':'new'}])
        self.assertEqual(active['state']['facts'],[{'event':'same-id','value':'semantic product'}])
        payload,sha=self.db.execute('SELECT payload,sha FROM memory_checkpoint_archives').fetchone()
        self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(),sha)
        archive=storage.decode(payload,self.key,lambda *args:None)
        for field in ('base','wire'):self.assertEqual(archive[field],self.expected)
        self.assertNotIn('old derived packet',model_json(archive))
        self.assertEqual(archive['state']['recent_messages'],active['state']['recent_messages'])
        self.assertEqual(list(self.db.execute('SELECT sources FROM memory_summaries')),[('[]',)]*3)
        self.assertEqual(self.db.execute('SELECT note,sources FROM memory_core_notes').fetchone(),('semantic core','[]'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_people').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_origins').fetchone()[0],1)
        with self.db:self.assertTrue(batch.purge(self.db,1))
        self.assertEqual(storage.read_snapshot(self.db,self.key),active)
        self.assertEqual(self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall(),committed)

    def test_operator_evidence_removes_every_exact_raw_alias(self):
        self.db.execute('CREATE TABLE fishbot_operator_session_archives(authorization,scope,archive,sha)')
        version,payload,sha=self.db.execute('SELECT version,payload,sha FROM memory_checkpoint_archives').fetchone()
        archive={'journal':{'version':version,'payload':payload,'sha':sha},'evidence':{
            table:{'columns':['scope','event','raw'],'rows':list(self.db.execute('SELECT * FROM '+table))}
            for table in TARGETS}}
        encoded=model_json(archive)
        self.db.execute('INSERT INTO fishbot_operator_session_archives VALUES(?,?,?,?)',('grant',self.key,encoded,hashlib.sha256(encoded.encode()).hexdigest()))
        self.db.commit()
        with self.db:batch.purge(self.db,1)
        saved=json.loads(self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0])
        for table in TARGETS:
            self.assertEqual({tuple(row[:2]) for row in saved['evidence'][table]['rows']},
                             {('bot_shared',self.new),(self.channel,'new'),(self.other,'same-id')},table)

    def test_operator_without_journal_still_retires_exact_evidence(self):
        self.db.execute('CREATE TABLE fishbot_operator_session_archives(authorization,scope,archive,sha)')
        archive={'journal':None,'evidence':{table:{'columns':['scope','event','raw'],'rows':list(self.db.execute('SELECT * FROM '+table))} for table in TARGETS}}
        raw=model_json(archive)
        self.db.execute('INSERT INTO fishbot_operator_session_archives VALUES(?,?,?,?)',('empty-journal',self.key,raw,hashlib.sha256(raw.encode()).hexdigest()));self.db.commit()
        with self.db:batch.purge(self.db,1)
        saved=json.loads(self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0]);self.assertIsNone(saved['journal'])
        for table in TARGETS:self.assertEqual(len(saved['evidence'][table]['rows']),3)

    def test_successful_purge_does_not_commit_the_callers_transaction(self):
        self.assertFalse(self.db.in_transaction)
        batch.purge(self.db,1)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        for table in TARGETS:self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],5)
        self.assertEqual(storage.read_snapshot(self.db,self.key),self.active_value)

    def test_reacquired_original_or_shared_work_aborts_before_any_raw_cleanup(self):
        for logical,event in ((self.channel,'same-id'),('bot_shared',self.old)):
            with self.subTest(scope=logical):
                self.db.execute('INSERT INTO pipeline_scope_work VALUES(?,?)',(logical,event));self.db.commit()
                with self.assertRaisesRegex(ValueError,'semantic_batch_work_still_active'):batch.purge(self.db,1)
                for table in TARGETS:self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],5)
                self.assertEqual(storage.read_snapshot(self.db,self.key),self.active_value)
                self.db.execute('DELETE FROM pipeline_scope_work');self.db.commit()

    def test_other_scope_and_new_batch_owned_work_are_preserved(self):
        for scope,event in ((self.other,'same-id'),(self.channel,'new')):
            self.db.execute("INSERT INTO pipeline_work_inputs VALUES(?,?,'owned','new active payload')",(scope,event))
        with self.db:batch.purge(self.db,1)
        self.assertEqual(list(self.db.execute('SELECT status,payload FROM pipeline_work_inputs')),[('owned','new active payload')]*2)

    def test_failure_after_raw_deletion_rolls_back_without_caller_rollback(self):
        before='\n'.join(self.db.iterdump())
        with patch.object(storage,'prune_unreferenced_blobs',side_effect=RuntimeError('GC failed')):
            with self.assertRaisesRegex(RuntimeError,'GC failed'):batch.purge(self.db,1)
        self.assertEqual('\n'.join(self.db.iterdump()),before)
        for table in TARGETS:self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],5)
        self.assertEqual(storage.read_snapshot(self.db,self.key),self.active_value)
        self.assertEqual(self.db.execute('SELECT sources FROM memory_core_notes').fetchone()[0],'["old source"]')

    def test_active_retired_material_fails_closed_without_erasing_foundation(self):
        # Exercise both the batch's canonical Journal and cross-scope copies.
        # A state-only stale row used to trigger deletion of every fixed packet
        # even when the submitted wire had already been legally rebuilt.
        for logical,location in (('bot_shared','state'),(self.channel,'state'),
                                 (self.other,'state'),(self.channel,'wire')):
            with self.subTest(scope=logical,location=location):
                key=model_json(logical);value=copy.deepcopy(self.active_value);value['scope']=key
                if location=='state':
                    value['state']['recent_messages'].append(
                        {'scope':self.channel,'event_id':'same-id','text':'retired active original'})
                else:
                    value['wire'].insert(1,copy.deepcopy(self.messages[2]))
                payload,blobs=storage.encode(value);self.assertEqual(blobs,{})
                self.db.execute("INSERT OR REPLACE INTO fishbot_readonly_journals VALUES(?,2,'ready',?,?,123.5)",
                                (key,payload,hashlib.sha256(payload.encode()).hexdigest()))
                self.db.commit();before='\n'.join(self.db.iterdump())
                with self.assertRaisesRegex(ValueError,'^semantic_active_journal_reconstruction_required$'):
                    batch.purge(self.db,1)
                self.assertEqual('\n'.join(self.db.iterdump()),before)
                self.assertEqual(storage.read_snapshot(self.db,key),value)
                for table in TARGETS:
                    self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],5,table)
                # Restore the independent subcase without altering archives.
                self.db.execute('DELETE FROM fishbot_readonly_journals WHERE scope=?',(key,))
                payload,_=storage.encode(self.active_value)
                self.db.execute("INSERT OR REPLACE INTO fishbot_readonly_journals VALUES(?,2,'ready',?,?,123.5)",
                                (self.key,payload,hashlib.sha256(payload.encode()).hexdigest()))
                self.db.commit()

    def legacy_archive(self,created=50,grant=True):
        self.db.execute('UPDATE memory_semantic_batches SET cutoff=100')
        self.db.execute('CREATE TABLE fishbot_operator_session_archives(authorization PRIMARY KEY,scope,archive,sha,created)')
        self.db.execute('CREATE TABLE memory_semantic_discard_audit(checkpoint_id,administrator,request_id,missing_count,incomplete_count,conflict_count,created)')
        value={'scope':self.key,'base':[self.system,{'role':'user','content':'UNBOUND ORIGINAL'}],
               'wire':[self.system,{'role':'user','content':'UNBOUND ORIGINAL'}],'state':{'raw':'OLD CACHE'}}
        payload,_=storage.encode(value)
        archive={'journal':{'version':2,'payload':payload,'sha':hashlib.sha256(payload.encode()).hexdigest()},
                 'sdkConversations':[['OLD NATIVE HISTORY']],
                 'evidence':{'dialogue_feedback':{'columns':['event'],'rows':[['unbound']]}}}
        raw=model_json(archive)
        self.db.execute('INSERT INTO fishbot_operator_session_archives VALUES(?,?,?,?,?)',('archive',self.key,raw,hashlib.sha256(raw.encode()).hexdigest(),created))
        if grant:self.db.execute("INSERT INTO memory_semantic_discard_audit VALUES(1,'administrator','request',0,0,0,1)")
        self.db.commit()

    def test_admin_loss_grant_cleans_old_unbound_archives_and_native_copies(self):
        self.legacy_archive()
        with self.db:batch.purge(self.db,1)
        raw,sha=self.db.execute('SELECT archive,sha FROM fishbot_operator_session_archives').fetchone()
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),sha)
        self.assertNotIn('UNBOUND ORIGINAL',raw);self.assertNotIn('OLD NATIVE HISTORY',raw);self.assertNotIn('OLD CACHE',raw)
        archive=json.loads(raw);journal=archive['journal'];value=storage.decode(journal['payload'],self.key,lambda *_:None)
        self.assertEqual(value['wire'],[self.system]);self.assertEqual(value['base'],[self.system])
        self.assertEqual(archive['evidence']['dialogue_feedback']['rows'],[])

    def test_default_and_new_archive_do_not_gain_loss_permission(self):
        self.legacy_archive(grant=False)
        with self.db:batch.purge(self.db,1)
        self.assertIn('UNBOUND ORIGINAL',self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0])
        self.db.execute("INSERT INTO memory_semantic_discard_audit VALUES(1,'administrator','request',0,0,0,1)")
        self.db.execute('UPDATE fishbot_operator_session_archives SET created=101');self.db.commit()
        with self.db:batch.purge(self.db,1)
        self.assertIn('UNBOUND ORIGINAL',self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0])

    def test_owned_input_blocks_unbound_archive_disposal(self):
        self.legacy_archive()
        self.db.execute("INSERT INTO pipeline_work_inputs VALUES(?,'unbound','owned','real input')",(self.channel,));self.db.commit()
        with self.assertRaisesRegex(ValueError,'semantic_archive_input_still_owned'):batch.purge(self.db,1)
        self.assertIn('UNBOUND ORIGINAL',self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0])

    def test_obsolete_coverage_index_leaves_but_new_hot_input_proof_survives(self):
        self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?)',(self.channel,'orphan-proof','obsolete proof'))
        self.db.execute("INSERT INTO pipeline_work_inputs VALUES(?,'owned-proof','owned','actual input')",(self.channel,))
        self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?)',(self.channel,'owned-proof','protected proof'))
        self.db.commit()
        with self.db:batch.purge(self.db,1)
        self.assertIsNone(self.db.execute("SELECT 1 FROM memory_summary_coverage WHERE event='orphan-proof'").fetchone())
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM memory_summary_coverage WHERE event='owned-proof'").fetchone())
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM memory_summary_coverage WHERE event='new'").fetchone())


if __name__=='__main__':unittest.main()
