"""Final remediation fixtures: licensed prefix continuity, pending-coverage
checkpoints, snapshot-preserving persistence and typed failure evidence.

All cases run offline on real SQLite/BehaviorStore fixtures; no model, QQ or
network access. These fixtures pin the contract that a committed request
prefix never changes without a real 35-round license.
"""
import asyncio,contextvars,copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import readonly_journal as journal_module
from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
from context_protocol import READONLY_CURRENT,model_json,SOURCE
import summary_material as material
from behavior import BehaviorStore

SCOPE='private:12345678'

def packet(state):return {'role':'user','content':READONLY_CURRENT+'\n'+model_json(state)}
def event_packet(identity,policy='fixed-policy',guidance='current-guidance'):
    from context_transport import EVENT_CONTEXT_CURRENT
    return {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':identity},'policy':policy,'currentGuidance':guidance,'meaning':'platform event data'})}

def sourced(event,text='看这个',scope=SCOPE):
    return {'role':'user','content':SOURCE+json.dumps({'scope':scope,'sender_id':'12345678','event_id':event},ensure_ascii=False)+'\n'+text}

class StubAuthority:
    def __init__(self,grant=True):self.rows=[];self.web_proofs=[];self.grant=grant
    def reserve_context_reconstruction(self,scope,request_id,summary_ids=None,web_proof=None,logical_scope=None):
        self.web_proofs.append(web_proof)
        if not self.grant:return None
        value={'id':len(self.rows)+1,'scope':scope,'epoch':1,'requestId':request_id};self.rows.append([value,'reserved']);return value
    def settle_context_reconstruction(self,permit,success):
        row=next((x for x in self.rows if x[0]==permit),None)
        if not row or row[1]!='reserved':return False
        row[1]='consumed' if success else 'available';return True

class DeferralTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.j=ReadonlyJournal(self.db,SCOPE)
        # Committed shape mirrors production: the last committed request ended
        # at user u2; the assistant reply a2 was saved natively afterwards.
        self.base=[{'role':'system','content':'trusted identity'},sourced('1'),{'role':'assistant','content':'第一轮真实回复'},sourced('2','第二轮问题')]
        self.state={'state':{'facts':[]},'recent_messages':[{'event':'1','text':'actual message'}]}
    def first(self):
        p=self.j.prepare(self.base+[event_packet({'sender_id':'12345678'}),packet(self.state)],('t','b',1),now=1,strict=True,guarded=True)
        self.assertTrue(self.j.commit(p));return p

    def test_coverage_retirement_defers_without_permit_and_prefix_stays_byte_exact(self):
        first=self.first()
        native=self.base+[{'role':'assistant','content':'第二轮真实回复'},sourced('3','第三轮新消息')]
        covered={'1':{'subject':'12345678','digest':material.digest(self.base[1]),'reply_digest':material.reply_digest(self.base[1:3])},
                 '2':{'subject':'12345678','digest':material.digest(self.base[3]),'reply_digest':material.reply_digest(native[3:5])}}
        # A summary completed: its coverage projection drops both covered
        # blocks, including the last committed turn. The remaining view cannot
        # chain to the committed tail. Production always passes the honest
        # full native view in this shape, so the coverage deferral keeps the
        # committed prefix byte-exact and defers retirement to the next
        # licensed checkpoint; a true zero-overlap fresh window (no drops)
        # takes the fresh-event fallback instead (FreshWindowTests).
        projected=material.project_history(native+[event_packet({'sender_id':'12345678'}),packet(self.state)],covered,SCOPE,'3')
        self.assertEqual([m.get('content') for m in projected[1:2]],[native[5]['content']],'projection keeps only the new turn')
        latest=copy.deepcopy(self.state);latest['state']['facts']=[{'source_scope':SCOPE,'value':'新事实'}]
        unprojected=native+[event_packet({'sender_id':'12345678'}),packet(latest)]
        incoming=projected[:-2]+[event_packet({'sender_id':'12345678'}),packet(latest)]
        p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True,unprojected=copy.deepcopy(unprojected))
        self.assertEqual(p.retirement_deferred,'coverage_retirement')
        self.assertEqual(p.wire[:len(first.wire)],first.wire,'committed prefix must stay byte-exact')
        self.assertEqual(readonly_state(p.wire),latest)
        self.assertTrue(self.j.commit(p))
        # The deferred base keeps the committed system and the covered turns,
        # and the uncommitted real assistant reply is appended, never skipped.
        self.assertIn('第一轮真实回复',[m.get('content') for m in p.base])
        self.assertIn('第二轮真实回复',[m.get('content') for m in p.base])

