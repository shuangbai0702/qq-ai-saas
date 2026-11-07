"""P0-1/P0-2: hold evidence survives batches and quotas, permits are guarded
honestly, big merges stay inside one audience, checkpoints are carried onto
the merge product, conflicts never waste a model call and an abandoned merge
cannot leave a half-written core note.

Offline fixtures only: real SQLite, fake provider, no live API or QQ sends.
"""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import summary_material as material
from behavior import BehaviorStore
from memory_reflection import GROUP_ROUND_MESSAGES
from memory_scope import summary_rows,value_hash

SCOPE='private:12345678';PERSON='12345678'
CLEAN_CFG={'memorySummary':True,'summaryRounds':35,'naturalConversation':False}


def message(event,text):
    return {'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':PERSON,'event_id':event})+'\n'+text}]}


def correction_claim(scope,event,text='我不叫江哥',subject='123'):
    """A valid stored protected claim, as the reflection itself would write."""
    return {'id':material.claim_identity(scope,event,'correction',text),'text':text[:material.PROTECTED_CLAIM_TEXT],
            'kind':'correction','speaker':{'role':'human','id':subject},'about':'称呼',
            'sourceRefs':[material.claim_ref(scope,event,'user','a'*64)],'occurredAt':1.0,
            'status':'applied','visibility':scope,'protected':True,'value':value_hash('江哥')}


