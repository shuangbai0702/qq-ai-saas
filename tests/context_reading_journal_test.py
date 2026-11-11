"""B is a future wire view: persisted A and possibly submitted tails stay exact."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
from context_protocol import READONLY_CURRENT, SOURCE, model_json
from context_reading import PROTOCOL_MESSAGE, decode_readonly, extract_pool
from readonly_journal import ReadonlyJournal,reading_pool


def packet(state):
    return {'role': 'user', 'content': READONLY_CURRENT + '\n' + model_json(state)}


def restored_state(wire):
    pool = extract_pool(wire)
    state = None
    for message in wire:
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, str) and content.startswith(READONLY_CURRENT + '\n'):
            state = decode_readonly(state, json.loads(content.split('\n', 1)[1]), pool)
    return state


def last_readonly(wire):
    return next(json.loads(m['content'].split('\n', 1)[1]) for m in reversed(wire)
                if m.get('role') == 'user' and isinstance(m.get('content'), str)
                and m['content'].startswith(READONLY_CURRENT + '\n'))


class ReadingJournalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.env = patch.dict(os.environ, {'FISHBOT_CONTEXT_READING_B': '0'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.native = [
            {'role': 'system', 'content': 'fixed trusted System'},
            {'role': 'user', 'content': SOURCE + model_json({'scope': 'private:12345', 'event_id': '1', 'sender_id': '12345'}) + '\nfirst real input'},
        ]
        self.state = {
            'social': {'meme_candidates': [
                {'id': 'm1', 'meaning': 'first exact meaning', 'score': 0.8},
                {'id': 'm2', 'meaning': 'second exact meaning', 'score': 0.4},
            ], 'sampledAt': '2026-11-11T10:01:02.123+08:00'},
            'autonomous_experience': {'sampledAt': 1794362462.125, 'actions': [
                {'id': 'a1', 'status': 'partial', 'effects': [{'status': 'unknown', 'receipt': None}]},
                {'id': 'a2', 'status': 'cancelled', 'confirmed': False},
            ]},
        }
        self.journal = ReadonlyJournal(self.db, 'private:12345')
        self.first = self.journal.prepare(self.native + [packet(self.state)], 'same', now=1)
        self.assertTrue(self.journal.commit(self.first))
        self.a_wire = copy.deepcopy(self.first.wire)
        self.assertNotIn(PROTOCOL_MESSAGE, self.a_wire)
        os.environ['FISHBOT_CONTEXT_READING_B'] = '1'
        self.journal = ReadonlyJournal(self.db, 'private:12345')
        self.assertEqual(self.journal.last.wire, self.a_wire)
        self.clock = 1

    def append(self, state, tools=False):
        old = copy.deepcopy(self.journal.last.wire)
        self.clock += 1
        if tools:
            call = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]}
            result = {'role': 'tool', 'tool_call_id': 'call1', 'content': '{"status":"unknown"}'}
            self.native.extend([call, result])
        else:
            self.native.append({'role': 'assistant', 'content': 'previous model draft'})
            self.native.append({'role': 'user', 'content': SOURCE + model_json({'scope': 'private:12345', 'event_id': str(self.clock), 'sender_id': '12345'}) + '\nnext real input'})
        candidate = self.journal.prepare(self.native + [packet(state)], 'same', now=self.clock)
        self.assertEqual(candidate.wire[:len(old)], old)
        self.assertEqual(restored_state(candidate.wire), candidate.state)
        self.assertEqual([m for m in candidate.wire if m['role'] == 'system'], [self.native[0]])
        return candidate

    def test_sqlite_a_to_b_preserves_prefix_and_tool_adjacency(self):
        state = copy.deepcopy(self.state)
        state['social']['meme_candidates'][0]['meaning'] = 'revised meaning'
        candidate = self.append(state, tools=True)
        self.assertIn(PROTOCOL_MESSAGE, candidate.wire)
        call_index = next(i for i, m in enumerate(candidate.wire) if m.get('tool_calls'))
        self.assertEqual(candidate.wire[call_index + 1]['tool_call_id'], 'call1')
        self.assertTrue(self.journal.commit(candidate))
        recovered = ReadonlyJournal(self.db, 'private:12345')
        self.assertIsNone(recovered.recovery_error)
        self.assertEqual(recovered.last.wire, candidate.wire)
        self.assertEqual(restored_state(recovered.last.wire), candidate.state)

    def test_candidates_sort_revision_remove_readd_and_definition_once(self):
        state = copy.deepcopy(self.state)
        variants = []
        state['social']['meme_candidates'].reverse()
        variants.append(copy.deepcopy(state))
        state['social']['meme_candidates'][0]['meaning'] = 'same ID new meaning'
        variants.append(copy.deepcopy(state))
        saved = state['social']['meme_candidates'].pop(0)
        variants.append(copy.deepcopy(state))
        state['social']['meme_candidates'].append(saved)
        variants.append(copy.deepcopy(state))
        for value in variants:
            candidate = self.append(value)
            self.assertEqual(restored_state(candidate.wire)['social']['meme_candidates'], value['social']['meme_candidates'])
            self.assertTrue(self.journal.commit(candidate))
            self.journal = ReadonlyJournal(self.db, 'private:12345')
        self.assertEqual(self.journal.last.wire.count(PROTOCOL_MESSAGE), 1)

    def test_actions_order_and_late_confirmation_preserve_exact_evidence_and_samples(self):
        state = copy.deepcopy(self.state)
        state['autonomous_experience']['actions'].reverse()
        candidate = self.append(state)
        self.assertEqual(last_readonly(candidate.wire)['actionOrder'], ['a2', 'a1'])
        self.assertTrue(self.journal.commit(candidate))
        state['autonomous_experience']['actions'][1]['effects'][0] = {
            'status': 'confirmed', 'receipt': {'message_id': 'late-ack', 'at': '2026-11-11T10:02:00+08:00'},
        }
        state['autonomous_experience']['actions'][1]['status'] = 'partial'
        state['autonomous_experience']['sampledAt'] = '2026-11-11T10:02:03.000001+08:00'
        candidate = self.append(state)
        self.assertEqual(last_readonly(candidate.wire)['observed_sampled_at'], state['autonomous_experience']['sampledAt'])
        restored = restored_state(candidate.wire)
        self.assertEqual(restored, state)
        self.assertEqual(restored['autonomous_experience']['actions'][0]['status'], 'cancelled')
        self.assertEqual(restored['social']['sampledAt'], self.state['social']['sampledAt'])

    def test_switch_off_keeps_b_history_and_appends_readable_a(self):
        state = copy.deepcopy(self.state)
        state['social']['meme_candidates'].reverse()
        candidate = self.append(state)
        self.assertTrue(self.journal.commit(candidate))
        self.assertIn(PROTOCOL_MESSAGE, candidate.wire)
        os.environ['FISHBOT_CONTEXT_READING_B'] = '0'
        state['social']['meme_candidates'][0]['meaning'] = 'off switch exact meaning'
        candidate = self.append(state)
        self.assertEqual(candidate.wire.count(PROTOCOL_MESSAGE), 1)
        self.assertTrue(self.journal.commit(candidate))
        self.assertEqual(ReadonlyJournal(self.db, 'private:12345').last.wire, candidate.wire)

    def test_tenants_have_independent_definition_pools(self):
        state = copy.deepcopy(self.state)
        state['social']['meme_candidates'][0]['meaning'] = 'tenant one private definition'
        candidate = self.append(state)
        self.assertTrue(self.journal.commit(candidate))
        other_db = sqlite3.connect(':memory:')
        self.addCleanup(other_db.close)
        other = ReadonlyJournal(other_db, 'private:12345')
        other_state = copy.deepcopy(self.state)
        other_state['social']['meme_candidates'][0]['meaning'] = 'tenant two private definition'
        fresh = other.prepare(self.native[:2] + [packet(other_state)], 'same', now=2)
        self.assertTrue(other.commit(fresh))
        self.assertEqual(restored_state(fresh.wire), fresh.state)
        self.assertNotIn('tenant one private definition', model_json(fresh.wire))
        self.assertNotIn('tenant two private definition', model_json(candidate.wire))

    def test_stage_crash_unknown_submission_restores_exact_b_candidate(self):
        state = copy.deepcopy(self.state)
        state['social']['meme_candidates'][0]['meaning'] = 'possibly submitted exact revision'
        candidate = self.append(state)
        self.assertTrue(self.journal.stage_candidate(candidate))
        crashed = ReadonlyJournal(self.db, 'private:12345')
        self.assertEqual(crashed.last.wire, candidate.wire)
        self.assertEqual(restored_state(crashed.last.wire), candidate.state)
        self.assertTrue(self.journal.retain_submitted(candidate, 'unknown'))
        recovered = ReadonlyJournal(self.db, 'private:12345')
        self.assertEqual(recovered.last.wire, candidate.wire)
        self.assertEqual(recovered.last.wire[:len(self.a_wire)], self.a_wire)
        self.assertEqual(recovered.last.wire.count(PROTOCOL_MESSAGE), 1)

    def test_stage_refusal_keeps_previous_sqlite_a_and_does_not_publish_pool(self):
        state = copy.deepcopy(self.state)
        state['social']['meme_candidates'][0]['meaning'] = 'never submitted definition'
        candidate = self.append(state)
        with patch.object(self.journal, '_persist', return_value=False):
            self.assertFalse(self.journal.stage_candidate(candidate))
        recovered = ReadonlyJournal(self.db, 'private:12345')
        self.assertEqual(recovered.last.wire, self.a_wire)
        self.assertEqual(extract_pool(recovered.last.wire), {})
        self.assertNotIn('never submitted definition', model_json(recovered.last.wire))

    def test_event_protocol_inheritance_and_identity_never_become_qq_input(self):
        from context_transport import EVENT_CONTEXT_CURRENT
        def event(owner,clock):
            return {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({
                'authority':{'sender_id':'12345','owner':owner,'current_time':clock},
                'policy':{'explanation':'fixed trustworthy platform policy '*80},
                'currentGuidance':'reply to actual sourced user', 'meaning':'platform event data'})}
        journal=ReadonlyJournal()
        first=journal.prepare(self.native+[event(False,1)],'same',strict=True,compact_policy=True)
        self.assertTrue(journal.commit(first))
        second=journal.prepare(self.native+[event(False,2)],'same',strict=True,compact_policy=True)
        delta=json.loads(second.wire[-1]['content'].split('\n',1)[1])
        self.assertIn('eventChanges',delta)
        self.assertNotIn('meaning',delta)
        self.assertEqual(second.wire.count(PROTOCOL_MESSAGE),1)
        self.assertEqual(second.wire[:len(first.wire)],first.wire)
        from decision_history import apply_changes
        reconstructed=apply_changes(json.loads(first.authority['content'].split('\n',1)[1]),delta['eventChanges'])
        self.assertEqual(reconstructed,json.loads(second.authority['content'].split('\n',1)[1]))
        self.assertFalse(reconstructed['authority']['owner'])
        self.assertEqual(reading_pool(second.wire),{})
        with self.assertRaisesRegex(ValueError,'definition_or_baseline'):
            reading_pool([m for m in second.wire if m!=PROTOCOL_MESSAGE])
        with self.assertRaisesRegex(ValueError,'definition_or_baseline'):
            reading_pool([PROTOCOL_MESSAGE,second.wire[-1]])

    def test_fresh_b_baseline_is_self_contained_and_requires_definition_on_recovery(self):
        fresh=ReadonlyJournal(self.db,'other-scope')
        candidate=fresh.prepare(self.native+[packet(self.state)],'same')
        self.assertEqual(restored_state(candidate.wire),self.state)
        self.assertTrue(fresh.commit(candidate))
        recovered=ReadonlyJournal(self.db,'other-scope')
        self.assertEqual(recovered.last.wire,candidate.wire)
        malformed=copy.deepcopy(candidate)
        malformed.wire.remove(PROTOCOL_MESSAGE)
        self.assertTrue(fresh._persist(malformed))
        rejected=ReadonlyJournal(self.db,'other-scope')
        self.assertEqual(rejected.recovery_error,'journal_reading_definition_invalid')
        self.assertIsNone(rejected.last)

    def test_legacy_hot_basis_repair_keeps_fixed_foundation_separate(self):
        from context_foundation import FOUNDATION_CURRENT
        foundation={'conversation_summaries':[],'core_note':{'text':'fixed exact material'}}
        self.state['memory_foundation']=foundation
        # Model-visible fixed foundation survived a historical retirement,
        # whose durable full semantic state omitted the first hot packet.
        self.journal.last.state=copy.deepcopy(self.state)
        self.journal.last.wire=self.native+[{'role':'user','content':FOUNDATION_CURRENT+'\n'+model_json(foundation)}]
        self.assertTrue(self.journal._persist(self.journal.last))
        self.journal=ReadonlyJournal(self.db,'private:12345')
        old=copy.deepcopy(self.journal.last.wire)
        candidate=self.journal.prepare(self.native+[packet(self.state)],'same')
        self.assertEqual(candidate.wire[:len(old)],old)
        expected={k:v for k,v in self.state.items() if k!='memory_foundation'}
        self.assertEqual(restored_state(candidate.wire),expected)
        self.assertEqual(candidate.state,self.state)
        self.assertEqual(candidate.wire.count(PROTOCOL_MESSAGE),1)
        self.assertTrue(self.journal.commit(candidate))
        self.assertIsNone(ReadonlyJournal(self.db,'private:12345').recovery_error)


if __name__ == '__main__':
    unittest.main()
