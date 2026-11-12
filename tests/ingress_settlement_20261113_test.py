"""Ingress refused/settled ledger contract (2026-11-13 audit fixes).

1. persist_input retains an authenticated raw event before admission; an
   admission refusal that happens before bind can never create a
   pipeline_events row, so the row must settle terminally instead of burning
   five futile recovery passes into blocked_recovery.
2. notice/request retained rows have no executable pipeline identity: the
   recovery sweep settles them instead of replaying them into a dead end.
3. A proven pre-dispatch rejection retracts its exactly-one recorded attempt,
   so the Work settles as error/silent evidence rather than unknown.
"""
import asyncio
import hashlib
import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
from bot_pipeline import BotPipeline
import initiative
import message_provenance


def message_event(identifier='101', at=None):
    extra = {}
    at = at or time.time()
    raw = {'post_type': 'message', 'message_id': identifier, 'user_id': '12345',
           'self_id': '99999', 'message_type': 'private', 'time': int(at),
           'message': [{'type': 'text', 'data': {'text': 'hi'}}], 'raw_message': 'hi'}
    return NS(get_group_id=lambda: '', get_sender_id=lambda: '12345', get_self_id=lambda: '99999',
              get_platform_id=lambda: 'qq',
              get_extra=lambda k, d=None: extra.get(k, d), set_extra=lambda k, v: extra.update({k: v}),
              message_obj=NS(message_id=identifier, timestamp=at, raw_message=raw))


LEASE = {'tenantId': 'tenant', 'botId': 'bot', 'generation': 1, 'revision': 1}
ISSUES_DDL = '''CREATE TABLE IF NOT EXISTS memory_checkpoint_issues(
  scope TEXT,checkpoint_id INTEGER,kind TEXT,status TEXT,detail TEXT,
  first_seen REAL,updated REAL,PRIMARY KEY(scope,checkpoint_id,kind))'''


class RefusedInputSettlement(unittest.TestCase):
    """settle_refused_input: only unowned pending/queued rows settle."""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.p = BotPipeline(self.db)
        self.db.execute(ISSUES_DDL)

    def tearDown(self):
        self.db.close()

    def _persist(self, identifier):
        self.assertTrue(self.p.persist_input(message_event(identifier), LEASE))

    def _status(self, identifier):
        return self.db.execute("SELECT status,owner FROM pipeline_work_inputs WHERE event=?", (identifier,)).fetchone()

    def test_pending_row_settles_and_resolves_open_issue(self):
        self._persist('101')
        self.db.execute("INSERT INTO memory_checkpoint_issues VALUES('bot_shared',0,?,'open','{}',?,?)",
                        ('input_recovery:private:12345:101', time.time(), time.time()))
        self.assertTrue(self.p.settle_refused_input('private:12345', '101'))
        status, owner = self._status('101')
        self.assertEqual(status, 'settled_refused')
        self.assertIsNone(owner)
        issue = self.db.execute("SELECT status FROM memory_checkpoint_issues WHERE kind=?",
                                ('input_recovery:private:12345:101',)).fetchone()
        self.assertEqual(issue[0], 'resolved')

    def test_queued_row_settles(self):
        self._persist('102')
        self.db.execute("UPDATE pipeline_work_inputs SET status='queued',retry_at=? WHERE event='102'", (time.time() + 30,))
        self.assertTrue(self.p.settle_refused_input('private:12345', '102'))
        self.assertEqual(self._status('102')[0], 'settled_refused')

    def test_settlement_is_idempotent_and_ignores_other_events(self):
        self._persist('104')
        self.assertTrue(self.p.settle_refused_input('private:12345', '104'))
        self.assertFalse(self.p.settle_refused_input('private:12345', '104'))
        self.assertEqual(self._status('104')[0], 'settled_refused')
        self._persist('105')
        self.assertFalse(self.p.settle_refused_input('private:999', '105'))  # scope mismatch ignored
        self.assertEqual(self._status('105')[0], 'pending')

    def test_active_scope_work_protects_owned_and_deferred_rows(self):
        self._persist('106')
        self.db.execute("UPDATE pipeline_work_inputs SET status='owned',owner='106' WHERE event='106'")
        self.db.execute("INSERT INTO pipeline_scope_work VALUES('private:12345','106','12345','runtime-x',123)")
        self.assertFalse(self.p.settle_refused_input('private:12345', '106'))
        self.assertEqual(self._status('106')[0], 'owned')
        self._persist('107')
        self.db.execute("UPDATE pipeline_work_inputs SET status='deferred' WHERE event='107'")
        self.assertFalse(self.p.settle_refused_input('private:12345', '107'))
        self.assertEqual(self._status('107')[0], 'deferred')

    def test_terminal_rows_never_return_to_settlement(self):
        self._persist('108')
        self.db.execute("UPDATE pipeline_work_inputs SET status='blocked_unknown' WHERE event='108'")
        self.assertFalse(self.p.settle_refused_input('private:12345', '108'))
        self.assertEqual(self._status('108')[0], 'blocked_unknown')


