"""Finish evidence must separate entry time, real settlement, and recovery."""
import ast
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/astrbot'))
import bot_pipeline
from bot_pipeline import BotPipeline


def event(identifier='event-one'):
    extra = {}
    return SimpleNamespace(get_group_id=lambda: '', get_sender_id=lambda: '12345',
        get_self_id=lambda: '99999', get_platform_id=lambda: 'qq',
        get_extra=lambda key, default=None: extra.get(key, default),
        set_extra=lambda key, value: extra.update({key: value}),
        message_obj=SimpleNamespace(message_id=identifier, timestamp=1700000000))


class CompletionEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(':memory:')
        self.pipeline = BotPipeline(self.db)
        self.lease = {'tenantId': 'tenant', 'botId': 'bot', 'root': self.temp.name,
                      'generation': 1, 'revision': 2}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_cancelled_finish_has_real_time_and_safe_existing_diagnostic(self):
        e = event()
        with patch('bot_pipeline.time.time', return_value=100):
            work = self.pipeline.bind(e, self.lease)
        work.stage('model_tools')
        work.stop('cancelled')
        with patch('bot_pipeline.time.time', return_value=140):
            self.pipeline.finish(work, {})
        self.assertEqual(self.db.execute('SELECT at,updated_at,finished_at,status,reason FROM pipeline_events').fetchone(),
                         (100, 140, 140, 'blocked', 'cancelled'))
        diagnostic = json.loads((Path(self.temp.name) / 'model-diagnostics.jsonl').read_text())
        self.assertEqual((diagnostic['kind'], diagnostic['phase'], diagnostic['workId']),
                         ('pipeline_result', 'pipeline', 'event-one'))
        self.assertEqual((diagnostic['status'], diagnostic['reason']), ('blocked', 'cancelled'))
        detail = json.loads(diagnostic['diagnosticDetail'])
        self.assertEqual(detail, {'stage': 'model_tools', 'source': 'message', 'finishedAt': 140,
                                  'protocolAttempts': 0, 'protocolConfirmed': 0})
        # A protocol redelivery must not rewrite the original finish or create
        # a new execution merely because diagnostic columns were added.
        with patch('bot_pipeline.time.time', return_value=180):
            duplicate = self.pipeline.bind(event(), self.lease)
            self.pipeline.finish(duplicate, {})
        self.assertEqual(duplicate.status, 'duplicate')
        self.assertEqual(self.db.execute('SELECT finished_at FROM pipeline_events').fetchone()[0], 140)
        self.assertEqual(len((Path(self.temp.name) / 'model-diagnostics.jsonl').read_text().splitlines()), 1)

    def test_existing_terminal_rows_keep_unknown_finish_and_recovery_only_updates_observation(self):
        self.db.close()
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE pipeline_events(scope TEXT,subject TEXT,event TEXT,at REAL,source TEXT,stage TEXT,status TEXT,reason TEXT,attempted INTEGER DEFAULT 0,confirmed INTEGER DEFAULT 0,PRIMARY KEY(scope,subject,event))')
        for identifier, status, attempts in [('old-cancel', 'blocked', 0), ('old-active', 'received', 0), ('old-send', 'sending', 1)]:
            self.db.execute('INSERT INTO pipeline_events VALUES(?,?,?,?,?,?,?,?,?,?)',
                            ('private:12345', '12345', identifier, 100, 'message', 'model_tools', status, 'cancelled' if status == 'blocked' else '', attempts, 0))
        self.db.commit()
        with patch('bot_pipeline.time.time', return_value=200):
            BotPipeline(self.db)
        rows = {r[0]: r[1:] for r in self.db.execute('SELECT event,status,updated_at,finished_at FROM pipeline_events')}
        self.assertEqual(rows['old-cancel'], ('blocked', None, None))
        self.assertEqual(rows['old-active'], ('interrupted', 200, None))
        self.assertEqual(rows['old-send'], ('unknown', 200, None))

    def test_dispatch_and_finish_timestamps_do_not_make_unknown_result_replayable(self):
        with patch('bot_pipeline.time.time', return_value=100):
            work = self.pipeline.bind(event(), self.lease)
        with patch('bot_pipeline.time.time', return_value=110):
            self.pipeline.sending(work)
        self.assertEqual(self.db.execute('SELECT updated_at,finished_at FROM pipeline_events').fetchone(), (110, None))
        work.stop('cancelled')
        with patch('bot_pipeline.time.time', return_value=120):
            self.pipeline.finish(work, {})
        self.assertEqual(self.db.execute('SELECT status,updated_at,finished_at FROM pipeline_events').fetchone(), ('unknown', 120, 120))
        self.assertEqual(self.pipeline.bind(event(), self.lease).status, 'duplicate')

    def test_unwritable_diagnostic_does_not_change_waiting_activation_settlement(self):
        work = self.pipeline.bind(event(), self.lease)
        work.stop('group_waiting', 'waiting')
        with patch('bot_pipeline.time.time', return_value=150), patch('model_cache.os.open', side_effect=PermissionError('diagnostic unavailable')):
            self.pipeline.finish(work, {})
        self.assertEqual(self.db.execute('SELECT status,reason,finished_at FROM pipeline_events').fetchone(),
                         ('waiting', 'group_waiting', 150))
        self.assertEqual((work.status, work.reason), ('waiting', 'group_waiting'))

    def test_cancellation_uses_actual_safe_raise_site_without_exception_text(self):
        # Exercise the real guarded_execute cancellation handler without
        # booting the SDK; the actual diagnostic writer remains installed.
        tree = ast.parse((ROOT / 'adapters/astrbot/behavior.py').read_text(encoding='utf8'))
        guard = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'guarded_execute')
        handler = next(n for n in ast.walk(guard) if isinstance(n, ast.ExceptHandler)
                       and isinstance(n.type, ast.Attribute) and n.type.attr == 'CancelledError')
        wrapped = ast.Try(body=[ast.Raise(exc=ast.Name(id='error', ctx=ast.Load()))], handlers=[handler], orelse=[], finalbody=[])
        fn = ast.AsyncFunctionDef(name='capture', args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=n) for n in ('event','lease','error')], kwonlyargs=[], kw_defaults=[], defaults=[]), body=[wrapped], decorator_list=[])
        namespace = {'asyncio': asyncio, 'bot_pipeline': bot_pipeline}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), 'pipeline_cancel_handler', 'exec'), namespace)
        e = event()
        work = self.pipeline.bind(e, self.lease)
        async def cancelled_tool():
            raise asyncio.CancelledError('sensitive text must never appear')
        async def scenario():
            try:
                await cancelled_tool()
            except asyncio.CancelledError as error:
                await namespace['capture'](e, self.lease, error)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(scenario())
        raw = (Path(self.temp.name) / 'model-diagnostics.jsonl').read_text()
        diagnostic = json.loads(raw)
        self.assertEqual((diagnostic['kind'], diagnostic['phase'], diagnostic['reason']),
                         ('request_error', 'pipeline_cancelled', 'CancelledError'))
        self.assertEqual(json.loads(diagnostic['diagnosticDetail'])['symbol'], 'cancelled_tool')
        self.assertNotIn('sensitive text', raw)
        self.assertEqual((work.status, work.reason), ('blocked', 'cancelled'))


if __name__ == '__main__':
    unittest.main()
