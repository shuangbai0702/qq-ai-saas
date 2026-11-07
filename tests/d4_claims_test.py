"""D4 summary evidence and fidelity: claim attribution, delivery roles,
protected survival across merge and restart, coverage/release gating.

Offline fixtures only: real SQLite, no live API, no QQ sends, no production
writes. Model fixtures supply only the structured replies the deployed
reflection already consumes; claims themselves are derived by the system, so
the model never picks speakers, delivery state or visibility.
"""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import summary_material as material
from behavior import BehaviorStore
from data_admin import run as admin_run
from memory_scope import value_hash

SCOPE='private:12345678';PERSON='12345678'


def message(event,text):
    return {'role':'user','content':[{'type':'text','text':material.SOURCE+json.dumps({'scope':SCOPE,'sender_id':PERSON,'event_id':event})+'\n'+text}]}


def correction_claim(scope,event,text='我不叫江哥',subject='123'):
    """A valid stored protected claim, as the reflection itself would write."""
    return {'id':material.claim_identity(scope,event,'correction',text),'text':text[:material.PROTECTED_CLAIM_TEXT],
            'kind':'correction','speaker':{'role':'human','id':subject},'about':'称呼',
            'sourceRefs':[material.claim_ref(scope,event,'user','a'*64)],'occurredAt':1.0,
            'status':'applied','visibility':scope,'protected':True,'value':value_hash('江哥')}


class ClaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db
        self.var=contextvars.ContextVar('d4',default=None);self.now=time.time()-100000
    def tearDown(self):self.store.close();self.tmp.cleanup()

    def rounds(self,start=0,count=35,special=None):
        for i in range(start,start+count):
            user,bot=(special or {}).get(i,('普通话题'+str(i),'普通回复'+str(i)))
            self.store.reflection.record(SCOPE,PERSON,str(i),user,bot,self.now+i,materials=material.snapshots([message(str(i),user)],SCOPE))

    async def drain(self):
        for _ in range(12):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break

    def claims_of(self,identifier):
        row=self.db.execute('SELECT schema_version,claims FROM memory_summaries WHERE id=?',(identifier,)).fetchone()
        self.assertEqual(row[0],2,'new writes are schema_version=2')
        return json.loads(row[1]) if row[1] else []

    async def test_guess_denial_correction_survives_summary_merge_and_restart(self):
        """Bot guess -> user denial -> 35-round summary -> big merge -> restart:
        claims still tell who said it, whether it was delivered, when corrected."""
        self.store.memory.remember(SCOPE,PERSON,'称呼','江哥','before',self.now);self.db.commit()
        self.rounds(special={0:('你是不是叫江哥？','我猜你是江哥吧'),1:('我不叫江哥，以后叫我小寒','好的，小寒')})
        calls=[]
        async def chat(**kw):
            calls.append(kw)
            if len(calls)==1:
                return NS(completion_text=json.dumps({'summary':'机器人猜测称呼被本人否认并纠正为小寒。',
                    'evidence':[{'event':'0','who':'bot','quote':'我猜你是江哥吧'},{'event':'1','who':'user','quote':'我不叫江哥，以后叫我小寒'}],
                    'corrections':[{'event':'1','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，以后叫我小寒'}]}))
            return NS(completion_text=json.dumps({'summary':'普通持续背景'}))
        cfg={'memory':True,'memoryAutoCapture':True,'memorySummary':True,'summaryRounds':35,'naturalConversation':True}
        self.assertTrue(self.store.reflection.maybe_start(SCOPE,cfg,NS(text_chat=chat),lambda:True,{},self.var));await self.drain()
        self.assertEqual(self.db.execute('SELECT value FROM facts WHERE scope=? AND name=? AND active=1',(SCOPE,'称呼')).fetchone()[0],'小寒')
        small1=self.db.execute('SELECT id FROM memory_summaries ORDER BY id DESC LIMIT 1').fetchone()[0]
        claims=self.claims_of(small1)
        correction=next(c for c in claims if c['kind']=='correction')
        self.assertEqual(correction['speaker'],{'role':'human','id':PERSON},'the denial belongs to its real author')
        self.assertEqual(correction['about'],'称呼');self.assertEqual(correction['status'],'applied')
        self.assertEqual(correction['value'],value_hash('江哥'));self.assertTrue(correction['protected'])
        self.assertEqual(correction['sourceRefs'][0]['event'],'1')
        guess=next(c for c in claims if c['kind']=='bot_statement' and '江哥吧' in c['text'])
        self.assertEqual(guess['speaker'],{'role':'bot'},'the guess stays a bot statement')
        self.assertEqual((guess['delivery'],guess['status']),('confirmed','confirmed'))
        self.assertFalse(any(c['speaker']['role']=='human' and '江哥吧' in c['text'] for c in claims),'roles never mix')
        entry=json.loads(self.db.execute("SELECT rejected_values FROM memory_blocks WHERE scope=? AND subject=? AND name='称呼'",(SCOPE,PERSON)).fetchone()[0])[0]
        self.assertEqual(entry['v'],correction['value']);self.assertEqual(str(entry['event']),correction['sourceRefs'][0]['event'],'claim and block reference each other')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
        small_protected={c['id'] for c in claims if material.protected_claim(c)}
        for start in (35,70):
            self.rounds(start=start);self.store.reflection.retry();await self.drain()
        big=self.db.execute("SELECT s.id,s.claims FROM memory_summaries s JOIN memory_summary_levels l ON l.id=s.id WHERE l.kind='big'").fetchone()
        self.assertIsNotNone(big)
        big_claims=json.loads(big[1])
        inherited=next(c for c in big_claims if c['id']==correction['id'])
        self.assertEqual(inherited['speaker'],correction['speaker']);self.assertEqual(inherited['value'],correction['value'])
        self.assertTrue(any(p['summary']==small1 and p['claim']==correction['id'] for p in inherited['parents']),'merge keeps a traceable parent chain')
        self.assertTrue(small_protected<={c['id'] for c in big_claims},'protected set survives the merge unchanged')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage WHERE summary_id=?',(big[0],)).fetchone()[0],105)
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        row=self.db.execute('SELECT schema_version,claims FROM memory_summaries WHERE id=?',(big[0],)).fetchone()
        self.assertEqual(row[0],2)
        after={c['id']:c for c in json.loads(row[1])}
        self.assertIn(correction['id'],after)
        self.assertEqual(after[correction['id']]['speaker'],{'role':'human','id':PERSON})
        self.assertEqual(after[correction['id']]['status'],'applied')
        entry=json.loads(self.db.execute("SELECT rejected_values FROM memory_blocks WHERE scope=? AND subject=? AND name='称呼'",(SCOPE,PERSON)).fetchone()[0])[0]
        self.assertEqual(entry['v'],after[correction['id']]['value'])

    async def test_native_only_reply_is_observed_never_human_and_holds_release(self):
        event='native-only'
        item=material.snapshots([message(event,'看这个')],SCOPE)[0]
        item['native_reply']='模型历史原文，未确认投递'
        self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,event,PERSON,json.dumps(item)))
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,PERSON,event,'看这个','',self.now));self.db.commit()
        self.rounds()
        metrics={}
        async def chat(**kw):return NS(completion_text=json.dumps({'summary':'普通背景，未确认的模型原文只作观察','evidence':[{'event':'native-only','who':'bot','quote':'模型历史原文，未确认投递'}]}))
        self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=chat),lambda:True,metrics,self.var)
        await self.drain()
        identifier=self.db.execute('SELECT id FROM memory_summaries ORDER BY id DESC LIMIT 1').fetchone()[0]
        claims=self.claims_of(identifier)
        observed=next(c for c in claims if c.get('delivery')=='observed')
        self.assertEqual((observed['kind'],observed['status'],observed['speaker']),('bot_statement','observed',{'role':'bot'}))
        self.assertIn('模型历史原文',observed['text'])
        self.assertFalse(any(c['speaker']['role']=='human' and '模型历史原文' in c['text'] for c in claims),'native wording is never a human statement')
        self.assertNotIn(event,material.coverage(self.db,SCOPE),'no complete proof without a delivery confirmation')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material WHERE event=?',(event,)).fetchone()[0],1,'raw native history is retained')
        self.assertEqual(metrics.get('summaryReleaseHolds'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0,'covered rounds without holds still retire')

    async def test_v1_rows_read_without_claims_and_merge_without_fabrication(self):
        for i in range(3):
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(SCOPE,'旧版摘要'+str(i),'["e"]',self.now+i)).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
        self.db.commit()
        for row in self.db.execute('SELECT id,schema_version,claims FROM memory_summaries').fetchall():
            self.assertEqual((row[1],row[2]),(1,None));self.assertEqual(material.summary_claims(self.db,row[0]),[])
        async def chat(**kw):return NS(completion_text='旧摘要合并后的持续背景')
        self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'naturalConversation':False},NS(text_chat=chat),lambda:True,{},self.var)
        await self.drain()
        big=self.db.execute("SELECT s.id,s.schema_version,s.claims FROM memory_summaries s JOIN memory_summary_levels l ON l.id=s.id WHERE l.kind='big'").fetchone()
        self.assertIsNotNone(big);self.assertEqual(big[1],2);self.assertIsNone(big[2],'v1 sources are not back-filled with invented claims')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)

    async def test_dropped_protected_claim_holds_its_round_and_material(self):
        self.rounds(special={7:('我们约定周末一起去平江路','好的')})
        metrics={}
        async def chat(**kw):return NS(completion_text=json.dumps({'summary':'普通背景','commitments':[{'event':'7','topic':'周末平江路','quote':'我们约定周末一起去平江路','who':'user','stage':'accepted'}]}))
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'summaryRounds':35,'naturalConversation':True},NS(text_chat=chat),lambda:True,metrics,self.var)
            await self.drain()
        self.assertEqual(metrics.get('summaryReleaseHolds'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1,'exactly the held round stays')
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_rounds WHERE event='7'").fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],1)
        self.assertNotIn('7',material.coverage(self.db,SCOPE),'unretained protected evidence cannot authorize native history omission')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0,'incomplete protection cannot authorize rebuilding')

    async def test_big_merge_reports_conflict_instead_of_dropping_protected(self):
        identifiers=[]
        for i in range(3):
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'来源'+str(i),'["e"]',self.now+i,json.dumps([correction_claim(SCOPE,'e'+str(i))]))).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
            self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,SCOPE));identifiers.append(identifier)
        self.db.commit();metrics={};calls=[]
        async def chat(**kw):calls.append(kw);return NS(completion_text='合并背景')
        with patch.object(material,'MAX_CLAIMS_BYTES',2):
            self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'naturalConversation':False},NS(text_chat=chat),lambda:True,metrics,self.var)
        self.assertEqual(calls,[],'a merge that cannot carry protected claims never runs')
        self.assertEqual(metrics.get('summaryProtectedConflicts'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],3)
        self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
        self.store.reflection.retry();await self.drain()
        big=self.db.execute("SELECT s.id,s.claims FROM memory_summaries s JOIN memory_summary_levels l ON l.id=s.id WHERE l.kind='big'").fetchone()
        self.assertIsNotNone(big)
        self.assertEqual({c['id'] for c in json.loads(big[1])},{material.claim_identity(SCOPE,'e'+str(i),'correction','我不叫江哥') for i in range(3)})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)

    def test_claim_shape_gate_and_mutual_block_reference(self):
        good=correction_claim(SCOPE,'e1')
        self.assertTrue(material.valid_claim(good))
        for key,value in (('speaker',{'role':'owner','id':PERSON}),('kind','model_fact'),('text','x'*401),('sourceRefs',[]),('visibility','')):
            self.assertFalse(material.valid_claim({**good,key:value}),key)
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'x','[]',1,json.dumps([good,{**good,'speaker':{'role':'owner'}},'garbage']))).lastrowid
        self.db.commit()
        self.assertEqual([c['id'] for c in material.summary_claims(self.db,identifier)],[good['id']])
        self.db.executemany('INSERT INTO memory_blocks(scope,subject,name,block_all,rejected_values,revision) VALUES(?,?,?,?,?,?)',[
            (SCOPE,PERSON,'称呼',0,json.dumps([{'v':'a','event':'e1','quote':'q','at':1}]),1),
            (SCOPE,PERSON,'昵称',1,json.dumps([{'v':'b','event':'e2','quote':'q','at':1}]),0),
            (SCOPE,'99999999','称呼',0,json.dumps([{'v':'c','event':'e3','quote':'q','at':1}]),0)])
        found=material.batch_corrections(self.db,SCOPE,['e1','e2','e3'])
        self.assertEqual([(c['name'],c['event']) for c in found],[('称呼','e1')],'hard forgets and other people contribute nothing')
        batch=material.derive_claims(SCOPE,[{'event':'e1','sender_id':PERSON,'user':'我们约定明天爬山','bot':'好','at':1.0}],{},found)
        self.assertEqual(batch[0]['kind'],'correction');self.assertEqual(batch[0]['speaker']['id'],PERSON)
        agreement=material.derive_claims(SCOPE,[{'event':'e4','sender_id':PERSON,'user':'我们约定明天爬山','bot':'好','at':1.0}],{},[])
        self.assertEqual(agreement,[],'unselected chat is never transcribed: a keyword mention is neither carried nor protected')
        staged=material.derive_claims(SCOPE,[{'event':'e4','sender_id':PERSON,'user':'我们约定明天爬山','bot':'好','at':1.0}],{},[],{'commitments':[{'event':'e4','topic':'明天爬山','quote':'我们约定明天爬山','who':'user','stage':'accepted'}]})
        hit=[claim for claim in staged if claim.get('protected')]
        self.assertEqual(len(hit),1);self.assertEqual(hit[0]['status'],'open');self.assertEqual(hit[0]['about'],'明天爬山')
        plain=material.derive_claims(SCOPE,[{'event':'e5','sender_id':PERSON,'user':'今天天气不错','bot':'嗯','at':1.0}],{},[])
        self.assertEqual(plain,[],'ordinary chatter without a semantic selection stays summary prose only')
        selected=material.derive_claims(SCOPE,[{'event':'e5','sender_id':PERSON,'user':'今天天气不错','bot':'嗯','at':1.0}],{},[],{'evidence':[{'event':'e5','who':'user','quote':'今天天气不错'}]})
        self.assertEqual((selected[0]['kind'],selected[0]['speaker']),('human_statement',{'role':'human','id':PERSON}))
        self.assertNotIn('protected',selected[0])
        turns=[{'event':'e6','sender_id':PERSON,'user':'改天教我昆曲吧','bot':'下次我们聊昆曲。','at':1.0}]
        open_topic=material.derive_claims(SCOPE,turns,{},[],{'motives':[{'event':'e6','topic':'昆曲','quote':'下次我们聊昆曲。','status':'open'}]})
        interpretation=next(c for c in open_topic if c['kind']=='interpretation')
        self.assertEqual((interpretation['status'],interpretation['speaker']),('open',{'role':'bot'}))
        unevidenced=[{'event':'e7','sender_id':PERSON,'user':'无','bot':'无','at':1.0}]
        self.assertEqual(material.derive_claims(SCOPE,unevidenced,{},[],{'motives':[{'event':'e7','topic':'昆曲','quote':'没有说过的话','status':'open'}]}),material.derive_claims(SCOPE,unevidenced,{},[]),'unevidenced motives stay out')

    async def test_core_note_inherits_protected_claims(self):
        identifiers=[]
        for i in range(2):
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'来源'+str(i),'["e"]',self.now+i,json.dumps([correction_claim(SCOPE,'e'+str(i))]))).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
            self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,SCOPE));identifiers.append(identifier)
        self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(SCOPE,'旧版','["e"]',self.now+9));self.db.commit()
        async def chat(**kw):return NS(completion_text=json.dumps({'summary':'合并后的大摘要','core_note':{'text':'一段核心背景','summaries':identifiers}}))
        self.store.reflection.maybe_start(SCOPE,{'memorySummary':True,'naturalConversation':False},NS(text_chat=chat),lambda:True,{},self.var)
        await self.drain()
        row=self.db.execute('SELECT text,schema_version FROM memory_core_notes WHERE slot=1').fetchone()
        self.assertEqual((row[0],row[1]),('一段核心背景',2))
        claims=self.store.reflection.core_note_claims()
        self.assertEqual({c['id'] for c in claims},{material.claim_identity(SCOPE,'e'+str(i),'correction','我不叫江哥') for i in range(2)})
        self.assertTrue(all(any(p['summary'] in identifiers for p in c['parents']) for c in claims))

    def test_admin_listing_shows_schema_and_claim_count(self):
        self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(SCOPE,'旧版','[]',1))
        self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',(SCOPE,'新版','[]',2,json.dumps([correction_claim(SCOPE,'e1'),correction_claim(SCOPE,'e2')])))
        self.db.commit()
        result=admin_run(self.path,{'action':'list'},connection=self.db)
        listed=sorted(result['summaries'],key=lambda s:s['updated'])
        self.assertEqual([s['schema_version'] for s in listed],[1,2])
        self.assertEqual(listed[0]['claim_count'],0);self.assertEqual(listed[1]['claim_count'],2)

    def test_fit_keeps_protected_first_within_bounds(self):
        claims=[correction_claim(SCOPE,'e'+str(i)) for i in range(3)]
        claims+=[{'id':material.claim_identity(SCOPE,'c'+str(i),'human_statement','普通话题'+str(i)),
                  'text':'普通话题'+str(i),'kind':'human_statement','speaker':{'role':'human','id':PERSON},'about':'',
                  'sourceRefs':[material.claim_ref(SCOPE,'c'+str(i),'user','b'*64)],'occurredAt':float(i),
                  'status':'active','visibility':SCOPE} for i in range(60)]
        kept,dropped=material.fit_claims(claims,budget=8192,max_claims=20)
        self.assertEqual(len([c for c in kept if c['kind']=='correction']),3,'protected claims are kept first')
        self.assertEqual(len(kept),20)
        self.assertLessEqual(len(json.dumps(kept,ensure_ascii=False,separators=(',',':')).encode()),8192)


if __name__=='__main__':unittest.main(verbosity=2)
