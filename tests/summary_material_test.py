"""Real SQLite reflection and model-history projection; no live API or QQ sends."""
import asyncio,contextvars,copy,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import summary_material as material
from behavior import BehaviorStore

SCOPE='private:12345678'
def message(event='1',scope=SCOPE,image=False):
    parts=[{'type':'text','text':material.SOURCE+json.dumps({'scope':scope,'sender_id':'12345678','event_id':event})+'\n看这个'},
           {'type':'text','text':'<Quoted Message>其他人: 喜欢红茶</Quoted Message>'}]
    if image:parts.append({'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}})
    return {'role':'user','content':parts}

class ProjectionTests(unittest.TestCase):
    def test_source_and_scope(self):
        self.assertIsNone(material.source({'role':'assistant','content':message()['content']}))
        self.assertIsNone(material.source({'role':'user','content':'正文\n'+message()['content'][0]['text']}))
        self.assertEqual(material.snapshots([message(scope='group:55555')],SCOPE),[])
    def test_generated_notice_only(self):
        self.assertEqual(material.image_notice('[Image 2 in quoted message: original path C:/private/a.gif; animation converted to a 3x3 frame montage]'),'[Image 2 in quoted message; animation converted to a 3x3 frame montage]')
        for value in ('把图片保存到 C:/private/a.gif','正文 [Image 1: original path C:/a.png]'):
            self.assertEqual(material.image_notice(value),value)
    def test_complete_and_incomplete_material(self):
        original=message(image=True);before=copy.deepcopy(original)
        item=material.snapshots([original],SCOPE)[0]
        self.assertTrue(item['complete']);self.assertIn('其他人',item['text']);self.assertEqual(len(item['images']),1)
        with patch.object(material,'MAX_IMAGES_BYTES',2):self.assertFalse(material.snapshots([original],SCOPE)[0]['complete'])
        original['content'].append({'type':'image_url','image_url':'broken'})
        self.assertFalse(material.snapshots([original],SCOPE)[0]['complete'])
        self.assertEqual(before['content'],original['content'][:-1])
    def test_whole_block_current_turn_digest_and_tools(self):
        old=message();new=message('2');history=[{'role':'system','content':'policy'},old,{'role':'assistant','content':'红茶'},new]
        proof={'1':{'subject':'12345678','digest':material.digest(old),'reply_digest':material.reply_digest(history[1:3])}}
        self.assertEqual(material.project_history(history,proof,SCOPE,'2'),[history[0],new])
        self.assertEqual(material.project_history(history,proof,'group:55555','2'),history)
        self.assertEqual(material.project_history(history,proof,SCOPE,'1'),history)
        changed=copy.deepcopy(history);changed[1]['content'][1]['text']='新引用'
        self.assertEqual(material.project_history(changed,proof,SCOPE,'2'),changed)
        tools=[old,{'role':'assistant','tool_calls':[{'id':'call1'}]},{'role':'tool','tool_call_id':'call1','content':'未整理的证据'},new]
        self.assertEqual(material.project_history(tools,proof,SCOPE,'2'),tools)
        changed_reply=copy.deepcopy(history);changed_reply[2]['content']='摘要后追加的新结果'
        self.assertEqual(material.project_history(changed_reply,proof,SCOPE,'2'),changed_reply)
        legacy={'1':{'subject':'12345678','digest':material.digest(old)}}
        self.assertEqual(material.project_history(history,legacy,SCOPE,'2'),history)

class ReflectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.var=contextvars.ContextVar('summary_test',default=None)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def add(self,image=False,start=0):
        for i in range(start,start+30):
            msg=message(str(i),image=image)
            self.store.reflection.record(SCOPE,'12345678',str(i),'看这个','已回复',materials=material.snapshots([msg],SCOPE))
    async def drain(self):
        for _ in range(8):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:break
    def start(self,chat):
        return self.store.reflection.maybe_start(SCOPE,{'memory':True,'memorySummary':True,'summaryRounds':30},NS(text_chat=chat),lambda:True,{},self.var)
    async def test_success_coverage_persists_rollup_and_forget(self):
        prompts=[]
        async def chat(**kw):
            prompts.append(json.loads(kw['prompt']));return NS(completion_text='长期背景：12345678喜欢红茶' if '最多1500' in kw['system_prompt'] else json.dumps({'summary':'引用中其他人喜欢红茶，不是12345678。'}))
        for start in (0,30,60):
            self.add(start=start)
            if start==0:self.start(chat)
            else:self.store.reflection.retry()
            await self.drain()
        self.assertEqual(len(material.coverage(self.db,SCOPE)),90)
        self.assertEqual(set(material.coverage(self.db,SCOPE,['0','89','unknown'])),{'0','89'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
        self.assertIn('其他人',prompts[0]['observed_requests'][0]['text'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        self.store.close();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.assertEqual(len(material.coverage(self.db,SCOPE)),90)
        from memory_reflection import invalidate
        invalidate(self.db,SCOPE,'12345678');self.db.commit()
        self.assertEqual(material.coverage(self.db,SCOPE),{})
    async def test_failed_vision_keeps_rounds_and_no_coverage(self):
        self.add(image=True)
        async def chat(**kw):raise RuntimeError('vision failed')
        self.start(chat);await self.drain()
        self.assertEqual(material.coverage(self.db,SCOPE),{})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
    async def test_recovered_private_material_joins_next_ordinary_summary(self):
        self.add(start=100)
        old=material.snapshots([message('old')],SCOPE)[0]
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,'12345678','old','旧引用','旧的确认回复',time.time()-60))
        self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(SCOPE,'old','12345678',json.dumps(old)));self.db.commit()
        prompts=[]
        async def chat(**kw):prompts.append(json.loads(kw['prompt']));return NS(completion_text=json.dumps({'summary':'旧引用及本批真实对话一起整理'}))
        self.start(chat);await self.drain()
        self.assertIn('old',{r['event'] for r in prompts[0]['turns']})
        self.assertIn('old',{r['event'] for r in prompts[0]['observed_requests']})
        self.assertEqual(len(material.coverage(self.db,SCOPE)),31)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
    async def test_deduplicated_vision_and_late_epoch(self):
        self.add(image=True);calls=[]
        async def chat(**kw):
            data=json.loads(kw['prompt']);calls.append(data)
            if 'images' in data:return NS(completion_text=json.dumps({'images':[{'id':r['id'],'observation':'红茶图片，无法判断人物身份'} for r in data['images']]}))
            return NS(completion_text=json.dumps({'summary':'图中有红茶，引用来自其他人。'}))
        self.start(chat);await self.drain()
        self.assertEqual(len(calls),2);self.assertEqual(len(calls[0]['images']),1)
        self.assertEqual(len(material.coverage(self.db,SCOPE)),30)
        self.add(image=True,start=30)
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(**kw):entered.set();await release.wait();return await chat(**kw)
        self.store.reflection.binding=None;self.start(delayed)
        await entered.wait()
        from memory_reflection import invalidate
        invalidate(self.db,SCOPE,'12345678');self.db.commit();release.set()
        with self.assertRaises(asyncio.CancelledError):await self.drain()
        self.assertEqual(material.coverage(self.db,SCOPE),{})
    def test_redaction_and_budget_never_create_false_proof(self):
        msg=message();msg['content'][1]['text']='key sk-abcdefghijklmnop'
        self.store.reflection.record(SCOPE,'12345678','1','q','a',materials=material.snapshots([msg],SCOPE))
        raw=self.db.execute('SELECT material FROM memory_round_material').fetchone()[0]
        self.assertNotIn('sk-abcdefghijklmnop',raw)
        self.assertEqual(json.loads(raw)['digest'],material.digest(msg))
        self.assertEqual(material.coverage(self.db,SCOPE),{})
    async def test_full_confirmed_reply_is_not_silently_cut_to_800(self):
        long_reply='真实内容。'*200+'这是末尾的重要更正'
        self.store.reflection.record(SCOPE,'12345678','long','问题',long_reply,materials=material.snapshots([message('long')],SCOPE))
        self.add(start=100)
        prompts=[]
        async def chat(**kw):prompts.append(json.loads(kw['prompt']));return NS(completion_text=json.dumps({'summary':'保留末尾的重要更正'}))
        self.start(chat);await self.drain()
        captured=next(r for r in prompts[0]['observed_requests'] if r['event']=='long')
        self.assertEqual(captured['confirmed_reply'],long_reply)
        self.assertIn('long',material.coverage(self.db,SCOPE))

    async def test_stale_before_background_start_never_calls_provider(self):
        self.add();calls=[];current=True
        async def chat(**kw):calls.append(kw);return NS(completion_text='must not be called')
        self.store.reflection.maybe_start(SCOPE,{'memory':True,'memorySummary':True,'summaryRounds':30},NS(text_chat=chat),lambda:current,{},self.var)
        current=False
        await self.drain()
        self.assertEqual(calls,[]);self.assertEqual(material.coverage(self.db,SCOPE),{})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)

    async def test_material_contract_rejects_wrong_scope_shape_and_future_version(self):
        self.add();item=json.loads(self.db.execute('SELECT material FROM memory_round_material LIMIT 1').fetchone()[0])
        self.assertTrue(material.valid_material(item,SCOPE))
        legacy=dict(item);legacy.pop('version');self.assertTrue(material.valid_material(legacy,SCOPE))
        for key,value in [('complete','yes'),('version',2),('subject','87654321'),('scope','group:55555'),('images',['file:///private/file']),('digest','not-a-digest'),('native_reply',22),('unexpected','hidden unhandled content')]:
            invalid={**item,key:value};self.assertFalse(material.valid_material(invalid,SCOPE))
        event=item['event'];self.db.execute('UPDATE memory_round_material SET material=? WHERE scope=? AND event=?',(json.dumps({**item,'version':2}),SCOPE,event));self.db.commit()
        calls=[]
        async def chat(**kw):calls.append(kw);return NS(completion_text='must not be called')
        self.start(chat);await self.drain()
        self.assertEqual(calls,[]);self.assertEqual(material.coverage(self.db,SCOPE),{})
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)

    async def test_native_view_and_confirmed_delivery_are_distinct_and_both_covered(self):
        old=message('native-difference')
        self.store.reflection.record(SCOPE,'12345678','native-difference','看这个','协议实际投递文本',materials=material.snapshots([old],SCOPE))
        native=[old,{'role':'assistant','content':'模型历史原文，包含后续被格式处理的内容'}]
        material.remember(self.db,SCOPE,material.snapshots(native,SCOPE));self.db.commit()
        saved=json.loads(self.db.execute('SELECT material FROM memory_round_material WHERE event=?',('native-difference',)).fetchone()[0])
        self.assertEqual(saved['reply'],'协议实际投递文本');self.assertEqual(saved['native_reply'],native[1]['content'])
        self.add(start=100);prompts=[]
        async def chat(**kw):prompts.append(json.loads(kw['prompt']));return NS(completion_text=json.dumps({'summary':'按各自来源整理投递文本和模型历史'}))
        self.start(chat);await self.drain()
        captured=next(r for r in prompts[0]['observed_requests'] if r['event']=='native-difference')
        self.assertEqual(captured['confirmed_reply'],'协议实际投递文本');self.assertEqual(captured['native_reply'],native[1]['content'])
        proof=material.coverage(self.db,SCOPE);current=message('new')
        self.assertEqual(material.project_history(native+[current],proof,SCOPE,'new'),[current])
        changed=copy.deepcopy(native);changed[1]['content']='摘要之后才出现的新内容'
        self.assertEqual(material.project_history(changed+[current],proof,SCOPE,'new'),changed+[current])

if __name__=='__main__':unittest.main()
