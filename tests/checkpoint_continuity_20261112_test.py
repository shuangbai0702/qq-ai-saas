"""Continuity is one optional checkpoint product, never another dialogue batch."""
import asyncio
import json
from pathlib import Path
import sys
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'adapters/astrbot')]
import summary_material_test as fixture
from behavior import BehaviorStore
from readonly_journal import ReadonlyJournal
import conversation_continuity as continuity


class CheckpointContinuity(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ReflectionTests.setUp
    tearDown = fixture.ReflectionTests.tearDown
    add = fixture.ReflectionTests.add
    drain = fixture.ReflectionTests.drain
    finish = fixture.ReflectionTests.finish

    async def summarize(self, product, *, start=0, current=lambda: True, chat=None):
        self.add(start=start)
        calls = []
        async def response(**kw):
            calls.append(kw)
            if chat is not None:
                return await chat(**kw)
            data = {'summary': '本批真实材料形成的独立长期摘要'}
            if product is not ...:
                data['conversation_continuity'] = product
            return NS(completion_text=json.dumps(data, ensure_ascii=False))
        self.assertTrue(self.store.reflection.maybe_start(
            fixture.SCOPE, {'memory': True, 'memorySummary': True, 'summaryRounds': 35, 'naturalConversation': False},
            NS(text_chat=response), current, {}, self.var))
        await self.drain()
        return calls

    def latest_checkpoint(self):
        return self.db.execute('SELECT id FROM memory_reconstruction_checkpoints WHERE scope=? ORDER BY id DESC LIMIT 1', (fixture.SCOPE,)).fetchone()[0]

    def product(self, text):
        return [{'source_scope': fixture.SCOPE, 'text': text}]

    async def test_two_checkpoints_replace_after_journal_and_never_feed_old_product_back(self):
        first = 'CONTINUITY_FIRST_ONLY：刚谈到杯子颜色，对话停在选择蓝色。'
        second = 'CONTINUITY_SECOND_ONLY：刚谈到新茶，尚未决定品种。'
        calls = await self.summarize(self.product(first))
        cp1 = self.latest_checkpoint()
        self.assertEqual(len(calls), 1)
        self.assertIn('conversation_continuity', calls[0]['system_prompt'])
        self.assertNotIn(first, calls[0]['prompt'])
        self.finish()
        self.assertIn(first, json.dumps(continuity.view(self.db, fixture.SCOPE), ensure_ascii=False))
        self.assertIn(first, json.dumps(self.journal.last.wire, ensure_ascii=False), 'the adopted Journal actually carries the optional product')
        captured = fixture.material.snapshots(self.journal.last.wire, fixture.SCOPE)
        self.assertNotIn(first, json.dumps(captured, ensure_ascii=False), 'read-only continuity never becomes real user summary material')
        calls = await self.summarize(self.product(second), start=35)
        cp2 = self.latest_checkpoint()
        self.assertNotEqual(cp1, cp2)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(first, calls[0]['prompt'], 'old continuity is not summary/observed-request material')
        self.assertEqual(len(json.loads(calls[0]['prompt'])['turns']), 35)
        self.finish()
        current = json.dumps(continuity.view(self.db, fixture.SCOPE), ensure_ascii=False)
        self.assertIn(second, current)
        self.assertNotIn(first, current)
        self.assertIsNone(self.db.execute('SELECT conversation_continuity FROM memory_reconstruction_checkpoints WHERE id=?', (cp1,)).fetchone()[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints WHERE id=?', (cp2,)).fetchone()[0], 35)

    async def test_invalid_and_omitted_product_do_not_block_legal_summary_or_keep_stale_product(self):
        for bad in (..., None, {'text': 'wrong container'}, self.product('字' * 501), [{'source_scope': 'private:87654321', 'text': 'foreign'}]):
            with self.subTest(product=bad):
                # Each subcase uses a fresh real store, avoiding a third-checkpoint rollup.
                self.store.close()
                self.store = BehaviorStore(Path(self.tmp.name) / ('case-' + str(time.time_ns()) + '.sqlite'))
                self.db = self.store.db
                self.journal = ReadonlyJournal(self.db, fixture.SCOPE)
                self.store.model_journals[fixture.SCOPE] = self.journal
                await self.summarize(self.product('OLD_CURRENT_CONTINUITY'))
                self.finish()
                calls = await self.summarize(bad, start=35)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0], 2)
                self.assertEqual(self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?', (fixture.SCOPE,)).fetchone()[0], 70)
                self.finish()
                self.assertIsNone(continuity.view(self.db, fixture.SCOPE))
                self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0], 0)

    async def test_restart_scope_and_no_time_or_round_expiry(self):
        await self.summarize(self.product('仍然有效的承接语义'))
        self.finish()
        before = continuity.view(self.db, fixture.SCOPE)
        self.assertIsNotNone(before)
        self.assertIsNone(continuity.view(self.db, 'private:87654321'))
        self.assertIsNone(continuity.view(self.db, 'group:55555'))
        path = Path(self.tmp.name) / 'behavior.sqlite'
        self.store.close(); self.store = BehaviorStore(path); self.db = self.store.db
        self.assertEqual(continuity.view(self.db, fixture.SCOPE), before)
        self.add(start=35)
        with patch('time.time', return_value=time.time() + 100 * 365 * 86400):
            self.assertEqual(continuity.view(self.db, fixture.SCOPE), before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0], 35, 'reading continuity never creates a business round')

    def test_legacy_schema_upgrade_is_additive_and_does_not_backfill_history(self):
        self.add()
        before=self.db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall()
        self.db.execute('ALTER TABLE memory_reconstruction_checkpoints DROP COLUMN conversation_continuity')
        self.db.commit()
        path=Path(self.tmp.name)/'behavior.sqlite'
        self.store.close();self.store=BehaviorStore(path);self.db=self.store.db
        self.assertIn('conversation_continuity',{row[1] for row in self.db.execute('PRAGMA table_info(memory_reconstruction_checkpoints)')})
        self.assertEqual(self.db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall(),before)
        self.assertIsNone(continuity.view(self.db,fixture.SCOPE))

    async def test_concurrent_batch_mutation_cannot_publish_late_product(self):
        await self.summarize(self.product('安全旧版'))
        self.finish()
        before = continuity.view(self.db, fixture.SCOPE)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(**kw):
            entered.set(); await release.wait()
            return NS(completion_text=json.dumps({'summary': '迟到摘要', 'conversation_continuity': self.product('禁止发布的迟到版')}))
        task = asyncio.create_task(self.summarize(None, start=35, chat=delayed))
        await asyncio.wait_for(entered.wait(), 3)
        self.db.execute("UPDATE memory_rounds SET answer='并发修改真实回复' WHERE scope=? AND event='35'", (fixture.SCOPE,)); self.db.commit()
        release.set(); await task
        self.assertEqual(continuity.view(self.db, fixture.SCOPE), before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0], 35)

    async def test_model_failure_and_transaction_failure_preserve_previous_product(self):
        await self.summarize(self.product('故障前合法旧版'))
        self.finish()
        before = continuity.view(self.db, fixture.SCOPE)
        async def failed(**kw):
            raise TimeoutError('offline fixture')
        calls = await self.summarize(None, start=35, chat=failed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(continuity.view(self.db, fixture.SCOPE), before)
        self.db.execute('DELETE FROM memory_reflection_retry'); self.db.commit()
        import semantic_batch
        original_commit = semantic_batch.commit
        def fail_after_batch_write(*args, **kwargs):
            original_commit(*args, **kwargs)
            raise RuntimeError('fixture fault after batch write')
        with patch.object(semantic_batch, 'commit', side_effect=fail_after_batch_write):
            calls = await self.summarize(self.product('不得泄漏的事务内新版'), start=35)
        self.assertEqual(len(calls), 1)
        self.assertEqual(continuity.view(self.db, fixture.SCOPE), before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?', (fixture.SCOPE,)).fetchone()[0], 35)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0], 35)


class ProductContract(unittest.TestCase):
    def test_omitted_product_has_explicit_optional_receipt(self):
        from memory_operation_audit import Audit
        audit=Audit(fixture.SCOPE,1,'small',{})
        self.assertIsNone(continuity.accept({},fixture.SCOPE,[],audit))
        self.assertTrue(audit.counts['conversation_continuity']['omitted'])
        self.assertEqual(audit.details[('conversation_continuity',0)]['reason'],'optional_continuity_not_provided')
        self.assertFalse(audit.details[('conversation_continuity',0)]['changed'])

    def authors(self, *scopes):
        return [{'sender_id': '12345678', 'source': {'source_scope': scope}} for scope in scopes]

    def test_scope_provenance_and_shared_string_ambiguity(self):
        authors = self.authors('private:12345678', 'group:55555')
        self.assertIsNone(continuity.accept({'conversation_continuity': 'unattributed'}, 'bot_shared', authors))
        self.assertIsNone(continuity.accept({'conversation_continuity': [{'source_scope': 'group:99999', 'text': 'not observed'}]}, 'bot_shared', authors))
        accepted = continuity.accept({'conversation_continuity': [{'source_scope': 'group:55555', 'text': '真实群聊承接'}]}, 'bot_shared', authors)
        self.assertEqual(json.loads(accepted), [{'source_scope': 'group:55555', 'text': '真实群聊承接'}])

    def test_total_unicode_budget_is_not_a_fixed_five_turn_slice(self):
        scope = fixture.SCOPE
        data = {'conversation_continuity': [{'source_scope': scope, 'text': '字' * 500}]}
        self.assertIsNotNone(continuity.accept(data, scope, self.authors(scope)))
        scopes = (scope, 'group:55555')
        data = {'conversation_continuity': [{'source_scope': s, 'text': '字' * 250} for s in scopes]}
        self.assertIsNotNone(continuity.accept(data, 'bot_shared', self.authors(*scopes)))
        data['conversation_continuity'][1]['text'] += '超'
        self.assertIsNone(continuity.accept(data, 'bot_shared', self.authors(*scopes)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