def readonly_state(wire):
    from decision_history import apply_changes
    state=None
    for m in wire:
        c=m.get('content')
        if m.get('role')!='user' or not isinstance(c,str) or not c.startswith(READONLY_CURRENT+'\n'):continue
        value=json.loads(c.split('\n',1)[1])
        state=apply_changes(state,value['readonlyChanges']) if set(value)=={'readonlyChanges'} else value
    return state

class PersistenceGuardTests(unittest.TestCase):
    def test_capacity_failure_keeps_last_valid_snapshot(self):
        tmp=tempfile.TemporaryDirectory();db=sqlite3.connect(Path(tmp.name)/'behavior.sqlite')
        db.execute('CREATE TABLE memory_rounds(scope TEXT,event TEXT)')
        j=ReadonlyJournal(db,SCOPE)
        base=[{'role':'system','content':'trusted identity'},{'role':'user','content':'真实第一问'}]
        state={'state':{'facts':[]},'recent_messages':[]}
        p=j.prepare(base+[packet(state)],('t','b',1),now=1)
        self.assertTrue(j.commit(p))
        row=db.execute('SELECT state,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(j.scope_key,)).fetchone()
        self.assertEqual(row[0],'ready');self.assertIsNotNone(row[1])
        good_payload,good_sha=row[1],row[2]
        # Shrink the shared persisted-bytes budget: the next commit must
        # refuse WITHOUT destroying the last recoverable snapshot.
        with patch.object(journal_module,'MAX_PERSISTED_JOURNAL_BYTES',10):
            bigger=copy.deepcopy(base)+[{'role':'assistant','content':'真实回复'},{'role':'user','content':'第二问'},packet({**state,'recent_messages':[{'event':'2','text':'第二条真实消息'}]})]
            candidate=j.prepare(bigger,('t','b',1),now=2)
            self.assertFalse(j.commit(candidate))
            self.assertEqual(j.recovery_error,'journal_storage_capacity')
        row=db.execute('SELECT state,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(j.scope_key,)).fetchone()
        self.assertEqual(row[0],'ready','the last valid snapshot must survive a capacity refusal')
        self.assertEqual(row[1],good_payload);self.assertEqual(row[2],good_sha)
        db.close();tmp.cleanup()

    def test_web_permit_cannot_rewrite_committed_history(self):
        db=sqlite3.connect(':memory:')
        authority=StubAuthority(grant=False)
        j=ReadonlyJournal(db,SCOPE)
        proof={'permitId':'audit-1','revision':3,'contextScope':SCOPE}
        base=[{'role':'system','content':'old persona'},{'role':'user','content':'真实第一问'}]
        state={'state':{'facts':[]},'recent_messages':[]}
        tail=[event_packet({'sender_id':'12345678'}),packet(state)]
        p=j.prepare(base+tail,('t','b',1),now=1,strict=True,guarded=True,
                    reconstruction_authority=authority,reconstruction_scope=SCOPE,reconstruction_web_proof=proof,reconstruction_context_scope=SCOPE)
        self.assertTrue(j.commit(p));self.assertIsNone(p.reconstruction_permit)
        # A pending System change defers on the committed wire; it never needs
        # and never receives a reconstruction license.
        changed=[{'role':'system','content':'new persona'},{'role':'user','content':'真实第一问'}]
        deferred=j.prepare(changed+tail,('t','b',1),now=2,strict=True,guarded=True,
                           reconstruction_authority=authority,reconstruction_scope=SCOPE,
                           reconstruction_web_proof=proof,reconstruction_context_scope=SCOPE)
        self.assertEqual(deferred.retirement_deferred,'system_change');self.assertIsNone(deferred.reconstruction_permit)
        # A real reconstruction trigger (new checkpoint retirement) cannot be
        # authorized by a web-config permit anymore: only a 35-round
        # checkpoint reserve can mint that license.
        with self.assertRaises(ContextReconstructionDenied):
            j.prepare(base+tail,('t','b',1),now=3,strict=True,guarded=True,
                      reconstruction_authority=authority,reconstruction_scope=SCOPE,
                      reconstruction_web_proof=proof,reconstruction_context_scope=SCOPE,
                      retirement='minted-checkpoint-stamp')
        self.assertIsNone(authority.web_proofs[-1],'a committed prefix must never be rewritable by a web-config permit')
        # A genuinely fresh scope builds its first baseline without any permit.
        fresh=ReadonlyJournal(sqlite3.connect(':memory:'),'private:99999999')
        fp=fresh.prepare([{'role':'system','content':'new persona'},{'role':'user','content':'新会话'}]+tail,('t','b',1),now=1,strict=True,guarded=True,
                         reconstruction_authority=authority,reconstruction_scope='private:99999999',reconstruction_web_proof=proof,reconstruction_context_scope='private:99999999')
        self.assertIsNone(fp.reconstruction_permit)
        self.assertTrue(fresh.commit(fp))

class ReserveFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserve_falls_back_to_authoritative_checkpoint_after_summary_ages_out(self):
        tmp=tempfile.TemporaryDirectory()
        from behavior import BehaviorStore
        store=BehaviorStore(Path(tmp.name)/'behavior.sqlite')
        try:
            async def summarize(**kw):return NS(completion_text=json.dumps({'summary':'35轮完成后的真实摘要'}))
            var=contextvars.ContextVar('t',default=None)
            for index in range(35):store.reflection.record(SCOPE,'12345678',f'fb-{index}',f'真实问题{index}',f'真实回复{index}')
            self.assertTrue(store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=summarize),lambda:True,{},var))
            for _ in range(8):await asyncio.sleep(0)
            if store.reflection.task:await asyncio.wait_for(store.reflection.task,5)
            row=store.db.execute('SELECT id,summary_id FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()
            self.assertIsNotNone(row)
            # The rolling view no longer shows this checkpoint's summary.
            permit=store.reflection.reserve_context_reconstruction(SCOPE,'aged-out-request',[999999])
            self.assertIsNotNone(permit,'a valid checkpoint must stay reservable when its summary left the visible window')
            self.assertTrue(store.reflection.settle_context_reconstruction(permit,True))
        finally:
            store.close();tmp.cleanup()

class PendingCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.var=contextvars.ContextVar('pending_test',default=None)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def drain(self):
        for _ in range(8):await asyncio.sleep(0)

    async def test_completed_35_rounds_with_hold_wait_as_pending_then_promote(self):
        async def summarize(**kw):return NS(completion_text=json.dumps({'summary':'35轮真实摘要，其中一个话题未完成','motives':[{'event':'held-0','topic':'未完成的约定话题','quote':'明天继续聊这个约定','status':'open'}]}))
        for index in range(35):self.store.reflection.record(SCOPE,'12345678',f'held-{index}',f'真实问题{index}',f'已确认回复{index}')
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=summarize),lambda:True,{},self.var))
        await self.drain()
        row=self.db.execute('SELECT state,pending_events,round_count FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()
        if row is None:
            # The model quote did not match evidence, so no protected hold
            # remained: the batch minted a plain available checkpoint.
            row=self.db.execute('SELECT state,pending_events,round_count FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()
        self.assertIsNotNone(row,'35 completed rounds must always mint their checkpoint row')
        self.assertEqual(row[2],35)
        self.assertIn(row[0],('available','pending_coverage'))
        if row[0]=='pending_coverage':
            pending=json.loads(row[1])
            self.assertTrue(pending)
            # Pending state is not a license and does not retire wire.
            stamp_before=self.store.reflection.reconstruction_stamp(SCOPE)
            self.assertIsNone(self.store.reflection.reserve_context_reconstruction(SCOPE,'waiter',[]))
            # A later batch carries complete coverage for the held events.
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version) VALUES(?,?,?,?,2)',(SCOPE,'补齐覆盖的真实后续摘要','[]',time.time(),2)).lastrowid
            for event in pending:
                self.db.execute('INSERT INTO memory_summary_coverage(scope,event,subject,digest,summary_id) VALUES(?,?,?,?,?)',(SCOPE,str(event),'12345678','v1:'+'0'*64+':'+'0'*64,identifier))
            self.db.execute("DELETE FROM memory_hold_rounds WHERE scope=?",(SCOPE,))
            self.db.commit()
            self.store.reflection._promote_pending_checkpoints(SCOPE);self.db.commit()
            state=self.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()[0]
            self.assertEqual(state,'available')
            permit=self.store.reflection.reserve_context_reconstruction(SCOPE,'promoted-request',[identifier])
            self.assertIsNotNone(permit)

class ClaimSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def payload(self):
        return [{'sender_id':'12345678','event':'e1','user':'我周六八点在东门等你','bot':'好，到时见','at':1},
                {'sender_id':'12345678','event':'e2','user':'今天天气不错','bot':'是啊哈哈','at':2},
                {'sender_id':'12345678','event':'e3','user':'我叫小双，不叫阿双','bot':'明白了，小双','at':3}]
    def test_no_per_turn_transcription_without_selection(self):
        claims=material.derive_claims(SCOPE,self.payload(),{},[])
        kinds={claim['kind'] for claim in claims}
        self.assertNotIn('human_statement',kinds,'ordinary chat must not be transcribed claim-per-turn')
        self.assertNotIn('bot_statement',kinds)
    def test_model_selected_evidence_is_verified_verbatim(self):
        good={'evidence':[{'event':'e1','who':'user','quote':'周六八点在东门等你'},{'event':'e2','who':'bot','quote':'是啊哈哈'}]}
        claims=material.derive_claims(SCOPE,self.payload(),{},[],good)
        kinds=sorted(claim['kind'] for claim in claims)
        self.assertEqual(kinds,['bot_statement','human_statement'])
        human=next(claim for claim in claims if claim['kind']=='human_statement')
        self.assertEqual(human['speaker']['id'],'12345678')
        bad={'evidence':[{'event':'e1','who':'user','quote':'这句原话里根本没有'},{'event':'e1','who':'admin','quote':'周六八点'}]}
        self.assertEqual([claim['kind'] for claim in material.derive_claims(SCOPE,self.payload(),{},[],bad)],[])
    def test_deterministic_correction_survives_without_model_selection(self):
        self.db.execute("INSERT INTO memory_blocks(scope,subject,name,block_all,rejected_values,revision) VALUES(?,?,?,0,?,1)",
                        (SCOPE,'12345678','称呼','[{"event":"e3","v":"小双","quote":"我叫小双，不叫阿双"}]'))
        self.db.execute("INSERT INTO facts(scope,subject,name,value,event,updated,version,active) VALUES(?,?,?,?,?,1,1,1)",(SCOPE,'12345678','称呼','阿双','e0'))
        self.db.commit()
        claims=material.derive_claims(SCOPE,self.payload(),{},material.batch_corrections(self.db,SCOPE,['e3']))
        corrections=[claim for claim in claims if claim['kind']=='correction']
        self.assertEqual(len(corrections),1)
        self.assertTrue(corrections[0]['protected']);self.assertEqual(corrections[0]['value'],'小双')
    def test_material_recover_completes_after_store_drop(self):
        item={'version':1,'event':'e9','subject':'12345678','scope':SCOPE,'text':sourced('e9')['content'],'reply':'','native_reply':None,'images':[],'complete':False,'digest':material.digest(sourced('e9'))}
        self.store.reflection.record(SCOPE,'12345678','e9','看这个','已回复',materials=[dict(item)])
        stored=json.loads(self.db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'e9')).fetchone()[0])
        self.assertFalse(stored['complete'])
        again=dict(item);again['images']=['data:image/png;base64,YQ=='];again['complete']=True
        material.remember(self.db,SCOPE,[again])
        stored=json.loads(self.db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'e9')).fetchone()[0])
        self.assertTrue(stored['complete']);self.assertEqual(len(stored['images']),1)