class NoticeRecoverySettlement(unittest.IsolatedAsyncioTestCase):
    """resume_scope_input keeps replaying notice/request rows (the recovery
    contract in unstarted_input_recovery_20261111_test). The live-path notice
    debt is settled at the execute-layer gates in behavior.py instead; only
    the ordinary message path is guarded here."""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.p = BotPipeline(self.db)
        self.store = NS(db=self.db, reflection=NS(checkpoint_issue=lambda *a, **k: None), model_journals={})
        self.core = NS(platform_manager=NS(get_insts=lambda: []), event_bus=NS(stopped_platforms=set()))
        self.settings = {'qq': 'lease-marker'}

    def tearDown(self):
        self.db.close()

    async def test_message_row_keeps_normal_recovery_path(self):
        self.assertTrue(self.p.persist_input(message_event('201'), LEASE))
        result = await initiative.resume_scope_input(self.core, 'qq', LEASE, self.store, self.settings)
        self.assertFalse(result)  # no platform instance in this fake core
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='201'").fetchone()[0],
                         'pending')


class PreDispatchRetractLedger(unittest.TestCase):
    """A proven pre-dispatch rejection retracts its one attempt, so the Work
    settles as error with pending inputs instead of unknown+blocked_unknown."""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.p = BotPipeline(self.db)
        message_provenance.initialize(self.db)
        self.db.execute('''CREATE TABLE fishbot_shared_context(
          id INTEGER PRIMARY KEY CHECK(id=1),state TEXT,authorization TEXT,created REAL,group_limit INTEGER)''')
        self.db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active',?,?,2)", ('a' * 40, time.time()))
        self.metrics = {}

    def tearDown(self):
        self.db.close()

    def test_retract_returns_attempt_and_finish_settles_pending(self):
        work = self.p.bind(message_event('301'), LEASE)
        self.assertEqual(work.status, 'received')
        self.p.sending(work)
        row = self.db.execute("SELECT status,attempted,confirmed FROM pipeline_events WHERE event='301'").fetchone()
        self.assertEqual((row[0], row[1], row[2]), ('sending', 1, 0))
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='301'").fetchone()[0], 'owned')
        self.assertTrue(BotPipeline.retract_pre_dispatch(self.db, work, 1))
        self.assertEqual(work.attempted, 0)
        self.assertFalse(BotPipeline.retract_pre_dispatch(self.db, work, 1))  # second retract refused
        work.stop('PreDispatchRejected', 'error')
        self.p.finish(work, self.metrics)
        row = self.db.execute("SELECT status,attempted,confirmed FROM pipeline_events WHERE event='301'").fetchone()
        # Proven not dispatched: attempted stays 0 and the Work is 'error',
        # NOT 'unknown'. delivery_view therefore classifies this draft as
        # 'not_sent' (draft_only) instead of a possibly-sent 'unknown'.
        self.assertEqual((row[0], row[1], row[2]), ('error', 0, 0))
        # Root inputs of an errored Work stay frozen (errors are deliberately
        # not auto-retried: behavior.py "the next user message may retry").
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='301'").fetchone()[0],
                         'blocked_unknown')

    def test_unretracted_rejection_still_settles_unknown(self):
        work = self.p.bind(message_event('302'), LEASE)
        self.p.sending(work)
        work.stop('SomeTransportError', 'error')
        self.p.finish(work, self.metrics)
        row = self.db.execute("SELECT status,attempted,confirmed FROM pipeline_events WHERE event='302'").fetchone()
        self.assertEqual((row[0], row[1], row[2]), ('unknown', 1, 0))
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='302'").fetchone()[0],
                         'blocked_unknown')  # unchanged conservative behavior for unproven outcomes

    def test_retract_refuses_after_a_confirmed_bubble_mismatch(self):
        work = self.p.bind(message_event('303'), LEASE)
        self.p.sending(work)
        self.p.confirmed(work)  # bubble 1 confirmed
        self.p.sending(work)    # bubble 2 attempt recorded
        self.assertEqual((work.attempted, work.confirmed), (2, 1))
        self.assertTrue(BotPipeline.retract_pre_dispatch(self.db, work, 2))
        self.assertEqual((work.attempted, work.confirmed), (1, 1))
        self.assertFalse(BotPipeline.retract_pre_dispatch(self.db, work, 1))  # confirmed prefix is not retractable


if __name__ == '__main__':
    unittest.main()
