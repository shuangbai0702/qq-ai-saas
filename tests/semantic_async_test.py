"""Barrier regressions for retirement on the shared AI event loop."""
import asyncio
import sqlite3
import sys
import threading
import inspect
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parents[1]/'references/astrbot')]
import semantic_runtime_test as fixture
import semantic_async
import semantic_batch
from native_ingress import RawInput


class AsyncRetirement(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=fixture.RuntimeTests('test_callback_retires_without_next_message_without_copying_settled_execution')
        self.fixture.setUp();self.st=self.fixture.st

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def test_retirement_preview_and_commit_use_thread_owned_connection(self):
        owner=threading.get_ident();seen=[];purge=semantic_batch.purge
        def observed(db,*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),owner)
            self.assertIsNot(db,self.st.db)
            seen.append((threading.get_ident(),kwargs.get('preview',False)))
            return purge(db,*args,**kwargs)
        old=self.st.model_journals[fixture.SCOPE]
        async with self.st.compression_admission():
            with patch.object(semantic_batch,'purge',side_effect=observed):
                result=await semantic_async.finish(self.st,{},fixture.SCOPE)
        self.assertEqual(result['retired'],1,result)
        self.assertTrue(any(preview for _,preview in seen))
        self.assertTrue(any(not preview for _,preview in seen))
        self.assertIsNot(self.st.model_journals[fixture.SCOPE],old)
        self.assertIs(self.st.model_journals[fixture.SCOPE].db,self.st.db)
        self.assertNotIn('original old',str(self.st.model_journals[fixture.SCOPE].last.wire))

    async def test_cancellation_keeps_writer_until_transaction_and_reload_finish(self):
        entered=threading.Event();release=threading.Event();purge=semantic_batch.purge
        def delayed(db,*args,**kwargs):
            if kwargs.get('preview'):
                entered.set()
                if not release.wait(5):raise TimeoutError('test barrier')
            return purge(db,*args,**kwargs)
        async def run():
            async with self.st.compression_admission():return await semantic_async.finish(self.st,{},fixture.SCOPE)
        with patch.object(semantic_batch,'purge',side_effect=delayed):
            task=asyncio.create_task(run())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                # Other bots and /health can continue while this bot is held.
                await asyncio.wait_for(asyncio.sleep(0),0.3)
                task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done());self.assertTrue(self.st.compression_active)
                self.assertIsNotNone(self.st.model_writer.owner)
            finally:release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.st.compression_active);self.assertIsNone(self.st.model_writer.owner)
        self.assertNotIn('original old',str(self.st.model_journals[fixture.SCOPE].last.wire))

    async def test_ingress_lock_wait_is_off_loop_and_same_ledger_is_durable(self):
        entered=threading.Event();release=threading.Event();purge=semantic_batch.purge
        def delayed(db,*args,**kwargs):
            if kwargs.get('preview'):
                entered.set()
                if not release.wait(5):raise TimeoutError('test barrier')
            return purge(db,*args,**kwargs)
        event=RawInput('bot',{'post_type':'message','message_id':987,'user_id':12345678,'self_id':99999999,'message_type':'private','message':'new reliable input'})
        lease={'tenantId':'tenant','botId':'bot','generation':1,'revision':1}
        async with self.st.compression_admission():
            with patch.object(semantic_batch,'purge',side_effect=delayed):
                retirement=asyncio.create_task(semantic_async.finish(self.st,{},fixture.SCOPE))
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                path=self.st.db.execute('PRAGMA database_list').fetchone()[2]
                ingress=asyncio.create_task(asyncio.to_thread(semantic_async.persist_input,path,self.st.pipeline,event,lease))
                try:
                    await asyncio.wait_for(asyncio.sleep(0),0.3)
                    self.assertFalse(ingress.done())
                finally:release.set()
                self.assertEqual((await retirement)['retired'],1)
                self.assertTrue(await ingress)
        rows=self.st.db.execute('SELECT status,payload FROM pipeline_work_inputs WHERE event=?',('987',)).fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],'pending')
        self.assertIn('new reliable input',rows[0][1])

    async def test_close_invalidates_publication_and_waits_for_worker(self):
        entered=threading.Event();release=threading.Event();purge=semantic_batch.purge
        def delayed(db,*args,**kwargs):
            if kwargs.get('preview'):
                entered.set()
                if not release.wait(5):raise TimeoutError('test barrier')
            return purge(db,*args,**kwargs)
        old=self.st.model_journals[fixture.SCOPE]
        async with self.st.compression_admission():
            with patch.object(semantic_batch,'purge',side_effect=delayed):
                task=asyncio.create_task(semantic_async.finish(self.st,{},fixture.SCOPE))
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                try:
                    self.st.close()
                    self.assertTrue(self.st.reflection.closed)
                    self.assertFalse(getattr(self.st,'_storage_closed',False))
                    self.assertEqual(self.st.db.execute('SELECT 1').fetchone(),(1,))
                finally:release.set()
                self.assertEqual((await task)['retired'],1)
        self.assertIs(self.st.model_journals[fixture.SCOPE],old)
        self.assertTrue(self.st._storage_closed)
        with self.assertRaises(sqlite3.ProgrammingError):self.st.db.execute('SELECT 1')

    async def test_failed_worker_purge_rolls_back_journal_permit_and_retries(self):
        import journal_storage
        from context_protocol import model_json
        before=journal_storage.read_snapshot(self.st.db,model_json(fixture.SCOPE))
        purge=semantic_batch.purge
        def fail(db,*args,**kwargs):
            value=purge(db,*args,**kwargs)
            if not kwargs.get('preview'):raise OSError('controlled cleanup failure')
            return value
        async with self.st.compression_admission():
            with patch.object(semantic_batch,'purge',side_effect=fail):
                result=await semantic_async.finish(self.st,{},fixture.SCOPE)
            self.assertEqual(result['pending'],1)
            self.assertEqual(journal_storage.read_snapshot(self.st.db,model_json(fixture.SCOPE)),before)
            self.assertEqual(self.st.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone(),('available',))
            self.assertEqual(self.st.db.execute('SELECT state FROM memory_semantic_batches').fetchone(),('committed',))
            self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_semantic_purges').fetchone(),(0,))
            self.assertEqual((await semantic_async.finish(self.st,{},fixture.SCOPE))['retired'],1)

    async def test_replacement_drain_waits_for_real_worker_even_if_cancelled(self):
        entered=threading.Event();release=threading.Event();purge=semantic_batch.purge
        def delayed(db,*args,**kwargs):
            if kwargs.get('preview'):
                entered.set()
                if not release.wait(5):raise TimeoutError('test barrier')
            return purge(db,*args,**kwargs)
        old=self.st.model_journals[fixture.SCOPE]
        async with self.st.compression_admission():
            with patch.object(semantic_batch,'purge',side_effect=delayed):
                retirement=asyncio.create_task(semantic_async.finish(self.st,{},fixture.SCOPE,lambda:not self.st.reflection.closed))
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                drain=asyncio.create_task(semantic_async.drain_store(self.st))
                try:
                    await asyncio.sleep(0);drain.cancel();await asyncio.sleep(0)
                    self.assertFalse(drain.done());self.assertTrue(self.st.reflection.closed)
                finally:release.set()
                await retirement
                with self.assertRaises(asyncio.CancelledError):await drain
        self.assertFalse(self.st._local_db_workers)
        self.assertIs(self.st.model_journals[fixture.SCOPE],old)

    async def test_pending_input_persistence_retains_ingress_and_stop_fence(self):
        import native_ingress_retention_20261111_test as ingress_fixture
        from types import SimpleNamespace as NS
        from runtime_care import RuntimeCare
        from worker_lifecycle import StopFence
        entered=threading.Event();release=threading.Event()
        def delayed_input():
            entered.set()
            if not release.wait(5):raise TimeoutError('test barrier')
        async def retained():
            task=semantic_async.track_worker(self.st,asyncio.create_task(asyncio.to_thread(delayed_input)))
            await semantic_async.settled(task)
            return False
        acquire=ingress_fixture.extract(Path('adapters/astrbot/behavior.py'),'acquire_raw_ingress',
            {'retain_ingress':lambda *args:retained(),'stores':{'bot':self.st},'inspect':inspect,
             'care':NS(stamp=lambda root:None)})
        fence=StopFence(lambda:{'idle':not RuntimeCare.busy(self.st)})
        task=asyncio.create_task(acquire('bot',{}))
        self.assertTrue(await asyncio.to_thread(entered.wait,3))
        try:
            self.assertEqual(self.st.ingress,1)
            self.assertFalse(fence.request_if_idle())
            task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done());self.assertEqual(self.st.ingress,1)
            self.assertFalse(fence.request_if_idle())
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.st.ingress,0);self.assertTrue(fence.request_if_idle())

    async def test_sdk_tail_cleanup_does_not_wait_on_its_own_compression_boundary(self):
        from types import SimpleNamespace as NS
        from semantic_runtime import install_native_retirement_guard
        path=self.fixture.native();manager=self.fixture.manager
        async with self.st.compression_admission():
            self.assertEqual((await semantic_async.finish(self.st,{},fixture.SCOPE))['retired'],1)
        async def save(*args,**kwargs):return NS(platform_id='tenant',conversation_id='own')
        manager.db.update_conversation=save
        install_native_retirement_guard(manager,{'tenant':self.st},{'tenant':{}})
        admitted=asyncio.Event()
        async def compress():
            async with self.st.compression_admission():admitted.set()
        self.st.active=1
        compression=asyncio.create_task(compress())
        try:
            await asyncio.sleep(0)
            self.assertIsNotNone(self.st.compression_boundary)
            # This is still the admitted conversation's SDK tail. Compression
            # cannot acquire until that conversation completes after this call.
            await asyncio.wait_for(manager.db.update_conversation(),1)
            self.assertFalse(admitted.is_set())
        finally:
            self.st.active=0;self.st.slot_changed.set()
            await compression
        with closing(sqlite3.connect(path)) as db:
            self.assertNotIn('original old',db.execute("SELECT content FROM conversations WHERE conversation_id='own'").fetchone()[0])


class SummaryToThread(unittest.IsolatedAsyncioTestCase):
    async def test_actual_sdk_summary_and_next_wire_through_async_retirement(self):
        import semantic_provider_wire_test as provider_fixture
        f=provider_fixture.SemanticProviderWire('test_summary_http_then_checkpoint_retires_originals_before_next_http')
        await f.asyncSetUp()
        owner=threading.get_ident();seen=[];purge=semantic_batch.purge
        async def checkpoint(scope):
            result=await semantic_async.finish(f.store,f.cfg,scope)
            f.callbacks.append((scope,result))
            f.journal=f.store.model_journals[scope]
        def observed(db,*args,**kwargs):
            seen.append(threading.get_ident())
            self.assertNotEqual(threading.get_ident(),owner)
            return purge(db,*args,**kwargs)
        f.store.reflection.checkpoint_callback=checkpoint
        try:
            with patch.object(semantic_batch,'purge',side_effect=observed):
                await f.test_summary_http_then_checkpoint_retires_originals_before_next_http()
            self.assertTrue(seen)
        finally:await f.asyncTearDown()

if __name__=='__main__':unittest.main()
