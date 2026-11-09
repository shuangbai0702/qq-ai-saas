"""Final remediation regression (2026-11-07 second audit A/B packages).

A: submitted-unknown candidates are retained; provably unsent ones leave no
   trace; a crash after staging resumes on the staged candidate.
B: this system's own tool round-trips become complete bounded v2 material;
   held rounds recover without new 35 rounds and never re-pay for unsupported
   or protected evidence.
All offline: real SQLite, fake provider, no network, no QQ.
"""
import asyncio,contextvars,copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import readonly_journal as journal_module
from readonly_journal import ReadonlyJournal
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
    def __init__(self,db=None):self.rows=[];self.db=db
    def reserve_context_reconstruction(self,scope,request_id,summary_ids=None,web_proof=None,logical_scope=None):
        value={'id':len(self.rows)+1,'scope':scope,'epoch':1,'requestId':request_id};self.rows.append([value,'reserved']);return value
    def settle_context_reconstruction(self,permit,success,in_transaction=False):
        row=next((x for x in self.rows if x[0]==permit),None)
        if not row or row[1]!='reserved':return False
        row[1]='consumed' if success else 'available';return True

def base_messages():
    return [{'role':'system','content':'trusted identity'},sourced('1'),{'role':'assistant','content':'第一轮真实回复'},sourced('2','第二轮问题')]

def prepare_first(j):
    p=j.prepare(base_messages()+[event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})],('t','b',1),now=1,strict=True,guarded=True)
    j.commit(p);return p

class SubmissionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=sqlite3.connect(Path(self.tmp.name)/'behavior.sqlite')
        self.db.execute('CREATE TABLE memory_rounds(scope TEXT,event TEXT)')
        self.j=ReadonlyJournal(self.db,SCOPE);self.first=prepare_first(self.j)

    def tearDown(self):
        self.j.db.close();self.tmp.cleanup()

    def staged(self):
        incoming=base_messages()+[{'role':'assistant','content':'第二轮真实回复'},sourced('3','第三轮新消息'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p=self.j.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True)
        self.assertTrue(p.wire[:len(self.first.wire)]==self.first.wire)
        self.assertTrue(self.j.stage_candidate(p))
        return p

    def row(self):
        return self.db.execute('SELECT state,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(self.j.scope_key,)).fetchone()

    def test_stage_persists_candidate_before_submission(self):
        p=self.staged()
        self.assertEqual(self.j._staged,p.serial)
        state,payload,sha=self.row()
        self.assertEqual(state,'ready')
        saved=ReadonlyJournal(self.db,SCOPE).last
        self.assertIn('第三轮新消息',json.dumps(saved.wire,ensure_ascii=False),'the outgoing tail is recoverable before any HTTP submission')

    def test_unsent_candidate_abort_restores_previous_snapshot_exactly(self):
        before=self.row()
        p=self.staged()
        self.assertTrue(self.j.abort_candidate(p))
        after=self.row()
        self.assertEqual(before[1:],after[1:],'a provably unsent candidate leaves no trace in the persisted journal')
        self.assertIsNone(self.j._staged)
        self.assertEqual(self.j.last.wire,self.first.wire)

    def test_submitted_unknown_candidate_is_retained_as_baseline(self):
        p=self.staged()
        self.assertTrue(self.j.retain_submitted(p,'provider_request_sent'))
        self.assertEqual(self.j.last.wire,p.wire,'a possibly-received request must not fall back to a prefix without its tail')
        self.assertIsNone(self.j._staged)
        state,payload,sha=self.row()
        self.assertIn('第三轮新消息',json.dumps(ReadonlyJournal(self.db,SCOPE).last.wire,ensure_ascii=False))
        # The next request continues from the retained baseline byte-exactly.
        incoming=base_messages()+[{'role':'assistant','content':'第二轮真实回复'},sourced('3','第三轮新消息'),{'role':'assistant','content':'第三轮真实回复'},sourced('4','第四轮'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        nxt=self.j.prepare(incoming,('t','b',1),now=3,strict=True,guarded=True)
        self.assertEqual(nxt.wire[:len(p.wire)],p.wire)
        self.assertTrue(self.j.commit(nxt))

    def test_unknown_submission_consumes_the_reconstruction_permit(self):
        authority=StubAuthority()
        incoming=base_messages()+[{'role':'assistant','content':'第二轮真实回复'},sourced('3','第三轮新消息'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        # Force the reconstruction branch: retire the committed snapshot first.
        self.j.retire_committed(self.first,'invalidated')
        j2=ReadonlyJournal(self.db,SCOPE)
        self.assertEqual(j2.retired_reason,'persisted_retirement','a retired journal needs a licensed rebuild')
        with patch.object(StubAuthority,'reserve_context_reconstruction',authority.reserve_context_reconstruction), \
             patch.object(StubAuthority,'settle_context_reconstruction',authority.settle_context_reconstruction):
            p=j2.prepare(incoming,('t','b',1),now=2,strict=True,guarded=True,reconstruction_authority=authority,reconstruction_scope=SCOPE)
            self.assertIsNotNone(p.reconstruction_permit,'the licensed rebuild minted a reservation')
            self.assertTrue(j2.stage_candidate(p))
            self.assertTrue(j2.retain_submitted(p,'provider_stream_started'))
        self.assertEqual(authority.rows[0][1],'consumed','an unknown-submission rebuild never returns its permit to available')
        self.assertEqual(j2.last.wire,p.wire)

    def test_crash_after_stage_resumes_on_the_staged_candidate(self):
        p=self.staged()
        # A crash loses the in-memory journal; the reloaded one owns the tail.
        fresh=sqlite3.connect(Path(self.tmp.name)/'behavior.sqlite')
        reloaded=ReadonlyJournal(fresh,SCOPE)
        try:
            self.assertIsNone(reloaded.recovery_error)
            self.assertEqual(reloaded.last.wire,p.wire,'the staged candidate survives a crash as the committed baseline')
            incoming=base_messages()+[{'role':'assistant','content':'第二轮真实回复'},sourced('3','第三轮新消息'),{'role':'assistant','content':'第三轮真实回复'},sourced('4','第四轮'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
            nxt=reloaded.prepare(incoming,('t','b',2),now=3,strict=True,guarded=True)
            self.assertEqual(nxt.wire[:len(p.wire)],p.wire)
            self.assertTrue(reloaded.commit(nxt))
        finally:
            reloaded.db.close();fresh.close()

    def test_stage_failure_keeps_previous_row_and_guard_state(self):
        p=self.staged()
        before=self.row()
        # Fill the shared persisted budget: staging must refuse without
        # corrupting the previous snapshot or the journal's guard state.
        with patch.object(journal_module,'MAX_PERSISTED_JOURNAL_BYTES',1):
            p2=self.j.prepare(base_messages()+[{'role':'assistant','content':'x'},sourced('9','y'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})],('t','b',1),now=4,strict=True,guarded=True)
            self.assertFalse(self.j.stage_candidate(p2))
            self.assertEqual(self.j.recovery_error,None,'staging refusal must not poison later prepares')
            self.assertEqual(self.row()[1],before[1])
        # Once storage is available, the candidate can be saved. Runtime must
        # not send it while staging refuses (covered at the provider boundary).
        self.assertTrue(self.j.stage_candidate(p2))
        self.assertTrue(self.j.commit(p2))

class ToolRoundMaterialTests(unittest.TestCase):
    def tool_block(self,complete=True):
        user={'role':'user','content':[{'type':'text','text':SOURCE+json.dumps({'scope':SCOPE,'sender_id':'12345678','event_id':'t1'})+'\n查一下'},
                                        {'type':'text','text':SOURCE+json.dumps({'scope':SCOPE,'sender_id':'12345678','event_id':'t1'})}]}
        block=[user,{'role':'assistant','content':None,'tool_calls':[{'id':'call1','function':{'name':'hosted_qq_group_context','arguments':'{"group":1}'}}]},
               {'role':'tool','tool_call_id':'call1','content':'群上下文工具结果：三条真实消息'}]
        if complete:block.append({'role':'assistant','content':'工具查完了，结果是三条消息。'})
        return block

    def test_complete_tool_round_becomes_version2_material(self):
        items=material.snapshots(self.tool_block(),SCOPE)
        self.assertEqual(len(items),1)
        item=items[0]
        self.assertTrue(item['complete'])
        self.assertEqual(item['version'],2)
        self.assertEqual([t['id'] for t in item['tools']],['call1'])
        self.assertEqual(item['tools'][0]['name'],'hosted_qq_group_context')
        self.assertIn('三条真实消息',item['tools'][0]['result'])
        self.assertTrue(material.valid_material(item,SCOPE))
        proof=material.coverage_digest(item)
        self.assertTrue(proof.startswith('v2:'))
        # The tool round-trip is bound into the proof.
        self.assertEqual(proof.split(':')[3],__import__('hashlib').sha256(model_json(item['tools']).encode()).hexdigest())

    def test_unpaired_tool_shape_stays_incomplete(self):
        # A tool result may be missing: the declared call stays unanswered.
        broken=[self.tool_block()[0],self.tool_block()[1]]
        item=material.snapshots(broken,SCOPE)[0]
        self.assertFalse(item['complete'],'an unanswered tool call can never prove coverage')
        self.assertIsNone(item.get('tools'))
        missing_result=[self.tool_block()[0],{'role':'assistant','content':None,'tool_calls':[{'id':'c2','function':{'name':'x','arguments':'{}'}}]},{'role':'tool','tool_call_id':'other','content':'错位回执'}]
        self.assertFalse(material.snapshots(missing_result,SCOPE)[0]['complete'])

    def test_projection_retires_tool_round_only_with_v2_proof(self):
        block=self.tool_block()
        history=[{'role':'system','content':'policy'}]+block+[sourced('t2','新问题')]
        user=block[0]
        item=material.snapshots([user]+block[1:],SCOPE)[0]
        proof={'t1':{'subject':'12345678','digest':material.digest(user),'reply_digest':material.reply_digest(block),'tools_digest':material.coverage_digest(item).split(':',3)[3]}}
        projected=material.project_history(history,proof,SCOPE,'t2')
        self.assertEqual([m.get('content') for m in projected if isinstance(m,dict) and m.get('role')=='user'][0],history[-1]['content'],'the complete tool round retires atomically with its bound proof')
        v1proof={'t1':{'subject':'12345678','digest':material.digest(user),'reply_digest':material.reply_digest(block)}}
        self.assertEqual(material.project_history(history,v1proof,SCOPE,'t2'),history,'a v1 proof can never retire a tool round')
        changed=copy.deepcopy(history);changed[2]['content']='摘要之后才出现的工具回执'
        self.assertEqual(material.project_history(changed,proof,SCOPE,'t2'),changed)

    def test_remember_upgrades_incomplete_v1_to_complete_v2_same_digest(self):
        user=self.tool_block()[0]
        partial=material.snapshots(self.tool_block()[:2],SCOPE)[0]  # unanswered call: incomplete v1
        db=sqlite3.connect(':memory:')
        from memory_reflection import Reflection
        store=BehaviorStore.__new__(BehaviorStore)  # not used; initialize tables directly
        db.close()
        tmp=tempfile.TemporaryDirectory();st=BehaviorStore(Path(tmp.name)/'behavior.sqlite')
        try:
            st.reflection.record(SCOPE,'12345678','t1','查一下','工具查完了',materials=[partial])
            row=json.loads(st.db.execute('SELECT material FROM memory_round_material WHERE event=?',('t1',)).fetchone()[0])
            self.assertFalse(row['complete'])
            full=material.snapshots(self.tool_block(),SCOPE)[0]
            material.remember(st.db,SCOPE,[full]);st.db.commit()
            upgraded=json.loads(st.db.execute('SELECT material FROM memory_round_material WHERE event=?',('t1',)).fetchone()[0])
            self.assertTrue(upgraded['complete'])
            self.assertEqual(upgraded['version'],2)
            self.assertIn('三条真实消息',upgraded['tools'][0]['result'])
            # Never a downgrade of an already complete proof.
            worse=dict(full);worse['complete']=False
            material.remember(st.db,SCOPE,[worse]);st.db.commit()
            still=json.loads(st.db.execute('SELECT material FROM memory_round_material WHERE event=?',('t1',)).fetchone()[0])
            self.assertTrue(still['complete'])
        finally:
            st.close();tmp.cleanup()

class HoldRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.var=contextvars.ContextVar('hr',default=None);self.now=time.time()-100000;self.calls=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def rounds(self,count=35):
        for i in range(count):
            msg={'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':'12345678','event_id':str(i)})+'\n普通话题'+str(i)}]}
            if i==0:
                # Round 0's stored material is incomplete (media dropped by the
                # store budget): it can only earn coverage after a repair.
                incomplete=material.snapshots([msg],SCOPE)[0];incomplete['images']=[];incomplete['complete']=False
                self.store.reflection.record(SCOPE,'12345678',str(i),'普通话题0','普通回复0',self.now+i,materials=[incomplete])
            else:
                self.store.reflection.record(SCOPE,'12345678',str(i),'普通话题'+str(i),'普通回复'+str(i),self.now+i,materials=material.snapshots([msg],SCOPE))

    def chat(self):
        async def fixture(**kw):
            self.calls.append(kw)
            return NS(completion_text=json.dumps({'summary':'本批真实背景摘要'},ensure_ascii=False))
        return fixture

    async def drain(self):
        for _ in range(14):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break

    async def test_hold_recovers_without_new_rounds_and_promotes_pending(self):
        self.rounds()
        metrics={}
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=self.chat()),lambda:True,metrics,self.var))
        await self.drain()
        row=self.db.execute('SELECT state,pending_events,summary_id FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()
        self.assertIsNotNone(row);self.assertEqual(row[0],'pending_coverage')
        self.assertIn('0',row[1])
        hold=self.db.execute('SELECT reason FROM memory_hold_rounds WHERE scope=? AND event=?',(SCOPE,'0')).fetchone()
        self.assertEqual(hold[0],'uncovered')
        self.assertEqual(len(self.calls),1)
        watermark=self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(SCOPE,)).fetchone()[0]
        # Repair the material from the committed journal (digest-bound recapture).
        msg={'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':'12345678','event_id':'0'})+'\n普通话题0'}]}
        def recapture(scope,events):
            self.assertEqual(scope,SCOPE);self.assertEqual(list(events),['0'])
            material.remember(self.db,SCOPE,material.snapshots([msg],SCOPE))
        self.store.reflection.recapture=recapture
        self.assertTrue(self.store.reflection.retry(),'held evidence recovers without waiting for 35 new rounds')
        await self.drain()
        promoted=self.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()[0]
        self.assertEqual(promoted,'available','complete coverage promotes the pending identity into the license')
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_hold_rounds WHERE scope=? AND event=?',(SCOPE,'0')).fetchone())
        self.assertEqual(self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(SCOPE,)).fetchone()[0],watermark,'recovery earns no watermark movement')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],1,'recovery never mints an extra checkpoint')
        self.assertEqual(metrics.get('holdRecoverySummaries'),1)
        self.assertEqual(len(self.calls),2,'exactly one bounded recovery model call')

    async def test_unsupported_hold_never_reaches_the_model(self):
        self.rounds()
        metrics={}
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=self.chat()),lambda:True,metrics,self.var))
        await self.drain()
        self.assertEqual(len(self.calls),1)
        # Native-only reply without a confirmed delivery can never cover.
        msg={'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':'12345678','event_id':'0'})+'\n普通话题0'}]}
        item=material.snapshots([msg,{'role':'assistant','content':'仅模型历史的回复'}],SCOPE)[0]
        item['reply']='';item['complete']=True
        item['native_reply']='仅模型历史的回复'
        self.db.execute('UPDATE memory_round_material SET material=? WHERE scope=? AND event=?',(json.dumps(item,ensure_ascii=False),SCOPE,'0'));self.db.commit()
        self.store.reflection.recapture=lambda scope,events:(_ for _ in ()).throw(AssertionError('recapture must not run for an already-complete material'))
        self.assertFalse(self.store.reflection.retry(),'an uncoverable hold stays blocked instead of re-paying')
        await asyncio.sleep(0)
        self.assertEqual(len(self.calls),1,'no model call for unsupported evidence')
        self.assertGreaterEqual(metrics.get('summaryHoldUnsupported'),1)

    async def test_protected_hold_is_left_to_the_batch_path(self):
        self.rounds()
        metrics={}
        async def protected(**kw):
            self.calls.append(kw)
            return NS(completion_text=json.dumps({'summary':'本批真实背景摘要','commitments':[{'event':'0','topic':'周末平江路','quote':'普通话题0','who':'user','stage':'accepted'}]},ensure_ascii=False))
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.assertTrue(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=protected),lambda:True,metrics,self.var))
            await self.drain()
        holds=self.db.execute("SELECT reason,count(*) FROM memory_hold_rounds WHERE scope=? GROUP BY reason",(SCOPE,)).fetchall()
        self.assertTrue(any(r=='protected' for r,_ in holds),holds)
        calls_before=len(self.calls)
        self.assertFalse(self.store.reflection.retry(),'protected claim-budget conflicts are carried by ordinary batches, not re-paid recoveries')
        await asyncio.sleep(0)
        self.assertEqual(len(self.calls),calls_before)

