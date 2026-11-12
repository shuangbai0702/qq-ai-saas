import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('snapshot', Path(__file__).resolve().parents[1] / 'scripts/mobile-predeploy-snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class ProjectionTest(unittest.TestCase):
    def test_products_keep_identity_raw_empty_source_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source.sqlite'; target = root / 'target.sqlite'
            db = sqlite3.connect(source)
            db.executescript('''CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT,text);
              CREATE TABLE memory_pending(id INTEGER PRIMARY KEY AUTOINCREMENT,question);
              CREATE TABLE contacts(subject PRIMARY KEY,last_text,last_output);
              INSERT INTO contacts VALUES('synthetic user','fresh real input','fresh real answer');
              CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY,text);
              CREATE TABLE memory_reconstruction_checkpoints(id INTEGER PRIMARY KEY,summary_id,state);
              CREATE TABLE memory_semantic_batches(checkpoint_id INTEGER PRIMARY KEY,state);
              CREATE TABLE memory_checkpoint_admin_overrides(checkpoint_id INTEGER PRIMARY KEY,administrator);
              INSERT INTO facts VALUES(40,'durable synthetic cognition');
              INSERT INTO memory_pending VALUES(99,'private raw input must not migrate');
              INSERT INTO memory_summaries VALUES(7,'accepted summary');
              INSERT INTO memory_reconstruction_checkpoints VALUES(3,7,'consumed');
              INSERT INTO memory_reconstruction_checkpoints VALUES(4,NULL,'pending_merge');
              INSERT INTO memory_reconstruction_checkpoints VALUES(5,999,'consumed');
              INSERT INTO memory_semantic_batches VALUES(3,'retired');
              INSERT INTO memory_semantic_batches VALUES(4,'preparing');
              INSERT INTO memory_semantic_batches VALUES(5,'retired');
              INSERT INTO memory_checkpoint_admin_overrides VALUES(3,'synthetic admin');
              INSERT INTO memory_checkpoint_admin_overrides VALUES(5,'synthetic orphan');''')
            db.executescript("CREATE VIRTUAL TABLE memory_search USING fts5(text); INSERT INTO memory_search VALUES('raw derived index');")
            db.commit(); db.close(); before = source.read_bytes()
            proof = snapshot.project_sqlite(source, target)
            self.assertTrue(proof['rawTablesEmpty']); self.assertFalse(proof['executable'])
            self.assertEqual(source.read_bytes(), before)
            result = sqlite3.connect(target)
            self.assertEqual(result.execute('SELECT * FROM facts').fetchall(), [(40, 'durable synthetic cognition')])
            self.assertEqual(result.execute('SELECT * FROM memory_pending').fetchall(), [])
            self.assertEqual(result.execute('SELECT * FROM contacts').fetchall(), [])
            self.assertEqual(result.execute('SELECT * FROM memory_search').fetchall(), [])
            self.assertEqual(result.execute('SELECT * FROM memory_reconstruction_checkpoints').fetchall(), [(3, 7, 'consumed')])
            self.assertEqual(result.execute('SELECT * FROM memory_semantic_batches').fetchall(), [(3, 'retired')])
            self.assertEqual(result.execute('SELECT * FROM memory_checkpoint_admin_overrides').fetchall(), [(3, 'synthetic admin')])
            self.assertEqual(result.execute("SELECT seq FROM sqlite_sequence WHERE name='memory_pending'").fetchone(), (99,))
            result.close()
            with self.assertRaises(FileExistsError): snapshot.project_sqlite(source, target)


if __name__ == '__main__': unittest.main()
