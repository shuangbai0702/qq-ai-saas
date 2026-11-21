"""Summary products carry prose/claims, not accumulated event inventories."""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from summary_material_test import message,SCOPE
import summary_material as material

from summary_stream_fixture import summary_provider

class SummarySources(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db
        self.var=contextvars.ContextVar('sources_test',default=None);self.calls=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def chat(self,**kw):
        self.calls.append(json.loads(kw['prompt']))
        return NS(completion_text=json.dumps({'summary':'持续记忆'}) if '最多1500' in kw['system_prompt'] else json.dumps({'summary':'真实经历'}))
    async def batch(self,start):
        for i in range(start,start+30):
            self.r.record(SCOPE,'12345678',str(i),'看这个','已回复',materials=material.snapshots([message(str(i))],SCOPE))
        self.assertTrue(self.r.maybe_start(SCOPE,{'memory':True,'memorySummary':True},summary_provider(self.chat),lambda:True,{},self.var))
        await self.r.task;await asyncio.sleep(0)
    async def test_rollups_keep_coverage_without_accumulating_sources(self):
        for start in (0,30,60,90,120,150):await self.batch(start)
        self.assertEqual([x['level'] for x in self.r.foundation(SCOPE)],['big'])
        self.assertEqual(self.db.execute('SELECT sources FROM memory_summaries').fetchall(),[('[]',)])
        self.assertNotIn('source_events',self.r.foundation(SCOPE)[0])
        self.assertTrue(all('sources' not in row for call in self.calls for row in call.get('summaries',[])))
        # Current retirement authority is the exact opaque semantic batch,
        # not the removed per-event Coverage product (six batches x 30 rounds).
        batches=self.db.execute('SELECT members FROM memory_semantic_batches').fetchall()
        self.assertEqual(len(batches),6)
        self.assertEqual(len({event for row in batches for event in json.loads(row[0])}),180)
        self.assertEqual(material.coverage(self.db,SCOPE),{})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_unresolved').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT round_count,state FROM memory_reconstruction_checkpoints').fetchall(),[(30,'available')]*6)
        summary_id=self.db.execute('SELECT id FROM memory_summaries').fetchone()[0]
        permit=self.r.reserve_context_reconstruction(SCOPE,'once',[summary_id]);self.assertIsNotNone(permit)
        self.assertTrue(self.r.settle_context_reconstruction(permit,True))
        self.assertIsNone(self.r.reserve_context_reconstruction(SCOPE,'replay',[summary_id]))
        from memory_reflection import invalidate
        invalidate(self.db,SCOPE,'12345678');self.db.commit()
        self.assertEqual(material.coverage(self.db,SCOPE),{})
    async def test_more_than_600_pending_are_read_without_minting_extra_rounds(self):
        now=time.time()
        for i in range(625):
            event='pending-'+str(i);m=material.snapshots([message(event),{'role':'assistant','content':'尚未确认的原稿'}],SCOPE)[0]
            m['reply']=''
            self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,'12345678',event,m['text'],'',now))
            self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,event,m['subject'],json.dumps(m)))
        self.db.commit();await self.batch(0)
        events={r['event'] for r in self.calls[0]['turns']}
        self.assertEqual(len(events),655);self.assertIn('pending-624',events)
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchall(),[(30,)])
        from semantic_batch import event_key
        members=json.loads(self.db.execute('SELECT members FROM memory_semantic_batches').fetchone()[0])
        self.assertEqual(len(members),655)
        self.assertIn(event_key(SCOPE,'pending-624'),members)
        # No transport success is invented; originals remain until replacement commits.
        self.assertEqual(self.db.execute("SELECT answer FROM memory_pending WHERE event='pending-624'").fetchone()[0],'')
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM memory_round_material WHERE event='pending-624'").fetchone())
    async def test_legacy_migration_keeps_only_live_holds_and_preserves_product(self):
        from readonly_journal import ReadonlyJournal
        from unified_context_20261108_test import request
        journal=ReadonlyJournal(self.db,'bot_shared')
        candidate=journal.prepare(request(message('wire-media',image=True)),('bot',),strict=True,reconstruction_scope='bot_shared')
        self.assertTrue(journal.stage_candidate(candidate));self.assertTrue(journal.commit(candidate))
        before_wire=material.model_json(journal.last.wire)
        m=material.snapshots([message('held')],SCOPE)[0]
        self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,'held',m['subject'],json.dumps(m)))
        sources=json.dumps(['ancient','held','other'])
        sid=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,0)',(SCOPE,'旧产品',sources)).lastrowid
        self.db.execute("INSERT INTO memory_hold_rounds VALUES(?,?,NULL,'uncovered',0)",(SCOPE,'held'));self.db.commit()
        self.store.close();self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db
        self.assertEqual(self.db.execute('SELECT sources FROM memory_summaries WHERE id=?',(sid,)).fetchone()[0],sources)
        self.assertEqual(self.db.execute('SELECT event,summary_id,digest FROM memory_summary_unresolved').fetchall(),[('held',sid,m['digest'])])
        self.assertNotIn('source_events',self.r.foundation(SCOPE)[0])
        self.assertEqual(material.model_json(ReadonlyJournal(self.db,'bot_shared').last.wire),before_wire)
    async def test_provider_capacity_failure_preserves_the_entire_batch(self):
        now=time.time()
        for i in range(625):
            event='pending-'+str(i);msg=message(event)
            msg['content'][0]['text']=msg['content'][0]['text'].replace('看这个','完整待整理正文'+str(i))
            m=material.snapshots([msg],SCOPE)[0]
            self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,'12345678',event,m['text'],'',now))
            self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,event,'12345678',json.dumps(m)))
        self.db.commit()
        for i in range(30):
            self.r.record(SCOPE,'12345678',str(i),'看这个','已回复',materials=material.snapshots([message(str(i))],SCOPE))
        seen=[]
        async def capacity(**kw):
            seen.append(json.loads(kw['prompt'])['turns'])
            raise ValueError('provider_context_capacity')
        self.assertTrue(self.r.maybe_start(SCOPE,{'memorySummary':True},summary_provider(capacity),lambda:True,{},self.var))
        await self.r.task
        self.assertEqual(len(seen),2)
        self.assertTrue(all(len(batch)==655 for batch in seen))
        self.assertEqual(seen[0],seen[1])
        self.assertIn('完整待整理正文624',next(x['user'] for x in seen[0] if x['event']=='pending-624'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],655)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],655)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
        self.assertIsNotNone(self.db.execute('SELECT next_at FROM memory_reflection_retry WHERE scope=?',(SCOPE,)).fetchone())
    async def test_unanswered_material_has_exact_semantic_membership_without_forged_receipt(self):
        m=material.snapshots([message('held')],SCOPE)[0]
        m.update(version=3,input_settlement=None,native_outputs=[])
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,m['subject'],'held',m['text'],'',time.time()))
        self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,'held',m['subject'],json.dumps(m)))
        self.db.commit();await self.batch(0)
        from semantic_batch import event_key
        members=json.loads(self.db.execute('SELECT members FROM memory_semantic_batches').fetchone()[0])
        self.assertIn(event_key(SCOPE,'held'),members);self.assertEqual(len(members),31)
        offered=next(row for row in self.calls[0]['turns'] if row['event']=='held')
        self.assertEqual(offered['bot'],'')
        self.assertEqual(self.db.execute("SELECT answer FROM memory_pending WHERE event='held'").fetchone()[0],'')
        stored=json.loads(self.db.execute("SELECT material FROM memory_round_material WHERE event='held'").fetchone()[0])
        self.assertIsNone(stored['input_settlement'])
        self.assertEqual(material.coverage(self.db,SCOPE),{})
        sid=self.db.execute('SELECT id FROM memory_summaries').fetchone()[0]
        permit=self.r.reserve_context_reconstruction(SCOPE,'real-replacement',[sid]);self.assertIsNotNone(permit)
        self.assertTrue(self.r.settle_context_reconstruction(permit,True))
        from semantic_batch import purge
        # Consuming a permit alone is insufficient: no replacement Journal
        # has committed in this fixture, so cleanup must remain blocked.
        with self.db:self.assertFalse(purge(self.db,permit['id']))
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM memory_pending WHERE event='held'").fetchone())
    async def test_legacy_source_field_change_appends_through_real_context_and_journal(self):
        import copy,context_foundation,shared_context
        from context_protocol import READONLY_CURRENT,model_json
        from readonly_journal import ReadonlyJournal
        from unified_context_20261108_test import request
        logical='bot_shared';self.store.shared_context=True
        sid=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,0)',(logical,'旧大摘要',json.dumps(['old-1','old-2']))).lastrowid
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'big')",(sid,));self.db.commit()
        cfg={'memory':True,'memorySummary':True}
        foundation=context_foundation.read(self.store,cfg,SCOPE,'12345678')
        old_foundation=copy.deepcopy(foundation)
        old_foundation['conversation_summaries'][0]['source_events']=['old-1','old-2']
        def context_request(m,base):
            messages=request(m)
            messages[1]['content']=READONLY_CURRENT+'\n'+model_json({'memory_foundation':base,'state':{}})
            return messages
        journal=ReadonlyJournal(self.db,logical)
        old=context_request(message('old-media',image=True),old_foundation)
        before_dependency=self.r.context_dependency([sid],logical);retirement=self.r.reconstruction_stamp(logical)
        # Seed the historical committed fixture, then exercise the upgraded
        # foreground path with all current reconstruction guards enabled.
        p=journal.prepare(old,('bot',1,1),strict=True,guarded=False,reconstruction_scope=logical,reconstruction_authority=self.r,retirement=retirement)
        self.assertTrue(journal.stage_candidate(p));self.assertTrue(journal.commit(p))
        journal.append_completion({'role':'assistant','content':'旧确认答复'})
        before_wire=copy.deepcopy(journal.last.wire)
        self.store.close();self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db;self.store.shared_context=True
        new_foundation=context_foundation.read(self.store,cfg,SCOPE,'12345678')
        self.assertNotIn('source_events',new_foundation['conversation_summaries'][0])
        self.assertEqual(self.r.context_dependency([sid],logical),before_dependency)
        self.assertEqual(self.r.reconstruction_stamp(logical),retirement)
        journal=ReadonlyJournal(self.db,logical);incoming=message('new-after-upgrade')
        composed,_=shared_context.compose(journal,context_request(incoming,new_foundation),SCOPE,'new-after-upgrade',[incoming])
        p=journal.prepare(composed,('bot',1,1),strict=True,guarded=True,compact_policy=True,reconstruction_scope=logical,reconstruction_authority=self.r,retirement=self.r.reconstruction_stamp(logical))
        self.assertEqual(p.reason,'');self.assertIsNone(p.reconstruction_permit)
        self.assertEqual(model_json(p.wire[:len(before_wire)]),model_json(before_wire))
        # Core Note now has a separate owned foundation region; immutable
        # committed wire above remains byte-identical, including historical Core.
        expected_dynamic={k:v for k,v in old_foundation.items() if k!='core_note'}
        self.assertEqual(p.state['memory_foundation'],expected_dynamic)
        self.assertTrue(journal.stage_candidate(p));self.assertTrue(journal.commit(p))
        self.assertEqual(model_json(ReadonlyJournal(self.db,logical).last.wire[:len(before_wire)]),model_json(before_wire))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)

if __name__=='__main__':unittest.main()
