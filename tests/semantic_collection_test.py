"""Shared collection includes all old pools without manufacturing rounds."""
import contextvars,copy,hashlib,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from context_protocol import event_message,model_json
import journal_storage,semantic_collection,summary_material
from semantic_batch import event_key


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'one.sqlite');self.db=self.store.db
        self.channel='private:12345678'
        self.db.executescript('''CREATE TABLE memory_checkpoint_archives(scope,version,payload,sha);
          CREATE TABLE IF NOT EXISTS fishbot_readonly_journal_blobs(scope,sha,data,PRIMARY KEY(scope,sha));''')
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def message(self,event='old',at=50,body='完整原文',scope=None,image=False):
        parts=[{'type':'text','text':body}]
        if image:parts.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*9000}})
        return event_message({'scope':scope or self.channel,'sender_id':'12345678','event_id':event,'at':at},parts)
    def old(self,event='old',at=50,scope=None,material=True):
        scope=scope or self.channel;message=self.message(event,at,scope=scope,image=True)
        self.db.execute('INSERT INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',(scope,'12345678',event,'display','真实已发回复',at))
        if material:
            item=summary_material.snapshots([message,{'role':'assistant','content':'真实已发回复'}],scope)[0]
            summary_material.remember(self.db,scope,[item])
        return message
    def archive(self,messages,scope=None):
        key=model_json(scope or self.channel);value={'scope':key,'base':messages,'wire':messages,'state':{}}
        payload,blobs=journal_storage.encode(value)
        for digest,raw in blobs.items():self.db.execute('INSERT OR IGNORE INTO fishbot_readonly_journal_blobs VALUES(?,?,?)',(key,digest,raw))
        self.db.execute('INSERT INTO memory_checkpoint_archives VALUES(?,?,?,?)',(key,2,payload,hashlib.sha256(payload.encode()).hexdigest()))
        return payload
    def collect(self,payload=None):return semantic_collection.collect(self.db,'bot_shared',100,payload or [])
    def test_unknown_occurrence_time_uses_real_later_order_bound_only(self):
        first=event_message({'scope':self.channel,'sender_id':'12345678','event_id':'unknown-time'},'real old text')
        self.archive([first,self.message(event='next',at=90)])
        rows=self.collect();old=next(r for r in rows if 'unknown-time' in r['event'])
        self.assertIsNone(old['at']);self.assertEqual(old['observed_before'],90)
        self.assertEqual(len(rows),2)

    def test_future_order_bound_cannot_retire_undated_message(self):
        first=event_message({'scope':self.channel,'sender_id':'12345678','event_id':'unknown-time'},'real new text')
        self.archive([first,self.message(event='future',at=150)])
        self.assertEqual(self.collect(),[])

    def test_operator_evidence_without_journal_is_not_lost(self):
        self.old();evidence={}
        for table in ('memory_rounds','memory_round_material'):
            columns=[r[1] for r in self.db.execute('PRAGMA table_info('+table+')')]
            rows=self.db.execute('SELECT * FROM '+table).fetchall()
            evidence[table]={'columns':columns,'rows':rows}
        self.db.execute('DELETE FROM memory_rounds');self.db.execute('DELETE FROM memory_round_material')
        self.db.execute('CREATE TABLE fishbot_operator_session_archives(scope,archive,sha)')
        raw=model_json({'journal':None,'evidence':evidence})
        self.db.execute('INSERT INTO fishbot_operator_session_archives VALUES(?,?,?)',(model_json(self.channel),raw,hashlib.sha256(raw.encode()).hexdigest()))
        rows=self.collect();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['sender_id'],'12345678')
        item=json.loads(self.db.execute("SELECT material FROM memory_round_material WHERE scope='bot_shared'").fetchone()[0])
        self.assertEqual(len(item['images']),1)

    def test_old_scope_under_35_is_full_shared_input_without_new_rounds(self):
        self.old();before=self.db.execute('SELECT * FROM memory_rounds').fetchall()
        rows=self.collect();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['event'],model_json([self.channel,'old']))
        self.assertEqual(rows[0]['sender_id'],'12345678');self.assertEqual(rows[0]['bot'],'真实已发回复')
        self.assertEqual(self.db.execute('SELECT * FROM memory_rounds').fetchall(),before)
        item=json.loads(self.db.execute("SELECT material FROM memory_round_material WHERE scope='bot_shared'").fetchone()[0])
        self.assertEqual(item['images'],[self.message(image=True)['content'][-1]['image_url']['url']]);self.assertTrue(item['complete'])
    def test_archive_only_original_tools_and_images_and_other_bot_isolation(self):
        messages=[self.message(image=True),{'role':'assistant','content':None,'tool_calls':[{'id':'c','type':'function','function':{'name':'look','arguments':'{}'}}]},
                  {'role':'tool','tool_call_id':'c','content':'完整工具结果'},{'role':'assistant','content':'模型原稿'}]
        encoded=self.archive(messages);other=BehaviorStore(Path(self.tmp.name)/'other.sqlite')
        try:
            other.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(self.channel,'12345678','foreign','其他bot原文','',20));other.db.commit()
            rows=self.collect();self.assertEqual(len(rows),1)
            item=json.loads(self.db.execute("SELECT material FROM memory_round_material WHERE scope='bot_shared'").fetchone()[0])
            self.assertEqual(item['tools'][0]['result'],'完整工具结果');self.assertEqual(item['native_reply'],'模型原稿')
            self.assertEqual(self.db.execute('SELECT payload FROM memory_checkpoint_archives').fetchone()[0],encoded)
            self.assertEqual(other.db.execute('SELECT question FROM memory_pending').fetchone()[0],'其他bot原文')
        finally:other.close()
    def test_cutoff_active_work_and_retired_archive_cannot_reenter(self):
        self.old('eligible',30);self.old('later',101);self.old('owned',40)
        self.db.execute('INSERT INTO pipeline_scope_work(scope,subject,event,runtime,pid) VALUES(?,?,?,?,?)',(self.channel,'12345678','owned','runtime',999))
        # A retired native identity also fences the canonical shared archive.
        self.archive([self.message('retired',20)])
        self.db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,?,?,?)',(999,self.channel,'digest',model_json([event_key(self.channel,'retired')]),20,'retired',20,20))
        rows=self.collect();self.assertEqual([json.loads(r['event'])[1] for r in rows],['eligible'])
        self.assertIsNone(self.db.execute("SELECT 1 FROM memory_pending WHERE scope='bot_shared' AND event=?",(model_json([self.channel,'later']),)).fetchone())
    def test_missing_material_is_explicit_and_atomic(self):
        self.old('good');self.old('missing',material=False)
        with self.assertRaisesRegex(ValueError,'material_missing'):self.collect()
        self.assertIsNone(self.db.execute("SELECT 1 FROM memory_pending WHERE scope='bot_shared'").fetchone())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],2)
    def test_conflicting_originals_do_not_pick_convenient_archive(self):
        self.old();self.archive([self.message(body='冲突原文')])
        with self.assertRaisesRegex(ValueError,'material_conflict'):self.collect()
        self.assertIsNone(self.db.execute("SELECT 1 FROM memory_pending WHERE scope='bot_shared'").fetchone())
    def test_same_transport_id_in_different_scopes_is_distinct(self):
        self.old('same',scope=self.channel);self.old('same',scope='group:55555')
        rows=self.collect();self.assertEqual(len(rows),2);self.assertEqual(len({r['event'] for r in rows}),2)
    def test_bad_archive_checksum_and_missing_timestamp_fail(self):
        self.archive([self.message()]);self.db.execute("UPDATE memory_checkpoint_archives SET sha='bad'")
        with self.assertRaisesRegex(ValueError,'integrity'):self.collect()
        self.db.execute('DELETE FROM memory_checkpoint_archives');message=self.message();o=json.loads(message['content'][0]['text'].split('\n')[0].split(']',1)[1]);o.pop('at')
        from context_protocol import SOURCE
        message['content'][0]['text']=SOURCE+model_json(o)+'\n原始正文';self.archive([message])
        with self.assertRaisesRegex(ValueError,'time_missing'):self.collect()
    def test_recapture_runs_before_selection_and_no_helper_commit(self):
        self.old(material=False);self.db.commit();calls=[]
        def recapture(scope,events):
            calls.append((scope,events))
            if scope==self.channel:
                item=summary_material.snapshots([self.message(image=True),{'role':'assistant','content':'真实已发回复'}],scope)[0]
                summary_material.remember(self.db,scope,[item])
        rows=semantic_collection.collect(self.db,'bot_shared',100,[],recapture)
        self.assertEqual(len(rows),1);self.assertTrue(calls)
        self.db.rollback();self.assertIsNone(self.db.execute("SELECT 1 FROM memory_pending WHERE scope='bot_shared'").fetchone())


class SharedSummaryIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'shared.sqlite');self.db=self.store.db
        self.reflection=self.store.reflection;self.reflection.shared_context=True;self.metrics={};self.calls=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def populate(self):
        for i in range(35):
            message=event_message({'scope':'private:12345678','sender_id':'12345678','event_id':str(i),'at':100+i},'本批真实正文'+str(i))
            event=model_json(['private:12345678',str(i)])
            materials=summary_material.snapshots([message,{'role':'assistant','content':'已发回复'}],'bot_shared',db=self.db)
            materials[0]['reply']='已发回复'
            self.reflection.record('bot_shared','12345678',event,'显示文本','已发回复',now=100+i,materials=materials)
        old=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',('group:55555','旧摘要完整正文_人物一起在海边画画',model_json(['raw_old_id']*173),50)).lastrowid
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(old,));self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(old,'group:55555'))
        self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(old,'87654321'))
        claims=[{'id':'claimlegacy01','kind':'human_statement','text':'正文外真实语义：更喜欢水彩画',
                 'speaker':{'role':'human','id':'87654321'},'status':'active','visibility':'group:55555',
                 'sourceRefs':[{'scope':'group:55555','event':'RAW_CLAIM_EVENT_NOT_LONG_TERM','role':'user','digest':'RAW_DIGEST_NOT_LONG_TERM'}]}]
        self.db.execute('UPDATE memory_summaries SET schema_version=2,claims=? WHERE id=?',(model_json(claims),old))
        self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id) VALUES(?,1,35,?,'consumed',50,?)",('group:55555',self.reflection.epoch(),old))
        self.db.commit();return old
    async def run_summary(self,mutate=False):
        from types import SimpleNamespace as NS
        async def chat(**kwargs):
            self.calls.append(json.loads(kwargs['prompt']))
            if mutate:
                self.db.execute("UPDATE memory_summaries SET text=text||'并发修订' WHERE scope='group:55555'");self.db.commit()
            return NS(completion_text=model_json({'summary':'新的有损共享摘要'}))
        self.reflection.binding=({'memory':True,'memorySummary':True,'naturalConversation':False},NS(text_chat=chat),lambda:True,self.metrics,contextvars.ContextVar('shared_collection',default=None))
        self.assertTrue(self.reflection.retry());await self.reflection.task
    async def test_same_call_consumes_legacy_product_relinks_checkpoint_and_keeps_35_rounds(self):
        old=self.populate();await self.run_summary();self.assertEqual(len(self.calls),1)
        prompt=self.calls[0];self.assertEqual(len(prompt['turns']),35)
        self.assertEqual(prompt['previous_summaries'][0]['text'],'旧摘要完整正文_人物一起在海边画画')
        self.assertNotIn('raw_old_id',model_json(prompt['previous_summaries']))
        self.assertIn('正文外真实语义：更喜欢水彩画',model_json(prompt['previous_summaries']))
        self.assertNotIn('RAW_CLAIM_EVENT_NOT_LONG_TERM',model_json(prompt['previous_summaries']))
        self.assertNotIn('RAW_DIGEST_NOT_LONG_TERM',model_json(prompt['previous_summaries']))
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_summaries WHERE id=?',(old,)).fetchone())
        new=self.db.execute("SELECT id FROM memory_summaries WHERE scope='bot_shared'").fetchone()[0]
        self.assertEqual(self.db.execute("SELECT summary_id,state FROM memory_reconstruction_checkpoints WHERE scope='group:55555'").fetchone(),(new,'consumed'))
        self.assertIn(('group:55555',),self.db.execute('SELECT scope FROM memory_summary_origins WHERE summary_id=?',(new,)).fetchall())
        self.assertIn(('87654321',),self.db.execute('SELECT subject FROM memory_summary_people WHERE summary_id=?',(new,)).fetchall())
        self.assertEqual(self.db.execute("SELECT round_count FROM memory_reconstruction_checkpoints WHERE scope='bot_shared'").fetchone()[0],35)
    async def test_concurrent_product_edit_aborts_replacement(self):
        old=self.populate();await self.run_summary(mutate=True)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_summaries WHERE id=?',(old,)).fetchone())
        self.assertIsNone(self.db.execute("SELECT 1 FROM memory_summaries WHERE scope='bot_shared'").fetchone())
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_semantic_batches').fetchone())
    def test_dependency_ignores_indexes_but_fences_semantic_people(self):
        old=self.populate();before=self.reflection.context_dependency([old],'bot_shared')
        self.db.execute("UPDATE memory_summaries SET sources='[]' WHERE id=?",(old,))
        self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',('group:55555','event','87654321','digest',old))
        self.assertEqual(self.reflection.context_dependency([old],'bot_shared'),before)
        self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(old,'11111111'))
        self.assertNotEqual(self.reflection.context_dependency([old],'bot_shared'),before)


