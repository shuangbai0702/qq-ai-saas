"""Actual receipts correct immutable drafts through the existing readonly state."""
import copy,json,sys,tempfile,time,unittest
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import shared_context as shared
import message_provenance
import bot_pipeline
from behavior import BehaviorStore
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from context_transport import EVENT_CONTEXT_CURRENT
from readonly_journal import ReadonlyJournal
from summary_material import snapshots,coverage,project_history


def inbound(scope,event):
    return {'role':'user','content':SOURCE+model_json({'scope':scope,'event_id':str(event),'sender_id':'12345678','at':time.time()})+'\nreal request '+str(event)}


def packet(message,store,journal):
    return [{'role':'system','content':'fixed persona'},
            {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{},'dialogue':shared.delivery_context(store.db,journal)})},
            {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':{'scope':'current'}},'policy':'fixed','currentGuidance':'@ permits NO_REPLY','meaning':'platform'})},message]


class Delivery(unittest.IsolatedAsyncioTestCase):
    async def test_first_work_before_journal_persistence_has_no_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh=BehaviorStore(Path(tmp)/'new.sqlite')
            try:
                self.assertIsNone(fresh.db.execute("SELECT 1 FROM sqlite_master WHERE name='fishbot_readonly_journals'").fetchone())
                self.assertEqual(shared.delivery_context(fresh.db,None)['deliveries'],[])
            finally:fresh.close()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'bot.sqlite')
        self.store.shared_context=True;self.store.reflection.shared_context=True
        self.db=self.store.db;self.journal=ReadonlyJournal(self.db,shared.JOURNAL_SCOPE)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def append(self,scope,event,draft='model draft'):
        m=inbound(scope,event)
        assembled,_=shared.compose(self.journal,packet(m,self.store,self.journal),scope,str(event),[m])
        p=self.journal.prepare(assembled,('bot',),strict=True,reconstruction_scope=shared.JOURNAL_SCOPE,
                               retirement=self.store.reflection.reconstruction_stamp(shared.JOURNAL_SCOPE))
        self.assertTrue(self.journal.stage_candidate(p));self.assertTrue(self.journal.commit(p))
        self.journal.append_completion({'role':'assistant','content':draft})
        return m
    def settle(self,scope,event,status='sent',text='actual replacement',proof=True,complete=True,attempted=1):
        self.db.execute('''INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed)
                           VALUES(?,?,?,?,'message','commit',?,'',?,?)
                           ON CONFLICT(scope,subject,event) DO UPDATE SET status=excluded.status,attempted=excluded.attempted,confirmed=excluded.confirmed''',
                        (scope,'12345678',str(event),time.time(),status,attempted,1 if proof else 0))
        if text:
            self.db.execute("INSERT INTO dialogue_feedback VALUES(?,?,?,?,?,'observing') ON CONFLICT(scope,event) DO UPDATE SET reply=excluded.reply",
                            (scope,str(event),'12345678',time.time(),text[:2000]))
        if proof:
            message_provenance.put(self.db,scope,event,'outgoing',{'receipt':'confirmed','message_ids':['qq-'+str(event)],'complete':complete})
        self.db.commit()
    async def test_replaced_draft_stays_exact_and_next_other_scope_sees_real_delivery(self):
        self.append('group:55555','one','obsolete model draft')
        before=copy.deepcopy(self.journal.last.wire)
        self.settle('group:55555','one',text='actual revised answer')
        self.assertEqual(shared.delivery_view(self.db,self.journal)[0]['confirmed_text'],'actual revised answer')
        self.append('private:12345678','two')
        self.assertEqual(self.journal.last.wire[:len(before)],before)
        self.assertEqual(self.journal.last.state['dialogue']['deliveries'][0]['delivery'],'confirmed')
        self.assertEqual(ReadonlyJournal(self.db,shared.JOURNAL_SCOPE).last.wire,self.journal.last.wire)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
    async def test_unknown_partial_drop_and_missing_evidence_never_become_success(self):
        for n,(status,proof,complete,attempts,expected) in enumerate([
                ('unknown',False,False,1,'unknown'),('partial',True,False,2,'partial'),
                ('superseded',False,False,0,'not_sent'),('sent',False,True,1,'unknown')]):
            self.append('group:55555',str(n));self.settle('group:55555',str(n),status,proof=proof,complete=complete,attempted=attempts)
            row=shared.delivery_view(self.db,self.journal)[-1]
            self.assertEqual(row['delivery'],expected)
            if not proof:self.assertNotIn('confirmed_text',row)
    async def test_late_receipt_refresh_only_changes_new_packet_not_frozen_or_committed_history(self):
        self.append('group:55555','old')
        self.settle('group:55555','old','unknown',proof=False)
        next_input=inbound('private:12345678','new')
        frozen=packet(next_input,self.store,self.journal);old_input=copy.deepcopy(frozen)
        prefix=copy.deepcopy(self.journal.last.wire)
        self.settle('group:55555','old',text='late confirmed real send')
        refreshed=shared.refresh_deliveries(frozen,self.db,self.journal)
        self.assertEqual(frozen,old_input);self.assertEqual(self.journal.last.wire,prefix)
        data=json.loads(refreshed[1]['content'].split('\n',1)[1])
        self.assertEqual(data['dialogue']['deliveries'][0]['delivery'],'confirmed')
        self.assertEqual(data['dialogue']['deliveries'][0]['confirmed_text'],'late confirmed real send')
        self.assertEqual(refreshed[-1],next_input)
    async def test_unshared_scope_and_event_collisions_do_not_enter_delivery_view(self):
        self.append('private:12345678','same')
        self.settle('group:99999','same',text='not in this Journal')
        self.assertEqual(shared.delivery_view(self.db,self.journal),[])
        self.settle('private:12345678','same',text='this channel only')
        self.assertEqual([x['confirmed_text'] for x in shared.delivery_view(self.db,self.journal)],['this channel only'])
        with tempfile.TemporaryDirectory() as folder:
            other=BehaviorStore(Path(folder)/'other.sqlite')
            try:self.assertEqual(shared.delivery_view(other.db,ReadonlyJournal(other.db,shared.JOURNAL_SCOPE)),[])
            finally:other.close()
    async def test_bounded_and_truncation_is_explicit(self):
        for n in range(12):self.append('group:55555',str(n));self.settle('group:55555',str(n),text='ack '+str(n))
        view=shared.delivery_view(self.db,self.journal)
        self.assertEqual(len(view),8);self.assertLessEqual(sum(len(model_json(x)) for x in view),3000)
        self.settle('group:55555','11',text='x'*3000)
        last=shared.delivery_view(self.db,self.journal)[-1]
        self.assertEqual(len(last['confirmed_text']),1200);self.assertFalse(last['text_complete'])
    async def test_auxiliary_memory_uses_confirmed_cross_scope_results_without_repeating_current_scope(self):
        cfg={'memory':True,'memorySummary':False,'ownerUin':'12345678','groupParticipationGroups':['55555']}
        shared.activate_authorized(self.db,cfg,{},'test fixture','f'*40)
        self.append('group:55555','group','unsent original group draft')
        self.settle('group:55555','group',text='confirmed group revision')
        self.append('private:12345678','private')
        self.settle('private:12345678','private',text='confirmed current private reply')
        result=bot_pipeline.decision_memory(self.db,cfg,'private:12345678','12345678',time.time())
        self.assertEqual([r['confirmed_text'] for r in result['cross_scope_deliveries']],['confirmed group revision'])
        self.assertTrue(any(r.get('delivery')=='model_output_not_send_receipt' for r in result['recent_cross_session_messages']))
        self.assertEqual(bot_pipeline.decision_memory(self.db,{'memory':False},'private:12345678','12345678',time.time()),{})
    async def test_35_checkpoint_retires_old_drafts_without_uncovered_receipt_turns(self):
        for n in range(35):
            scope=['private:12345678','group:55555'][n%2]
            m=self.append(scope,str(n),'original draft '+str(n))
            actual='freshness replacement '+str(n);self.settle(scope,str(n),text=actual)
            materials=snapshots(self.journal.last.base,shared.JOURNAL_SCOPE)
            identifier=shared.event_key({'scope':scope,'event_id':str(n)})
            self.store.reflection.record(shared.JOURNAL_SCOPE,'12345678',identifier,m['content'],actual,materials=materials)
        class Summary:
            async def text_chat(self,**kwargs):return NS(completion_text=model_json({'summary':'35 completed real requests and confirmed answers.','evidence':[],'commitments':[]}))
        metrics={}
        self.assertTrue(self.store.reflection.maybe_start(shared.JOURNAL_SCOPE,{'memory':True,'memorySummary':True,'naturalConversation':False},Summary(),lambda:True,metrics,ContextVar('receipt-summary',default=None)))
        await self.store.reflection.task
        self.assertEqual(self.db.execute('SELECT round_count,state FROM memory_reconstruction_checkpoints').fetchall(),[(35,'available')],metrics)
        m=inbound('private:87654321','36')
        composed,_=shared.compose(self.journal,packet(m,self.store,self.journal),'private:87654321','36',[m])
        projected=project_history(composed,coverage(self.db,shared.JOURNAL_SCOPE),shared.JOURNAL_SCOPE,shared.event_key({'scope':'private:87654321','event_id':'36'}))
        self.assertFalse(any(x.get('role')=='assistant' and str(x.get('content','')).startswith('original draft') for x in projected))
        p=self.journal.prepare(projected,('bot',),strict=True,reconstruction_scope=shared.JOURNAL_SCOPE,
                               reconstruction_authority=self.store.reflection,retirement=self.store.reflection.reconstruction_stamp(shared.JOURNAL_SCOPE))
        self.assertIsNotNone(p.reconstruction_permit);self.assertTrue(self.journal.stage_candidate(p));self.assertTrue(self.journal.commit(p))
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
        self.assertEqual(len([x for x in self.journal.last.base if x.get('role')=='user']),1)


if __name__=='__main__':unittest.main()
