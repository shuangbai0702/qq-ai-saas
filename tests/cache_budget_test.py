"""Encoded media is transport, not text tokens; bounded journals remain lossless."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from decision_history import AppendHistory,history_usage,MAX_ADDED_CHARS,MAX_RETAINED_TEXT_CHARS,MAX_RETAINED_WIRE_CHARS
from decision_context import restore_append
from readonly_journal import ReadonlyJournal,ContextCapacityExceeded
from context_protocol import READONLY_CURRENT,model_json

def packet(state):return {'role':'user','content':READONLY_CURRENT+'\n'+model_json(state)}

class Budgets(unittest.TestCase):
    def test_multimodal_same_prefix_is_retained_without_losing_image_or_text(self):
        image='data:image/png;base64,'+'A'*450000
        base=[{'role':'system','content':'actual owner and permissions'}, {'role':'user','content':[
            {'type':'text','text':'question'},{'type':'image_url','image_url':{'url':image}}]}]
        state={'state':{'facts':['actual fact']}};incoming=base+[packet(state)];before=copy.deepcopy(incoming)
        h=ReadonlyJournal();first=h.prepare(incoming,1,now=1)
        self.assertGreater(first.usage['wireChars'],160000);self.assertLess(first.usage['textChars'],1000)
        self.assertEqual(first.usage['mediaChars'],len(image));self.assertTrue(h.commit(first))
        changed={'state':{'facts':['actual fact'],'emotion':'calm'}}
        second=h.prepare(base+[{'role':'assistant','content':'real reply'},{'role':'user','content':'next question'},packet(changed)],1,now=2)
        self.assertEqual(second.reason,'');self.assertEqual(second.wire[:len(first.wire)],first.wire)
        self.assertTrue(h.commit(second));self.assertEqual(incoming,before)
        continued=h.prepare(second.base+[packet(changed)],2,now=3)
        self.assertEqual(continued.reason,'');self.assertEqual(continued.wire[:len(second.wire)],second.wire)

    def test_media_byte_and_text_budgets_cannot_be_bypassed(self):
        for content,detail in [([{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*MAX_RETAINED_WIRE_CHARS}}],'wire_size_limit'),
                               ('x'*(MAX_RETAINED_TEXT_CHARS+1),'text_size_limit')]:
            h=ReadonlyJournal();incoming=[{'role':'user','content':content},packet({'state':{'facts':[]}})]
            with self.assertRaises(ContextCapacityExceeded):h.prepare(incoming,1)
            self.assertIsNone(h.last,'A capacity conflict cannot silently bootstrap an oversized history')
        literal='data:image/png;base64,'+'A'*200000
        usage=history_usage([{'role':'user','content':literal}]);self.assertEqual(usage['mediaChars'],0)
        self.assertGreater(usage['plainTextChars'],200000)

    def test_large_baseline_allows_bounded_growth_and_roundtrip(self):
        h=AppendHistory();state={'environment':{'now':1,'sharedContext':{'memoryEnabled':True}},'facts':['x'*130000]}
        first=h.prepare('authority',model_json(state),1,now=1);self.assertTrue(h.commit(first,'{}'));prefix=copy.deepcopy(h.history)
        state['newEvidence']='y'*40000;state['environment']['now']=2
        second=h.prepare('authority',model_json(state),1,now=2)
        self.assertEqual(second.reset_reason,'');self.assertGreater(sum(len(m['content']) for m in second.contexts),160000)
        self.assertEqual(second.contexts[:len(prefix)],prefix)
        self.assertEqual(restore_append(second.contexts+[{'role':'user','content':second.prompt}]),state)
        self.assertTrue(h.commit(second,'{}'))
        state['moreEvidence']='z'*MAX_ADDED_CHARS
        third=h.prepare('authority',model_json(state),1,now=3)
        self.assertEqual(third.reset_reason,'oversized_active_snapshot');self.assertEqual(third.reset_detail,'text_size_limit')
        self.assertEqual(restore_append(third.contexts+[{'role':'user','content':third.prompt}]),state)
        self.assertFalse(h.commit(third,'{}'),'An oversized current snapshot is served but must not be retained')

    def test_growth_budget_is_independent_of_absolute_limit(self):
        h=AppendHistory();state={'facts':['baseline']}
        first=h.prepare('authority',model_json(state),1,now=1);self.assertTrue(h.commit(first,'{}'))
        state['evidence']='x'*120000
        second=h.prepare('authority',model_json(state),1,now=2)
        self.assertEqual(second.reset_reason,'');self.assertTrue(h.commit(second,'{}'))
        state['more']='y'*(MAX_ADDED_CHARS-120000+100)
        third=h.prepare('authority',model_json(state),1,now=3)
        self.assertEqual(third.reset_reason,'history_size_boundary');self.assertEqual(third.reset_detail,'added_text_limit')
        self.assertEqual(restore_append(third.contexts+[{'role':'user','content':third.prompt}]),state)

    def test_unretained_result_is_still_complete_and_stale_candidate_cannot_commit(self):
        h=AppendHistory();state={'facts':['x'*(MAX_RETAINED_TEXT_CHARS+1)]}
        p=h.prepare('authority',model_json(state),1)
        self.assertEqual(p.reset_reason,'oversized_active_snapshot');self.assertEqual(p.reset_detail,'text_size_limit')
        self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),state)
        self.assertFalse(h.commit(p,'{}'));self.assertEqual(h.history,[])
        a=h.prepare('authority',model_json({'facts':['small']}),1)
        b=h.prepare('authority',model_json({'facts':['new']}),2)
        self.assertFalse(h.commit(a,'{}'));self.assertTrue(h.commit(b,'{}'))
        self.assertFalse(h.retire_committed(a));self.assertTrue(h.history)

if __name__=='__main__':unittest.main()