class SharedCollectionProviderWire(unittest.IsolatedAsyncioTestCase):
    # Use the existing pinned real SDK/HTTP fixture, without inheriting its
    # unrelated test methods or replacing Provider serialization with a stub.
    from semantic_provider_wire_test import SemanticProviderWire as _Fixture
    asyncSetUp=_Fixture.asyncSetUp
    asyncTearDown=_Fixture.asyncTearDown
    summarize=_Fixture.summarize
    async def test_full_shared_collection_reaches_actual_provider_http_once(self):
        db=self.store.db;r=self.store.reflection;r.shared_context=True;r.checkpoint_callback=None
        for i in range(35):
            message=event_message({'scope':'private:12345678','sender_id':'12345678','event_id':str(i),'at':100+i},'新批正文'+str(i))
            key=model_json(['private:12345678',str(i)]);items=summary_material.snapshots([message,{'role':'assistant','content':'已发'}],'bot_shared',db=db)
            r.record('bot_shared','12345678',key,'显示文本','已发',now=100+i,materials=items)
        original='LEGACY_FULL_TEXT_BEGIN_'+('不可截断的旧会话原始材料'*130)+'_LEGACY_FULL_TEXT_END'
        message=event_message({'scope':'group:55555','sender_id':'87654321','event_id':'old-group','at':50},[
            {'type':'text','text':original},{'type':'image_url','image_url':{'url':self.image}}])
        value={'scope':model_json('group:55555'),'base':[message],'wire':[message],'state':{}}
        encoded,blobs=journal_storage.encode(value)
        db.executescript('CREATE TABLE memory_checkpoint_archives(scope,version,payload,sha); CREATE TABLE IF NOT EXISTS fishbot_readonly_journal_blobs(scope,sha,data,PRIMARY KEY(scope,sha));')
        for digest,raw in blobs.items():db.execute('INSERT INTO fishbot_readonly_journal_blobs VALUES(?,?,?)',(value['scope'],digest,raw))
        db.execute('INSERT INTO memory_checkpoint_archives VALUES(?,?,?,?)',(value['scope'],2,encoded,hashlib.sha256(encoded.encode()).hexdigest()))
        db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',('group:55555','LEGACY_SUMMARY_BODY_FULL','["OBSOLETE_SOURCE_INDEX"]',60));db.commit()
        await self.summarize();self.assertEqual(len(self.requests),1)
        wire=model_json(self.requests[0]['messages']);self.assertIn(original,wire);self.assertIn('LEGACY_SUMMARY_BODY_FULL',wire);self.assertNotIn('OBSOLETE_SOURCE_INDEX',wire)
        images=[p['image_url']['url'] for m in self.requests[0]['messages'] if isinstance(m.get('content'),list) for p in m['content'] if p.get('type')=='image_url']
        self.assertEqual(images,[self.image])
        self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone()[0],35)


if __name__=='__main__':unittest.main()
