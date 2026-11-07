"""P1-1/P1-2: model-judged semantic importance in the same summary request.

Keyword-less real agreements are carried as protected claims with their real
stage; keyword chatter stays ordinary material; stages require verbatim
speaker evidence; the big merge sees the structured protected evidence.

Offline fixtures only: real SQLite, fake provider, no live calls.
"""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import summary_material as material
from behavior import BehaviorStore

SCOPE='private:12345678';PERSON='12345678'
CLEAN_CFG={'memorySummary':True,'summaryRounds':35,'naturalConversation':True}


def turns(*pairs):
    return [{'event':event,'sender_id':PERSON,'user':user,'bot':bot,'at':index} for index,(event,user,bot) in enumerate(pairs)]


class DeriveTests(unittest.TestCase):
    def derive(self,payload,data):
        return material.derive_claims(SCOPE,payload,{},[],data)

    def test_keywordless_agreement_is_protected_with_stage(self):
        payload=turns(('5','周六八点在东门见，下雨就改周日','好的，到时候见'))
        data={'commitments':[{'event':'5','topic':'周六东门见面','quote':'周六八点在东门见，下雨就改周日','who':'user','stage':'accepted','due':'周六八点'}]}
        claims=self.derive(payload,data)
        hit=[c for c in claims if c['kind']=='agreement' and c.get('protected')]
        self.assertEqual(len(hit),1)
        claim=hit[0]
        self.assertEqual(claim['status'],'open')
        self.assertEqual(claim['speaker']['role'],'human')
        self.assertEqual(claim['speaker']['id'],PERSON)
        self.assertEqual(claim['about'],'周六东门见面')
        self.assertEqual(claim['sourceRefs'][0]['role'],'user')
        self.assertTrue(material.protected_claim(claim))
        kept,dropped=material.fit_claims(claims)
        self.assertIn(claim['id'],{c['id'] for c in kept})
        self.assertEqual(dropped,set())

    def test_keyword_chatter_stays_ordinary_material(self):
        payload=turns(('6','今天学习承诺协议的定义','挺好的'))
        claims=self.derive(payload,{})
        self.assertEqual([c for c in claims if '承诺' in c['text']],[],
            'unselected keyword chatter is not transcribed, hence never protected')
        self.assertTrue(all(not material.protected_claim(c) for c in claims))
        kept,dropped=material.fit_claims(claims)
        self.assertEqual(dropped,set())

    def test_canceled_and_executed_stages_age_out(self):
        payload=turns(('7','我们约定周末去爬山','好'),('8','上周说的一起爬山去过了','是呀'))
        data={'commitments':[
            {'event':'7','topic':'周末爬山','quote':'我们约定周末去爬山','who':'user','stage':'canceled'},
            {'event':'8','topic':'爬山已完成','quote':'上周说的一起爬山去过了','who':'user','stage':'executed'}]}
        claims=self.derive(payload,data)
        by_about={c['about']:c for c in claims if c['kind']=='agreement'}
        self.assertEqual(by_about['周末爬山']['status'],'canceled')
        self.assertFalse(material.protected_claim(by_about['周末爬山']))
        self.assertEqual(by_about['爬山已完成']['status'],'done')
        self.assertFalse(material.protected_claim(by_about['爬山已完成']))
        kept,_=material.fit_claims(claims)
        self.assertTrue(all(len(c['text'])<=material.CLAIM_TEXT for c in kept if not material.protected_claim(c)))

    def test_commitment_requires_verbatim_speaker_evidence(self):
        payload=turns(('9','随便聊聊天气','那我周六八点在东门见你'))
        forged=self.derive(payload,{'commitments':[{'event':'9','topic':'东门见面','quote':'周六八点在东门见你','who':'user','stage':'accepted'}]})
        self.assertFalse([c for c in forged if c['kind']=='agreement'],'a quote not in the declared speaker text is rejected')
        honest=self.derive(payload,{'commitments':[{'event':'9','topic':'东门见面','quote':'周六八点在东门见你','who':'bot','stage':'proposed'}]})
        hit=[c for c in honest if c['kind']=='agreement']
        self.assertEqual(len(hit),1)
        self.assertEqual(hit[0]['speaker']['role'],'bot')
        self.assertTrue(material.protected_claim(hit[0]),'a bot proposal still awaits its answer')

    def test_third_fact_is_not_dropped_by_mechanical_quota(self):
        payload=turns(('10','我叫小寒','好'),('11','我住杭州','好'),('12','我属蛇','好'))
        data={'facts':[
            {'event':'10','name':'称呼','value':'小寒','quote':'我叫小寒','kind':'stable'},
            {'event':'11','name':'居住城市','value':'杭州','quote':'我住杭州','kind':'stable'},
            {'event':'12','name':'生肖','value':'蛇','quote':'我属蛇','kind':'stable'}]}
        self.store=BehaviorStore(Path(tempfile.mkdtemp())/'behavior.sqlite')
        try:
            before=None
            from companion_insights import apply,snapshot
            before=snapshot(self.store.db,SCOPE,{PERSON})
            saved=apply(self.store.db,self.store.memory,SCOPE,{'memory':True,'memoryAutoCapture':True},payload,data,before,now=1)
            values={r[0] for r in self.store.db.execute('SELECT name FROM facts WHERE active=1').fetchall()}
            self.assertTrue({'称呼','居住城市','生肖'}<=values,'all three facts survive the processing capacity')
        finally:
            self.store.close()


class BigMergeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.var=contextvars.ContextVar('p1',default=None);self.calls=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def small(self,scope,text,claims):
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',
            (scope,text,'["e"]',time.time(),json.dumps(claims,ensure_ascii=False))).lastrowid
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
        self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,scope));return identifier

    async def drain(self):
        for _ in range(12):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break

    async def test_big_merge_prompt_carries_protected_evidence(self):
        open_claim={'id':'aabbccdd11223344','text':'周三前把链接发我','kind':'agreement','speaker':{'role':'human','id':PERSON},
            'about':'要链接','sourceRefs':[material.claim_ref(SCOPE,'e1','user','a'*64)],'occurredAt':1.0,'status':'open','visibility':SCOPE,'protected':True}
        correction={'id':'bbeeccdd11223344','text':'不叫江哥，叫阿寒','kind':'correction','speaker':{'role':'human','id':PERSON},
            'about':'称呼','sourceRefs':[material.claim_ref(SCOPE,'e2','user','b'*64)],'occurredAt':2.0,'status':'applied','visibility':SCOPE,'protected':True,'value':'x'}
        for text,claims in (('第一条摘要',[open_claim]),('第二条摘要',[correction]),('第三条摘要',[])):
            self.small(SCOPE,text,claims)
        async def fixture(**kw):
            self.calls.append(kw);return NS(completion_text=json.dumps({'summary':'合并背景'},ensure_ascii=False))
        with patch.object(material,'MAX_CLAIMS_BYTES',32*1024):
            self.assertTrue(self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=fixture),lambda:True,{},self.var))
            await self.drain()
        self.assertTrue(self.calls,'the merge ran')
        prompt=json.loads(self.calls[0]['prompt'])
        evidence={item['id']:item for item in prompt.get('protected_evidence',[])}
        self.assertIn(open_claim['id'],evidence)
        self.assertEqual(evidence[open_claim['id']]['status'],'open')
        self.assertEqual(evidence[open_claim['id']]['speaker'],{'role':'human','id':PERSON})
        self.assertEqual(evidence[open_claim['id']]['sourceRefs'][0],{'scope':SCOPE,'event':'e1','role':'user'})
        self.assertLessEqual(len(json.dumps(prompt['protected_evidence'],ensure_ascii=False,separators=(',',':'))),8000)
        self.assertIn(correction['id'],evidence)
        self.assertIn('protected_evidence',self.calls[0]['system_prompt'])
        merged=self.db.execute('SELECT scope,kind FROM memory_summaries s JOIN memory_summary_levels l ON l.id=s.id').fetchall()
        self.assertEqual([(row[0],row[1]) for row in merged],[(SCOPE,'big')])


if __name__=='__main__':
    unittest.main()
