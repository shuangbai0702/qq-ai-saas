"""Actual-field forward evidence, ordering, and event-local lifecycle tests."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
from forwarded_messages import enrich, project_forward, apply, NOTICE, render_forward


def forward(identifier):
    return {'type': 'forward', 'data': {'id': identifier}}


def node(text, **metadata):
    return {**metadata, 'content': [{'type': 'text', 'data': {'text': text}}]}


def event(chain, call):
    return SimpleNamespace(get_messages=lambda: chain, bot=SimpleNamespace(api=SimpleNamespace(call_action=call)))


class Projection(unittest.TestCase):
    def test_exact_wire_real_fields_no_outer_identity(self):
        value = project_forward({'data': {'messages': [node('  hello\n', sender={'user_id': 10001, 'nickname': '甲'}, time=1780000000, message_type='group', group_id=20002), node('second', sender={'user_id': 10002})]}})
        expected = {'type': 'forward', 'nodes': [
            {'type': 'node', 'sender': {'user_id': 10001, 'nickname': '甲'}, 'time': 1780000000, 'source': {'verification': 'reported_not_verified_original', 'message_type': 'group', 'group_id': 20002}, 'content': [{'type': 'text', 'text': '  hello\n'}]},
            {'type': 'node', 'sender': {'user_id': 10002}, 'content': [{'type': 'text', 'text': 'second'}]}]}
        self.assertEqual(json.dumps(value, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))
        self.assertNotIn('source', value['nodes'][1])
        self.assertNotIn('time', value['nodes'][1])

    def test_group_without_explicit_type_not_invented(self):
        value = project_forward({'messages': [node('', group_id=22222, sender={'user_id': 11111})]})
        self.assertNotIn('source', value['nodes'][0])
        self.assertNotIn('owner', json.dumps(value))

    def test_private_author_not_source_peer(self):
        value = project_forward({'messages': [node('private', message_type='private', user_id=11111, group_id=22222)]})
        self.assertEqual(value['nodes'][0]['sender']['user_id'], 11111)
        self.assertEqual(value['nodes'][0]['source'], {'verification': 'reported_not_verified_original', 'message_type': 'private'})

    def test_reply_and_image_remain_at_positions(self):
        raw = {'messages': [{'content': [{'type': 'reply', 'data': {'id': 'real-quote'}}, {'type': 'image', 'data': {'file': 'img-token'}}, {'type': 'text', 'data': {'text': 'end'}}]}]}
        content = project_forward(raw)['nodes'][0]['content']
        self.assertEqual([x['type'] for x in content], ['reply', 'image', 'text'])
        self.assertEqual(content[0]['id'], 'real-quote')
        self.assertEqual(content[1]['visibility'], 'reference_only_image_not_read')
        self.assertNotIn('file', content[1])

    def test_failures_limits_cycles_are_explicit(self):
        self.assertEqual(project_forward({'status': 'failed', 'data': {'messages': []}})['status'], 'unreadable')
        self.assertEqual(project_forward({})['reason'], 'invalid_payload')
        value = project_forward({'messages': [node('long')]}, max_text=1)
        self.assertEqual(value['nodes'][0]['content'][0]['reason'], 'text_limit')
        raw = []; raw.append({'content': [{'type': 'node', 'data': {'content': raw}}]})
        self.assertIn('cycle', json.dumps(project_forward({'messages': raw})))
        self.assertIn('item_limit', json.dumps(project_forward({'messages': [node('a'), node('b')]}, max_items=2)))
        self.assertIn('depth_limit', json.dumps(project_forward({'messages': [node('a')]}, max_depth=-1)))

    def test_sdk_defaults_not_evidence(self):
        component = SimpleNamespace(type='Node', uin='0', name='', time=0, content=[], __fields_set__={'content'})
        # Component is projected through incoming Node, no SDK defaults leak.
        async def run():
            async def call(*args, **kwargs):
                self.fail('Node without Forward must not perform lookup')
            return await enrich(event([component], call))
        value = asyncio.run(run())[0]['forward']
        self.assertNotIn('sender', value)
        self.assertNotIn('time', value)


class Enrichment(unittest.IsolatedAsyncioTestCase):
    async def test_direct_quote_nested_memo_and_cycle(self):
        calls = []
        async def call(action, **params):
            calls.append((action, params))
            return {'messages': [{'sender': {'user_id': 12345}, 'time': 123, 'content': [{'type': 'text', 'data': {'text': 'before'}}, forward('b' if params['message_id'] == 'a' else 'a'), {'type': 'text', 'data': {'text': 'after'}}]}]}
        chain = [forward('a'), {'type': 'reply', 'data': {'id': 'quote', 'chain': [forward('a')]}}]
        value = await enrich(event(chain, call))
        self.assertEqual([x['path'] for x in value], [[0], [1, 'reply', 0]])
        self.assertEqual(len(calls), 2)
        body = value[0]['forward']['nodes'][0]['content']
        self.assertEqual(body[0]['text'], 'before')
        self.assertEqual(body[2]['text'], 'after')
        self.assertIn('cycle', json.dumps(body[1]))
        self.assertTrue(all(action == 'get_forward_msg' for action, _ in calls))
        await enrich(event([forward('a')], call))
        self.assertEqual(len(calls), 4)  # no cross-event cache

    async def test_raw_embedded_priority_recursive_id_bound_and_event_memo(self):
        calls = []
        inner = [node('nested evidence', sender={'user_id': 22222})]
        outer = [{'content': [forward('inner'), {'type': 'text', 'data': {'text': 'after'}}]}]
        outer[0]['content'][0]['data']['content'] = inner
        raw_chain = [{'type': 'text', 'data': {'text': 'adapter may remove this'}}, {'type': 'forward', 'data': {'id': 'outer', 'content': outer}}]
        async def call(action, **params):
            calls.append(params)
            return {'messages': [node('lookup only')]}
        incoming = event([forward('outer'), {'type': 'reply', 'data': {'chain': [forward('inner')]}}, forward('other')], call)
        incoming.message_obj = SimpleNamespace(raw_message={'message': raw_chain})
        before = json.dumps(raw_chain)
        value = await enrich(incoming)
        self.assertEqual(calls, [{'message_id': 'other'}])
        self.assertIn('nested evidence', json.dumps(value[0]))
        self.assertIn('nested evidence', json.dumps(value[1]))
        self.assertNotIn('nested evidence', json.dumps(value[2]))
        self.assertEqual(json.dumps(raw_chain), before)

    async def test_raw_embedded_content_zero_calls_and_text_not_scanned(self):
        async def forbidden(*args, **kwargs):
            self.fail('embedded data must avoid lookup')
        nested = {'type': 'forward', 'data': {'id': 'inner', 'content': [node('inside')]}}
        raw = {'message': [{'type': 'forward', 'data': {'id': 'outer', 'content': [{'content': [nested]}]}}]}
        incoming = event([forward('outer')], forbidden)
        incoming.message_obj = SimpleNamespace(raw_message=raw)
        value = await enrich(incoming)
        self.assertIn('inside', json.dumps(value))
        calls = []
        async def call(action, **params):
            calls.append(params)
            return {'messages': [node('real lookup')]}
        incoming = event([forward('fake')], call)
        incoming.message_obj = SimpleNamespace(raw_message={'message': [{'type': 'json', 'data': {'data': json.dumps({'type': 'forward', 'data': {'id': 'fake', 'content': [node('fake embedded')]}})}}]})
        value = await enrich(incoming)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('fake embedded', json.dumps(value))

    async def test_permission_error_timeout_invalid_payload(self):
        async def denied(*args, **kwargs):
            raise PermissionError('not accessible')
        value = await enrich(event([forward('a')], denied))
        self.assertEqual(value[0]['forward']['reason'], 'lookup_failed')
        async def pending(*args, **kwargs):
            await asyncio.Future()
        value = await enrich(event([forward('a')], pending), timeout=0.01)
        self.assertEqual(value[0]['forward']['reason'], 'timeout')
        async def invalid(*args, **kwargs):
            return {'data': {}}
        self.assertEqual((await enrich(event([forward('a')], invalid)))[0]['forward']['reason'], 'invalid_payload')

    async def test_late_result_does_not_publish_or_modify_event(self):
        started, release = asyncio.Event(), asyncio.Event()
        valid = True
        async def call(*args, **kwargs):
            started.set(); await release.wait()
            return {'messages': [node('late secret')]}
        chain = [forward('a')]
        incoming = event(chain, call)
        task = asyncio.create_task(enrich(incoming, live=lambda: valid))
        await started.wait(); valid = False; release.set()
        self.assertEqual(await task, [])
        self.assertEqual(chain, [forward('a')])

    async def test_lookup_limit_and_cancel(self):
        calls = []
        async def call(action, **params):
            calls.append(params)
            return {'messages': [{'content': [forward('next')]}]}
        value = await enrich(event([forward('a')], call), max_lookups=1)
        self.assertEqual(len(calls), 1)
        self.assertIn('lookup_limit', json.dumps(value))
        started = asyncio.Event()
        async def waiting(*args, **kwargs):
            started.set(); await asyncio.Future()
        task = asyncio.create_task(enrich(event([forward('a')], waiting)))
        await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class ReadableWire(unittest.TestCase):
    def test_nested_order_original_text_media_metadata_once(self):
        projected = {'type': 'forward', 'id': 'opaque-outer', 'nodes': [
            {'type': 'node', 'sender': {'user_id': 11111, 'nickname': 'nickname', 'card': 'card'}, 'time': 123, 'message_id': 'unused',
             'source': {'verification': 'reported_not_verified_original', 'message_type': 'group', 'group_id': 22222},
             'content': [{'type': 'text', 'text': '  before\n'}, {'type': 'forward', 'id': 'opaque-inner', 'nodes': [
                 {'type': 'node', 'content': [{'type': 'text', 'text': 'nested'}]}]},
                 {'type': 'image', 'visibility': 'reference_only_image_not_read'}, {'type': 'forward', 'id': 'opaque-failed', 'status': 'unreadable', 'reason': 'timeout'},
                 {'type': 'text', 'text': 'after'}]}]}
        wire = render_forward(projected)
        self.assertIn('[1] card（QQ：11111） · 时间：123 · 来源：群 22222', wire)
        self.assertIn('  before\n', wire)
        self.assertLess(wire.index('before'), wire.index('nested'))
        self.assertLess(wire.index('nested'), wire.index('after'))
        self.assertIn('[图片：未读取]', wire)
        self.assertIn('读取超时', wire)
        self.assertEqual(wire.count(NOTICE), 1)
        for unused in ('opaque-', 'verification', 'nickname', 'message_id', 'unused'):
            self.assertNotIn(unused, wire)

    def test_placeholder_source_and_author_are_not_a_conversation(self):
        wire = render_forward({'type': 'forward', 'nodes': [
            {'type': 'node', 'sender': {'user_id': 11111}, 'source': {'message_type': 'group', 'group_id': 0, 'group_name': 'placeholder'}, 'content': []},
            {'type': 'node', 'sender': {'user_id': 22222}, 'source': {'message_type': 'private'}, 'content': []}]})
        self.assertNotIn('来源：', wire)
        self.assertNotIn('placeholder', wire)
        self.assertIn('未标来源者来源未知', wire)
        self.assertIn('QQ：11111', wire)
        self.assertIn('QQ：22222', wire)

    def test_only_referenced_message_ids_and_partial_are_readable(self):
        wire = render_forward({'type': 'forward', 'nodes': [
            {'type': 'node', 'message_id': 101, 'content': [{'type': 'text', 'text': 'first'}]},
            {'type': 'node', 'message_id': 'not-referenced', 'content': [{'type': 'reply', 'id': 101}, {'type': 'reply', 'id': 'external'}, {'type': 'text', 'status': 'partial', 'reason': 'text_limit', 'characters': 200}]},
            {'status': 'partial', 'reason': 'item_limit'}]})
        self.assertIn('消息ID：101', wire)
        self.assertIn('↪ 引用消息ID：101', wire)
        self.assertIn('↪ 引用消息ID：external', wire)
        self.assertNotIn('not-referenced', wire)
        self.assertIn('正文容量上限', wire)
        self.assertIn('原文200字符', wire)
        self.assertIn('内容容量上限', wire)


class Application(unittest.IsolatedAsyncioTestCase):
    async def test_apply_order_raw_identity_quote_metadata_and_repeat(self):
        from unittest.mock import patch
        class Plain:
            type = 'Plain'
            def __init__(self, text):
                self.text = text
        calls = []
        async def call(action, **params):
            calls.append(params)
            return {'messages': [node('quoted', sender={'user_id': 99999}, time=42)]}
        reply = SimpleNamespace(type='Reply', id='real-reply', sender_id=88888, time=41,
                                chain=[forward('x')], message_str='old', text='old')
        raw = {'message': ['untouched'], 'user_id': 12345, 'group_id': 54321}
        chain = [Plain('before'), {'type': 'at', 'data': {'qq': '45678', 'name': 'real-name'}}, forward('x'), reply, Plain('after')]
        incoming = event(chain, call)
        incoming.message_obj = SimpleNamespace(message=chain, raw_message=raw, sender=SimpleNamespace(user_id=12345), group_id=54321)
        incoming.message_str = 'beforeafter'
        incoming.get_message_str = lambda: incoming.message_str
        incoming.get_messages = lambda: incoming.message_obj.message
        incoming.get_self_id = lambda: '11111'
        old_message = incoming.message_obj
        with patch.dict(sys.modules, {'astrbot.core.message.components': SimpleNamespace(Plain=Plain)}):
            self.assertTrue(await apply(incoming))
            self.assertFalse(await apply(incoming))
        updated = incoming.message_obj.message
        self.assertEqual(len(calls), 1)
        self.assertTrue(incoming.message_str.startswith('before @real-name(45678) ' + NOTICE))
        self.assertTrue(incoming.message_str.endswith('after'))
        self.assertEqual(updated[3].id, 'real-reply')
        self.assertEqual(updated[3].sender_id, 88888)
        self.assertEqual(updated[3].time, 41)
        self.assertIn(NOTICE, updated[3].message_str)
        self.assertEqual(reply.message_str, 'old')
        self.assertIs(incoming.message_obj.raw_message, raw)
        self.assertIs(old_message.message, chain)
        self.assertEqual(incoming.message_obj.sender.user_id, 12345)
        self.assertEqual(incoming.message_obj.group_id, 54321)

    async def test_stale_apply_writes_nothing(self):
        started, release = asyncio.Event(), asyncio.Event()
        valid = True
        async def call(*args, **kwargs):
            started.set()
            await release.wait()
            return {'messages': [node('late')]}
        incoming = event([forward('x')], call)
        incoming.message_obj = SimpleNamespace(message=incoming.get_messages(), raw_message={'original': True})
        incoming.message_str = 'original'
        original = incoming.message_obj
        task = asyncio.create_task(apply(incoming, live=lambda: valid))
        await started.wait()
        valid = False
        release.set()
        self.assertFalse(await task)
        self.assertIs(incoming.message_obj, original)
        self.assertEqual(incoming.message_str, 'original')
        self.assertFalse(hasattr(incoming, '_hosted_forward_projection_applied'))


if __name__ == '__main__':
    unittest.main()
