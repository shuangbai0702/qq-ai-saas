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
    def test_checkpoint_system_adoption_requires_config_permit_and_no_new_submitted_turn(self):
        from context_protocol import READONLY_CURRENT
        j=self.st.model_journals[SCOPE]
        j.last.base=j.last.base[:-1];j.last.wire=j.last.wire[:-1]
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
        # Restore from real SQLite: the proof must survive Worker restart.
        j=ReadonlyJournal(self.st.db,SCOPE);before=copy.deepcopy(j.last.wire)
        new_system={'role':'system','content':'tenant System with new authorized quote rule'}
        from context_transport import READONLY_FIELDS
        readonly={k:v for k,v in j.last.state.items() if k in READONLY_FIELDS}
        candidate=[new_system,message('next'),{'role':'user','content':READONLY_CURRENT+'\n'+model_json(readonly)}]
        held=j.prepare(candidate,('new-config',),reconstruction_authority=self.st.reflection,
            reconstruction_scope=SCOPE,retirement=j.last.retirement)
        self.assertEqual(held.wire[0],before[0])
        # Exercise the real shared composer before Journal admission: it used
        # to discard the configured System before the permit check ran.
        import shared_context
        from unittest.mock import patch
        with patch.object(shared_context,'JOURNAL_SCOPE',SCOPE):
            held_composed,_=shared_context.compose(j,candidate,SCOPE,'next',inputs=[message('next')])
            self.assertEqual(held_composed[0],before[0])
            composed,_=shared_context.compose(j,candidate,SCOPE,'next',inputs=[message('next')],system_transition_authorized=True)
        self.assertEqual(composed[0],new_system)
        updated=j.prepare(composed,('new-config',),reconstruction_authority=self.st.reflection,
            reconstruction_scope=SCOPE,retirement=j.last.retirement,system_transition_authorized=True)
        self.assertEqual(updated.wire[0],new_system);self.assertEqual(j.last.wire,before)
        self.assertTrue(j.commit(updated))
        # A later authorized save cannot rewrite this newly submitted prefix.
        later=copy.deepcopy(candidate);later[0]['content']='another change'
        frozen=j.prepare(later,('later',),reconstruction_authority=self.st.reflection,
            reconstruction_scope=SCOPE,retirement=j.last.retirement,system_transition_authorized=True)
        self.assertEqual(frozen.wire[0],new_system)
    def test_callback_retires_without_next_message_without_copying_settled_execution(self):
        result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['retired'],1,result)
        journal=self.st.model_journals[SCOPE]
        self.assertNotIn('original old',model_json(journal.last.wire))
        self.assertNotIn('original old',model_json(journal.last.state))
        self.assertIn('semantic summary',model_json(journal.last.wire))
        self.assertNotIn('keep exact execution experience',model_json(journal.last.wire))
        self.assertNotIn('completed_experience',journal.last.state['memory_foundation'])
        self.assertNotIn('execution_experience',journal.last.state['memory_foundation'])
        self.assertIn('original new',model_json(journal.last.wire))
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_pending WHERE event=?',('old',)).fetchone()[0],0)
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'retired')
    def test_retirement_publishes_full_readonly_basis_before_future_deltas(self):
        from context_protocol import READONLY_CURRENT
        from decision_history import apply_changes
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
        journal=self.st.model_journals[SCOPE]
        packets=[json.loads(m['content'].split('\n',1)[1]) for m in journal.last.wire if isinstance(m.get('content'),str) and m['content'].startswith(READONLY_CURRENT+'\n')]
        self.assertEqual(len(packets),1)
        self.assertNotIn('readonlyChanges',packets[0])
        self.assertEqual(packets[0]['execution_experience'],journal.last.state['execution_experience'])
        self.assertNotIn('memory_foundation',packets[0])
        self.assertNotIn('original old',model_json(packets))

    def test_legacy_missing_basis_is_repaired_only_by_append_once(self):
        from context_protocol import READONLY_CURRENT
        from context_transport import request_context_view
        from decision_history import apply_changes
        journal=ReadonlyJournal()
        state={'autonomous_experience':{'actions':[]},'execution_experience':{'status':'current'}}
        native=[{'role':'user','content':'first'}]
        system,tail=request_context_view('fixed',native,None,'policy','guidance',model_json(state))
        first=journal.prepare([{'role':'system','content':system}]+tail,'same')
        self.assertTrue(journal.commit(first))
        # Reproduce the old retired representation: metadata survived, its
        # readonly body did not. Existing malformed deltas must remain exact.
        journal.last.wire[:]=[m for m in journal.last.wire if not (isinstance(m.get('content'),str) and m['content'].startswith(READONLY_CURRENT+'\n'))]
        journal.last.wire.append({'role':'user','content':READONLY_CURRENT+'\n'+model_json({'readonlyChanges':[['set',['autonomous_experience','actions'],[]]]})})
        before=copy.deepcopy(journal.last.wire)
        native.extend([{'role':'assistant','content':'answer'},{'role':'user','content':'next'}])
        system,tail=request_context_view('fixed',native,None,'policy','guidance',model_json(state))
        second=journal.prepare([{'role':'system','content':system}]+tail,'same')
        self.assertEqual(second.wire[:len(before)],before)
        added=[json.loads(m['content'].split('\n',1)[1]) for m in second.wire[len(before):] if isinstance(m.get('content'),str) and m['content'].startswith(READONLY_CURRENT+'\n')]
        self.assertEqual(len(added),1)
        self.assertEqual(added[0]['readonlyChanges'][0][:2],['set',[]])
        self.assertEqual(apply_changes({},added[0]['readonlyChanges']),second.state)
        self.assertTrue(journal.commit(second))
        native.extend([{'role':'assistant','content':'answer 2'},{'role':'user','content':'third'}])
        system,tail=request_context_view('fixed',native,None,'policy','guidance',model_json(state))
        third=journal.prepare([{'role':'system','content':system}]+tail,'same')
        self.assertEqual(third.wire[:len(second.wire)],second.wire)
        self.assertNotIn('readonlyChanges',model_json(third.wire[len(second.wire):]))
    def test_busy_candidate_keeps_permit_available_and_retry_finishes(self):
        journal=self.st.model_journals[SCOPE];journal._staged=1
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['pending'],1)
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'available')
        journal._staged=None
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
    def test_cleanup_failure_rolls_back_journal_and_permit_then_retry_finishes(self):
        from unittest.mock import patch
        import semantic_batch,journal_storage
        before=journal_storage.read_snapshot(self.st.db,model_json(SCOPE))
        purge=semantic_batch.purge
        def fail_cleanup(db,checkpoint_id,*,preview=False):
            if not preview:raise OSError('injected archive cleanup failure')
            return purge(db,checkpoint_id,preview=True)
        with patch.object(semantic_batch,'purge',side_effect=fail_cleanup):
            result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['retired'],0,result);self.assertEqual(result['pending'],1)
        self.assertEqual(journal_storage.read_snapshot(self.st.db,model_json(SCOPE)),before)
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'available')
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'committed')
        self.assertTrue(self.st.db.execute("SELECT 1 FROM memory_pending WHERE event='old'").fetchone())
        self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
        self.assertNotIn('original old',model_json(self.st.model_journals[SCOPE].last.wire))
        self.assertIn('semantic summary',model_json(self.st.model_journals[SCOPE].last.wire))

    def scoped_journal(self,scope,db=None):
        journal=ReadonlyJournal(db or self.st.db,scope);journal.serial=1
        foreign={'role':'user','content':SOURCE+model_json({'scope':'group:87654321','sender_id':'12345678','event_id':'old'})+'\nforeign old'}
        messages=copy.deepcopy(self.base)+[foreign]
        state={'recent_messages':[{'scope':SCOPE,'event_id':'old','text':'old raw'},
                                   {'scope':SCOPE,'event_id':'new','text':'new raw'}]}
        prepared=Prepared(1,None,messages,messages,state,1,time.time(),'initial')
        self.assertTrue(journal.commit(prepared));self.st.model_journals[scope]=journal
        return journal
    def test_cross_scope_active_copy_requires_its_owner_reconstruction(self):
        import journal_storage
        other=self.scoped_journal('group:12345')
        before=copy.deepcopy(other.last);serial=other.serial
        primary=copy.deepcopy(self.st.model_journals[SCOPE].last)
        result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['retired'],0,result);self.assertEqual(result['pending'],1)
        # A different Scope's checkpoint cannot edit this submitted history.
        self.assertEqual(other.last,before);self.assertEqual(other.serial,serial)
        self.assertEqual(journal_storage.read_snapshot(self.st.db,model_json('group:12345'))['wire'],before.wire)
        self.assertEqual(journal_storage.read_snapshot(self.st.db,model_json(SCOPE))['wire'],primary.wire)
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'available')
        self.assertTrue(self.st.db.execute("SELECT 1 FROM memory_pending WHERE event='old'").fetchone())
        detail=self.st.db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind='semantic_retirement'").fetchone()[0]
        self.assertIn('semantic_active_journal_reconstruction_required',detail)

    def test_other_scope_staged_candidate_and_unlicensed_history_both_defer(self):
        other=self.scoped_journal('group:12345')
        self.assertTrue(other.stage_candidate(other.last));serial=other.serial
        result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['pending'],1,result);self.assertEqual(result['retired'],0)
        self.assertEqual(other.serial,serial);self.assertEqual(other._staged,serial)
        self.assertIn('original old',model_json(other.last.wire))
        self.assertTrue(self.st.db.execute("SELECT 1 FROM memory_pending WHERE event='old'").fetchone())
        self.assertTrue(other.commit(other.last))
        # Settling an unrelated staged request is not a reconstruction permit.
        result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['retired'],0,result);self.assertEqual(result['pending'],1)
        self.assertIn('original old',model_json(other.last.wire))
        self.assertTrue(self.st.db.execute("SELECT 1 FROM memory_pending WHERE event='old'").fetchone())
    def test_foreign_store_journal_is_untouched(self):
        from behavior import BehaviorStore
        foreign_store=BehaviorStore(self.root/'foreign.sqlite')
        try:
            foreign=self.scoped_journal('group:12345',foreign_store.db)
            before=copy.deepcopy(foreign.last);serial=foreign.serial
            self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
            self.assertEqual(foreign.last,before);self.assertEqual(foreign.serial,serial)
        finally:foreign_store.close()
    def test_original_scope_prepare_applies_shared_tombstone_before_native_comparison(self):
        from dataclasses import replace
        from context_protocol import READONLY_CURRENT
        journal=self.st.model_journals[SCOPE]
        clean=[self.base[0],self.base[-1]]
        journal.last=replace(journal.last,base=copy.deepcopy(clean),wire=copy.deepcopy(clean))
        self.st.db.execute("INSERT INTO memory_semantic_batches VALUES(2,'bot_shared','digest',?,0,'retired',0,0)",
                           (model_json([event_key('bot_shared',model_json([SCOPE,'old']))]),));self.st.db.commit()
        foreign={'role':'user','content':SOURCE+model_json({'scope':'group:12345','sender_id':'12345678','event_id':'old'})+'\nforeign old'}
        incoming=copy.deepcopy(self.base)+[foreign,{'role':'user','content':READONLY_CURRENT+'\n'+model_json(journal.last.state)}]
        native=copy.deepcopy(incoming);before=copy.deepcopy(incoming)
        prepared=journal.prepare(incoming,None,reconstruction_scope=SCOPE,unprojected=native)
        self.assertNotIn('original old',model_json(prepared.base));self.assertNotIn('old reply',model_json(prepared.wire))
        self.assertIn('original new',model_json(prepared.wire));self.assertIn('foreign old',model_json(prepared.wire))
        self.assertEqual(prepared.wire[0],self.base[0]);self.assertEqual(incoming,before)

    def test_internal_tuple_owner_is_not_bound_as_sql_scope(self):
        from context_protocol import READONLY_CURRENT
        self.st.db.execute("INSERT INTO memory_semantic_batches VALUES(2,'bot_shared','digest',?,0,'retired',0,0)",
                           (model_json([event_key('bot_shared',model_json([SCOPE,'old']))]),));self.st.db.commit()
        journal=ReadonlyJournal(self.st.db,None)
        incoming=copy.deepcopy(self.base)+[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'execution_experience':{'status':'ok'}})}]
        before=copy.deepcopy(incoming)
        prepared=journal.prepare(incoming,None,reconstruction_scope=('autonomous','actual-action-id'),guarded=False)
        self.assertIsNotNone(prepared)
        self.assertNotIn('original old',model_json(prepared.wire));self.assertIn('original new',model_json(prepared.wire))
        self.assertEqual(incoming,before);self.assertTrue(journal.commit(prepared))
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
        with self.assertRaisesRegex(ValueError,'semantic_batch_input_still_owned'):
            with db:semantic_batch.purge(db,2)
        self.assertTrue(db.execute("SELECT 1 FROM pipeline_quote_sources WHERE handle='retired-quote'").fetchone())
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

    def test_admin_legacy_reset_clears_native_copies_but_keeps_canonical_new_batch_and_other_bot(self):
        path=self.native();finish_semantic_batches(self.st,{},SCOPE)
        self.st.shared_context=True
        self.st.db.execute("INSERT INTO memory_semantic_batches VALUES(2,'bot_shared','shared',?,100,'retired',0,0)",(model_json([event_key('bot_shared',model_json([SCOPE,'old']))]),))
        self.st.db.execute('CREATE TABLE memory_semantic_discard_audit(checkpoint_id,administrator,request_id)')
        self.st.db.execute("INSERT INTO memory_semantic_discard_audit VALUES(2,'admin','request')");self.st.db.commit()
        canonical=copy.deepcopy(self.st.model_journals[SCOPE].last.wire)
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE platform_message_history(platform_id,created_at,content)')
            db.executemany('INSERT INTO platform_message_history VALUES(?,?,?)',[('tenant','1970-01-01 00:00:50','old'),('tenant','1970-01-01 00:03:00','new'),('other','1970-01-01 00:00:50','foreign')])
        result=clean_native_history(self.manager,'tenant',self.st,reset_legacy=True)
        self.assertTrue(result['legacyReset']);self.assertEqual(result['historyRows'],0)
        self.assertEqual(result['result'],'pending');self.assertEqual(result['unresolvedHistoryRows'],2)
        with closing(sqlite3.connect(path)) as db:
            own=db.execute("SELECT content FROM conversations WHERE conversation_id='own'").fetchone()[0]
            self.assertNotIn('original',own);self.assertIn('original old',db.execute("SELECT content FROM conversations WHERE conversation_id='other'").fetchone()[0])
            self.assertEqual(list(db.execute('SELECT content FROM platform_message_history ORDER BY content')),[('foreign',),('new',),('old',)])
        self.assertEqual(self.st.model_journals[SCOPE].last.wire,canonical)

    def test_reset_flag_without_admin_grant_keeps_unprocessed_native_content(self):
        path=self.native();finish_semantic_batches(self.st,{},SCOPE)
        result=clean_native_history(self.manager,'tenant',self.st,reset_legacy=True)
        self.assertFalse(result['legacyReset'])
        with closing(sqlite3.connect(path)) as db:self.assertIn('original new',db.execute("SELECT content FROM conversations WHERE conversation_id='own'").fetchone()[0])

    def test_native_cleanup_failure_retries_without_another_summary_and_then_stops_resetting(self):
        from unittest.mock import patch
        self.native();finish_semantic_batches(self.st,{},SCOPE);self.st.shared_context=True
        self.st.db.execute("INSERT INTO memory_semantic_batches VALUES(2,'bot_shared','shared',?,100,'retired',0,0)",(model_json([event_key('bot_shared',model_json([SCOPE,'old']))]),))
        self.st.db.execute('CREATE TABLE memory_semantic_discard_audit(checkpoint_id,administrator,request_id)')
        self.st.db.execute("INSERT INTO memory_semantic_discard_audit VALUES(2,'admin','request')");self.st.db.commit()
        self.st.semantic_native=(self.manager,'tenant')
        with patch('semantic_runtime.clean_native_history',side_effect=sqlite3.OperationalError('locked')):
            self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['native']['result'],'pending')
        self.assertEqual(self.st.db.execute("SELECT status FROM memory_checkpoint_issues WHERE kind='native_legacy_cleanup'").fetchone(),('open',))
        self.assertTrue(finish_semantic_batches(self.st,{},SCOPE)['native']['legacyReset'])
        self.assertEqual(self.st.db.execute("SELECT status FROM memory_checkpoint_issues WHERE kind='native_legacy_cleanup'").fetchone(),('resolved',))
        self.assertFalse(finish_semantic_batches(self.st,{},SCOPE)['native']['legacyReset'])

