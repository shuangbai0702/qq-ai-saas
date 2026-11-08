"""One Journal, real channels, append-only wire and one model writer."""
import asyncio,copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import shared_context as shared
from readonly_journal import ReadonlyJournal
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from context_transport import EVENT_CONTEXT_CURRENT
from summary_material import snapshots,valid_material,remember,coverage,project_history,coverage_digest
from behavior import BehaviorStore


def message(scope,event='same',who='12345678',text='hello'):
    return {'role':'user','content':SOURCE+model_json({'scope':scope,'event_id':event,'sender_id':who,'at':time.time()})+'\n'+text}


def request(m):
    return [{'role':'system','content':'fixed bot identity'},
            {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})},
            {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':{'scope':'current'}},'policy':'fixed','currentGuidance':'@ permits NO_REPLY','meaning':'platform'})},m]


class Unified(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'bot.sqlite');self.db=self.store.db
        self.cfg={'ownerUin':'12345678','groupParticipationGroups':['55555','66666']}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def prepare(self,j,m,inputs=None,previous=None):
        from context_protocol import source
        origin=source(m)
        composed,tools=shared.compose(j,request(m),origin['scope'],origin['event_id'],inputs if inputs is not None else [m],previous)
        p=j.prepare(composed,('bot',),strict=True,reconstruction_scope=shared.JOURNAL_SCOPE)
        self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p))
        return p,tools
    def test_one_journal_all_private_and_two_groups_same_message_id(self):
        j=ReadonlyJournal(self.db,shared.JOURNAL_SCOPE);prefix=[]
        for scope in ['private:12345678','group:55555','group:66666','private:87654321']:
            m=message(scope)
            p,_=self.prepare(j,m)
            self.assertEqual(p.wire[:len(prefix)],prefix)
            j.append_completion({'role':'assistant','content':'answer for '+scope})
            prefix=copy.deepcopy(j.last.wire)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journals').fetchone()[0],1)
        restored=ReadonlyJournal(self.db,shared.JOURNAL_SCOPE)
        self.assertEqual(restored.last.wire,prefix)
        self.assertEqual(len([x for x in shared.recent(self.db) if x.get('sender_id')]),4)
    def test_two_group_limit_is_not_silent_truncation(self):
        self.assertTrue(shared.included('private:99999999',self.cfg))
        self.assertTrue(shared.included('group:55555',self.cfg))
        self.assertFalse(shared.included('group:77777',self.cfg))
        with self.assertRaisesRegex(ValueError,'at_most_two'):shared.group_whitelist({**self.cfg,'groupParticipationGroups':['55555','66666','77777']})
        four={**self.cfg,'groupParticipationGroups':['55555','66666','77777','88888']}
        self.assertEqual(len(shared.group_whitelist(four,4)),4)
        with self.assertRaisesRegex(ValueError,'group_limit_exceeded'):shared.group_whitelist({**four,'groupParticipationGroups':four['groupParticipationGroups']+['99999']},4)
        shared.activate_authorized(self.db,four,{},'explicit-four-group-exception','b'*40,4)
        self.assertEqual(self.db.execute('SELECT group_limit FROM fishbot_shared_context').fetchone()[0],4)
    def test_failed_completion_persist_does_not_publish_memory_only_history(self):
        from readonly_journal import ContextRecoveryUnavailable
        j=ReadonlyJournal(self.db,shared.JOURNAL_SCOPE);self.prepare(j,message('private:12345678'))
        before=copy.deepcopy(j.last.wire)
        original=j._persist;j._persist=lambda *a,**k:False
        try:
            with self.assertRaises(ContextRecoveryUnavailable):j.append_completion({'role':'assistant','content':'not durable'})
        finally:j._persist=original
        self.assertEqual(j.last.wire,before)
        self.assertEqual(ReadonlyJournal(self.db,shared.JOURNAL_SCOPE).last.wire,before)
    def test_material_identity_and_coverage_are_channel_bound(self):
        a=message('private:12345678');b=message('group:55555')
        material=snapshots([a,{'role':'assistant','content':'one'},b,{'role':'assistant','content':'two'}],shared.JOURNAL_SCOPE)
        self.assertEqual(len(material),2);self.assertNotEqual(material[0]['event'],material[1]['event'])
        for item in material:self.assertTrue(valid_material(item,shared.JOURNAL_SCOPE))
        forged={**material[0],'event':material[1]['event']}
        self.assertFalse(valid_material(forged,shared.JOURNAL_SCOPE))
        for item in material:self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(shared.JOURNAL_SCOPE,item['subject'],item['event'],item['text'],'',time.time()))
        remember(self.db,shared.JOURNAL_SCOPE,material)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],2)
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(shared.JOURNAL_SCOPE,'compressed','[]',time.time())).lastrowid
        self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',(shared.JOURNAL_SCOPE,material[0]['event'],material[0]['subject'],coverage_digest(material[0]),identifier))
        original=[a,{'role':'assistant','content':'one'},b,{'role':'assistant','content':'two'}]
        projected=project_history(original,coverage(self.db,shared.JOURNAL_SCOPE),shared.JOURNAL_SCOPE,material[1]['event'])
        self.assertEqual(projected,original[2:])
    def test_atomic_authorized_cutover_keeps_exact_old_journals_no_license(self):
        expected={};old={}
        for scope in ['private:12345678','group:55555']:
            j=ReadonlyJournal(self.db,scope);p=j.prepare(request(message(scope)),('bot',),strict=True)
            self.assertTrue(j.commit(p));row=self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals WHERE scope=?',(model_json(scope),)).fetchone()
            old[scope]=row[0];expected[scope]=row[1]
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'inventory_changed'):shared.activate_authorized(self.db,self.cfg,{},'user-explicit-once','a'*40)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journals').fetchone()[0],2)
        result=shared.activate_authorized(self.db,self.cfg,expected,'user-explicit-once','a'*40)
        self.assertEqual(result['archived'],2);self.assertTrue(shared.active(self.db))
        for scope,encoded in self.db.execute('SELECT scope,archive FROM fishbot_operator_session_archives'):
            self.assertEqual(json.loads(encoded)['journal']['payload'],old[json.loads(scope)])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute('DELETE FROM fishbot_operator_session_archives')
        self.db.rollback()