class UncommittedWindowHeadTests(unittest.TestCase):
    """A successful round aborted by a mid-request dependency change leaves an
    uncommitted completed round in the SDK window; without adoption the scope
    deadlocks forever (live 2026-11-08 00:3x evidence on one private scope)."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=sqlite3.connect(Path(self.tmp.name)/'behavior.sqlite')
        self.db.execute("CREATE TABLE IF NOT EXISTS pipeline_events(scope TEXT,subject TEXT,event TEXT,at REAL,source TEXT,stage TEXT,status TEXT,reason TEXT,attempted INTEGER,confirmed INTEGER,PRIMARY KEY(scope,subject,event))")
        self.j=ReadonlyJournal(self.db,SCOPE);self.first=prepare_first(self.j)

    def tearDown(self):
        self.j.db.close();self.tmp.cleanup()

    def window(self,event='9',reply='未提交轮的真实回复',sent=True,confirmed=1):
        # The known tail must chain exactly onto the committed base's own tail
        # (the base ends at user '2'; its reply belongs to a later window).
        known_tail=[sourced('2','第二轮问题')]
        head=[sourced(event,'第三轮新消息'),{'role':'assistant','content':reply}]
        self.db.execute("INSERT OR REPLACE INTO pipeline_events VALUES(?,?,?,?,?,?,?,?,?,?)",(SCOPE,'12345678',event,time.time(),'message','commit','sent' if sent else 'failed','',confirmed,confirmed))
        self.db.commit()
        return head+known_tail

    def test_delivered_uncommitted_round_is_adopted_once(self):
        raw=self.window()
        incoming=base_messages()+[event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        # The projected view carries only the new packets; the honest full
        # native view is passed via unprojected, as production always does.
        suffix=journal_module.uncommitted_window_head_extension(self.j.last,raw,self.db)
        self.assertIsNotNone(suffix,'a delivery-proven completed round must be adoptable')
        self.assertEqual(suffix,raw[:2])
        # The stuck production shape: the SDK window trimmed the old base and
        # now starts at the uncommitted round, so every prefix-overlap fails.
        stuck=[raw[0],raw[1],raw[2]]+[event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p=self.j.prepare(stuck,('t','b',1),now=3,strict=True,guarded=True,unprojected=copy.deepcopy(stuck))
        self.assertEqual(p.retirement_deferred,'uncommitted_window_head')
        self.assertEqual(p.wire[:len(self.first.wire)],self.first.wire,'committed prefix stays byte-exact')
        self.assertTrue(self.j.commit(p))
        self.assertIn('未提交轮的真实回复',[m.get('content') for m in p.wire if isinstance(m.get('content'),str)])
        # After adoption the round is known; a second adoption is impossible.
        self.assertIsNone(journal_module.uncommitted_window_head_extension(self.j.last,raw,self.db))

    def test_unsent_or_incomplete_heads_are_never_adopted(self):
        # A failed (never delivered) event must not be adopted.
        raw=self.window(sent=False)
        self.assertIsNone(journal_module.uncommitted_window_head_extension(self.j.last,raw,self.db))
        # An unanswered user turn (refused request) cannot prove completion.
        raw2=[sourced('8','被拒绝的请求')]
        self.db.execute("INSERT OR REPLACE INTO pipeline_events VALUES(?,?,?,?,?,?,?,?,?,?)",(SCOPE,'12345678','8',time.time(),'message','admission','blocked','',0,0));self.db.commit()
        self.assertIsNone(journal_module.uncommitted_window_head_extension(self.j.last,raw2,self.db))
        # A head whose remainder does not chain onto the committed tail fails.
        raw3=self.window(event='7')
        raw3[2]={'role':'user','content':SOURCE+json.dumps({'scope':SCOPE,'sender_id':'12345678','event_id':'2'},ensure_ascii=False)+'\n被改写的历史'}
        self.assertIsNone(journal_module.uncommitted_window_head_extension(self.j.last,raw3,self.db))

class WindowReloadExtensionTests(unittest.TestCase):
    """Live 2026-11-08 00:3x deadlock shape: the reloaded SDK window keeps
    turns older than a rebuilt baseline, and the baseline ends with a staged
    turn the restart killed before submission. Every prefix overlap failed
    for two hours; the baseline itself still appears verbatim in the window."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=sqlite3.connect(Path(self.tmp.name)/'behavior.sqlite')
        self.db.execute("CREATE TABLE IF NOT EXISTS pipeline_events(scope TEXT,subject TEXT,event TEXT,at REAL,source TEXT,stage TEXT,status TEXT,reason TEXT,attempted INTEGER,confirmed INTEGER,PRIMARY KEY(scope,subject,event))")
        self.j=ReadonlyJournal(self.db,SCOPE);self.first=prepare_first(self.j)
        # Second committed round: the base ends with a completed turn.
        second=base_messages()+[{'role':'assistant','content':'第二轮真实回复'},sourced('5','第三轮问题'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p=self.j.prepare(second,('t','b',1),now=2,strict=True,guarded=True)
        self.assertTrue(self.j.commit(p))
        # Delivery ledger for the committed turns.
        for delivered in ('1','2','5'):
            self.db.execute("INSERT OR REPLACE INTO pipeline_events VALUES(?,?,?,?,?,?,?,?,?,?)",(SCOPE,'12345678',delivered,time.time(),'message','commit','sent','',1,1))
        # A staged turn the restart killed before submission: PE interrupted.
        self.db.execute("INSERT OR REPLACE INTO pipeline_events VALUES(?,?,?,?,?,?,?,?,?,?)",(SCOPE,'12345678','6',time.time(),'message','ingress','interrupted','runtime_restarted',0,0));self.db.commit()
        third=base_messages()+[{'role':'assistant','content':'第二轮真实回复'},sourced('5','第三轮问题'),sourced('6','被重启打断的轮次'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p3=self.j.prepare(third,('t','b',1),now=3,strict=True,guarded=True)
        self.assertTrue(self.j.commit(p3))
        self.phantom_base=len(p3.base);self.committed=p3

    def tearDown(self):
        self.j.db.close();self.tmp.cleanup()

    def committed_window(self,extra_head=True,new_event='7'):
        # The reloaded SDK window: two turns older than the rebuilt baseline,
        # then the committed baseline WITHOUT the never-submitted phantom,
        # then the genuinely new user turn.
        window=[]
        if extra_head:
            window+=[sourced('old-1','重建前就被退休的旧消息'),{'role':'assistant','content':'旧回复'}]
        window+=base_messages()[1:]+[{'role':'assistant','content':'第二轮真实回复'},sourced('5','第三轮问题'),sourced(new_event,'第四轮新消息')]
        return window

    def test_reloaded_window_with_staged_phantom_is_adopted(self):
        window=self.committed_window()
        incoming=window+[event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p=self.j.prepare(incoming,('t','b',1),now=4,strict=True,guarded=True,unprojected=copy.deepcopy(incoming))
        self.assertEqual(p.retirement_deferred,'window_reload_extension')
        self.assertEqual(p.wire[:len(self.committed.wire)],self.committed.wire,'committed wire stays byte-exact, phantom included')
        self.assertTrue(self.j.commit(p))
        self.assertIn('7',[str(journal_module.source(m)['event_id']) for m in p.wire if journal_module.source(m)],'the new turn is appended once')
        self.assertNotIn('old-1',[str(journal_module.source(m)['event_id']) for m in p.wire if journal_module.source(m)],'the retired head stays outside the journal')
        # The next request continues the ordinary overlap path.
        follow=base_messages()[1:]+[{'role':'assistant','content':'第二轮真实回复'},sourced('5','第三轮问题'),sourced('7','第四轮新消息'),{'role':'assistant','content':'第四轮回复'},sourced('8','第五轮'),event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p2=self.j.prepare(follow,('t','b',1),now=5,strict=True,guarded=True)
        self.assertEqual(p2.wire[:len(p.wire)],p.wire)
        self.assertTrue(self.j.commit(p2))

    def test_refused_unanswered_turns_in_tail_are_adopted(self):
        # Refused rounds leave their real user turn in the SDK conversation
        # without a reply; the extension must append them so the model finally
        # sees the backlog instead of deadlocking (live 00:42 shape).
        window=self.committed_window()
        backlog=window+[sourced('r1','被拒绝的真实消息一'),sourced('r2','被拒绝的真实消息二')]
        self.db.executemany("INSERT OR REPLACE INTO pipeline_events VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(SCOPE,'12345678',e,time.time(),'message','admission','blocked','',0,0) for e in ('r1','r2')])
        self.db.commit()
        incoming=backlog+[event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        p=self.j.prepare(incoming,('t','b',1),now=4,strict=True,guarded=True,unprojected=copy.deepcopy(incoming))
        self.assertEqual(p.retirement_deferred,'window_reload_extension')
        self.assertTrue(self.j.commit(p))
        events=[str(journal_module.source(m)['event_id']) for m in p.wire if journal_module.source(m)]
        self.assertIn('r1',events);self.assertIn('r2',events);self.assertIn('7',events)

    def test_window_head_with_known_events_is_never_adopted(self):
        # A head that contains a known event means a reorder, not a reload.
        window=self.committed_window()
        reordered=[sourced('5','第三轮问题')]+window
        incoming=reordered+[event_packet({'sender_id':'12345678'}),packet({'state':{'facts':[]}})]
        with self.assertRaises(Exception) as caught:
            self.j.prepare(incoming,('t','b',1),now=4,strict=True,guarded=True,unprojected=copy.deepcopy(incoming))
        self.assertIn(type(caught.exception).__name__,('ContextReconstructionDenied','ContextPrefixViolation'))

if __name__=='__main__':unittest.main()
