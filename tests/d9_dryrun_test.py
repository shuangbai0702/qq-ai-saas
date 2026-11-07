"""D9 dry-run preview: real legacy anomalies are listed and nothing is written."""
import importlib.util,hashlib,json,os,sqlite3,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))


def load():
    spec=importlib.util.spec_from_file_location('d9_dryrun',ROOT/'scripts'/'d9_legacy_migration_dryrun.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def build(path):
    db=sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE facts(scope TEXT,subject TEXT,name TEXT,value TEXT,event TEXT,updated REAL,version INTEGER,active INTEGER);
    CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY,text TEXT,sources TEXT,updated REAL,scope TEXT);
    CREATE TABLE memory_summary_origins(summary_id INTEGER,scope TEXT,PRIMARY KEY(summary_id,scope));
    CREATE TABLE memory_core_notes(slot INTEGER PRIMARY KEY,text TEXT,scopes TEXT,people TEXT,sources TEXT,updated REAL);
    CREATE TABLE memory_blocks(scope TEXT,subject TEXT,name TEXT,block_all INTEGER,rejected_values TEXT,revision INTEGER);
    CREATE TABLE memory_pending(scope TEXT,subject TEXT,event TEXT,question TEXT,answer TEXT,at REAL,PRIMARY KEY(scope,event));
    INSERT INTO facts VALUES('private:10001','10002','称呼','小寒','e1',1,1,1);
    INSERT INTO memory_summaries VALUES(1,'mixed','[]',1,'self');
    INSERT INTO memory_summary_origins VALUES(1,'group:70001'),(1,'group:70002');
    INSERT INTO memory_core_notes VALUES(1,'legacy note','["*"]','[]','[]',1);
    INSERT INTO memory_blocks VALUES('private:10001','10002','称呼',0,'[]',1);
    INSERT INTO memory_pending VALUES('private:10001','10002','e2','q','',1);
    ''')
    db.commit();db.close()


class D9(unittest.TestCase):
    def test_anomalies_listed_and_zero_writes(self):
        module=load()
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'bot.sqlite');build(path)
            before=hashlib.sha256(open(path,'rb').read()).hexdigest()
            db=module.open_readonly(path)
            report=module.preview(db)
            # A read-only driver refuses writes even if a future edit tries.
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM facts")
            db.close()
            self.assertEqual(before,hashlib.sha256(open(path,'rb').read()).hexdigest())
            self.assertIn('facts_cross_scope',report)
            self.assertEqual(report['facts_cross_scope'][0][1],'10002')
            self.assertIn('summaries_mixed_origins',report)
            self.assertEqual(report['summaries_mixed_origins'][0][2],'group:70001,group:70002')
            self.assertIn('core_notes_legacy_unverified',report)
            self.assertIn('blocks_cross_scope',report)
            self.assertIn('pending_cross_scope',report)
            clean_db=module.open_readonly(path)
            try:module.preview(clean_db)
            finally:clean_db.close()
            self.assertEqual(before,hashlib.sha256(open(path,'rb').read()).hexdigest())


if __name__=='__main__':
    unittest.main()
