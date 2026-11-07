"""Real process death between license consumption and durable wire commit.

The only legitimate license is a real 35-round checkpoint. A web-config save
never rewrites a committed prefix (its proof is refused outright), so the
crash window is exercised through the checkpoint reserve inside the same
persist transaction: process death rolls the whole candidate back, the last
valid snapshot survives, a consumed license never revives, and the next
legal reserve still works.
"""
import json,sqlite3,subprocess,sys,tempfile,time,unittest
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
db=sqlite3.connect(sys.argv[2]);authority=Reflection(db);scope='private:12345678'
journal=ReadonlyJournal(db,scope);base=journal.last.base.copy();base[0]={'role':'system','content':'new'}
from context_protocol import READONLY_CURRENT,model_json
base.append({'role':'user','content':READONLY_CURRENT+'\\n'+model_json({'state':{'facts':['retained fact'],'recent_messages':[]}})})
stamp=authority.reconstruction_stamp(scope)
candidate=journal.prepare(base,'two',retirement=stamp,reconstruction_authority=authority,reconstruction_scope=scope)
original=authority.settle_context_reconstruction
def fault(permit,success,**kw):
 result=original(permit,success,**kw)
 if success:os._exit(73)
 return result
authority.settle_context_reconstruction=fault
journal.commit(candidate)
'''
                # The same fixture also proves the R1 tightening: with
                # committed history, no rebuild happens without a real 35-round
                # checkpoint — a config save mints no license at all.
                refuse='''import sys,sqlite3,os
sys.path[:0]=[sys.argv[1]+'/references/astrbot',sys.argv[1]+'/adapters/astrbot']
from memory_reflection import Reflection
from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
db=sqlite3.connect(sys.argv[2]);authority=Reflection(db);scope='private:12345678'
journal=ReadonlyJournal(db,scope);base=journal.last.base.copy();base[0]={'role':'system','content':'newer'}
from context_protocol import READONLY_CURRENT,model_json
base.append({'role':'user','content':READONLY_CURRENT+'\\n'+model_json({'state':{'facts':['retained fact'],'recent_messages':[]}})})
try:
 journal.prepare(base,'web',retirement='minted-checkpoint-stamp',reconstruction_authority=authority,reconstruction_scope=scope)
 os._exit(0)
except ContextReconstructionDenied:os._exit(73)
'''
                refused=subprocess.run([sys.executable,'-c',refuse,str(ROOT),str(path)],capture_output=True,text=True,timeout=30)
                self.assertEqual(refused.returncode,73,refused.stderr)
                self.assertEqual(db.execute('SELECT count(*) FROM memory_web_context_permits').fetchone()[0],0,'a web-config proof never reaches the permit table over committed history')
                # The legitimate pre-crash state: one real completed summary
                # and its available 35-round checkpoint for this scope.
                summary_id=db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version) VALUES(?,?,?,?,?)',(scope,'35轮真实完成摘要','[]',time.time(),2)).lastrowid
                db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(summary_id,))
                db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(summary_id,scope))
                db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id) VALUES(?,1,35,0,'available',?,?)",(scope,time.time(),summary_id))
                db.commit()
                result=subprocess.run([sys.executable,'-c',program,str(ROOT),str(path)],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                restored=ReadonlyJournal(db,scope);self.assertEqual(restored.last.wire,first.wire,'the last valid snapshot survives process death')
                self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'reserved','the reserve committed; the in-transaction consumption rolled back')
                # R1 conservative isolation: a dead owner's reserved permit is
                # never reclaimed or re-issued, because a journal row that no
                # longer shows the candidate is not proof the request was never
                # staged or submitted. The license stays spent; the scope waits
                # for its next legal 35-round checkpoint.
                from readonly_journal import ContextReconstructionDenied
                incoming=[{'role':'system','content':'new'},base[1]]
                stamp=store.reflection.reconstruction_stamp(scope)
                with self.assertRaises(ContextReconstructionDenied):
                    restored.prepare(incoming,'retry',retirement=stamp,reconstruction_authority=store.reflection,reconstruction_scope=scope)
                self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'reserved','no path may revive or refund the orphaned license')
            finally:store.close()

if __name__=='__main__':unittest.main()
