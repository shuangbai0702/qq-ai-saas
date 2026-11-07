"""Targeted regression evidence. SQLite and providers are isolated; no QQ/API."""
import asyncio,contextvars,json,os,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(os.environ.get('FISHBOT_REVIEW_BASE',Path(__file__).resolve().parents[1]))
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import summary_material as material
import group_actions
from behavior import BehaviorStore
from memory_reflection import invalidate

SCOPE='private:12345678';PERSON='12345678'
CFG={'memorySummary':True,'summaryRounds':35,'naturalConversation':False}


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.var=contextvars.ContextVar('review',default=None);self.calls=[];self.now=time.time()-10000
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def rounds(self,start=0,count=35):
        for i in range(start,start+count):
            text='周六八点东门见' if i<12 else '普通问题'+str(i)
            observed={'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':PERSON,'event_id':str(i)})+'\n'+text}]}
            self.store.reflection.record(SCOPE,PERSON,str(i),text,'好',self.now+i,materials=material.snapshots([observed],SCOPE))
    async def chat(self,**kwargs):
        self.calls.append(kwargs)
        return NS(completion_text=json.dumps({'summary':'持续背景','commitments':[
            {'event':str(i),'topic':'周六东门','quote':'周六八点东门见','who':'user','stage':'accepted'} for i in range(12)]},ensure_ascii=False))
    async def drain(self):
        for _ in range(8):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break
    async def start(self):
        started=self.store.reflection.maybe_start(SCOPE,CFG,NS(text_chat=self.chat),lambda:True,{},self.var)
        await self.drain();return started

    async def test_retry_does_not_count_old_rounds_as_new_checkpoint_rounds(self):
        self.rounds()
        with patch.object(material,'MAX_CLAIMS_BYTES',2):await self.start()
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],12)
        self.rounds(35,27)
        self.assertFalse(self.store.reflection.retry(),'8 held + 27 new is not 35 new rounds')
        await self.drain()
        self.assertEqual(len(self.calls),1)
        # A held batch keeps at most its pending-coverage identity; it never
        # becomes a usable license and old rounds never earn a new one.
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_reconstruction_checkpoints WHERE state IN ('available','reserved','consumed')").fetchone()[0],0)

    async def test_cleanup_preserves_holds_outside_retry_window(self):
        self.rounds()
        with patch.object(material,'MAX_CLAIMS_BYTES',2):await self.start()
        # Saturate the bounded pending selection with older evidence. Held
        # rounds 8..11 are in neither this retry window nor its pending input.
        for i in range(601):
            self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,PERSON,'older-'+str(i),'旧材料','好',self.now-2000+i))
        self.db.commit()
        self.rounds(35)
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.assertTrue(self.store.reflection.retry());await self.drain()
        events={r[0] for r in self.db.execute('SELECT event FROM memory_rounds')}
        self.assertTrue({str(i) for i in range(12)}<=events,events)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_hold_rounds WHERE reason='lost'").fetchone()[0],0)

    async def test_forget_cannot_clear_another_scope_lost_hold(self):
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,?,?,?)',('group:99999','lost-other',1,'lost',time.time()))
        self.db.commit();invalidate(self.db,SCOPE,PERSON)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM memory_hold_rounds WHERE event='lost-other'").fetchone())

    async def test_held_emotion_is_not_reapplied_as_a_new_experience(self):
        self.rounds();cfg={**CFG,'naturalConversation':True};metrics={}
        async def chat(**kwargs):
            return NS(completion_text=json.dumps({'summary':'持续背景','emotion':{'event':'0','kind':'happy','intensity':2,'quote':'周六八点东门见'},'commitments':[{'event':'0','topic':'东门见面','quote':'周六八点东门见','who':'user','stage':'accepted'}]},ensure_ascii=False))
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.store.reflection.maybe_start(SCOPE,cfg,NS(text_chat=chat),lambda:True,metrics,self.var);await self.drain()
        before=self.db.execute('SELECT kind,intensity,updated,expires FROM companion_emotion WHERE scope=?',(SCOPE,)).fetchone();self.assertIsNotNone(before)
        self.rounds(35);self.assertTrue(self.store.reflection.retry());await self.drain()
        self.assertEqual(before,self.db.execute('SELECT kind,intensity,updated,expires FROM companion_emotion WHERE scope=?',(SCOPE,)).fetchone())

    async def test_reserved_checkpoint_survives_merge_without_reissuing_permission(self):
        for i in range(2):
            self.rounds(i*35)
            if i==0:await self.start()
            else:self.store.reflection.retry();await self.drain()
        checkpoints=self.db.execute('SELECT id,summary_id FROM memory_reconstruction_checkpoints ORDER BY id').fetchall()
        reserved=self.store.reflection.reserve_context_reconstruction(SCOPE,'review-reserved',[checkpoints[0][1]])
        self.assertIsNotNone(reserved)
        self.rounds(70);self.assertTrue(self.store.reflection.retry());await self.drain()
        row=self.db.execute('SELECT state,request_id,summary_id FROM memory_reconstruction_checkpoints WHERE id=?',(reserved['id'],)).fetchone()
        self.assertEqual(row[:2],('reserved','review-reserved'));self.assertNotEqual(row[2],checkpoints[0][1])
        self.assertTrue(self.store.reflection.settle_context_reconstruction(reserved,True))
        self.assertFalse(self.store.reflection.settle_context_reconstruction(reserved,True),'consumed permission cannot settle twice')

    async def test_merge_rejects_source_edit_during_model_call(self):
        ids=[]
        for i in range(3):
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(SCOPE,'旧背景'+str(i),'[]',time.time())).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,));self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,SCOPE));ids.append(identifier)
        self.db.commit();metrics={}
        async def edit(**kwargs):
            self.db.execute('UPDATE memory_summaries SET text=? WHERE id=?',('已纠正的新背景',ids[0]));self.db.commit()
            return NS(completion_text='旧背景合并结果')
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,CFG,NS(text_chat=edit),lambda:True,metrics,self.var));await self.drain()
        self.assertEqual(metrics.get('summaryFailures'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT text FROM memory_summaries WHERE id=?',(ids[0],)).fetchone()[0],'已纠正的新背景')

    async def test_tool_owns_only_actual_destination_and_uncertain_receipts(self):
        group_actions.initialize(self.db)
        event=NS(get_group_id=lambda:'34567890',get_sender_id=lambda:PERSON,message_obj=NS(message_id='real-event'))
        key='34567890:'+PERSON+':real-event'
        for state in ('attempted','confirmed','unknown','not_sent'):
            self.db.execute('INSERT OR REPLACE INTO group_message_attempts VALUES(?,?,?,?,?)',(key,'group:34567890',time.time(),state,''))
            self.assertEqual(group_actions.owns_reply(self.db,event,'group:34567890'),state!='not_sent')
            self.assertFalse(group_actions.owns_reply(self.db,event,SCOPE),'private acknowledgement still allowed')
            self.assertFalse(group_actions.owns_reply(self.db,event,'group:45678901'))

    async def test_stages_and_oversized_evidence_are_not_silently_rewritten(self):
        payload=[{'event':'e','sender_id':PERSON,'user':'周六八点东门见','bot':'好','at':1}]
        for stage,status in (('proposed','proposed'),('unknown','unknown'),('accepted','open')):
            claims=material.derive_claims(SCOPE,payload,{},[],{'commitments':[{'event':'e','topic':'东门见面','quote':'周六八点东门见','who':'user','stage':stage,'due':'周六八点'}]})
            hit=next(c for c in claims if c.get('protected'))
            self.assertEqual((hit['stage'],hit['status'],hit['due']),(stage,status,'周六八点'))
        quote='重要条件'*101
        payload[0]['user']=quote
        claims=material.derive_claims(SCOPE,payload,{},[],{'commitments':[{'event':'e','topic':'条件','quote':quote,'who':'user','stage':'accepted'}]})
        kept,_=material.fit_claims(claims)
        self.assertIn('e',material.uncarried_events(claims,{c['id'] for c in kept}),'truncated important evidence cannot earn coverage')

    async def test_speaker_roles_and_condition_tails_have_distinct_claim_ids(self):
        quote='周六八点东门见'
        payload=[{'event':'same','sender_id':PERSON,'user':quote,'bot':quote,'at':1}]
        claims=material.derive_claims(SCOPE,payload,{},[],{'commitments':[{'event':'same','topic':'东门见面','quote':quote,'who':who,'stage':'proposed'} for who in ('user','bot')]})
        protected=[c for c in claims if c.get('protected')]
        self.assertEqual(len(protected),2);self.assertEqual(len({c['id'] for c in protected}),2)
        self.assertNotEqual(material.claim_identity(SCOPE,'same','agreement','条件'*40+'仅限本人'),material.claim_identity(SCOPE,'same','agreement','条件'*40+'允许代办'))

    async def test_important_quote_beyond_display_prefix_uses_verified_material(self):
        text='背景'*450+'周六八点东门见'
        observed={'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':PERSON,'event_id':'long'})+'\n'+text}]}
        source=material.snapshots([observed],SCOPE)[0]
        row={'event':'long','sender_id':PERSON,'user':text[:800],'bot':'好','at':1}
        data={'commitments':[{'event':'long','topic':'东门见面','quote':'周六八点东门见','who':'user','stage':'accepted'}]}
        claims=material.derive_claims(SCOPE,[row],{'long':source},[],data)
        self.assertEqual(len([c for c in claims if c.get('protected')]),1)
        forged={**source,'subject':'87654321'}
        self.assertFalse([c for c in material.derive_claims(SCOPE,[row],{'long':forged},[],data) if c.get('protected')])

    async def test_foreign_and_older_evidence_cannot_retire_an_agreement(self):
        original={'event':'old','sender_id':PERSON,'user':'周六八点东门见','bot':'好','at':20}
        old=next(c for c in material.derive_claims(SCOPE,[original],{},[],{'commitments':[{'event':'old','topic':'东门见面','quote':original['user'],'who':'user','stage':'accepted'}]}) if c.get('protected'))
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'旧约定','[]',1,json.dumps([old]))).lastrowid
        for person,at,scope in (('87654321',30,SCOPE),(PERSON,10,SCOPE),(PERSON,30,'group:99999')):
            row={'event':'new','sender_id':person,'user':'这次东门见面取消','bot':'好','at':at}
            terminal=material.derive_claims(scope,[row],{},[],{'commitments':[{'event':'new','topic':'东门见面','quote':row['user'],'who':'user','stage':'canceled','supersedes':[old['id']]}]})
            material.settle_commitments(self.db,scope,terminal,[old])
            self.assertTrue(material.protected_claim(material.summary_claims(self.db,identifier)[0]))

    async def test_cancel_from_later_batch_retires_prior_protection(self):
        self.rounds()
        async def first(**kwargs):
            return NS(completion_text=json.dumps({'summary':'周六东门见','commitments':[{'event':'0','topic':'东门见面','quote':'周六八点东门见','who':'user','stage':'accepted'}]},ensure_ascii=False))
        self.store.reflection.maybe_start(SCOPE,CFG,NS(text_chat=first),lambda:True,{},self.var);await self.drain()
        old=next(c for c in material.summary_claims(self.db,1) if c.get('protected'))
        text='周六东门不去了，这次安排取消'
        observed={'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':PERSON,'event_id':'35'})+'\n'+text}]}
        self.store.reflection.record(SCOPE,PERSON,'35',text,'知道了',self.now+35,materials=material.snapshots([observed],SCOPE));self.rounds(36,34)
        async def cancel(**kwargs):
            prompt=json.loads(kwargs['prompt']);self.assertIn(old['id'],{c['id'] for c in prompt['previous_commitments']})
            return NS(completion_text=json.dumps({'summary':'原定周六东门安排已取消','commitments':[{'event':'35','topic':'东门见面','quote':text,'who':'user','stage':'canceled','supersedes':[old['id']]}]},ensure_ascii=False))
        self.store.reflection.binding=(CFG,NS(text_chat=cancel),lambda:True,{},self.var)
        self.assertTrue(self.store.reflection.retry());await self.drain()
        stored=next(c for c in material.summary_claims(self.db,1) if c['id']==old['id'])
        self.assertEqual(stored['status'],'superseded');self.assertFalse(material.protected_claim(stored))

    async def test_rolling_claims_share_one_actual_recall_budget(self):
        from semantic_context import recall
        for i in range(3):
            payload=[{'event':str(i),'sender_id':PERSON,'user':'东门'+str(i)+'条件'*90,'bot':'好','at':i}]
            claims=material.derive_claims(SCOPE,payload,{},[],{'commitments':[{'event':str(i),'topic':'东门约定','quote':payload[0]['user'],'who':'user','stage':'accepted'}]})
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'简短背景','[]',i,json.dumps(claims))).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
        self.db.commit()
        _,summaries=recall(self.store,SCOPE,PERSON,'东门',{'memorySummary':True},lambda:True,{})
        attached=[c for item in summaries for c in item.get('protected_claims',[])]
        self.assertTrue(attached)
        self.assertLessEqual(len(json.dumps(attached,ensure_ascii=False,separators=(',',':'))),1200)
        self.assertEqual(len(attached),len({c['id'] for c in attached}))

    async def test_final_memory_view_keeps_same_scope_query_hit_outside_rolling(self):
        from bot_pipeline import conversation_context,finish_memory_view
        ids=[]
        for i in range(5):
            row={'event':str(i),'sender_id':PERSON,'user':'罕见的东门安排' if i==0 else '重要事项'+str(i),'bot':'好','at':i}
            claims=material.derive_claims(SCOPE,[row],{},[],{'commitments':[{'event':str(i),'topic':'重要事项','quote':row['user'],'who':'user','stage':'accepted'}]})
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'当前安排'+str(i),'[]',i,json.dumps(claims))).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,));ids.append(identifier)
        self.db.commit()
        from memory_scope import summary_item
        old=summary_item(self.db.execute('SELECT id,text,sources,updated,scope FROM memory_summaries WHERE id=?',(ids[0],)).fetchone(),'scoped_bm25',db=self.db)
        shared=conversation_context(self.store,{'memorySummary':True},SCOPE,PERSON,'东门',time.time())
        shared['state']['conversation_summaries']=[old]
        finish_memory_view(self.store,shared,{'memorySummary':True},SCOPE,'东门')
        attached=shared['state']['conversation_summaries']
        self.assertIn(ids[0],{item['id'] for item in attached})
        self.assertEqual(attached[0]['id'],ids[0])
        self.assertTrue(any('东门' in claim['text'] for item in attached for claim in item.get('protected_claims',[])))

    async def test_accepted_handoff_survives_epoch_edit_and_long_delay(self):
        from bot_pipeline import BotPipeline,Work
        identity={'tenantId':'t','botId':'b','epoch':1,'generation':2,'revision':3}
        work=Work('b',SCOPE,PERSON,'90000000','input','message',time.time(),identity)
        work.ledger_owner=True
        # A seven-day delay must not already be expired when it becomes due.
        request={'objective':'保留完整任务来源','delaySeconds':604800}
        response=self.store.pipeline.propose_handoff(work,identity,request)
        identifier=response['requestId']
        self.store.pipeline.acknowledge_handoff(1,identifier,{'status':'accepted','goalId':identifier})
        row=json.loads(self.db.execute('SELECT data FROM pipeline_handoffs WHERE id=?',(identifier,)).fetchone()[0]);due=row['at']+row['delaySeconds']*1000
        source={'handoffId':identifier,'eventId':'input','scope':SCOPE,'senderId':PERSON}
        with patch('bot_pipeline.time.time',return_value=(due+3600000)/1000):
            self.assertIsNotNone(self.store.pipeline.request_reply({**identity,'epoch':3},identifier,source))
            self.assertIsNone(self.store.pipeline.request_reply({**identity,'epoch':3,'botId':'foreign'},identifier,source))
            self.assertIsNone(self.store.pipeline.request_reply({**identity,'epoch':3,'generation':4},identifier,source))
            # A subsequent foreground task must not prune that accepted source.
            another=Work('b',SCOPE,PERSON,'90000000','another','message',(due+3600000)/1000,identity);another.ledger_owner=True
            self.store.pipeline.propose_handoff(another,identity,request)
            self.assertIsNotNone(self.store.pipeline.request_reply({**identity,'epoch':3},identifier,source))


if __name__=='__main__':unittest.main()