class NativeMessageRetirementTests(unittest.TestCase):
    setUp=RuntimeTests.setUp
    tearDown=RuntimeTests.tearDown
    native=RuntimeTests.native

    def content(self,event='old',scope=SCOPE,role='user'):
        return {'type':role,'message':[{'type':'plain','text':'same raw original'}],
                '_fishbot_source':{'scope':scope,'event_id':event}}

    def prepare(self):
        path=self.native()
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE platform_message_history(id INTEGER PRIMARY KEY,platform_id,user_id,content)')
        async def insert(platform_id,user_id,content,**kwargs):
            with closing(sqlite3.connect(path)) as db,db:
                ident=db.execute('INSERT INTO platform_message_history(platform_id,user_id,content) VALUES(?,?,?)',(platform_id,user_id,model_json(content))).lastrowid
            return NS(id=ident,platform_id=platform_id,user_id=user_id,content=content)
        async def get(platform_id,user_id,**kwargs):
            with closing(sqlite3.connect(path)) as db:
                return [NS(id=i,content=json.loads(raw)) for i,raw in db.execute('SELECT id,content FROM platform_message_history WHERE platform_id=? AND user_id=?',(platform_id,user_id))]
        self.manager.db.insert_platform_message_history=insert
        self.manager.db.get_platform_message_history=get
        self.insert=insert
        self.path=path
        return path

    def event(self,identifier='old',scope=SCOPE,work=None):
        from astrbot.core.platform.astr_message_event import current_message_event
        return current_message_event,NS(message_obj=NS(message_id=identifier),unified_msg_origin='umo',
            get_platform_id=lambda:'tenant',get_group_id=lambda:scope[6:] if scope.startswith('group:') else '',
            get_sender_id=lambda:scope[8:] if scope.startswith('private:') else 'sender',
            get_extra=lambda name:work if name=='_hosted_pipeline' else None)

    def test_ordinary_checkpoint_exact_rows_keep_same_text_new_event_scope_and_bot(self):
        self.prepare()
        async def seed():
            for platform,content in [('tenant',self.content()),('tenant',self.content(role='bot')),
                    ('tenant',self.content('new')),('tenant',self.content(scope='group:other')),
                    ('other',self.content())]:
                await self.insert(platform,'umo',content)
        asyncio.run(seed());self.st.semantic_native=(self.manager,'tenant')
        result=finish_semantic_batches(self.st,{},SCOPE)
        self.assertEqual(result['native']['result'],'clean',result)
        self.assertEqual(result['native']['historyRows'],2)
        with closing(sqlite3.connect(self.path)) as db:
            rows=[(p,json.loads(raw)['_fishbot_source']) for p,raw in db.execute('SELECT platform_id,content FROM platform_message_history')]
        self.assertEqual(len(rows),3)
        self.assertIn(('tenant',{'scope':SCOPE,'event_id':'new'}),rows)
        self.assertIn(('tenant',{'scope':'group:other','event_id':'old'}),rows)
        self.assertIn(('other',{'scope':SCOPE,'event_id':'old'}),rows)

    def test_unproven_legacy_rows_remain_pending_and_are_not_returned_to_tools(self):
        self.prepare();legacy={'type':'user','message':[{'type':'plain','text':'legacy raw'}]}
        asyncio.run(self.insert('tenant','umo',legacy));finish_semantic_batches(self.st,{},SCOPE)
        result=clean_native_history(self.manager,'tenant',self.st)
        self.assertEqual(result['result'],'pending');self.assertEqual(result['unresolvedHistoryRows'],1)
        install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
        rows=asyncio.run(self.manager.db.get_platform_message_history('tenant','umo'))
        self.assertEqual(rows,[])
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM platform_message_history').fetchone()[0],1)

    def test_input_and_work_output_late_insert_cannot_revive_retired_batch(self):
        for role in ('user','bot'):
            with self.subTest(role=role):
                if hasattr(self,'path'):
                    # New store/native fixture for each independent ordering.
                    self.tearDown();self.setUp()
                self.prepare()
                async def scenario():
                    started=asyncio.Event();release=asyncio.Event()
                    async def delayed(*args,**kwargs):
                        started.set();await release.wait()
                        return await self.insert(*args,**kwargs)
                    self.manager.db.insert_platform_message_history=delayed
                    install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
                    var,event=self.event('different' if role=='bot' else 'old',work=NS(scope=SCOPE,event_id='old') if role=='bot' else None)
                    token=var.set(event)
                    try:task=asyncio.create_task(self.manager.db.insert_platform_message_history(platform_id='tenant',user_id='umo',content={'type':role,'message':[{'type':'plain','text':'late raw'}]}))
                    finally:var.reset(token)
                    await started.wait()
                    self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['retired'],1)
                    release.set();self.assertIsNone(await task)
                    with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM platform_message_history').fetchone()[0],0)
                asyncio.run(scenario())

    def test_already_retired_insert_is_rejected_before_native_write(self):
        self.prepare();finish_semantic_batches(self.st,{},SCOPE)
        async def unexpected(**kwargs):raise AssertionError('native insert must not run')
        self.manager.db.insert_platform_message_history=unexpected
        install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
        var,event=self.event();token=var.set(event)
        try:
            result=asyncio.run(self.manager.db.insert_platform_message_history(platform_id='tenant',user_id='umo',content=self.content()))
            self.assertIsNone(result)
        finally:var.reset(token)

    def test_query_started_before_retirement_filters_after_await_even_if_delete_fails(self):
        self.prepare();asyncio.run(self.insert('tenant','umo',self.content()))
        async def scenario():
            started=asyncio.Event();release=asyncio.Event();original=self.manager.db.get_platform_message_history
            async def delayed(*args,**kwargs):
                rows=await original(*args,**kwargs);started.set();await release.wait();return rows
            self.manager.db.get_platform_message_history=delayed
            install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
            task=asyncio.create_task(self.manager.db.get_platform_message_history('tenant','umo'))
            await started.wait()
            self.st.semantic_native=(self.manager,'tenant')
            from unittest.mock import patch
            with patch('semantic_runtime.clean_native_history',side_effect=sqlite3.OperationalError('locked')):
                result=finish_semantic_batches(self.st,{},SCOPE)
            self.assertEqual(result['native']['result'],'pending')
            self.assertEqual(self.st.db.execute("SELECT status FROM memory_checkpoint_issues WHERE kind='native_history_cleanup'").fetchone()[0],'open')
            release.set();self.assertEqual(await task,[])
            self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['native']['result'],'clean')
            self.assertEqual(self.st.db.execute("SELECT status FROM memory_checkpoint_issues WHERE kind='native_history_cleanup'").fetchone()[0],'resolved')
            self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        asyncio.run(scenario())

    def test_late_insert_delete_failure_is_pending_and_retry_does_not_resummarize(self):
        self.prepare()
        async def scenario():
            started=asyncio.Event();release=asyncio.Event()
            async def delayed(*args,**kwargs):
                started.set();await release.wait();return await self.insert(*args,**kwargs)
            self.manager.db.insert_platform_message_history=delayed
            install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
            var,event=self.event();token=var.set(event)
            try:task=asyncio.create_task(self.manager.db.insert_platform_message_history('tenant','umo',self.content()))
            finally:var.reset(token)
            await started.wait();finish_semantic_batches(self.st,{},SCOPE)
            from unittest.mock import patch
            with patch('semantic_runtime.clean_native_history',side_effect=sqlite3.OperationalError('locked')):
                release.set()
                with self.assertRaisesRegex(ValueError,'native_history_cleanup_pending'):await task
            self.assertEqual(self.st.semantic_native_status['result'],'pending')
            self.assertEqual(await self.manager.db.get_platform_message_history('tenant','umo'),[])
            self.st.semantic_native=(self.manager,'tenant')
            self.assertEqual(finish_semantic_batches(self.st,{},SCOPE)['native']['historyRows'],1)
            self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        asyncio.run(scenario())

    def test_caller_metadata_cannot_override_real_new_event_identity(self):
        self.prepare();finish_semantic_batches(self.st,{},SCOPE)
        install_native_retirement_guard(self.manager,{'tenant':self.st},{'tenant':{}})
        var,event=self.event('new');token=var.set(event)
        try:row=asyncio.run(self.manager.db.insert_platform_message_history('tenant','umo',self.content('old')))
        finally:var.reset(token)
        self.assertEqual(row.content['_fishbot_source'],{'scope':SCOPE,'event_id':'new'})
        self.assertEqual(len(asyncio.run(self.manager.db.get_platform_message_history('tenant','umo'))),1)

    def test_shared_batch_tombstone_uses_original_scope_and_event(self):
        self.prepare();scope='group:one'
        members=[event_key('bot_shared',model_json([scope,'42']))]
        self.st.db.execute("UPDATE memory_semantic_batches SET scope='bot_shared',members=?,state='retired'",(model_json(members),));self.st.db.commit()
        asyncio.run(self.insert('tenant','umo',self.content('42',scope)))
        asyncio.run(self.insert('tenant','umo',self.content('42','group:two')))
        result=clean_native_history(self.manager,'tenant',self.st)
        self.assertEqual(result['historyRows'],1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(json.loads(db.execute('SELECT content FROM platform_message_history').fetchone()[0])['_fishbot_source']['scope'],'group:two')

    def test_real_sdk_database_manager_and_history_tool_exclude_full_35_round_batch(self):
        from astrbot.core.db.sqlite import SQLiteDatabase
        from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
        from astrbot.core.tools.message_tools import GetGroupMessageHistoryTool
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.message.components import Plain
        from astrbot.core.message.message_event_result import MessageChain
        async def scenario():
            native=SQLiteDatabase(str(self.root/'real-native.db'))
            await native.initialize()
            try:
                manager=NS(db=native)
                history=PlatformMessageHistoryManager(native)
                scope='group:one'
                for index in range(35):
                    for role in ('user','bot'):
                        content=self.content(str(index),scope,role)
                        content['message'][0]['text']='retired raw '+str(index)
                        await history.insert('tenant','umo',content,max_messages=100)
                fresh=self.content('new',scope);fresh['message'][0]['text']='new batch retained'
                await history.insert('tenant','umo',fresh,max_messages=100)
                members=[event_key('bot_shared',model_json([scope,str(i)])) for i in range(35)]
                self.st.db.execute("UPDATE memory_semantic_batches SET scope='bot_shared',members=?,state='retired'",(model_json(members),));self.st.db.commit()
                install_native_retirement_guard(manager,{'tenant':self.st},{'tenant':{}})
                result=clean_native_history(manager,'tenant',self.st)
                self.assertEqual(result['historyRows'],70,result)
                self.assertEqual(result['result'],'clean')
                # The real manager serializes a chain, while the DB wrapper
                # derives its identity from the actual SDK event ContextVar.
                var,event=self.event('later',scope=scope)
                token=var.set(event)
                try:
                    await history.insert_message_chain('tenant','umo',MessageChain(chain=[Plain(text='later chain retained')]),'user')
                finally:var.reset(token)
                event.get_message_type=lambda:MessageType.GROUP_MESSAGE
                sdk=NS(message_history_manager=history,get_config=lambda **kw:{'provider_ltm_settings':{'group_message_history_enable':True,'group_message_history_max_cnt':100}})
                context=NS(context=NS(event=event,context=sdk))
                output=await GetGroupMessageHistoryTool().call(context)
                self.assertNotIn('retired raw',output)
                self.assertIn('new batch retained',output)
                self.assertIn('later chain retained',output)
                with closing(sqlite3.connect(native.db_path)) as db:
                    rows=db.execute('SELECT content FROM platform_message_history').fetchall()
                    self.assertEqual(len(rows),2)
                    self.assertNotIn('retired raw',str(rows))
            finally:await native.engine.dispose()
        asyncio.run(scenario())

if __name__=='__main__':unittest.main()
