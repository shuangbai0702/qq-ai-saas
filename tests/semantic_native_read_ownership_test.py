"""Late SDK reads cannot cross store or configuration ownership changes."""
import asyncio
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from semantic_runtime import install_native_retirement_guard


class NativeReadOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_late_read_is_discarded_when_owner_changes(self):
        for change in ('store','configuration','release','closed'):
            with self.subTest(change=change):
                entered=asyncio.Event();resume=asyncio.Event()
                rows=[NS(content={'text':'old binding data'})]
                async def original(platform):
                    entered.set();await resume.wait();return rows
                db=NS(get_platform_message_history=original)
                ledger=sqlite3.connect(':memory:');self.addCleanup(ledger.close)
                st=NS(db=ledger,reflection=NS(closed=False))
                stores={'tenant':st};settings={'tenant':object()}
                install_native_retirement_guard(NS(db=db),stores,settings)
                task=asyncio.create_task(db.get_platform_message_history('tenant'))
                await entered.wait()
                if change=='store':stores['tenant']=NS(db=ledger,reflection=NS(closed=False))
                elif change=='configuration':settings['tenant']=object()
                elif change=='release':stores.pop('tenant');settings.pop('tenant')
                else:st.reflection.closed=True
                resume.set()
                self.assertEqual(await task,[])

    async def test_stable_owner_uses_tombstones_committed_during_read(self):
        from context_protocol import model_json
        from semantic_batch import event_key
        entered=asyncio.Event();resume=asyncio.Event()
        retired=NS(content={'_fishbot_source':{'scope':'private:12345','event_id':'old'}})
        fresh=NS(content={'_fishbot_source':{'scope':'private:12345','event_id':'new'}})
        async def original(platform):
            entered.set();await resume.wait();return [retired,fresh]
        with sqlite3.connect(':memory:') as ledger:
            ledger.execute('CREATE TABLE memory_semantic_batches(scope,members,state)')
            st=NS(db=ledger,reflection=NS(closed=False))
            db=NS(get_platform_message_history=original)
            install_native_retirement_guard(NS(db=db),{'tenant':st},{'tenant':object()})
            task=asyncio.create_task(db.get_platform_message_history('tenant'))
            await entered.wait()
            ledger.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?)',
                ('private:12345',model_json([event_key('private:12345','old')]),'retired'))
            resume.set()
            self.assertEqual(await task,[fresh])

    async def test_new_binding_can_query_again_after_stale_result_is_discarded(self):
        entered=asyncio.Event();resume=asyncio.Event();calls=0
        stale=NS(content={'text':'previous binding'})
        fresh=NS(content={'text':'current binding'})
        async def original(platform):
            nonlocal calls
            calls+=1
            if calls==1:
                entered.set();await resume.wait();return [stale]
            return [fresh]
        with sqlite3.connect(':memory:') as ledger:
            st=NS(db=ledger,reflection=NS(closed=False))
            db=NS(get_platform_message_history=original)
            settings={'tenant':object()}
            install_native_retirement_guard(NS(db=db),{'tenant':st},settings)
            task=asyncio.create_task(db.get_platform_message_history('tenant'))
            await entered.wait();settings['tenant']=object();resume.set()
            self.assertEqual(await task,[])
            self.assertEqual(await db.get_platform_message_history('tenant'),[fresh])


if __name__=='__main__':unittest.main()
