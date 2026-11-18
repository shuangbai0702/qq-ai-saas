import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters' / 'astrbot'))
from context_protocol import event_message, model_json
from journal_cutover import OWNER_EARLY_MIGRATION, retire_empty_legacy_journals, source_timeline
import journal_storage
from readonly_journal import ReadonlyJournal
from shared_context import initialize


class OwnerJournalMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        initialize(self.db)
        ReadonlyJournal(self.db, None)
        self.db.execute('CREATE TABLE fishbot_shared_context(id,state,authorization,created,group_limit)')
        self.db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active','fixture',1,2)")
        self.bot = str(uuid4())
        self.grant = {'type':OWNER_EARLY_MIGRATION, 'id':str(uuid4()), 'bot':self.bot,
                      'destination':'bot_shared', 'sources':{}, 'reason':'owner migration test', 'release':'a'*40}
        self.destination = self.snapshot('bot_shared')

    def snapshot(self, scope, content=None):
        key = json.dumps(scope)
        messages = [{'role':'system','content':'committed'}]
        if content:
            messages.append(content if isinstance(content,dict) else {'role':'user','content':content})
        value = {'scope':key,'base':messages,'wire':messages,'state':{},'turns':1,'at':1,
                 'group_turn':None,'base_chars':0,'detail':'','usage':{},'authority':None,'retirement':None}
        payload, blobs = journal_storage.encode(value)
        self.assertFalse(blobs)
        sha = hashlib.sha256(payload.encode()).hexdigest()
        self.db.execute("INSERT INTO fishbot_readonly_journals VALUES (?,?,'ready',?,?,1)",
                        (key,journal_storage.VERSION,payload,sha))
        self.db.commit()
        if scope != 'bot_shared':
            self.grant['sources'][scope] = sha
        return sha

    def migrate(self, bot=None):
        return retire_empty_legacy_journals(self.db, bot or self.bot, self.grant, 'b'*64)

    def test_timeline_binds_protocol_clocks_and_preserves_same_id_in_two_scopes(self):
        private='private:11111';group='group:22222'
        for scope in (private,group):
            self.snapshot(scope,event_message({'scope':scope,'event_id':'54','sender_id':'11111'},'observed'))
        self.db.execute('CREATE TABLE pipeline_work_inputs(scope,event,payload)')
        self.db.executemany('INSERT INTO pipeline_work_inputs VALUES(?,?,?)',[
            (private,'54',model_json({'time':20,'user_id':11111,'message_id':54})),
            (group,'54',model_json({'time':10,'user_id':11111,'message_id':54,'group_id':22222}))])
        self.db.commit()
        before=list(self.db.iterdump())
        timeline=source_timeline(self.db,self.grant['sources'])
        self.assertEqual([(r['event'],r['at']) for r in timeline],[(model_json([group,'54']),10),(model_json([private,'54']),20)])
        self.assertEqual(list(self.db.iterdump()),before)
        self.db.execute('UPDATE pipeline_work_inputs SET payload=NULL WHERE scope=?',(private,))
        with self.assertRaisesRegex(ValueError,'protocol_time_missing'):
            source_timeline(self.db,self.grant['sources'])

    def test_retires_all_scopes_and_retry_returns_same_receipt(self):
        self.snapshot('private:11111')
        self.snapshot('group:22222')
        receipt = self.migrate()
        self.assertEqual(receipt, self.migrate())
        self.assertEqual(self.db.execute('SELECT scope,sha FROM fishbot_readonly_journals').fetchall(),
                         [('"bot_shared"',self.destination)])
        self.assertEqual([source['state'] for source in receipt['sources']], ['retired','retired'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('DELETE FROM fishbot_journal_owner_migrations')

    def test_history_coverage_failure_rolls_back_previous_source(self):
        self.snapshot('group:22222')
        self.snapshot('private:11111','uncovered history')
        before = self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall()
        with self.assertRaisesRegex(ValueError,'requires_history_coverage'):
            self.migrate()
        self.assertEqual(self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall(), before)

    def test_authorization_is_bound_to_bot_and_snapshot_inventory(self):
        self.snapshot('group:22222')
        with self.assertRaises(PermissionError):
            self.migrate(bot=str(uuid4()))
        self.grant['sources']['group:22222'] = '0'*64
        with self.assertRaisesRegex(ValueError,'scope_inventory_changed'):
            self.migrate()
        self.assertEqual(self.db.execute('SELECT count(*) FROM fishbot_readonly_journals').fetchone()[0],2)

    def test_owner_blocks_live_work_and_pending_old_rounds(self):
        self.snapshot('group:22222')
        self.db.execute('CREATE TABLE pipeline_scope_work(scope)')
        self.db.execute("INSERT INTO pipeline_scope_work VALUES('group:22222')")
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'work_still_active'):
            self.migrate()
        self.db.execute('DELETE FROM pipeline_scope_work')
        self.db.execute('CREATE TABLE memory_pending(scope)')
        self.db.execute("INSERT INTO memory_pending VALUES('group:22222')")
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'requires_history_coverage:memory_pending'):
            self.migrate()
