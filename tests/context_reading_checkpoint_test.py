"""B checkpoint wire fixture, not a model-backed 35-business-round E2E test.

The existing semantic-retirement fake permit isolates licensed baseline rebuild,
pool retirement and restart behavior. Real 35-round mint/retire coverage lives
in summary_material_test.py.
"""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'adapters/astrbot')]
import semantic_journal_retirement_test as retirement_fixture
from context_protocol import READONLY_CURRENT, model_json
from context_reading import PROTOCOL_MESSAGE, decode_readonly, extract_pool
from context_transport import EVENT_CONTEXT_CURRENT
from readonly_journal import ReadonlyJournal, reading_baseline, reading_pool


class ReadingCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'FISHBOT_CONTEXT_READING_B': '1'})
        self.env.start(); self.addCleanup(self.env.stop)
        retirement_fixture.SemanticJournalTests.setUp(self)

    def tearDown(self):
        retirement_fixture.SemanticJournalTests.tearDown(self)

    def test_licensed_fresh_root_drops_old_pool_keeps_live_work_and_recovers_event(self):
        old_state = {'social': {'meme_candidates': [{'id': 'same-id', 'meaning': 'retired definition only'}]}}
        self.p.state = copy.deepcopy(old_state)
        self.p.wire = reading_baseline(self.base + [{'role': 'user', 'content': READONLY_CURRENT + '\n' + model_json(old_state)}], old_state)
        self.p.authority = {'role': 'user', 'content': EVENT_CONTEXT_CURRENT + '\n' + model_json({
            'authority': {'event': 'old'}, 'policy': {'trusted': 'old'}, 'currentGuidance': 'retired event identity', 'meaning': 'event meaning'})}
        self.assertTrue(self.j._persist(self.p)); self.j.last = self.p
        old_pool = extract_pool(self.p.wire)
        self.assertEqual(len(old_pool), 1)
        live = {
            'social': {'meme_candidates': [{'id': 'same-id', 'meaning': 'fresh definition only', 'avoid': ['keep']}]},
            'autonomous_experience': {'sampledAt': 'exact sample', 'actions': [
                {'id': 'unfinished', 'status': 'partial', 'effects': [{'status': 'unknown', 'receipt': None}]},
                {'id': 'future-reminder', 'kind': 'reminder', 'due': 'later', 'status': 'pending'}]},
            'active_agreements': [{'text': 'keep this agreement', 'source': 'derived memory'}],
        }
        self.assertTrue(self.j.retire_semantic_batch(self.permit, self.authority, live))
        loaded = ReadonlyJournal(self.db, 'bot_shared')
        self.assertIsNone(loaded.recovery_error)
        self.assertEqual(loaded.last.state, live)
        wire = loaded.last.wire
        self.assertEqual(wire.count(PROTOCOL_MESSAGE), 1)
        self.assertNotIn('retired definition only', model_json(wire))
        self.assertNotIn('retired event identity', model_json(wire))
        new_pool = reading_pool(wire)
        self.assertEqual(len(new_pool), 1)
        self.assertTrue(set(old_pool).isdisjoint(new_pool))
        readonly = [json.loads(m['content'].split('\n', 1)[1]) for m in wire if isinstance(m.get('content'), str) and m['content'].startswith(READONLY_CURRENT + '\n')]
        self.assertEqual(len(readonly), 1)
        self.assertEqual(readonly[0]['readonlyChanges'][0][1], [])
        self.assertEqual(decode_readonly({}, readonly[0], {}), live)
        # The metadata placeholder is never a visible event baseline. The next
        # actual event must therefore append a complete contract, including policy.
        event = {'authority': {'event': 'fresh'}, 'policy': {'trusted': 'fresh'}, 'currentGuidance': 'current event identity', 'meaning': 'full event meaning'}
        event_message = {'role': 'user', 'content': EVENT_CONTEXT_CURRENT + '\n' + model_json(event)}
        candidate = loaded.prepare(loaded.last.base + [event_message, {'role': 'user', 'content': READONLY_CURRENT + '\n' + model_json(live)}], None, strict=True)
        self.assertIsNone(candidate.reconstruction_permit)
        self.assertEqual(candidate.wire[:len(wire)], wire)
        events = [json.loads(m['content'].split('\n', 1)[1]) for m in candidate.wire if isinstance(m.get('content'), str) and m['content'].startswith(EVENT_CONTEXT_CURRENT + '\n')]
        self.assertEqual(events, [event])
        self.assertNotIn('已完成语义检查点', model_json(candidate.wire))
        self.assertTrue(loaded.commit(candidate))
        self.assertEqual(ReadonlyJournal(self.db, 'bot_shared').last.wire, candidate.wire)


if __name__ == '__main__':
    unittest.main()