class Writers(unittest.IsolatedAsyncioTestCase):
    async def test_35_completed_rounds_across_channels_mint_one_shared_checkpoint(self):
        from contextvars import ContextVar
        with tempfile.TemporaryDirectory() as folder:
            store=BehaviorStore(Path(folder)/'bot.sqlite');store.shared_context=True;store.reflection.shared_context=True
            try:
                db=store.db;j=ReadonlyJournal(db,shared.JOURNAL_SCOPE)
                for n in range(35):
                    channel=['private:12345678','group:55555','group:66666'][n%3]
                    m=message(channel,str(n),text='completed real event '+str(n))
                    composed,_=shared.compose(j,request(m),channel,str(n),[m])
                    prefix=copy.deepcopy(j.last.wire) if j.last else []
                    p=j.prepare(composed,('bot',),strict=True,reconstruction_scope=shared.JOURNAL_SCOPE,retirement=store.reflection.reconstruction_stamp(shared.JOURNAL_SCOPE))
                    self.assertEqual(p.wire[:len(prefix)],prefix);self.assertTrue(j.commit(p))
                    j.append_completion({'role':'assistant','content':'confirmed answer '+str(n)})
                    materials=snapshots(j.last.base,shared.JOURNAL_SCOPE)
                    identifier=shared.event_key({'scope':channel,'event_id':str(n)})
                    store.reflection.record(shared.JOURNAL_SCOPE,'12345678',identifier,m['content'],'confirmed answer '+str(n),materials=materials)
                self.assertEqual(db.execute('SELECT scope,count(*) FROM memory_rounds GROUP BY scope').fetchall(),[(shared.JOURNAL_SCOPE,35)])
                class Summary:
                    async def text_chat(self,**kwargs):return NS(completion_text=model_json({'summary':'35 completed events, authors and original sources retained.','evidence':[],'commitments':[]}))
                metrics={};provider=Summary()
                self.assertTrue(store.reflection.maybe_start(shared.JOURNAL_SCOPE,{'memory':True,'memorySummary':True,'naturalConversation':False},provider,lambda:True,metrics,ContextVar('shared-summary-event',default=None)))
                await store.reflection.task
                states=db.execute('SELECT scope,round_count,state FROM memory_reconstruction_checkpoints').fetchall()
                self.assertEqual(states,[(shared.JOURNAL_SCOPE,35,'available')],metrics)
                next_message=message('private:87654321','36',who='87654321')
                composed,_=shared.compose(j,request(next_message),'private:87654321','36',[next_message])
                projected=project_history(composed,coverage(db,shared.JOURNAL_SCOPE),shared.JOURNAL_SCOPE,shared.event_key({'scope':'private:87654321','event_id':'36'}))
                prepared=j.prepare(projected,('bot',),strict=True,reconstruction_authority=store.reflection,reconstruction_scope=shared.JOURNAL_SCOPE,retirement=store.reflection.reconstruction_stamp(shared.JOURNAL_SCOPE))
                self.assertIsNotNone(prepared.reconstruction_permit);self.assertTrue(j.stage_candidate(prepared));self.assertTrue(j.commit(prepared))
                self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
            finally:store.close()
    async def test_same_bot_serial_different_bot_parallel_and_send_unlocked(self):
        first=shared.Writer();second=shared.Writer();entered=asyncio.Event();release=asyncio.Event();order=[]
        async def a():
            await first.acquire();await first.acquire();order.append('a');entered.set();await release.wait();first.release()
        async def b():
            await first.acquire();order.append('b');first.release()
        task=asyncio.create_task(a());await entered.wait();waiting=asyncio.create_task(b())
        await second.acquire();self.assertEqual(order,['a']);self.assertTrue(second.release())
        release.set();await asyncio.gather(task,waiting);self.assertEqual(order,['a','b'])
        # Network work starts only after releasing the model writer.
        await first.acquire();first.release();self.assertFalse(first.lock.locked())
    async def test_cancel_waiter_cannot_release_real_owner(self):
        writer=shared.Writer();await writer.acquire()
        waiter=asyncio.create_task(writer.acquire());await asyncio.sleep(0);waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiter
        self.assertTrue(writer.lock.locked());self.assertTrue(writer.release());self.assertFalse(writer.release())


if __name__=='__main__':unittest.main()
