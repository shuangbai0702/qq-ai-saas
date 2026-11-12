"""Whole-batch semantic cutover verified at the pinned SDK's actual HTTP boundary."""
import base64
import contextvars
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'references/astrbot'), str(ROOT / 'adapters/astrbot')]
SCOPE = 'private:12345678'
SUMMARY = '人物在海边画了一只红色小船；以后周末继续共同绘画。'


class SemanticProviderWire(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'ASTRBOT_ROOT': str(self.root)})
        self.environment.start()
        import runtime_bootstrap
        runtime_bootstrap.initialize_runtime_bootstrap()
        # Other SDK tests may already have imported the full real entities module.
        # Lazy loading is needed only when this test starts the SDK itself.
        if 'astrbot.core.provider.entities' not in sys.modules:
            from slim_bootstrap import install
            install()
        import httpx
        from PIL import Image
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
        from behavior import BehaviorStore

        # Real decodable PNG, rather than a data URL whose bytes are not an image.
        png = io.BytesIO()
        Image.new('RGB', (3, 2), (255, 20, 20)).save(png, format='PNG')
        self.image = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode()
        self.requests = []
        self.mode = 'stop'
        self.semantic_operations = False
        self.cfg = {'memory': True, 'memorySummary': True, 'memoryAutoCapture': True,
                    'selfDevelopment': True, 'naturalConversation': False}

        async def handler(request):
            self.assertEqual(request.url.host, 'fixture.invalid')
            self.assertEqual(request.url.path, '/v1/chat/completions')
            self.requests.append(json.loads(request.content))
            output = {'summary': SUMMARY}
            if self.semantic_operations and len(self.requests) == 1:
                # Generate decisions from the actual serialized SDK HTTP body,
                # rather than a pre-transport Python prompt captured by a stub.
                prompt = None
                for message in self.requests[-1]['messages']:
                    content = message.get('content')
                    texts = [content] if isinstance(content, str) else [
                        p.get('text', '') for p in content or [] if p.get('type') == 'text']
                    for text in texts:
                        try:
                            value = json.loads(text)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(value, dict) and 'existing_memory' in value:
                            prompt = value
                self.assertIsNotNone(prompt)
                existing = prompt['existing_memory']
                self.assertEqual(existing['facts'][0]['value'], '旧偏好水彩')
                self.assertEqual(existing['relations'][0]['impression'], '早期交流仍在互相了解')
                self.assertEqual(existing['relations'][0]['trust'], .1)
                self.assertEqual(len(prompt['turns']), 35)
                self.assertEqual(existing['writable'], {'facts': True, 'relations': True})
                output.update({
                    'fact_operations': [{'op': 'revise', 'scope': SCOPE, 'subject': '12345678',
                                         'name': '绘画偏好', 'expected': existing['facts'],
                                         'value': '更喜欢绘制海边红船', 'importance': .9}],
                    'relation_operations': [{'scope': SCOPE, 'subject': '12345678',
                                             'expected': existing['relations'][0],
                                             'affinity': 41, 'trust': .85,
                                             'impression': '共同绘画让交流更亲近可靠'}],
                })
            if self.mode=='network_error':
                return httpx.Response(503,json={'error':{'message':'fixture unavailable'}})
            if self.requests[-1].get('stream'):
                chunk={'id':'semantic-wire-fixture','object':'chat.completion.chunk','created':1,'model':'fixture',
                       'choices':[{'index':0,'delta':{'role':'assistant','content':json.dumps(output,ensure_ascii=False)},'finish_reason':None}]}
                terminal={'id':'semantic-wire-fixture','object':'chat.completion.chunk','created':1,'model':'fixture',
                          'choices':[{'index':0,'delta':{},'finish_reason':self.mode}]}
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(chunk)+'\n\ndata: '+json.dumps(terminal)+'\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={
                'id':'semantic-wire-fixture','model':'fixture',
                'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(output,ensure_ascii=False)},'finish_reason':self.mode}]})


        self.http_patch = patch.object(
            ProviderOpenAIOfficial, '_create_http_client',
            lambda provider, config: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.http_patch.start()
        self.provider = ProviderOpenAIOfficial({
            'id': 'semantic-wire-fixture', 'type': 'openai_chat_completion',
            'model': 'fixture', 'key': ['fixture'],
            'api_base': 'https://fixture.invalid/v1'}, {})
        self.store = BehaviorStore(self.root / 'behavior.sqlite')
        self.metrics = {}
        self.callbacks = []

        from semantic_runtime import finish_semantic_batches
        def checkpoint(scope):
            result = finish_semantic_batches(self.store, self.cfg, scope)
            self.callbacks.append((scope, result))
        self.store.reflection.checkpoint_callback = checkpoint

    async def test_network_failure_is_exactly_two_real_stream_requests(self):
        from memory_reflection import summary_model_output
        async def no_retry(provider,error,*args,**kwargs):raise error
        self.mode='network_error'
        with patch.object(type(self.provider),'_handle_api_error',no_retry):
            with self.assertRaises(Exception):
                await summary_model_output(self.provider,dict(prompt='fixture',contexts=[],func_tool=None,request_max_retries=0),lambda:True,{},800)
        self.assertEqual(len(self.requests),2)
        self.assertTrue(all(r['stream'] is True for r in self.requests))

    async def test_complete_json_without_terminal_reason_never_commits(self):
        self.populate()
        before=self.store.db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall()
        self.mode=None
        await self.summarize()
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall(),before)
        self.assertEqual(self.callbacks,[])

    async def asyncTearDown(self):
        await self.provider.client.close()
        self.store.close()
        self.http_patch.stop()
        self.environment.stop()
        self.temp.cleanup()

    def populate(self):
        from context_protocol import SOURCE, model_json
        from readonly_journal import Prepared, ReadonlyJournal
        import summary_material
        self.system = {'role': 'system', 'content': 'TENANT_SYSTEM_AUTHORITY_KEEP_EXACT'}
        self.new = {'role': 'user', 'content': SOURCE + model_json({
            'scope': SCOPE, 'sender_id': '12345678', 'event_id': 'NEW_CURRENT_EVENT'}) +
            '\nNEW_CURRENT_TEXT_MUST_SURVIVE'}
        self.native = [self.system]
        self.originals = []
        self.events = []
        for i in range(35):
            event = f'ORIGINAL_EVENT_{i:02d}_END'
            # Long, distinct beginnings and endings detect clipping of any round.
            text = f'RAW_TEXT_{i:02d}_BEGIN_' + ('完整原文和视觉交流' * 170) + f'_RAW_TEXT_{i:02d}_END'
            answer = f'RAW_CONFIRMED_REPLY_{i:02d}_END'
            parts = [{'type': 'text', 'text': SOURCE + model_json({
                'scope': SCOPE, 'sender_id': '12345678', 'event_id': event}) + '\n' + text}]
            if i == 17:
                parts.append({'type': 'image_url', 'image_url': {'url': self.image}})
            user = {'role': 'user', 'content': parts}
            assistant = {'role': 'assistant', 'content': answer}
            material = summary_material.snapshots([user, assistant], SCOPE)
            self.assertTrue(material[0]['complete'])
            self.store.reflection.record(SCOPE, '12345678', event, text, answer,
                                         now=100 + i, materials=material)
            self.native.extend([user, assistant])
            self.originals.extend([text, answer])
            self.events.append(event)
        self.native.append(self.new)
        self.journal = ReadonlyJournal(self.store.db, SCOPE)
        self.journal.serial = 1
        prepared = Prepared(1, None, copy.deepcopy(self.native), copy.deepcopy(self.native),
                            {}, 1, 135, 'initial')
        self.assertTrue(self.journal.commit(prepared))
        self.store.model_journals[SCOPE] = self.journal

        # Same evidence shape as semantic_service_inputs_test, with direct and
        # shared source bindings. These failed wake-ups never reached a model.
        self.control_events = ['FAILED_INTERNAL_CONTROL_01', 'FAILED_INTERNAL_CONTROL_02']
        self.control_texts = []
        self.control_bindings = []
        for event in self.control_events:
            question = SOURCE + model_json({
                'scope': SCOPE, 'sender_id': '12345678', 'event_id': event,
                'kind': 'platform_service_not_user'}) + '\n[平台内部主动时刻]_' + event
            binding = {'source_scope': SCOPE, 'event_id': event, 'sender_id': '12345678',
                       'outputs': [], 'settlement': {'status': 'error', 'attempted': 0,
                       'confirmed': 0, 'planned': 0, 'confirmed_messages': 0}}
            self.store.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',
                                  (SCOPE, '12345678', event, question, '', 110))
            self.store.db.execute('''INSERT INTO pipeline_events(
                scope,subject,event,status,source,attempted,confirmed,at)
                VALUES(?,?,?,'error','owner_contact',0,0,110)''', (SCOPE, '12345678', event))
            for scope, key in ((SCOPE, event), ('bot_shared', model_json([SCOPE, event]))):
                self.control_bindings.append((scope, key))
                self.store.db.execute('INSERT INTO memory_message_sources VALUES(?,?,?,?)',
                                      (scope, key, 'model', model_json(binding)))
                self.store.db.execute('INSERT INTO memory_message_sources VALUES(?,?,?,?)',
                                      (scope, key, 'incoming', '{}'))
            self.control_texts.append(question)
        self.store.db.commit()
        self.control_ledger = self.store.db.execute('SELECT * FROM pipeline_events ORDER BY event').fetchall()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0], 37)

    def assert_failed_controls_discarded(self):
        db = self.store.db
        for event in self.control_events:
            self.assertIsNone(db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',
                                         (SCOPE, event)).fetchone(),
                              (self.metrics, db.execute('SELECT * FROM memory_checkpoint_issues').fetchall()))
        for scope, event in self.control_bindings:
            self.assertIsNone(db.execute('SELECT 1 FROM memory_message_sources WHERE scope=? AND event=?',
                                         (scope, event)).fetchone())
        self.assertEqual(db.execute('SELECT * FROM pipeline_events ORDER BY event').fetchall(),
                         self.control_ledger)

    async def summarize(self):
        reflection = self.store.reflection
        reflection.binding = (
            self.cfg,
            self.provider, lambda: True, self.metrics,
            contextvars.ContextVar('semantic_provider_wire', default=None))
        self.assertTrue(reflection.retry())
        await reflection.task

    def assert_complete_summary_http(self,attempts=1):
        self.assertEqual(len(self.requests), attempts)
        messages = self.requests[0]['messages']
        text = '\n'.join(
            m['content'] if isinstance(m.get('content'), str) else
            '\n'.join(p.get('text', '') for p in m.get('content', []) if isinstance(p, dict))
            for m in messages)
        for original in self.originals + self.events:
            self.assertIn(original, text)
        wire = json.dumps(messages, ensure_ascii=False)
        for control in self.control_events + self.control_texts:
            self.assertNotIn(control, wire)
        images = [p['image_url']['url'] for m in messages
                  if isinstance(m.get('content'), list) for p in m['content']
                  if p.get('type') == 'image_url']
        self.assertEqual(images, [self.image])
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(images[0].split(',', 1)[1]))) as image:
            self.assertEqual(image.size, (3, 2))
            image.verify()

    async def test_summary_http_then_checkpoint_retires_originals_before_next_http(self):
        self.store.memory.remember(SCOPE, '12345678', '绘画偏好', '旧偏好水彩', 'old-explicit', 1)
        self.store.db.execute('''INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen,trust,impression)
                                VALUES(?,?,7,'平静',0,1,.1,'早期交流仍在互相了解')''', (SCOPE, '12345678'))
        # Existing scheduled work and its independent safety ledger must keep
        # exact persisted bytes across a real 35-round summary and retirement.
        db=self.store.db
        db.execute('INSERT INTO initiative_schedule VALUES(?,?,?,?,?)',(SCOPE,100,3600,7200,2000000000))
        db.execute('''INSERT INTO memory_agreements(id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated,handoff_id,task_revision,evidence)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   ('future-agreement',3,'未来提醒','保持原已授权详情','12345678','["12345678"]',SCOPE,'prior-authorized-event','["private:12345678"]','accepted','2030-01-01T08:00:00+08:00',1,2,'future-handoff',3,'[{"kind":"trusted_authorization"}]'))
        db.execute('INSERT INTO pipeline_handoffs VALUES(?,?,?,?,?,?)',('future-handoff',0,'{"runAt":"2030-01-01T08:00:00+08:00","revision":3}','accepted','{"id":"persisted-task"}',2))
        db.execute('INSERT INTO pipeline_action_effects VALUES(?,?,?,?,?,?,?)',('prior-task',1,'send_private_msg','12345678','unknown','{"status":"unknown","attempt":"do-not-replay"}',3))
        protected_tables=('initiative_schedule','memory_agreements','pipeline_handoffs','pipeline_action_effects')
        protected={table:db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in protected_tables}
        db.commit()
        self.semantic_operations = True
        self.populate()
        await self.summarize()
        self.assert_complete_summary_http()
        self.assert_failed_controls_discarded()
        self.assertTrue(self.callbacks)
        self.assertTrue(all(scope == SCOPE for scope, _ in self.callbacks))
        # retry() may invoke the idempotent callback again after commit.
        self.assertEqual(sum(result['retired'] for _, result in self.callbacks), 1, self.callbacks)
        db = self.store.db
        self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0], 'retired')
        for table,original in protected.items():
            self.assertEqual(db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),original,table+' must remain byte exact')
        self.assertEqual(db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM fishbot_readonly_journal_blobs').fetchone()[0], 0)
        self.assertEqual(self.metrics['semanticMemoryUpdates'], 2)
        self.assertEqual(db.execute('SELECT value FROM facts WHERE active=1').fetchall(), [('更喜欢绘制海边红船',)])
        self.assertEqual(db.execute('SELECT affinity,trust,impression FROM relations WHERE scope=?', (SCOPE,)).fetchone(),
                         (41, .85, '共同绘画让交流更亲近可靠'))

        # A restart followed by stale native SDK history must not resurrect the batch.
        from readonly_journal import ReadonlyJournal
        from context_protocol import READONLY_CURRENT, model_json
        from context_transport import READONLY_FIELDS
        reloaded = ReadonlyJournal(db, SCOPE)
        self.assertEqual(reloaded.last.wire, self.journal.last.wire)
        readonly = {k: v for k, v in reloaded.last.state.items() if k in READONLY_FIELDS}
        packet = {'role': 'user', 'content': READONLY_CURRENT + '\n' + model_json(readonly)}
        candidate = reloaded.prepare(copy.deepcopy(self.native) + [packet], None,
                                     reconstruction_scope=SCOPE,
                                     retirement=reloaded.last.retirement)
        self.assertIsNotNone(candidate)
        await self.provider.text_chat(contexts=candidate.wire, prompt=None, func_tool=None)
        self.assertTrue(reloaded.commit(candidate))
        self.assertEqual(len(self.requests), 2)
        final = self.requests[1]['messages']
        wire = json.dumps(final, ensure_ascii=False)
        for control in self.control_events + self.control_texts:
            self.assertNotIn(control, wire)
        self.assertIn(SUMMARY, wire)
        self.assertIn('更喜欢绘制海边红船', wire)
        self.assertIn('共同绘画让交流更亲近可靠', wire)
        from context_foundation import FOUNDATION_CURRENT
        relations = []
        for message in final:
            content = message.get('content')
            if isinstance(content, str) and content.startswith(FOUNDATION_CURRENT + '\n'):
                relations.extend(json.loads(content.split('\n', 1)[1]).get('relations', []))
        self.assertTrue(any(r['trust'] == .85 and r['affinity'] == 41 and
                            r['impression'] == '共同绘画让交流更亲近可靠' for r in relations))
        self.assertNotIn('旧偏好水彩', wire)
        self.assertNotIn('早期交流仍在互相了解', wire)
        self.assertIn(self.system, final)
        self.assertIn(self.new, final)
        for original in self.originals + self.events + [self.image]:
            self.assertNotIn(original, wire)
        self.assertFalse(any(p.get('type') == 'image_url' for m in final
                             if isinstance(m.get('content'), list) for p in m['content']))

    async def test_actual_truncated_http_response_keeps_batch_and_journal_retryable(self):
        self.populate()
        before = copy.deepcopy(self.journal.last.wire)
        db = self.store.db
        material_before = db.execute('SELECT * FROM memory_round_material ORDER BY event').fetchall()
        rounds_before = db.execute('SELECT * FROM memory_rounds ORDER BY event').fetchall()
        blobs_before = db.execute('SELECT * FROM fishbot_readonly_journal_blobs').fetchall()
        pending_before = db.execute("SELECT * FROM memory_pending WHERE answer<>'' ORDER BY event").fetchall()
        self.mode = 'length'
        await self.summarize()
        self.assert_complete_summary_http(attempts=2)
        # The error checkpoint may commit cleanup of proven internal residue;
        # genuine conversation material must remain byte-for-byte retryable.
        self.assert_failed_controls_discarded()
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.journal.last.wire, before)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_semantic_batches').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT * FROM memory_round_material ORDER BY event').fetchall(), material_before)
        self.assertEqual(db.execute('SELECT * FROM memory_rounds ORDER BY event').fetchall(), rounds_before)
        self.assertEqual(db.execute('SELECT * FROM fishbot_readonly_journal_blobs').fetchall(), blobs_before)
        self.assertEqual(db.execute('SELECT * FROM memory_pending ORDER BY event').fetchall(), pending_before)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0], 35)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0], 35)
        self.assertEqual(self.metrics['summaryFailures'], 1)


if __name__ == '__main__':
    unittest.main()
