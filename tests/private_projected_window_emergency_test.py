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
    def test_covered_old_blocks_between_anchors_do_not_strand_journal(self):
        from context_protocol import digest,reply_digest
        self.db.executescript('CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY);'
            'CREATE TABLE memory_summary_coverage(scope TEXT,event TEXT,subject TEXT,digest TEXT,summary_id INTEGER);'
            'INSERT INTO memory_summaries VALUES(1);')
        covered=user('2','previously summarized');reply=assistant('covered reply')
        proof='v1:'+digest(covered)+':'+reply_digest([reply])
        self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,1)',
            ('private:12345678','2','12345678',proof));self.db.commit()
        raw=self.base[:2]+[covered,reply]+self.projected[1:]
        projected=self.base[:2]+self.projected[1:]
        before=copy.deepcopy(self.first.wire)
        p=self.j.prepare(projected+packet(),1,strict=True,guarded=True,unprojected=raw+packet(),
            reconstruction_scope='private:12345678',now=2)
        self.assertEqual(p.wire[:len(before)],before)
        self.assertIsNone(p.reconstruction_permit)
        self.assertEqual(p.base,self.base+[assistant('reply31'),user('32')])
        # An edited reply invalidates full-block evidence; do not blindly trust
        # a coverage row or permit projection to hide a new middle event.
        raw[3]=assistant('changed reply')
        with self.assertRaises(ContextReconstructionDenied):
            self.j.prepare(projected+packet(),1,strict=True,guarded=True,unprojected=raw+packet(),
                reconstruction_scope='private:12345678',now=3)
    def test_covered_image_before_resize_uses_original_proof_only(self):
        from context_protocol import digest,reply_digest
        self.db.executescript('CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY);'
            'CREATE TABLE memory_summary_coverage(scope TEXT,event TEXT,subject TEXT,digest TEXT,summary_id INTEGER);'
            'INSERT INTO memory_summaries VALUES(1);')
        original=user('2','summarized image')
        original['content']=[{'type':'text','text':original['content']},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,original'}}]
        limited=copy.deepcopy(original);limited['content'][1]['image_url']['url']='data:image/png;base64,limited'
        reply=assistant('covered image reply')
        self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,1)',
            ('private:12345678','2','12345678','v1:'+digest(original)+':'+reply_digest([reply])))
        self.db.commit()
        raw=self.base[:2]+[limited,reply]+self.projected[1:]
        proof=self.base[:2]+[original,reply]+self.projected[1:]
        projected=self.base[:2]+self.projected[1:]
        kwargs=dict(strict=True,reconstruction_scope='private:12345678',unprojected=raw+packet())
        with self.assertRaises(ContextReconstructionDenied):self.j.prepare(projected+packet(),1,**kwargs)
        p=self.j.prepare(projected+packet(),1,coverage_view=proof+packet(),**kwargs)
        self.assertEqual(p.wire[:len(self.first.wire)],self.first.wire)
        self.assertIsNone(p.reconstruction_permit)
        for mode in ('reply','author','text','order'):
            changed=copy.deepcopy(proof)
            if mode=='reply':changed[3]=assistant('changed reply')
            elif mode=='author':changed[2]['content'][0]['text']=changed[2]['content'][0]['text'].replace('12345678','87654321')
            elif mode=='text':changed[2]['content'][0]['text']+='edited'
            else:changed[2],changed[3]=changed[3],changed[2]
            with self.subTest(mode=mode),self.assertRaises(ContextReconstructionDenied):
                self.j.prepare(projected+packet(),1,coverage_view=changed+packet(),**kwargs)

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
