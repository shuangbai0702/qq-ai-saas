"""Optional memory switches cannot cancel a legal checkpoint; isolated SQLite."""
import contextvars,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parent))
import semantic_batch_summary_test as fixture
from summary_stream_fixture import summary_provider

class MandatoryCheckpoint(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.SemanticSummary.setUp
    tearDown=fixture.SemanticSummary.tearDown
    record=fixture.SemanticSummary.record
    chat=fixture.SemanticSummary.chat
    checkpoint=fixture.SemanticSummary.checkpoint

    def bind(self,provider):
        self.r.binding=({'memory':False,'memorySummary':False,'naturalConversation':False,'selfDevelopment':False},provider,lambda:self.live,self.metrics,contextvars.ContextVar('mandatory_checkpoint',default=None))

    async def test_disabled_optional_memory_still_compresses_only_after_35(self):
        self.record(34);self.bind(summary_provider(self.chat))
        self.assertFalse(self.r.retry());self.assertEqual(self.calls,[])
        # Same identity is ignored by the authoritative completed-round table.
        self.record(35)
        self.assertTrue(self.r.retry());await self.r.task
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.checkpoint()[1:],(35,'available',None))

    async def test_missing_provider_records_failure_and_recovers_same_due_batch(self):
        import json
        self.record(35);self.bind(NS())
        before=self.db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall()
        self.assertTrue(self.r.retry());await self.r.task
        self.assertIsNone(self.checkpoint());self.assertEqual(self.calls,[])
        self.assertEqual(self.db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall(),before)
        issue=json.loads(self.db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind='failure_summary'").fetchone()[0])
        self.assertEqual(issue['reason'],'semantic_summary_provider_unavailable')
        self.assertEqual(issue['automatic_recovery'],'persisted_backoff')
        self.assertFalse(self.r.retry())
        self.bind(summary_provider(self.chat))
        self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
        self.assertTrue(self.r.retry());await self.r.task
        self.assertEqual(self.checkpoint()[1:],(35,'available',None));self.assertEqual(len(self.calls),1)

import semantic_provider_wire_test as wire

class MandatoryCheckpointWire(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=wire.SemanticProviderWire.asyncSetUp
    asyncTearDown=wire.SemanticProviderWire.asyncTearDown
    populate=wire.SemanticProviderWire.populate
    summarize=wire.SemanticProviderWire.summarize
    assert_complete_summary_http=wire.SemanticProviderWire.assert_complete_summary_http

    async def test_disabled_memory_real_sdk_checkpoint_and_next_request(self):
        self.cfg.update(memory=False,memorySummary=False)
        self.populate()
        await self.summarize()
        self.assert_complete_summary_http()
        db=self.store.db
        self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'retired')
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
        self.assertEqual(sum(result['retired']for _,result in self.callbacks),1)
        from readonly_journal import ReadonlyJournal
        journal=ReadonlyJournal(db,wire.SCOPE)
        import json
        final=journal.last.wire
        serialized=json.dumps(final,ensure_ascii=False)
        self.assertIn(wire.SUMMARY,serialized)
        self.assertIn(self.system,final);self.assertIn(self.new,final)
        for original in self.originals+self.events+[self.image]:self.assertNotIn(original,serialized)
        await self.provider.text_chat(contexts=final,prompt=None,func_tool=None)
        self.assertEqual(self.requests[1]['messages'],final)

if __name__=='__main__':unittest.main()