class P0HoldMergeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.var=contextvars.ContextVar('p0',default=None);self.now=time.time()-100000;self.calls=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def rounds(self,start=0,count=35,special=None):
        for i in range(start,start+count):
            user,bot=(special or {}).get(i,('普通话题'+str(i),'普通回复'+str(i)))
            self.store.reflection.record(SCOPE,PERSON,str(i),user,bot,self.now+i,materials=material.snapshots([message(str(i),user)],SCOPE))

    def chat(self,summary='普通背景'):
        async def fixture(**kw):
            self.calls.append(kw)
            # P1-1: protection is model-judged in the same summary request.
            # The open commitment on event 0 is what holds that round once the
            # claim budget cannot carry it; derive_claims drops commitments
            # whose quote is not verbatim in the declared speaker's own turn,
            # so batches without that turn simply ignore this entry.
            return NS(completion_text=json.dumps({'summary':summary,'commitments':[
                {'event':'0','topic':'周末平江路','quote':'我们约定周末一起去平江路','who':'user','stage':'accepted'}]},ensure_ascii=False))
        return fixture

    async def drain(self):
        for _ in range(12):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break

    def small(self,scope,text,claims=None):
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',
            (scope,text,'["e"]',time.time(),json.dumps(claims) if claims else None)).lastrowid
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
        self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,scope));return identifier

    async def test_hold_round_rejoins_next_batch_and_blocks_permits_until_carried(self):
        """Round 0 is held in batch one; batch two must re-include it, release
        it once carried, and no permit may be minted while it is unresolved."""
        self.rounds(special={0:('我们约定周末一起去平江路','好的')})
        metrics={}
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.assertTrue(self.store.reflection.maybe_start(SCOPE,CLEAN_CFG,NS(text_chat=self.chat()),lambda:True,metrics,self.var));await self.drain()
        hold=self.db.execute('SELECT round_id,reason FROM memory_hold_rounds WHERE scope=? AND event=?',(SCOPE,'0')).fetchone()
        self.assertIsNotNone(hold,'an unresolved hold is registered')
        self.assertEqual(hold[1],'protected')
        self.assertEqual(hold[0],self.db.execute('SELECT id FROM memory_rounds WHERE event=?',('0',)).fetchone()[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material WHERE event=?',('0',)).fetchone()[0],1,'raw material is kept')
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',(SCOPE,'0')).fetchone(),'pending evidence is kept')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0,'no permit while the hold is open')
        self.assertEqual(metrics.get('summaryReleaseHolds'),1)
        watermark=self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(SCOPE,)).fetchone()[0]
        self.rounds(start=35)
        top=self.db.execute('SELECT max(id) FROM memory_rounds').fetchone()[0]
        self.store.reflection.retry();await self.drain()
        prompt=json.loads(self.calls[-1]['prompt'])
        self.assertIn('0',{r['event'] for r in prompt['turns']},'the held round re-enters the batch input')
        self.assertIn('0',{r['event'] for r in prompt['observed_requests']})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds WHERE scope=?',(SCOPE,)).fetchone()[0],0,'carried evidence drops the registration')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds WHERE event=?',('0',)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material WHERE event=?',('0',)).fetchone()[0],0)
        self.assertIn('0',material.coverage(self.db,SCOPE))
        advanced=self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(SCOPE,)).fetchone()[0]
        self.assertEqual(advanced,top,'35 new rounds are consumed in addition to the held evidence')
        self.assertGreater(advanced,watermark)
        self.assertEqual({r[0] for r in self.db.execute('SELECT event FROM memory_rounds')},set())
        permit=self.db.execute('SELECT state,summary_id FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()
        self.assertIsNotNone(permit);self.assertEqual(permit[0],'available')
        self.assertIsNotNone(self.store.reflection.reserve_context_reconstruction(SCOPE,'p0-after-hold',[permit[1]]))

    async def test_lost_hold_blocks_mint_honestly_until_registration_removed(self):
        """Evidence whose rows are all gone can never be forged as covered: it
        is marked lost, counted, excluded from batches and blocks permits."""
        self.rounds()
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,?,?,?)',(SCOPE,'ghost',1,'protected',time.time()));self.db.commit()
        metrics={}
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,CLEAN_CFG,NS(text_chat=self.chat()),lambda:True,metrics,self.var));await self.drain()
        self.assertEqual(len(self.calls),1,'the batch itself still completes')
        self.assertEqual(self.db.execute('SELECT reason FROM memory_hold_rounds WHERE scope=? AND event=?',(SCOPE,'ghost')).fetchone()[0],'lost')
        self.assertEqual(metrics.get('summaryHoldUnrecoverable'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
        self.store.reflection.retry();await self.drain()
        self.assertEqual(len(self.calls),1,'a lost round never re-enters a batch or retries the model')
        self.db.execute('DELETE FROM memory_hold_rounds WHERE scope=? AND event=?',(SCOPE,'ghost'));self.db.commit()
        for i in range(35):self.store.reflection.record(SCOPE,PERSON,'more-%d'%i,'问题%d'%i,'回复%d'%i,self.now+200+i)
        self.store.reflection.retry();await self.drain()
        row=self.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE scope=?',(SCOPE,)).fetchone()
        self.assertIsNotNone(row);self.assertEqual(row[0],'available','minting resumes once no hold blocks the range')

    async def test_checkpoint_permits_survive_single_scope_merge(self):
        """3x35 rounds: checkpoints follow their summary onto the merge product
        with state preserved, none dangles, and a migrated permit is usable."""
        for batch in range(2):
            self.rounds(start=batch*35)
            if batch==0:self.assertTrue(self.store.reflection.maybe_start(SCOPE,CLEAN_CFG,NS(text_chat=self.chat()),lambda:True,{},self.var))
            else:self.store.reflection.retry()
            await self.drain()
        checkpoints=self.db.execute('SELECT id,state,summary_id FROM memory_reconstruction_checkpoints WHERE scope=? ORDER BY id',(SCOPE,)).fetchall()
        self.assertEqual([r[1] for r in checkpoints],['available']*2)
        permit=self.store.reflection.reserve_context_reconstruction(SCOPE,'p0-consume',[checkpoints[0][2]])
        self.assertIsNotNone(permit);self.assertTrue(self.store.reflection.settle_context_reconstruction(permit,True))
        self.rounds(start=70);self.store.reflection.retry();await self.drain()
        big=self.db.execute("SELECT s.id,s.scope FROM memory_summaries s JOIN memory_summary_levels l ON l.id=s.id WHERE l.kind='big'").fetchone()
        self.assertIsNotNone(big);self.assertEqual(big[1],SCOPE,'a same-scope merge stays in its own scope')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        rows=self.db.execute('SELECT state,summary_id FROM memory_reconstruction_checkpoints WHERE scope=? ORDER BY id',(SCOPE,)).fetchall()
        self.assertEqual([r[0] for r in rows],['consumed','available','available'],'states are preserved, consumed never revives')
        self.assertTrue(all(r[1]==big[0] for r in rows))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints c LEFT JOIN memory_summaries s ON s.id=c.summary_id WHERE s.id IS NULL').fetchone()[0],0)
        recovered=self.store.reflection.reserve_context_reconstruction(SCOPE,'p0-recover',[big[0]])
        self.assertIsNotNone(recovered)
        self.assertTrue(self.store.reflection.settle_context_reconstruction(recovered,True))
        second=self.store.reflection.reserve_context_reconstruction(SCOPE,'p0-recover-2',[big[0]])
        self.assertIsNotNone(second);self.assertNotEqual(second['id'],recovered['id'],'a consumed permit is never reserved again')
        self.assertTrue(self.store.reflection.settle_context_reconstruction(second,True))
        self.assertIsNone(self.store.reflection.reserve_context_reconstruction(SCOPE,'p0-replay',[big[0]]),'every migrated permit is single use')

    async def test_audience_groups_never_merge_across_scopes(self):
        """Smalls of two groups never merge; each group keeps its own rolling
        and recall; a mixed-origin big stays out of group rolling; a legal
        single-scope merge is retrievable from its own scope."""
        group_a='group:111';group_b='group:222'
        a1=self.small(group_a,'群A约定周末爬山');a2=self.small(group_a,'群A的未解决话题');b1=self.small(group_b,'群B的背景')
        self.db.commit()
        self.store.reflection.maybe_start(SCOPE,{'memorySummary':True},NS(text_chat=self.chat()),lambda:True,{},self.var);await self.drain()
        self.assertEqual(self.calls,[],'two-scopes-of-smalls never merge across audiences')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],3)
        self.assertEqual({r['id'] for r in self.store.reflection.rolling(group_a)},{a1,a2},'group A keeps its own summaries in rolling')
        self.assertIsNotNone(self.store.reflection.recall(group_a,'爬山'))
        mixed=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version) VALUES(?,?,?,?,2)',('self','旧的全局合并','["e"]',time.time())).lastrowid
        for origin in (group_a,group_b):self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(mixed,origin))
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'big')",(mixed,));self.db.commit()
        self.assertNotIn(mixed,{r['id'] for r in self.store.reflection.rolling(group_a)},'a mixed-origin big never enters the group rolling')
        self.small(group_a,'群A第三条摘要');self.db.commit()
        self.store.reflection.retry();await self.drain()
        product=self.db.execute("SELECT s.id,s.scope,s.sources FROM memory_summaries s JOIN memory_summary_levels l ON l.id=s.id WHERE l.kind='big' AND s.scope=?",(group_a,)).fetchone()
        self.assertIsNotNone(product,'a single-scope group still merges')
        self.assertEqual(json.loads(product[2]),['e'])
        self.assertEqual({r[0] for r in self.db.execute('SELECT scope FROM memory_summary_origins WHERE summary_id=?',(product[0],))},{group_a})
        self.assertIn(product[0],{r['id'] for r in self.store.reflection.rolling(group_a)})
        self.assertIn(product[0],{r[0] for r in summary_rows(self.db,group_a,'')})
        self.assertIsNotNone(self.store.reflection.recall(group_a,'爬山'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries WHERE scope=?',(group_a,)).fetchone()[0],1)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_summaries WHERE id=?',(b1,)).fetchone(),'group B was never touched')

    async def test_capacity_conflict_refuses_before_any_model_call(self):
        claims=[correction_claim(SCOPE,'e'+str(i)) for i in range(3)]
        for i,claim in enumerate(claims):self.small(SCOPE,'来源'+str(i),[claim])
        self.db.commit();metrics={}
        budget=2+len(json.dumps(claims[0],ensure_ascii=False,separators=(',',':')).encode())+1
        with patch.object(material,'MAX_CLAIMS_BYTES',budget):
            self.assertFalse(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True},NS(text_chat=self.chat()),lambda:True,metrics,self.var),'a refused conflict starts no task')
        await self.drain()
        self.assertEqual(self.calls,[],'a group whose protected claims cannot all be carried never runs')
        self.assertEqual(metrics.get('summaryProtectedConflicts'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],3)
        backoff=self.db.execute("SELECT next_at FROM memory_reflection_retry WHERE scope='self'").fetchone()
        self.assertIsNotNone(backoff);self.assertGreater(backoff[0],time.time())

    async def test_abandoned_merge_leaves_core_note_byte_identical(self):
        payload=[{'event':'e0','sender_id':PERSON,'user':'叫我小寒','bot':'好','at':time.time()}]
        self.assertEqual(self.store.reflection.update_core_note({'core_note':{'text':'合并前的核心背景','events':['e0']}},self.store.reflection.core_note(),payload,[],SCOPE),1)
        before=self.db.execute('SELECT slot,text,scopes,people,sources,updated,schema_version,claims FROM memory_core_notes WHERE slot=1').fetchone()
        ids=[self.small(SCOPE,'来源'+str(i),[correction_claim(SCOPE,'e'+str(i))]) for i in range(3)]
        self.db.commit();metrics={}
        async def chat(**kw):
            # Concurrent change between the pre-model gate and the commit: the
            # last source gains protected claims the merge budget cannot carry.
            late=[correction_claim(SCOPE,'late-%d'%i,'纠正证据%d，我们约定了后续安排'%i) for i in range(120)]
            self.db.execute('UPDATE memory_summaries SET claims=? WHERE id=?',(json.dumps(late),ids[-1]));self.db.commit()
            self.calls.append(kw);return NS(completion_text=json.dumps({'summary':'合并后的大摘要','core_note':{'text':'迟到的核心背景','summaries':ids}}))
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True},NS(text_chat=chat),lambda:True,metrics,self.var));await self.drain()
        after=self.db.execute('SELECT slot,text,scopes,people,sources,updated,schema_version,claims FROM memory_core_notes WHERE slot=1').fetchone()
        self.assertEqual(before,after,'an abandoned merge commits nothing, not even the core note')
        self.assertEqual(metrics.get('summaryFailures'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],3,'sources stay, no product row')
        self.assertIsNotNone(self.db.execute("SELECT next_at FROM memory_reflection_retry WHERE scope='self'").fetchone())

    async def test_cleanup_quotas_never_drop_held_evidence(self):
        self.rounds(special={0:('我们约定周末一起去平江路','好的')})
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.assertTrue(self.store.reflection.maybe_start(SCOPE,CLEAN_CFG,NS(text_chat=self.chat()),lambda:True,{},self.var));await self.drain()
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_hold_rounds WHERE scope=? AND event=?',(SCOPE,'0')).fetchone())
        for i in range(1001):self.store.reflection.record(SCOPE,PERSON,'bulk-%d'%i,'问题%d'%i,'回复%d'%i,self.now+300+i)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_rounds WHERE event=?',('0',)).fetchone(),'the held round survives the 1000-row quota')
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_rounds WHERE event=?',('bulk-0',)).fetchone(),'unheld old rounds still retire')
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_rounds WHERE event=?',('bulk-1000',)).fetchone())
        self.store.reflection.prune_pending(time.time()+90*86400)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',(SCOPE,'0')).fetchone(),'held pending survives the TTL')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending WHERE scope=?',(SCOPE,)).fetchone()[0],1)
        group='group:9999';subject='12345678'
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,?,?,?)',(group,'held-event',1,'uncovered',time.time()))
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(group,subject,'held-event','群里的约定','',self.now))
        for i in range(GROUP_ROUND_MESSAGES+3):self.store.reflection.incoming(group,subject,'ev-%d'%i,'群消息%d'%i,now=time.time()+i)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',(group,'held-event')).fetchone(),'group window eviction never drops held evidence')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending WHERE scope=? AND event LIKE ?',(group,'ev-%')).fetchone()[0],GROUP_ROUND_MESSAGES)


if __name__=='__main__':unittest.main(verbosity=2)
