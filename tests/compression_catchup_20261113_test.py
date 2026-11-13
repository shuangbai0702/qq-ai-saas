"""Persisted summary failure recovers 35 at a time without losing later rounds."""
import asyncio,copy,json,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
import semantic_provider_wire_test as fixture
from context_protocol import event_message,model_json
from summary_material import snapshots

class Catchup(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.SemanticProviderWire.asyncSetUp
    asyncTearDown=fixture.SemanticProviderWire.asyncTearDown
    summarize=fixture.SemanticProviderWire.summarize
    def populate(self,count):
        db=self.store.db;r=self.store.reflection;r.shared_context=True
        native=[{'role':'system','content':'Stable persona'}]
        for i in range(count):
            message=event_message({'scope':fixture.SCOPE,'sender_id':'12345678','event_id':str(i),'at':100+i},'Completed dialogue '+str(i))
            native.extend([message,{'role':'assistant','content':'Confirmed reply'}])
            r.record('bot_shared','12345678',model_json([fixture.SCOPE,str(i)]),'Completed dialogue '+str(i),'Confirmed reply',now=100+i,materials=snapshots([message,{'role':'assistant','content':'Confirmed reply'}],'bot_shared',db=db))

        from readonly_journal import ReadonlyJournal,Prepared
        journal=ReadonlyJournal(db,'bot_shared');journal.serial=1
        self.assertTrue(journal.commit(Prepared(1,None,copy.deepcopy(native),copy.deepcopy(native),{},count,time.time(),'initial')))
        self.store.model_journals['bot_shared']=journal

    async def test_failed_first_batch_then_continuous_35_round_catchup_keeps_remainder(self):
        self.populate(78);db=self.store.db;r=self.store.reflection
        rows_before=db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall()
        async def no_retry(provider,error,*args,**kwargs):raise error
        self.mode='network_error'
        with patch.object(type(self.provider),'_handle_api_error',no_retry):
            await self.summarize()
        self.assertEqual((db.execute("SELECT summary_watermark FROM memory_reflection_progress WHERE scope='bot_shared'").fetchone() or (0,))[0],0)
        self.assertEqual(db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall(),rows_before)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
        self.assertFalse(r.retry())  # Persisted backoff, no immediate spin.
        failed_calls=len(self.requests);self.assertEqual(failed_calls,2)
        self.mode='stop';db.execute('UPDATE memory_reflection_retry SET next_at=0');db.commit()
        self.assertTrue(r.retry())
        # The success callback schedules the same existing retry owner. Await
        # task identities, never rely on wall-clock sleeps to win the race.
        for _ in range(3):
            task=r.task;await task;await asyncio.sleep(0)
            if r.task is task:break
        checkpoints=db.execute("SELECT through_round_id,round_count FROM memory_reconstruction_checkpoints WHERE scope='bot_shared' ORDER BY id").fetchall()
        self.assertEqual(checkpoints,[(rows_before[34][0],35),(rows_before[69][0],35)])
        watermark=db.execute("SELECT summary_watermark FROM memory_reflection_progress WHERE scope='bot_shared'").fetchone()[0]
        self.assertEqual(watermark,rows_before[69][0])
        self.assertEqual(db.execute("SELECT event FROM memory_rounds WHERE scope='bot_shared' AND id>? ORDER BY id",(watermark,)).fetchall(),[(model_json([fixture.SCOPE,str(i)]),) for i in range(70,78)])
        self.assertFalse(r.retry())
        self.assertEqual(len(self.requests)-failed_calls,2)
        for index,request in enumerate(self.requests[failed_calls:]):
            prompt=next(json.loads(m['content']) for m in request['messages'] if isinstance(m.get('content'),str) and m['content'].startswith('{') and '"turns"' in m['content'])
            self.assertEqual([json.loads(row['event'])[1] for row in prompt['turns']],[str(i) for i in range(index*35,index*35+35)])

    async def test_manual_result_reports_remainder_until_authorized_partial_completes(self):
        import contextvars
        self.populate(43);r=self.store.reflection
        r.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('manual_catchup',default=None))
        self.assertTrue(r.force_compress_once()['started'])
        first=await r.task
        self.assertTrue(first['ok'],first)
        self.assertEqual(first['remainingCompleteRounds'],8)
        self.assertFalse(first['allCompletedRoundsSummarized'])
        self.assertFalse(r.retry())
        self.assertTrue(r.force_compress_once(allow_partial=True,administrator='fixture-admin',request_id='fixture-partial')['started'])
        final=await r.task
        self.assertTrue(final['ok'],final)
        self.assertEqual(final['remainingCompleteRounds'],0)
        self.assertTrue(final['allCompletedRoundsSummarized'])
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints ORDER BY id')],[35,8])

    async def partial_concurrent(self,change_original=False):
        import contextvars
        self.populate(8);db=self.store.db;r=self.store.reflection
        r.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('partial_concurrent',default=None))
        entered=asyncio.Event();release=asyncio.Event()
        original=self.provider.text_chat_stream
        async def gated(*args,**kwargs):
            entered.set();await release.wait()
            async for response in original(*args,**kwargs):yield response
        initial=db.execute('SELECT id FROM memory_rounds ORDER BY id').fetchall()
        journal=self.store.model_journals['bot_shared'];before=copy.deepcopy(journal.last.base)
        with patch.object(self.provider,'text_chat_stream',gated):
            self.assertTrue(r.force_compress_once(allow_partial=True,administrator='fixture-admin',request_id='partial-concurrent')['started'])
            task=r.task
            await entered.wait()
            if change_original:
                db.execute('UPDATE memory_rounds SET question=? WHERE id=?',('Changed original during model await',initial[0][0]));db.commit()
            else:
                appended=[]
                for i in range(8,11):
                    message=event_message({'scope':fixture.SCOPE,'sender_id':'12345678','event_id':str(i),'at':100+i},'Later dialogue '+str(i))
                    block=[message,{'role':'assistant','content':'Later confirmed reply'}];appended.extend(block)
                    r.record('bot_shared','12345678',model_json([fixture.SCOPE,str(i)]),'Later dialogue '+str(i),'Later confirmed reply',now=100+i,materials=snapshots(block,'bot_shared',db=db))
                # Real concurrent requests have extended the committed Journal;
                # the older summary may retire only its fixed original batch.
                from readonly_journal import Prepared
                native=copy.deepcopy(before)+appended;journal.serial+=1
                self.assertTrue(journal.commit(Prepared(journal.serial,None,copy.deepcopy(native),copy.deepcopy(native),{},11,time.time(),'append')))
            release.set();result=await task
        self.assertEqual(len(self.requests),1)
        if change_original:
            self.assertFalse(result['ok']);self.assertEqual(result['reason'],'RuntimeError')
            self.assertEqual(self.metrics['summaryFailures'],1)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
            self.assertEqual((db.execute("SELECT summary_watermark FROM memory_reflection_progress WHERE scope='bot_shared'").fetchone() or (0,))[0],0)
            self.assertEqual(journal.last.base,before)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],8)
        else:
            self.assertTrue(result['ok'],result)
            self.assertEqual(db.execute('SELECT round_count,through_round_id FROM memory_reconstruction_checkpoints').fetchone(),(8,initial[-1][0]))
            self.assertEqual(result['remainingCompleteRounds'],3);self.assertFalse(result['allCompletedRoundsSummarized'])
            self.assertEqual(db.execute('SELECT event FROM memory_rounds ORDER BY id').fetchall(),[(model_json([fixture.SCOPE,str(i)]),) for i in range(8,11)])
            wire=model_json(journal.last.wire)
            self.assertNotIn('Completed dialogue 0',wire)
            for i in range(8,11):self.assertIn('Later dialogue '+str(i),wire)
            self.assertFalse(r.retry())

    async def test_partial_concurrent_later_rounds_keep_original_batch_valid(self):
        await self.partial_concurrent()

    async def test_partial_concurrent_original_mutation_still_rejects_stale_batch(self):
        await self.partial_concurrent(change_original=True)

if __name__=='__main__':unittest.main()
