"""Isolated real SQLite, controlled time/model and transport; no real sends."""
import asyncio,contextvars,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from unittest.mock import AsyncMock
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from memory_strength import *
import companion_state
from social import reinforce_expression
from media_egress import valid_url,download_file
from tool_outcome import project

class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_dns_never_connects(self):
        from model_egress import GuardedBackend
        backend=NS(connect_tcp=AsyncMock())
        loop=asyncio.get_running_loop()
        with patch.object(loop,'getaddrinfo',AsyncMock(return_value=[(2,1,6,'',('127.0.0.1',443))])):
            with self.assertRaises(httpx.ConnectError):await GuardedBackend(backend).connect_tcp('example.com',443)
        backend.connect_tcp.assert_not_awaited()
    def transport(self,handler):
        mock=httpx.MockTransport(handler);mock._pool=NS(_network_backend=None)
        return patch('media_egress.httpx.AsyncHTTPTransport',return_value=mock)
    async def test_stream_success_and_redirect_denial(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'media'
            with self.transport(lambda req:httpx.Response(200,content=b'image')):await download_file('https://example.com/a',path)
            self.assertEqual(path.read_bytes(),b'image')
            with self.transport(lambda req:httpx.Response(302,headers={'location':'http://169.254.169.254/'})):
                with self.assertRaises(ValueError):await download_file('https://example.com/a',path)
            self.assertEqual(path.read_bytes(),b'image')
            self.assertEqual(len(list(Path(tmp).iterdir())),1)
    async def test_byte_limit_and_timeout_cleanup(self):
        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):yield b'abcd';yield b'efgh'
        class Wait(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'first'
                await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'media'
            with self.transport(lambda req:httpx.Response(200,stream=Bytes())),patch('media_egress.MEDIA_BYTES',6):
                with self.assertRaises(ValueError):await download_file('https://example.com/a',path)
            self.assertEqual(list(Path(tmp).iterdir()),[])
            with self.transport(lambda req:httpx.Response(200,stream=Wait())),patch('media_egress.MEDIA_SECONDS',.02):
                with self.assertRaises(TimeoutError):await download_file('https://example.com/a',path)
            self.assertEqual(list(Path(tmp).iterdir()),[])

class StrengthTests(unittest.TestCase):
    def test_cross_time(self):
        for days in (2,30,90):
            self.assertAlmostEqual(interest_strength(1,1,0,days*DAY),2**(-days/2))
            self.assertAlmostEqual(interest_strength(1,3,0,days*DAY),2**(-days/30))
            self.assertEqual(fact_strength('stable',0,days*DAY),1)
        self.assertLess(fact_strength('state',0,30*DAY),fact_strength('event',0,30*DAY))
    def test_urls(self):
        for value in ('http://127.0.0.1/a','http://10.1.2.3/a','http://169.254.169.254/a','http://[::1]/','http://localhost/a','file:///private','https://x/a\n'):
            self.assertFalse(valid_url(value),value)
        self.assertTrue(valid_url('https://example.com/a'))
    def test_receipt_projection(self):
        self.assertEqual(project('已成功发出',write=True)['receipt']['status'],'unknown')
        for state in ('confirmed','rejected','partial','unknown'):
            self.assertEqual(project({'receipt':{'status':state}},write=True)['receipt']['status'],state)

class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.db=self.store.db;self.scope='private:12345678';self.var=contextvars.ContextVar('structural',default=None)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def add(self,count,start=0):
        for i in range(start,start+count):self.store.reflection.record(self.scope,'12345678',str(i),'我喜欢红茶'+str(i),'已回应',time.time())
    async def drain(self):
        for _ in range(12):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break
    async def test_shared_thirty_five_round_cadence_and_rollup(self):
        calls=[]
        async def chat(**kw):
            calls.append(kw)
            return NS(completion_text='持续背景：12345678喜欢红茶，来源私聊。' if '最多1500' in kw['system_prompt'] else json.dumps({'summary':'小摘要：12345678喜欢红茶。','facts':[]}))
        cfg={'memory':True,'memorySummary':True,'summaryRounds':35,'naturalConversation':True}
        self.add(34)
        self.assertFalse(self.store.reflection.maybe_start(self.scope,cfg,NS(text_chat=chat),lambda:True,{},self.var))
        self.add(1,34)
        self.assertTrue(self.store.reflection.maybe_start(self.scope,cfg,NS(text_chat=chat),lambda:True,{},self.var));await self.drain()
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        self.assertEqual(len(calls),1)
        self.add(35,35);self.store.reflection.retry();await self.drain()
        self.assertEqual(len(self.store.reflection.rolling(self.scope)),2)
        self.add(35,70);self.store.reflection.retry();await self.drain()
        rows=self.store.reflection.rolling(self.scope)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['level'],'big')
        self.assertIn('持续背景',self.store.reflection.recall(self.scope,'毫不相关的问题')[0]['summary'])
        self.assertEqual(sum('最多1500' in c['system_prompt'] for c in calls),1)
        self.assertEqual(sum('本次同时生成' in c['system_prompt'] for c in calls),3)
    async def test_default_thirty_five_round_cadence(self):
        calls=[]
        async def chat(**kw):calls.append(kw);return NS(completion_text=json.dumps({'summary':'有来源的持续信息'}))
        cfg={'memory':True,'memorySummary':True,'naturalConversation':True}
        self.add(34)
        self.assertFalse(self.store.reflection.maybe_start(self.scope,cfg,NS(text_chat=chat),lambda:True,{},self.var))
        self.add(1,34)
        self.assertTrue(self.store.reflection.retry());await self.drain()
        self.assertEqual(len(calls),1);self.assertEqual(len(json.loads(calls[0]['prompt'])['turns']),35)
        self.assertIn('本次同时生成',calls[0]['system_prompt'])
    async def test_rollup_failure_and_late_forget(self):
        for i in range(3):self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.scope,'旧摘要'+str(i),'[]',time.time()))
        self.db.commit()
        entered=asyncio.Event();release=asyncio.Event()
        async def chat(**kw):entered.set();await release.wait();return NS(completion_text='不得复活的合并')
        self.store.reflection.maybe_start(self.scope,{'memorySummary':True},NS(text_chat=chat),lambda:True,{},self.var)
        await entered.wait()
        from memory_reflection import invalidate
        invalidate(self.db,self.scope,'12345678');self.db.commit();release.set();await self.drain()
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
    async def test_one_global_background_keeps_all_origins(self):
        scopes=['group:55555','group:66666','private:12345678']
        for scope in scopes:
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,'来源'+scope,'[]',time.time())).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
        # Cross-scope smalls are never merged together; two legacy bigs with
        # identical origins may merge with each other instead.
        peers=[]
        for text in ('旧全局合并A','旧全局合并B'):
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version) VALUES(?,?,?,?,2)',('self',text,'[]',time.time())).lastrowid
            for origin in scopes:self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,origin))
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'big')",(identifier,));peers.append(identifier)
        self.db.commit();prompts=[]
        async def chat(**kw):prompts.append(json.loads(kw['prompt']));return NS(completion_text='同一个机器人跨会话的持续背景')
        self.store.reflection.maybe_start(scopes[0],{'memorySummary':True},NS(text_chat=chat),lambda:True,{},self.var)
        await self.drain()
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],4)
        # D2: the merged background keeps every origin, but a multi-origin
        # merge is bot-shared material shown in self, not re-injected into
        # each conversation's request (the audited id=52 cross-scope leak).
        for scope in scopes:
            self.assertEqual({r['id'] for r in self.store.reflection.rolling(scope)},{self.db.execute('SELECT id FROM memory_summaries WHERE scope=?',(scope,)).fetchone()[0],self.store.reflection.rolling('self')[0]['id']})
        self.assertEqual([r['id'] for r in self.store.reflection.rolling('group:77777')],[self.store.reflection.rolling('self')[0]['id']])
        shared=self.store.reflection.rolling('self')
        self.assertEqual(len(shared),1);self.assertEqual(set(shared[0]['source_scopes']),set(scopes))
        self.assertEqual({s for item in prompts[0]['summaries'] for s in item['source_scopes']},set(scopes))
        from memory_reflection import invalidate
        invalidate(self.db,scopes[1]);self.db.commit()
        self.assertEqual(self.store.reflection.rolling('self'),[])
        self.assertEqual([r['id'] for r in self.store.reflection.rolling(scopes[0])],[self.db.execute('SELECT id FROM memory_summaries WHERE scope=?',(scopes[0],)).fetchone()[0]])
    def test_interest_relevance_decay_and_repetition(self):
        now=time.time();cfg={'behavior':{'selfDevelopment':True}}
        for i in range(3):companion_state.note_interest(self.db,self.scope,'红茶',now+i*DAY,'event'+str(i),'conversation_heat')
        row=self.db.execute('SELECT hits,weight FROM companion_interests').fetchone();self.assertEqual(row[0],3)
        companion_state.note_interest(self.db,self.scope,'红茶',now+2*DAY+1,'repeat','bot_expression')
        self.assertEqual(self.db.execute('SELECT hits FROM companion_interests').fetchone()[0],3)
        self.db.execute('INSERT INTO companion_interests VALUES(?,?,?,?,?,?,?)',(self.scope,'热门话题',4,1,now+90*DAY,'new','conversation_heat'))
        ctx=companion_state.context(self.db,cfg,self.scope,now+90*DAY,'红茶')
        self.assertEqual(ctx['interests'][0]['topic'],'红茶')
    def test_expression_use_feedback_and_cold_recall(self):
        social=self.store.social;now=time.time()
        self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',(self.scope,'齁啦','喝红茶时','[]',1,now))
        reinforce_expression(self.db,self.scope,'齁啦','use','u',now+DAY)
        first=self.db.execute('SELECT weight FROM social_patterns').fetchone()[0]
        for i in range(30):reinforce_expression(self.db,self.scope,'齁啦','use',str(i),now+DAY+1)
        self.assertEqual(self.db.execute('SELECT weight FROM social_patterns').fetchone()[0],first)
        reinforce_expression(self.db,self.scope,'齁啦','positive','p',now+DAY+2)
        self.assertGreater(self.db.execute('SELECT weight FROM social_patterns').fetchone()[0],first+.2)
        with patch('social.time.time',return_value=now+90*DAY):self.assertEqual(social.expressions(self.scope,'喝红茶')[0]['expression'],'齁啦')
        self.db.execute('INSERT OR IGNORE INTO social_pattern_blocks VALUES(?,?)',(self.scope,'齁啦'))
        self.assertEqual(social.expressions(self.scope,'喝红茶'),[])
    def test_group_rounds_retain_thirteen_messages_and_thirty_five_rounds(self):
        scope='group:55555';base=time.time()-1000
        for turn in range(35):
            for i in range(25):
                at=base+turn*30+i
                self.store.reflection.incoming(scope,'12345678',f'{turn}-{i}','真实群消息'+str(i),at)
            self.store.reflection.record(scope,'12345678',f'{turn}-24','真实群消息24','本轮回复',base+turn*30+26,context_through=base+turn*30+25)
        history=self.store.reflection.group_context(scope)
        self.assertEqual(len(history),35)
        self.assertTrue(all(len(x['messages'])==13 and x['reply'] for x in history))
        self.assertEqual(sum(len(x['messages'])+1 for x in history),490)
        self.store.reflection.incoming(scope,'12345678','new','下一轮',base+1100)
        history=self.store.reflection.group_context(scope)
        self.assertEqual(len(history),35);self.assertEqual(len(history[-1]['messages']),1)
        self.assertIsNone(history[-1]['reply'])
        self.assertEqual(len(history[-2]['messages']),13)

    def test_legacy_round_retains_original_question(self):
        scope='group:55555';at=time.time()
        self.db.execute('INSERT INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',(scope,'12345678','legacy','原始问题','真实回答',at))
        self.db.commit()
        row=self.store.reflection.group_context(scope)[0]
        self.assertEqual(row['messages'][0]['text'],'原始问题')
        self.assertEqual(row['messages'][0]['sender_id'],'12345678')
        self.assertEqual(row['messages'][0]['source_event'],'legacy')

    async def test_group_summary_keeps_new_unanswered_messages(self):
        scope='group:55555';base=time.time()-1000
        for turn in range(35):
            at=base+turn*20
            self.store.reflection.incoming(scope,'12345678',str(turn),'本轮内容',at)
            self.store.reflection.record(scope,'12345678',str(turn),'本轮内容','真实回复',at+10,context_through=at+1)
        # Arrived after the final model snapshot, before its successful send.
        self.store.reflection.incoming(scope,'23456789','late','尚未回应的新问题',base+34*20+5)
        seen=[]
        async def chat(**kw):
            seen.append(json.loads(kw['prompt']))
            return NS(completion_text=json.dumps({'summary':'有会话与人物来源的持续背景'}))
        self.store.reflection.maybe_start(scope,{'memorySummary':True,'summaryRounds':35},NS(text_chat=chat),lambda:True,{},self.var)
        await self.drain()
        self.assertNotIn('late',[r['event'] for r in seen[0]['turns']])
        pending=self.db.execute('SELECT event FROM memory_pending WHERE scope=?',(scope,)).fetchall()
        self.assertEqual(pending,[('late',)])
        history=self.store.reflection.group_context(scope)
        self.assertEqual(len(history),1);self.assertEqual(history[0]['messages'][0]['source_event'],'late')
    def test_inflight_burst_cannot_erase_confirmed_round(self):
        scope='group:55555';at=time.time()-100
        for i in range(15):self.store.reflection.incoming(scope,'12345678','old'+str(i),'原始输入'+str(i),at+i)
        snapshot=self.store.reflection.group_context(scope)[-1]['messages']
        for i in range(40):self.store.reflection.incoming(scope,'23456789','new'+str(i),'新输入'+str(i),at+30+i)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending WHERE scope=?',(scope,)).fetchone()[0],13)
        self.store.reflection.record(scope,'12345678','old14','原始输入14','真实回复',at+80,context_through=at+20,context_messages=snapshot)
        history=self.store.reflection.group_context(scope)
        self.assertEqual(len(history),2)
        self.assertEqual([m['source_event'] for m in history[0]['messages']],['old'+str(i) for i in range(2,15)])
        self.assertEqual([m['source_event'] for m in history[1]['messages']],['new'+str(i) for i in range(27,40)])

if __name__=='__main__':unittest.main()
