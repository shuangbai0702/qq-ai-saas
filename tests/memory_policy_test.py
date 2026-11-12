"""Small regression checks for shared summary policy thresholds."""
import sqlite3,sys,unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from memory_policy import CONTINUITY_MAX_CHARS,DEFAULT_SUMMARY_ROUNDS
import semantic_batch,semantic_media,summary_material


class MemoryPolicyTest(unittest.TestCase):
    def test_default_rounds_and_optional_continuity_budget(self):
        self.assertEqual(DEFAULT_SUMMARY_ROUNDS,35)
        self.assertEqual(CONTINUITY_MAX_CHARS,500)

    def test_checkpoint_sql_uses_default_for_full_and_partial_grants(self):
        db=sqlite3.connect(':memory:')
        self.assertEqual(summary_material.checkpoint_round_gate(db),f'c.round_count={DEFAULT_SUMMARY_ROUNDS}')
        db.execute('CREATE TABLE memory_checkpoint_admin_overrides(checkpoint_id INTEGER,administrator TEXT,request_id TEXT)')
        gate=summary_material.checkpoint_round_gate(db)
        self.assertIn(f'c.round_count={DEFAULT_SUMMARY_ROUNDS}',gate)
        self.assertIn(f'c.round_count BETWEEN 1 AND {DEFAULT_SUMMARY_ROUNDS-1}',gate)

    def test_batch_commit_requires_default_checkpoint_for_automatic_discard(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE memory_reconstruction_checkpoints(id INTEGER PRIMARY KEY,scope TEXT,round_count INTEGER)')
        db.execute('INSERT INTO memory_reconstruction_checkpoints VALUES(1,?,?)',('private:test',DEFAULT_SUMMARY_ROUNDS-1))
        discarded=[{'event':'missing','missing':'full_original','images':[]}]
        with self.assertRaisesRegex(PermissionError,'administrator_authorization_required'):
            semantic_batch.commit(db,'private:test',1,{},0,discarded=discarded)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_semantic_batches').fetchone()[0],0)

        db.execute('UPDATE memory_reconstruction_checkpoints SET round_count=? WHERE id=1',(DEFAULT_SUMMARY_ROUNDS,))
        with mock.patch.object(semantic_media,'retire_batch',return_value=[]):
            semantic_batch.commit(db,'private:test',1,{},0,discarded=discarded)
        self.assertEqual(db.execute('SELECT administrator,request_id FROM memory_semantic_discard_audit').fetchone(),
                         (f'automatic:{DEFAULT_SUMMARY_ROUNDS}_round_checkpoint','checkpoint:1'))


if __name__=='__main__':unittest.main()
