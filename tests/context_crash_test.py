"""Real process death between permit consumption and durable wire commit."""
import json,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from readonly_journal import ReadonlyJournal
from context_protocol import READONLY_CURRENT,model_json

class ContextCrashTests(unittest.TestCase):
    def test_dead_candidate_cannot_consume_permit_without_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'behavior.sqlite';store=BehaviorStore(path);db=store.db
            try:
                scope='private:12345678';journal=ReadonlyJournal(db,scope)
                base=[{'role':'system','content':'old'},{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{'facts':['retained fact']}})}]
                first=journal.prepare(base,'one');self.assertTrue(journal.commit(first))
                program='''import sys,sqlite3,os
sys.path[:0]=[sys.argv[1]+'/references/astrbot',sys.argv[1]+'/adapters/astrbot']
from memory_reflection import Reflection
from readonly_journal import ReadonlyJournal
from system_contract import request_identity
db=sqlite3.connect(sys.argv[2]);authority=Reflection(db);scope='private:12345678'
journal=ReadonlyJournal(db,scope);base=journal.last.base.copy();base[0]={'role':'system','content':'new'}
from context_protocol import READONLY_CURRENT,model_json
base.append({'role':'user','content':READONLY_CURRENT+'\\n'+model_json({'state':{'facts':['retained fact']}})})
candidate=journal.prepare(base,'two',reconstruction_authority=authority,reconstruction_scope=scope,reconstruction_web_proof={'permitId':'fixture-trusted-save','revision':2,'contextScope':scope})
original=authority.settle_context_reconstruction
def fault(permit,success,**kw):
 result=original(permit,success,**kw)
 if success:os._exit(73)
 return result
authority.settle_context_reconstruction=fault
journal.commit(candidate)
'''
                result=subprocess.run([sys.executable,'-c',program,str(ROOT),str(path)],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                restored=ReadonlyJournal(db,scope);self.assertEqual(restored.last.wire,first.wire)
                self.assertEqual(db.execute('SELECT state FROM memory_web_context_permits').fetchone()[0],'reserved')
                incoming=[{'role':'system','content':'new'},base[1]]
                candidate=restored.prepare(incoming,'retry',reconstruction_authority=store.reflection,reconstruction_scope=scope,reconstruction_web_proof={'permitId':'fixture-trusted-save','revision':2,'contextScope':scope})
                self.assertTrue(restored.commit(candidate))
                self.assertEqual(db.execute('SELECT state FROM memory_web_context_permits').fetchone()[0],'consumed')
                self.assertEqual(ReadonlyJournal(db,scope).last.wire,candidate.wire)
            finally:store.close()

if __name__=='__main__':unittest.main()
