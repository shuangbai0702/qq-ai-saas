"""Archive recovery uses original protocol blocks, never shortened pending text."""
import ast,copy,hashlib,json,sys,tempfile,time,unittest
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from context_protocol import SOURCE,model_json
import journal_storage,summary_material,message_provenance
from semantic_batch import event_key


class ArchiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.channel='private:12345678';self.key=model_json(self.channel);self.event=model_json([self.channel,'original'])
        self.db.executescript('''CREATE TABLE memory_checkpoint_archives(scope,version,payload,sha);
          CREATE TABLE fishbot_operator_session_archives(scope,archive,sha,new_session_state);
          CREATE TABLE IF NOT EXISTS fishbot_readonly_journal_blobs(scope,sha,data,PRIMARY KEY(scope,sha));''')
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('bot_shared','12345678',self.event,'TRUNCATED DISPLAY ONLY','',time.time()))
        self.message={'role':'user','content':[{'type':'text','text':SOURCE+model_json({'scope':self.channel,'sender_id':'12345678','event_id':'original'})+'\n'+('完整原文'*240)+'末尾关键信息'},
          {'type':'text','text':'<Quoted Message>其他人原话</Quoted Message>'},
          {'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*9000}}]}
        self.tools=[{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'look','arguments':'{"query":"原始参数"}'}}]},
          {'role':'tool','tool_call_id':'call1','content':'原始工具回执'}, {'role':'assistant','content':'原始模型答复'}]
        message_provenance.bind_model_input(self.db,'bot_shared',self.channel,'original','12345678',self.message)
        self.db.commit()
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def sha(self,text):return hashlib.sha256(text.encode()).hexdigest()
    def archive(self,operator=False,messages=None,version=2):
        messages=copy.deepcopy(messages if messages is not None else [self.message]+self.tools)
        value={'scope':self.key,'base':messages,'wire':messages,'state':{}}
        if version==1:payload=model_json(value)
        else:
            payload,blobs=journal_storage.encode(value)
            for digest,raw in blobs.items():self.db.execute('INSERT OR IGNORE INTO fishbot_readonly_journal_blobs VALUES(?,?,?)',(self.key,digest,raw))
        if operator:
            raw=model_json({'journal':{'scope':self.key,'version':version,'payload':payload,'sha':self.sha(payload)}})
            self.db.execute('INSERT INTO fishbot_operator_session_archives(scope,archive,sha) VALUES(?,?,?)',(self.key,raw,self.sha(raw)))
        else:self.db.execute('INSERT INTO memory_checkpoint_archives VALUES(?,?,?,?)',(self.key,version,payload,self.sha(payload)))
        self.db.commit();return payload
    def recover(self):return summary_material.recover_archived_materials(self.db,'bot_shared',[self.event])

    def test_private_archive_recovers_canonical_shared_text_images_and_tools_without_rewrite(self):
        payload=self.archive();report=self.recover()
        self.assertEqual(report,{'recovered':1,'missing':[],'invalid_archives':0})
        raw=self.db.execute('SELECT material FROM memory_round_material').fetchone()[0];item=json.loads(raw)
        self.assertTrue(item['complete']);self.assertEqual(item['version'],3);self.assertEqual(item['event'],self.event)
        self.assertIn('末尾关键信息',item['text']);self.assertIn('其他人原话',item['text']);self.assertNotIn('TRUNCATED',item['text'])
        self.assertEqual(item['images'],[self.message['content'][-1]['image_url']['url']])
        self.assertEqual(item['tools'][0]['result'],'原始工具回执');self.assertEqual(item['native_reply'],'原始模型答复')
        self.assertEqual(item['reply'],'');self.assertIsNone(item['input_settlement'])
        self.assertEqual(self.db.execute('SELECT payload FROM memory_checkpoint_archives').fetchone()[0],payload)
        self.db.rollback();self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone(),'helper must not commit caller writes')

    def test_operator_outer_and_inner_checksums_are_both_required(self):
        self.archive(operator=True,version=1)
        original=self.db.execute('SELECT archive FROM fishbot_operator_session_archives').fetchone()[0]
        for tamper_outer in (True,False):
            raw=json.loads(original);raw['journal']['sha']='0'*64;encoded=model_json(raw)
            self.db.execute('UPDATE fishbot_operator_session_archives SET archive=?,sha=?',(encoded,'0'*64 if tamper_outer else self.sha(encoded)))
            report=self.recover();self.assertEqual(report['invalid_archives'],1);self.assertEqual(report['missing'],[self.event])
            self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())
        self.db.execute('UPDATE fishbot_operator_session_archives SET archive=?,sha=?',(original,self.sha(original)))
        self.assertEqual(self.recover()['recovered'],1)

    def test_missing_blob_unknown_media_and_pending_text_never_prove_complete(self):
        self.archive();self.db.execute('DELETE FROM fishbot_readonly_journal_blobs')
        report=self.recover();self.assertEqual(report['invalid_archives'],1);self.assertEqual(report['missing'],[self.event])
        self.db.execute('DELETE FROM memory_checkpoint_archives')
        unknown=copy.deepcopy(self.message);unknown['content'].append({'type':'audio_url','audio_url':{'url':'data:audio/test'}})
        self.archive(messages=[unknown])
        report=self.recover();self.assertEqual(report['recovered'],0);self.assertEqual(report['missing'],[self.event])
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())

    def test_wrong_author_foreign_scope_and_unknown_version_do_not_restore(self):
        wrong=copy.deepcopy(self.message);wrong['content'][0]['text']=wrong['content'][0]['text'].replace('12345678','87654321')
        self.archive(messages=[wrong]);self.assertEqual(self.recover()['missing'],[self.event])
        self.db.execute('DELETE FROM memory_checkpoint_archives');self.archive(version=99)
        report=self.recover();self.assertEqual(report['invalid_archives'],1);self.assertEqual(report['missing'],[self.event])
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())

    def test_unique_trusted_original_survives_obsolete_or_missing_input_hash(self):
        for signature in ('0'*64,None):
            bound=json.loads(self.db.execute("SELECT data FROM memory_message_sources WHERE direction='model'").fetchone()[0])
            if signature:bound['input_digest']=signature
            else:bound.pop('input_digest',None)
            self.db.execute("UPDATE memory_message_sources SET data=? WHERE direction='model'",(model_json(bound),))
            enriched=copy.deepcopy(self.message);enriched['name']='sdk_user';enriched['content'][0]['cache_control']={'type':'ephemeral'}
            self.archive(messages=[enriched]+self.tools)
            self.assertEqual(self.recover()['recovered'],1)
            item=json.loads(self.db.execute('SELECT material FROM memory_round_material').fetchone()[0])
            self.assertTrue(item['complete']);self.assertEqual(item['native_reply'],'原始模型答复')
            self.assertIsNone(item['input_settlement']);self.assertTrue(all(o['owner'] is None for o in item['native_outputs']))
            self.db.execute('DELETE FROM memory_round_material');self.db.execute('DELETE FROM memory_checkpoint_archives')

    def test_conflicting_originals_require_exact_input_binding(self):
        for suffix in ('first change','second change'):
            changed=copy.deepcopy(self.message);changed['content'][0]['text']+=suffix
            self.archive(messages=[changed]+self.tools)
        self.assertEqual(self.recover()['missing'],[self.event])
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())
        self.archive()
        self.assertEqual(self.recover()['recovered'],1)
        item=json.loads(self.db.execute('SELECT material FROM memory_round_material').fetchone()[0])
        self.assertNotIn('first change',item['text']);self.assertNotIn('second change',item['text'])

    def test_settlement_explanation_metadata_does_not_make_real_content_invalid(self):
        bound=json.loads(self.db.execute("SELECT data FROM memory_message_sources WHERE direction='model'").fetchone()[0])
        bound['settlement']={'status':'superseded','attempted':0,'confirmed':0,'planned':0,'confirmed_messages':0,'parent_work':'another work','basis':'explicit_no_response'}
        self.db.execute("UPDATE memory_message_sources SET data=? WHERE direction='model'",(model_json(bound),))
        self.archive(messages=[self.message]);self.assertEqual(self.recover()['recovered'],1)
        item=json.loads(self.db.execute('SELECT material FROM memory_round_material').fetchone()[0])
        self.assertTrue(summary_material.valid_material(item,'bot_shared'));self.assertEqual(item['input_settlement'],{k:v for k,v in bound['settlement'].items() if k not in ('parent_work','basis')})
        stored=json.loads(self.db.execute("SELECT data FROM memory_message_sources WHERE direction='model'").fetchone()[0])
        self.assertEqual(stored,bound,'material projection must never rewrite the settlement ledger')

    def test_successive_output_prefix_is_preserved_but_divergent_outputs_stay_missing(self):
        self.archive(messages=[self.message]+self.tools[:2])
        self.archive()
        self.assertEqual(self.recover()['recovered'],1)
        item=json.loads(self.db.execute('SELECT material FROM memory_round_material').fetchone()[0])
        self.assertEqual(item['native_reply'],'原始模型答复');self.assertEqual(len(item['native_outputs']),3)
        self.db.execute('DELETE FROM memory_round_material')
        divergent=copy.deepcopy(self.tools);divergent[-1]['content']='conflicting reply'
        self.archive(messages=[self.message]+divergent)
        self.assertEqual(self.recover()['missing'],[self.event])
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())

    def test_unavailable_image_content_is_not_restored_as_complete_text(self):
        unavailable=copy.deepcopy(self.message);unavailable['content'][-1]['image_url']['url']='https://example.invalid/missing.png'
        self.archive(messages=[unavailable]);self.assertEqual(self.recover()['missing'],[self.event])
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())

    def test_model_binding_source_identity_must_match_original(self):
        self.archive()
        for field in ('source_scope','event_id','sender_id'):
            bound=json.loads(self.db.execute("SELECT data FROM memory_message_sources WHERE direction='model'").fetchone()[0]);original=dict(bound)
            bound[field]='private:87654321' if field=='source_scope' else '87654321'
            self.db.execute("UPDATE memory_message_sources SET data=? WHERE direction='model'",(model_json(bound),))
            self.assertEqual(self.recover()['missing'],[self.event])
            self.db.execute("UPDATE memory_message_sources SET data=? WHERE direction='model'",(model_json(original),))

    def test_real_journal_recapture_callback_uses_archive_when_active_missing_or_unrelated(self):
        import behavior
        tree=ast.parse(Path(behavior.__file__).read_text(encoding='utf-8'))
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='journal_recapture')
        env={'message_provenance':message_provenance,'model_json':model_json}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual journal_recapture>','exec'),env)
        self.archive()
        unrelated=copy.deepcopy(self.message);unrelated['content'][0]['text']=unrelated['content'][0]['text'].replace('original','later')
        for active in (None,SimpleNamespace(last=SimpleNamespace(base=[unrelated]),recovery_error=None,retired_reason=None)):
            self.store.model_journals={} if active is None else {'bot_shared':active}
            env['journal_recapture'](self.store)('bot_shared',[self.event])
            item=json.loads(self.db.execute('SELECT material FROM memory_round_material').fetchone()[0])
            self.assertTrue(item['complete']);self.assertIn('末尾关键信息',item['text'])
            self.db.execute('DELETE FROM memory_round_material')

    def test_retired_members_are_never_resurrected_from_old_archive(self):
        self.archive()
        self.db.execute("INSERT INTO memory_semantic_batches VALUES(1,'bot_shared','digest',?,0,'retired',0,0)",(model_json([event_key('bot_shared',self.event)]),))
        self.assertEqual(self.recover()['recovered'],0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_round_material').fetchone())


if __name__=='__main__':unittest.main()
