"""Offline regression for covered SDK prefixes at a moving native window."""
import copy,json,sqlite3,sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
from context_protocol import SOURCE,READONLY_CURRENT,model_json

def user(event,text='real message'):
    return {'role':'user','content':SOURCE+model_json({'scope':'private:12345678','sender_id':'12345678','event_id':event})+'\n'+text}
def packet():
    from context_transport import EVENT_CONTEXT_CURRENT
    return [{'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':{'sender_id':'12345678'}},'policy':'fixed','currentGuidance':'current','meaning':'platform event data'})},
            {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}]
def assistant(text):return {'role':'assistant','content':text}

class ProjectedWindowTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.j=ReadonlyJournal(self.db,'private:12345678')
        self.system={'role':'system','content':'original system'}
        self.base=[self.system,user('1','old image anchor'),user('30'),assistant('reply30'),user('31')]
        self.first=self.j.prepare(self.base+packet(),1,strict=True,guarded=True,now=1)
        self.assertTrue(self.j.commit(self.first))
        self.projected=[self.system,user('30'),assistant('reply30'),user('31'),assistant('reply31'),user('32')]
        self.raw=[self.system,user('2','covered earlier SDK event'),assistant('covered reply')]+self.projected[1:]
    def test_projected_overlap_keeps_entire_wire_and_restarts_exactly(self):
        before=copy.deepcopy(self.first.wire)
        p=self.j.prepare(self.projected+packet(),1,strict=True,guarded=True,unprojected=self.raw+packet(),now=2)
        self.assertEqual(p.retirement_deferred,'native_window_overlap')
        self.assertIsNone(p.reconstruction_permit)
        self.assertEqual(p.wire[:len(before)],before)
        self.assertEqual(p.base,self.base+[assistant('reply31'),user('32')])
        self.assertTrue(self.j.commit(p))
        restored=ReadonlyJournal(self.db,'private:12345678')
        self.assertEqual(restored.last.wire,p.wire)
    def test_projection_cannot_hide_uncommitted_middle_message(self):
        raw=self.raw[:-1]+[user('unknown','must not be skipped'),assistant('unknown reply'),self.raw[-1]]
        with self.assertRaises(ContextReconstructionDenied):
            self.j.prepare(self.projected+packet(),1,strict=True,guarded=True,unprojected=raw+packet(),now=2)
        self.assertEqual(self.j.last.wire,self.first.wire)
    def test_changed_anchor_and_reordered_tail_still_refused(self):
        for mode in ('content','scope','order'):
            projected=copy.deepcopy(self.projected);raw=copy.deepcopy(self.raw)
            if mode=='order':
                projected[1],projected[3]=projected[3],projected[1]
                raw[3],raw[5]=raw[5],raw[3]
            else:
                projected[3]=user('31','rewritten') if mode=='content' else user('31')
                if mode=='scope':projected[3]['content']=projected[3]['content'].replace('private:12345678','private:87654321')
                raw[5]=copy.deepcopy(projected[3])
            with self.subTest(mode=mode),self.assertRaises(ContextReconstructionDenied):
                self.j.prepare(projected+packet(),1,strict=True,guarded=True,unprojected=raw+packet(),now=2)
            self.assertEqual(self.j.last.wire,self.first.wire)
if __name__=='__main__':unittest.main()