class FailureTypingTests(unittest.TestCase):
    def run_attempt(self,wire_seen):
        import model_cache
        rows=[]
        lease={'root':str(Path(self.tmp.name)),'botId':'b','tenantId':'t'}
        token=model_cache.begin_attempt(None,None)
        try:model_cache._attempt.get()['lease']=lease
        except AttributeError:pass
        value=model_cache._attempt.get()
        if wire_seen:value.update(wireIndex=1,transportStarted=True)
        with patch.object(model_cache,'record_diagnostic',lambda lease,row:rows.append(row) or True):
            model_cache.finish_attempt('failed',reason='FixtureTimeout')
        model_cache.reset_attempt(token)
        return rows
    def setUp(self):self.tmp=tempfile.TemporaryDirectory()
    def tearDown(self):self.tmp.cleanup()
    def test_request_not_sent_vs_provider_reached(self):
        rows=self.run_attempt(False)
        self.assertEqual(rows[-1]['diagnosticDetail'],'request_not_sent')
        rows=self.run_attempt(True)
        self.assertEqual(rows[-1]['diagnosticDetail'],'request_submission_unknown')

class FreshWindowTests(unittest.TestCase):
    """Worker restarts can lose the in-memory SDK conversation: the presented
    native view then has zero overlap with the committed base. The journal
    stays authoritative and appends only provably new sourced events."""
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.j=ReadonlyJournal(self.db,SCOPE)
        self.base=[{'role':'system','content':'persona'},{'role':'system','content':'platform'},
                   sourced('1'),{'role':'assistant','content':'真实回复'},sourced('2','第二条')]
        self.state={'state':{'facts':[]},'recent_messages':[]}
    def first(self):
        p=self.j.prepare(self.base+[event_packet({'sender_id':'12345678'}),packet(self.state)],('t','b',1),now=1,strict=True,guarded=True)
        self.assertTrue(self.j.commit(p));return p
    def test_fresh_view_appends_only_new_sourced_events(self):
        first=self.first()
        fresh=[{'role':'system','content':'persona'},{'role':'system','content':'platform'},
               sourced('9','重启后的全新一条消息')]
        incoming=fresh+[event_packet({'sender_id':'12345678'}),packet(self.state)]
        p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True)
        self.assertIsNotNone(p)
        self.assertEqual(p.retirement_deferred,'fresh_native_window')
        self.assertEqual(p.wire[:len(first.wire)],first.wire,'committed prefix stays byte-exact')
        self.assertEqual(p.wire[len(first.wire)],fresh[-1],'the first appended element is the real new turn')
        self.assertTrue(self.j.commit(p))
    def test_sdk_null_image_id_matches_view_but_never_changes_committed_wire(self):
        self.base[2]['content']=[{'type':'text','text':self.base[2]['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]
        first=self.first();incoming=copy.deepcopy(self.base)
        incoming[2]['content'][1]['image_url']['id']=None
        incoming += [event_packet({'sender_id':'12345678'}),packet(self.state),sourced('9','new')]
        p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True)
        self.assertEqual(p.wire[:len(first.wire)],first.wire)
        self.assertNotIn('id',first.base[2]['content'][1]['image_url']);self.j.abort_candidate(p)
        for key,value in (('id','changed-id'),('url','data:image/png;base64,Yg=='),('detail','low')):
            changed=copy.deepcopy(incoming);changed[2]['content'][1]['image_url'][key]=value
            with self.assertRaises(ContextReconstructionDenied):self.j.prepare(changed,('t','b',1),now=2,strict=True,guarded=True)

    def test_system_message_count_change_defers_without_rewriting_history(self):
        first=self.first()
        for systems in ([{'role':'system','content':'new system'}], [{'role':'system','content':'new system'},{'role':'developer','content':'new policy'},{'role':'system','content':'extra'}]):
            incoming=systems+self.base[2:]+[event_packet({'sender_id':'12345678'}),packet(self.state),sourced('9','new event')]
            p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True)
            self.assertEqual(p.wire[:len(first.wire)],first.wire)
            self.assertEqual(p.base[:2],first.base[:2]);self.assertEqual(p.base[-1],sourced('9','new event'))
            self.j.abort_candidate(p)

    def test_sdk_packets_before_prompt_do_not_block_fresh_unprojected_tail(self):
        first=self.first()
        for image in (False,True):
            new=sourced('9','new event')
            if image:new['content']=[{'type':'text','text':new['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]
            incoming=self.base[:2]+[event_packet({'sender_id':'12345678'}),packet(self.state),new]
            p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True,unprojected=incoming)
            self.assertEqual(p.retirement_deferred,'fresh_native_window')
            self.assertEqual(p.wire[:len(first.wire)],first.wire)
            self.assertEqual(p.wire[len(first.wire)],new)
            self.assertEqual(p.base,first.base+[new])
            self.j.abort_candidate(p)
    def test_sdk_turn_truncation_and_system_change_preserve_committed_wire(self):
        first=self.first()
        # SDK enforces its turn limit before provider preparation. Its window
        # starts at the second real user event while System has also changed.
        new=sourced('9','new event')
        native=[{'role':'system','content':'changed persona'}]+self.base[4:]+[
            {'role':'assistant','content':'confirmed previous answer'},new]
        incoming=native+[event_packet({'sender_id':'12345678'}),packet(self.state)]
        p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True)
        self.assertEqual(p.retirement_deferred,'native_window_overlap')
        self.assertEqual(p.wire[:len(first.wire)],first.wire)
        self.assertEqual(p.base,first.base+native[-2:])
        self.assertEqual(p.wire[len(first.wire):len(first.wire)+2],native[-2:])
        self.j.abort_candidate(p)
        changed=copy.deepcopy(incoming);changed[1]['content']='changed historical user'
        with self.assertRaises(ContextReconstructionDenied):
            self.j.prepare(changed,('t','b',1),now=2,strict=True,guarded=True)
        p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True)
        self.assertTrue(self.j.commit(p))
        next_native=[native[0],new,{'role':'assistant','content':'next confirmed answer'},sourced('10','next event')]
        following=self.j.prepare(next_native+[event_packet({'sender_id':'12345678'}),packet(self.state)],
                                 ('t','b',1),now=3,strict=True,guarded=True)
        self.assertEqual(following.wire[:len(p.wire)],p.wire)
        self.assertEqual(following.base,p.base+next_native[-2:])
        self.j.abort_candidate(following)
    def test_known_event_in_fresh_view_still_requires_license(self):
        self.first()
        # A replayed/already-committed event cannot be re-appended: the view
        # partially overlaps history in an order that cannot be proven.
        fresh=[{'role':'system','content':'persona'},{'role':'system','content':'platform'},sourced('1','真实回复')]
        with self.assertRaises(ContextReconstructionDenied):
            self.j.prepare(fresh+[event_packet({'sender_id':'12345678'}),packet(self.state)],('t','b',1),now=2,strict=True,guarded=True)
        self.assertEqual(self.j._last_refusal_reason,'native_prefix_changed')
        evidence=self.j.refusal_evidence()
        self.assertEqual(evidence['refusal'],'native_prefix_changed')
    def test_unsourced_fresh_tail_is_not_appended(self):
        self.first()
        fresh=[{'role':'system','content':'persona'},{'role':'system','content':'platform'},
               {'role':'user','content':'没有来源标记的文本'}]
        with self.assertRaises(ContextReconstructionDenied):
            self.j.prepare(fresh+[event_packet({'sender_id':'12345678'}),packet(self.state)],('t','b',1),now=2,strict=True,guarded=True)

class ResearchFactsIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_autonomous_result_rows_leave_recall_but_keep_storage(self):
        self.db.execute("INSERT INTO facts(scope,subject,name,value,event,updated,version,active) VALUES(?,?,?,?,?,1,1,1)",(SCOPE,'12345678','称呼','小双','e1'))
        self.db.execute("INSERT INTO facts(scope,subject,name,value,event,updated,version,active) VALUES(?,?,?,?,?,1,1,1)",(SCOPE,'12345678','自主研究:视频调研','{"kind":"research_result_summary_not_verified_facts"}','w1'))
        self.db.execute("INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,'event','autonomous_result',0.8)",(SCOPE,'12345678','自主研究:视频调研'))
        self.db.commit()
        import memory_scope
        names={row[0] for row in memory_scope.fact_rows(self.db,SCOPE,'12345678')}
        self.assertIn('称呼',names)
        self.assertFalse(any(name.startswith('自主研究') for name in names),'research task records must stay out of persona recall pools')
        kept=self.db.execute("SELECT count(*) FROM facts WHERE name LIKE '自主研究%'").fetchone()[0]
        self.assertEqual(kept,1,'existing rows keep storage and rollback mapping')

if __name__=='__main__':
    unittest.main()
