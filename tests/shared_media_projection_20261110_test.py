"""Event-bound media retention and exact submitted shared-wire continuity."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'adapters/astrbot'), str(ROOT / 'tests')]
from behavior import BehaviorStore
from context_protocol import event_message, model_json, source
from readonly_journal import ReadonlyJournal
from unified_context_20261108_test import request
import message_provenance as provenance
import shared_context as shared


def image(value='same-original'):
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + value}}


def message(scope, event, parts=None):
    return event_message(
        {'scope': scope, 'event_id': event, 'sender_id': '12345678', 'at': 123},
        [{'type': 'text', 'text': 'real event'}, *(parts if parts is not None else [image()])],
    )


def images(messages):
    return [p for m in messages if isinstance(m.get('content'), list)
            for p in m['content'] if p.get('type') == 'image_url']


class SharedMedia(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'bot.sqlite'
        self.store = BehaviorStore(self.path)
        self.db = self.store.db
        self.journal = ReadonlyJournal(self.db, shared.JOURNAL_SCOPE)
        self.scope = 'private:12345678'

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def commit(self, current, additions=None, tools=None, previous=None):
        origin = source(current)
        native = request(current) + (tools or [])
        composed, seen = shared.compose(
            self.journal, native, origin['scope'], origin['event_id'],
            additions, previous,
        )
        prepared = self.journal.prepare(composed, 'fixed', strict=True)
        self.assertTrue(self.journal.stage_candidate(prepared))
        self.assertTrue(self.journal.commit(prepared))
        return seen

    def test_same_image_in_distinct_real_events_and_scopes_is_preserved(self):
        previous = []
        for scope, event in [(self.scope, 'a'), (self.scope, 'b'), ('group:55555', 'a')]:
            current = message(scope, event)
            self.commit(current, [current])
            self.assertEqual(self.journal.last.wire[:len(previous)], previous)
            previous = copy.deepcopy(self.journal.last.wire)
        self.assertEqual(len(images(self.journal.last.wire)), 3)
        self.assertEqual(len({(o['scope'], o['event_id']) for m in self.journal.last.base
                              if (o := source(m))}), 3)
        restored = ReadonlyJournal(self.db, shared.JOURNAL_SCOPE)
        self.assertEqual(model_json(restored.last.wire), model_json(previous))

    def test_neighbour_submitted_event_is_not_readded_or_reencoded(self):
        first = message(self.scope, 'a')
        neighbour = message(self.scope, 'b')
        provenance.bind_model_input(self.db, shared.JOURNAL_SCOPE, self.scope,
                                    'b', '12345678', neighbour)
        self.db.commit()
        self.commit(first, [first, neighbour])
        old = model_json(self.journal.last.wire)
        # SDK bookkeeping is disposable after another Work submitted this event.
        sdk = copy.deepcopy(neighbour)
        sdk['content'][-1]['image_url']['id'] = None
        self.commit(sdk)
        self.assertEqual(model_json(self.journal.last.wire), old)
        self.assertEqual(len(images(self.journal.last.wire)), 2)

    def test_tool_continuation_preserves_old_wire_and_its_new_image(self):
        current = message(self.scope, 'a')
        self.commit(current, [current])
        old = copy.deepcopy(self.journal.last.wire)
        tools = [
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call-1', 'type': 'function',
                 'function': {'name': 'lookup', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': [image('tool-image')]},
        ]
        seen = self.commit(current, tools=tools)
        self.assertEqual(self.journal.last.wire[:len(old)], old)
        self.assertEqual(self.journal.last.base[-2:], tools)
        before = model_json(self.journal.last.wire)
        self.commit(current, tools=tools, previous=seen)
        self.assertEqual(model_json(self.journal.last.wire), before)
        self.assertEqual(len(images(self.journal.last.wire)), 2)

    def test_first_sdk_input_keeps_multiple_images_and_quote_order_once(self):
        parts = [image('quoted'), {'type': 'text', 'text': '[quoted image]'}, image('new')]
        current = message(self.scope, 'a', parts)
        before = copy.deepcopy(current)
        self.commit(current, [current])
        self.assertEqual(self.journal.last.base[-1], before)
        previous = model_json(self.journal.last.wire)
        self.commit(current)
        self.assertEqual(model_json(self.journal.last.wire), previous)
        self.assertEqual(current, before)
        self.assertEqual(images(self.journal.last.wire), [parts[0], parts[2]])


if __name__ == '__main__':
    unittest.main()
