"""Deterministic codec invariants; synthetic content only."""
import copy
import json
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
import context_reading as codec
from decision_history import apply_changes, changes


class ReadingCodec(unittest.TestCase):
    def state(self):
        return {'social': {'meme_candidates': [{'id': 'm', 'source': 'synthetic', 'avoid': ['x'], 'unknown': None}]},
                'autonomous_experience': {'actions': [{'id': 'a', 'status': 'pending', 'unknown': {'x': 1}}, {'id': 'b'}], 'sampledAt': '2026-01-01T01:02:03.123456Z'},
                'unknown': {'value': None}}

    def wire(self, packet):
        return [{'role': 'user', 'content': codec.READONLY_MARKER + '\n' + json.dumps(packet)}]

    def roundtrip(self, before, ops, pool=None):
        pool = {} if pool is None else pool
        state = apply_changes(before, ops)
        inputs = copy.deepcopy((before, state, ops, pool))
        packet = codec.encode_readonly(before, state, ops, pool)
        self.assertEqual(codec.decode_readonly(before, packet, pool), state)
        self.assertEqual((before, state, ops, pool), inputs)
        return packet

    def test_root_and_a_compatibility(self):
        state = self.state(); packet = self.roundtrip({}, [['set', [], state]])
        root = packet['readonlyChanges'][0][2]
        self.assertEqual(root['autonomous_experience'], state['autonomous_experience'])
        self.assertTrue(codec.has_b_payload(packet))
        self.assertEqual(codec.decode_readonly({}, state, {}), state)
        self.assertEqual(codec.decode_readonly({}, {'readonlyChanges': [['set', [], state]]}, {}), state)
        self.assertEqual(codec.PROTOCOL_MESSAGE['role'], 'user')

    def test_pool_first_a_to_b_definitions_duplicates_versions_and_no_mutation(self):
        before = self.state(); candidate = before['social']['meme_candidates'][0]
        rows = [candidate, candidate, {**candidate, 'avoid': ['new'], 'future': {'keep': True}}]
        packet = self.roundtrip(before, [['set', ['social', 'meme_candidates'], rows]])
        packed = packet['readonlyChanges'][0][2]
        self.assertEqual(len(packed['definition_updates']), 2)
        self.assertEqual(packed['active_order'][0], packed['active_order'][1])
        pool = codec.extract_pool(self.wire(packet))
        again = self.roundtrip(before, [['set', ['social', 'meme_candidates'], rows]], pool)
        self.assertEqual(again['readonlyChanges'][0][2]['definition_updates'], {})
        self.assertEqual(codec.extract_pool([]), {})
        fresh_scope = self.roundtrip(before, [['set', ['social', 'meme_candidates'], rows]], {})
        self.assertEqual(len(fresh_scope['readonlyChanges'][0][2]['definition_updates']), 2)
        with self.assertRaises(ValueError): codec.decode_readonly(before, again, {})

    def test_candidate_ancestor_child_remove_null_and_operation_order(self):
        old = self.state()
        cases = [
            [['set', ['social'], {'meme_candidates': [{'id': 'other'}], 'new': 1}]],
            [['set', ['social', 'meme_candidates', 0, 'id'], 'changed'], ['remove', ['social']]],
            [['remove', ['social', 'meme_candidates']], ['set', ['social', 'meme_candidates'], []]],
            [['set', ['social', 'meme_candidates'], None]],
            [['set', ['social'], None]],
            [['set', [], {'social': {'meme_candidates': []}}], ['set', ['social', 'meme_candidates'], [{'id': 'late'}]]],
            [['set', ['social', 'meme_candidates'], [{'id': 'early'}]], ['set', [], {'unknown': None}]],
        ]
        for ops in cases:
            with self.subTest(ops=ops): self.roundtrip(old, ops)

    def test_hash_collision_and_tampering_are_rejected(self):
        with patch.object(codec, '_key', return_value='v_collision'):
            with self.assertRaises(ValueError):
                self.roundtrip({}, [['set', [], {'social': {'meme_candidates': [{'id': 'x'}, {'id': 'y'}]}}]])
        packet = self.roundtrip({}, [['set', [], self.state()]])
        packed = packet['readonlyChanges'][0][2]['social']['meme_candidates']
        key = packed['active_order'][0]
        packed['definition_updates'][key]['id'] = 'tampered'
        with self.assertRaises(ValueError): codec.extract_pool(self.wire(packet))
        packed['definition_updates'] = {}; packed['active_order'] = ['v_unknown']
        with self.assertRaises(ValueError): codec.extract_pool(self.wire(packet))
        packed['format'] = 'candidate_pool_v2'
        with self.assertRaises(ValueError): codec.extract_pool(self.wire(packet))
        with self.assertRaises(ValueError): codec.decode_readonly({}, packet, {})

    def test_multiple_candidate_field_edits_define_only_final_versions(self):
        old=self.state()
        ops=[['set',['social','meme_candidates',0,'meaning'],'new meaning'],
             ['set',['social','meme_candidates',0,'avoid'],['new restriction']],
             ['set',['unknown','value'],'unrelated state']]
        packet=self.roundtrip(old,ops)
        candidates=[op for op in packet['readonlyChanges'] if op[1]==['social','meme_candidates']]
        self.assertEqual(len(candidates),1)
        definitions=list(candidates[0][2]['definition_updates'].values())
        self.assertEqual(len(definitions),1)
        self.assertEqual(definitions[0]['meaning'],'new meaning')
        self.assertEqual(definitions[0]['avoid'],['new restriction'])

    def test_pool_ignores_other_roles_and_unrelated_markers(self):
        packet = self.roundtrip({}, [['set', [], self.state()]])
        wire = self.wire(packet)
        wire[0]['role'] = 'assistant'
        self.assertEqual(codec.extract_pool(wire), {})
        wire[0]['role'] = 'user'; wire[0]['content'] = '[user quote]\n' + json.dumps(packet)
        self.assertEqual(codec.extract_pool(wire), {})

    def test_action_index_changes_order_and_member_boundary(self):
        old = self.state()
        ops = [['set', ['autonomous_experience', 'actions'], [{'id': 'b'}, {'id': 'c', 'status': 'unknown'}]]]
        packet = self.roundtrip(old, ops)
        self.assertEqual(packet['readonlyChanges'], [])
        self.assertEqual(packet['actionOrder'], ['b', 'c'])
        bad = copy.deepcopy(packet); bad.pop('actionOrder')
        with self.assertRaises(ValueError): codec.decode_readonly(old, bad, {})
        bad['actionOrder'] = ['b', 'b']
        with self.assertRaises(ValueError): codec.decode_readonly(old, bad, {})
        packet = self.roundtrip(old, [['set', ['autonomous_experience', 'actions', 0, 'status'], 'partial']])
        self.assertNotIn('actionOrder', packet)
        self.assertIn('actionChanges', packet)

    def test_invalid_action_ids_fall_back_to_raw(self):
        for rows in ([{'id': 'a'}, {'id': 'a'}], [{}], [{'id': None}], [{'id': 1}], ['x'], None):
            old = self.state()
            packet = self.roundtrip(old, [['set', ['autonomous_experience', 'actions'], rows]])
            self.assertNotIn('actionChanges', packet)
            old['autonomous_experience']['actions'] = rows
            packet = self.roundtrip(old, [['set', ['autonomous_experience', 'actions'], [{'id': 'valid'}]]])
            self.assertNotIn('actionChanges', packet)

    def test_parent_replacements_removals_and_sample_null_are_exact(self):
        old = self.state()
        packet = self.roundtrip(old, [['set', ['autonomous_experience', 'sampledAt'], None]])
        self.assertIn('observed_sampled_at', packet)
        self.assertIsNone(packet['observed_sampled_at'])
        for ops in (
            [['set', ['autonomous_experience'], {'actions': [{'id': 'new'}], 'sampledAt': 'exact', 'extra': None}]],
            [['set', ['autonomous_experience', 'sampledAt'], 'early'], ['set', ['autonomous_experience'], None]],
            [['set', ['autonomous_experience', 'actions', 0, 'status'], 'done'], ['remove', ['autonomous_experience']]],
            [['remove', ['autonomous_experience', 'sampledAt']]],
            [['set', ['autonomous_experience'], {'actions': []}], ['set', ['autonomous_experience', 'sampledAt'], 'late']],
        ):
            packet = self.roundtrip(old, ops)
            self.assertNotIn('actionChanges', packet)
            self.assertNotIn('observed_sampled_at', packet)

    def test_random_sequential_synthetic_roundtrips(self):
        rng = random.Random(42); state = self.state(); wire = []
        for _ in range(100):
            after = copy.deepcopy(state)
            after['social']['meme_candidates'] = [{'id': str(rng.randrange(4)), 'unknown': rng.choice([None, True, 1, 'x'])} for _ in range(rng.randrange(5))]
            rng.shuffle(after['autonomous_experience']['actions'])
            after['autonomous_experience']['sampledAt'] = rng.choice([None, 1, 'exact'])
            packet = self.roundtrip(state, changes(state, after), codec.extract_pool(wire))
            wire += self.wire(packet); state = after


if __name__ == '__main__':
    unittest.main()
