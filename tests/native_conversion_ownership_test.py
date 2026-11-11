"""Real recovery ledger and controlled conversion, without a QQ connection."""
import asyncio
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline
from native_ingress import bind,RawInput
from worker_lifecycle import StopFence


class ConversionOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_conversion_is_owned_before_await_and_duplicate_is_not_converted(self):
        db=sqlite3.connect(':memory:');pipeline=BotPipeline(db)
        lease={'tenantId':'tenant','botId':'bot','generation':1,'revision':1}
        raw={'post_type':'message','message_type':'group','message_id':101,
             'self_id':99999,'user_id':12345,'group_id':55555,'message':[]}
        held=0;calls=0;entered=asyncio.Event();finish=asyncio.Event()
        async def original(raw):
            nonlocal calls
            calls+=1;entered.set();await finish.wait();return NS()
        def acquire(raw):
            nonlocal held
            event=RawInput('qq',raw)
            if not pipeline.persist_input(event,lease):return None
            with db:
                accepted=db.execute("UPDATE pipeline_work_inputs SET status='queued',retry_at=? WHERE scope=? AND event=? AND status='pending'",(time.time()+30,'group:55555','101')).rowcount
            if not accepted:return None
            held+=1
            def release():
                nonlocal held
                held-=1
            return release
        platform=NS(convert_message=original)
        fence=StopFence(lambda:{'idle':held==0})
        bind(platform,acquire,quiescing=lambda:fence.quiescing)
        task=asyncio.create_task(platform.convert_message(raw));await entered.wait()
        self.assertEqual(db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0],'queued')
        self.assertFalse(fence.request_if_idle())
        self.assertIsNone(await platform.convert_message(raw));self.assertEqual(calls,1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(held,0)
        # Interrupted conversion remains owned by the existing delayed recovery.
        self.assertEqual(db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0],'queued')
        finish.set()
        recovered=await platform.convert_retained_message(raw)
        self.assertIsNotNone(recovered);self.assertEqual(calls,2)
        db.close()

    async def test_fenced_arrival_is_persisted_without_conversion(self):
        retained=[];released=[]
        async def original(raw):self.fail('fenced input must wait for recovery')
        platform=NS(convert_message=original)
        bind(platform,lambda raw:retained.append(raw) or (lambda:released.append(True)),quiescing=lambda:True)
        self.assertIsNone(await platform.convert_message({'post_type':'message'}))
        self.assertEqual(len(retained),1);self.assertEqual(released,[True])


if __name__=='__main__':unittest.main()
