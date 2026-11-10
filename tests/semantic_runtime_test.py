"""Real checkpoint callback and pinned native SQLite retirement regressions."""
import asyncio,copy,json,sqlite3,sys,tempfile,time,unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parents[1]/'references/astrbot')]
from behavior import BehaviorStore
from context_protocol import SOURCE,model_json
from readonly_journal import ReadonlyJournal,Prepared
from semantic_batch import event_key,initialize
from semantic_runtime import finish_semantic_batches,clean_native_history,install_native_retirement_guard,filter_retired_context

SCOPE='private:12345678'
def message(event):return {'role':'user','content':SOURCE+model_json({'scope':SCOPE,'sender_id':'12345678','event_id':event})+'\noriginal '+event}

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.st=BehaviorStore(self.root/'behavior.sqlite');db=self.st.db;initialize(db)
        db.execute("INSERT INTO memory_summaries(id,scope,text,sources,updated) VALUES(1,?,'semantic summary','[\"old\"]',?)",(SCOPE,time.time()))
        db.execute("INSERT INTO memory_summary_levels VALUES(1,'small')")
        db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id) VALUES(?,35,35,0,'available',?,1)",(SCOPE,time.time()))
        self.members=[event_key(SCOPE,'old')]
        db.execute("INSERT INTO memory_semantic_batches VALUES(1,?,'digest',?,0,'committed',?,NULL)",(SCOPE,model_json(self.members),time.time()))
        db.execute("INSERT INTO memory_pending VALUES(?,'12345678','old','original old','old reply',?)",(SCOPE,time.time()))
        db.commit()
        self.base=[{'role':'system','content':'tenant System'},message('old'),{'role':'assistant','content':'old reply'},message('new')]
        state={'execution_experience':{'status':'ok','experiences':[{'execution':'completed','receipt':{'status':'confirmed'},'lesson':'keep exact execution experience'}]}}
        journal=ReadonlyJournal(db,SCOPE);journal.serial=1
        p=Prepared(1,None,copy.deepcopy(self.base),copy.deepcopy(self.base),state,1,time.time(),'initial')
        self.assertTrue(journal.commit(p));self.st.model_journals[SCOPE]=journal
    def tearDown(self):self.st.close();self.temp.cleanup()
    def test_callback_retires_without_next_message_and_preserves_execution(self):
        result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['retired'],1,result)
        journal=self.st.model_journals[SCOPE]
        self.assertNotIn('original old',model_json(journal.last.wire))
        self.assertNotIn('original old',model_json(journal.last.state))
        self.assertIn('semantic summary',model_json(journal.last.wire))
        self.assertIn('keep exact execution experience',model_json(journal.last.wire))
        self.assertIn('original new',model_json(journal.last.wire))
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_pending WHERE event=?',('old',)).fetchone()[0],0)
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'retired')
    def test_busy_candidate_keeps_permit_available_and_retry_finishes(self):
        journal=self.st.model_journals[SCOPE];journal._staged=1
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['pending'],1)
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'available')
        journal._staged=None
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
    def test_retirement_removes_exact_raw_evidence_and_keeps_other_events_and_state(self):
        import quote_reply,shared_context
        db=self.st.db;quote_reply.initialize(db);shared_context.initialize(db)
        other='private:87654321';subject='12345678'
        for channel,event in ((SCOPE,'old'),(SCOPE,'new'),(other,'old')):
            db.execute('INSERT INTO relation_events VALUES(?,?,?,0)',(channel,subject,event))
            db.execute('INSERT INTO relation_evidence VALUES(?,?,?,1,?,?,0)',(channel,subject,event,'interaction','raw quote '+event))
            db.execute('INSERT INTO companion_seen VALUES(?,?,0)',(channel,event))
            db.execute('INSERT INTO companion_signals VALUES(?,?,?,?,0)',(channel,event,'emotion','raw quote '+event))
            db.execute('INSERT INTO pipeline_quote_sources(handle,scope,message,preview,media) VALUES(?,?,?,?,?)',(channel+event,channel,event,'raw preview','["raw media"]'))
            db.execute('INSERT INTO fishbot_shared_inputs(scope,event,message,created) VALUES(?,?,?,0)',(channel,event,'raw queued '+event))
        feedback={'state':'engaged','reaction_event':'old','sender_id':subject,'quote':'raw retired reaction'}
        db.execute("INSERT INTO companion_signals VALUES(?,?,'dialogue_feedback',?,0)",(SCOPE,'new',model_json(feedback)))
        db.execute("INSERT INTO companion_emotion VALUES(?,'happy',2,0,9999999999,0)",(SCOPE,))
        db.execute("INSERT INTO relations(scope,subject,affinity,last_seen) VALUES(?,?,7,0)",(SCOPE,subject))
        db.commit()
        result=finish_semantic_batches(self.st,{},SCOPE);self.assertEqual(result['retired'],1,result)
        for table,column in (('relation_events','event'),('relation_evidence','event'),('companion_seen','event'),('companion_signals','event'),('pipeline_quote_sources','message'),('fishbot_shared_inputs','event')):
            self.assertFalse(db.execute('SELECT 1 FROM '+table+' WHERE scope=? AND '+column+'=?',(SCOPE,'old')).fetchone(),table)
            self.assertTrue(db.execute('SELECT 1 FROM '+table+' WHERE scope=? AND '+column+'=?',(SCOPE,'new')).fetchone(),table)
            self.assertTrue(db.execute('SELECT 1 FROM '+table+' WHERE scope=? AND '+column+'=?',(other,'old')).fetchone(),table)
        saved=json.loads(db.execute("SELECT quote FROM companion_signals WHERE scope=? AND event='new' AND kind='dialogue_feedback'",(SCOPE,)).fetchone()[0])
        self.assertEqual(saved,{'state':'engaged','reaction_event':'semantic:'+event_key(SCOPE,'old'),'sender_id':subject})
        self.assertEqual(db.execute('SELECT kind,intensity FROM companion_emotion WHERE scope=?',(SCOPE,)).fetchone(),('happy',2))
        self.assertEqual(db.execute('SELECT affinity FROM relations WHERE scope=? AND subject=?',(SCOPE,subject)).fetchone()[0],7)

    def test_shared_retirement_retry_discovers_exact_quote_and_protects_owned_input(self):
        import quote_reply,shared_context,semantic_batch
        db=self.st.db;quote_reply.initialize(db);shared_context.initialize(db)
        channel='group:12345';old='previously-purged'
        db.execute("INSERT INTO memory_semantic_batches VALUES(2,'bot_shared','shared',?,0,'retired',0,0)",(model_json([event_key('bot_shared',model_json([channel,old]))]),))
        db.execute('INSERT INTO pipeline_quote_sources(handle,scope,message,preview,media) VALUES(?,?,?,?,?)',('retired-quote',channel,old,'raw quote','["raw media"]'))
        db.execute('INSERT INTO fishbot_shared_inputs(scope,event,message,created) VALUES(?,?,?,0)',(channel,old,'raw owned input'))
        db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,status,created) VALUES(?,?,?,'owned',0)",(channel,old,'12345678'))
        db.commit()
        with db:self.assertTrue(semantic_batch.purge(db,2))
        self.assertFalse(db.execute("SELECT 1 FROM pipeline_quote_sources WHERE handle='retired-quote'").fetchone())
        self.assertTrue(db.execute('SELECT 1 FROM fishbot_shared_inputs WHERE scope=? AND event=?',(channel,old)).fetchone())
        with db:db.execute("UPDATE pipeline_work_inputs SET status='settled' WHERE scope=? AND event=?",(channel,old))
        with db:self.assertTrue(semantic_batch.purge(db,2))
        self.assertFalse(db.execute('SELECT 1 FROM fishbot_shared_inputs WHERE scope=? AND event=?',(channel,old)).fetchone())
    def test_hot_filter_preserves_semantics_and_same_text_new_event(self):
        old={'source_scope':SCOPE,'source_event':'old','text':'identical text'}
        new={**old,'source_event':'new'}
        self.st.ambient.scopes[SCOPE]=[old,new]
        self.st.participation.scopes[SCOPE]={'turns':[old,new]}
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
        value={'recent_messages':[old,new],'conversation_timeline':[old,new],
            'memory_foundation':{'facts':[old],'core_note':old},'active_agreements':[old],
            'persona_core_note':old,'dialogue':{'feedback':[{'event_id':'old'},{'event_id':'new'}]}}
        filtered=filter_retired_context(self.st,SCOPE,value)
        self.assertEqual(filtered['recent_messages'],[new]);self.assertEqual(filtered['conversation_timeline'],[new])
        self.assertEqual(filtered['memory_foundation'],value['memory_foundation'])
        self.assertEqual(filtered['active_agreements'],[old]);self.assertEqual(filtered['persona_core_note'],old)
        self.assertEqual(filtered['dialogue']['feedback'],[{'event_id':'new'}])
        self.assertEqual(self.st.ambient.scopes[SCOPE],[new]);self.assertEqual(self.st.participation.scopes[SCOPE]['turns'],[new])
    def test_shared_hash_filter_keeps_unprocessed_group_round_tail(self):
        group='group:12345';members=[event_key('bot_shared',model_json([group,'old']))]
        self.st.db.execute("INSERT INTO memory_semantic_batches VALUES(2,'bot_shared','shared',?,0,'retired',0,0)",(model_json(members),));self.st.db.commit()
        old={'source_scope':group,'source_event':'old','text':'same'};new={**old,'source_event':'new'}
        value={'recent_cross_session_messages':[old,new],'group_round_history':[{'source_scope':group,'round_event':'old','messages':[old,new],'reply':{'text':'old reply'}}]}
        filtered=filter_retired_context(self.st,group,value)
        self.assertEqual(filtered['recent_cross_session_messages'],[new])
        self.assertEqual(filtered['group_round_history'][0]['messages'],[new])
        self.assertIsNone(filtered['group_round_history'][0]['round_event']);self.assertIsNone(filtered['group_round_history'][0]['reply'])
    def native(self):
        path=self.root/'astrbot.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE conversations(conversation_id PRIMARY KEY,platform_id,content,token_usage)')
            for cid,platform in [('own','tenant'),('other','other')]:db.execute('INSERT INTO conversations VALUES(?,?,?,99)',(cid,platform,model_json(self.base)))
        self.manager=NS(db=NS(db_path=str(path)))
        return path
    def test_native_sqlite_removes_exact_members_and_keeps_other_tenant(self):
        path=self.native();finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(clean_native_history(self.manager,'tenant',self.st)['conversations'],1)
        with closing(sqlite3.connect(path)) as db, db:
            own=json.loads(db.execute("SELECT content FROM conversations WHERE conversation_id='own'").fetchone()[0]);other=db.execute("SELECT content FROM conversations WHERE conversation_id='other'").fetchone()[0]
        self.assertNotIn('original old',model_json(own));self.assertIn('original new',model_json(own));self.assertIn('original old',other)
    def test_late_sdk_save_cannot_restore_retired_original(self):
        path=self.native();finish_semantic_batches(self.st,{},SCOPE)
        async def save(*args,**kwargs):
            with closing(sqlite3.connect(path)) as db, db:db.execute("UPDATE conversations SET content=? WHERE conversation_id='own'",(model_json(self.base),))
            return NS(platform_id='tenant',conversation_id='own')
        self.manager.db.update_conversation=save
        install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
        asyncio.run(self.manager.db.update_conversation(cid='own',content=self.base))
        with closing(sqlite3.connect(path)) as db, db:self.assertNotIn('original old',db.execute("SELECT content FROM conversations WHERE conversation_id='own'").fetchone()[0])

if __name__=='__main__':unittest.main()
