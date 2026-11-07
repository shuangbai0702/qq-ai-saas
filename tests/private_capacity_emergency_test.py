"""Offline regression for a private journal that reached the old text limit.

No provider, QQ, Bilibili or production database is used.
"""
import json,sqlite3,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from context_protocol import READONLY_CURRENT,model_json
from decision_history import history_usage,MAX_RETAINED_TEXT_CHARS,MAX_RETAINED_WIRE_CHARS
from readonly_journal import ReadonlyJournal,ContextCapacityExceeded,ContextReconstructionDenied

class PrivateCapacityEmergencyTests(unittest.TestCase):
    def test_old_capacity_can_append_and_restart_without_a_permit(self):
        with sqlite3.connect(':memory:') as db:
            journal=ReadonlyJournal(db,'private:12345678')
            base=[{'role':'system','content':'trusted system'},
                  {'role':'user','content':'x'*380000}]
            packet={'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}
            first=journal.prepare(base+[packet],1,now=1)
            self.assertTrue(journal.commit(first))
            incoming=base+[{'role':'assistant','content':'reply'},
                           {'role':'user','content':'new user '+'y'*8000},packet]
            second=journal.prepare(incoming,1,now=2)
            self.assertGreater(history_usage(second.wire)['textChars'],384000)
            self.assertLess(history_usage(second.wire)['textChars'],MAX_RETAINED_TEXT_CHARS)
            self.assertEqual(second.wire[:len(first.wire)],first.wire)
            self.assertIsNone(second.reconstruction_permit)
            self.assertTrue(journal.commit(second))
            restored=ReadonlyJournal(db,'private:12345678')
            self.assertEqual(restored.last.wire,second.wire)
            self.assertEqual(restored.last.turns,2)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_reconstruction_checkpoints'").fetchone())
            changed=[dict(m) for m in incoming];changed[1]={'role':'user','content':'rewritten old history'}
            with self.assertRaises(ContextReconstructionDenied):restored.prepare(changed,1,now=3)

    def test_new_text_and_existing_wire_limits_still_reject(self):
        for message in (
            {'role':'user','content':'x'*(MAX_RETAINED_TEXT_CHARS+1)},
            {'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*MAX_RETAINED_WIRE_CHARS}}]},
        ):
            with self.subTest(kind='text' if isinstance(message['content'],str) else 'wire'):
                packet={'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}
                with self.assertRaises(ContextCapacityExceeded):ReadonlyJournal().prepare([message,packet],1)

if __name__=='__main__':unittest.main()
