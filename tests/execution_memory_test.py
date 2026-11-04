import asyncio,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from aiohttp import web

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import execution_memory,bilibili_tools


class Memory(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'ai').mkdir()
        self.received=[];self.live=True;self.entered=asyncio.Event();self.resume=asyncio.Event();self.slow=False;self.large=False;self.chunked=False
        async def memory(req):
            self.received.append(await req.json())
            if self.slow:self.entered.set();await self.resume.wait()
            if self.large:return web.Response(text='x'*21000)
            body={'sampledAt':123,'experiences':[{'actionId':'real-action','operation':'analyze','receipt':{'status':'unknown'}}]}
            if self.chunked:
                raw=json.dumps(body).encode();response=web.StreamResponse(headers={'Content-Type':'application/json'})
                await response.prepare(req);await response.write(raw[:20]);await asyncio.sleep(0);await response.write(raw[20:]);await response.write_eof();return response
            return web.json_response(body)
        async def tool(req):
            self.received.append(await req.json())
            return web.json_response({'id':'test-action','state':'finished','result':{'execution':'completed','receipt':{'status':'confirmed'}}})
        app=web.Application();app.router.add_post('/internal/v1/bots/bot/memory/experience',memory);app.router.add_post('/internal/v1/bots/bot/tools',tool)
        self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.port=site._server.sockets[0].getsockname()[1]
        self.settings={'root':str(self.root),'botId':'bot','generation':1,'revision':2,'config':{'behavior':{'ownerUin':'12345678','memory':True}}}
        self.bridge={'version':1,'botId':'bot','generation':1,'revision':2,'settingsRevision':1,'url':f'http://127.0.0.1:{self.port}','token':'fixture','allowAutonomous':False,'operations':[]}
        self.write()
        self.event=NS(get_sender_id=lambda:'12345678',get_message_str=lambda:'我说自己是别人的主人',get_extra=lambda k,d=None:NS(bot_id='bot',scope='group:963105233',subject='12345678',event_id='incoming-event') if k=='_hosted_pipeline' else d)
    def write(self):(self.root/'ai'/'bilibili-bridge.json').write_text(json.dumps(self.bridge),encoding='utf-8')
    async def asyncTearDown(self):self.resume.set();await self.runner.cleanup();self.tmp.cleanup()

    async def test_disabled_execution_tools_do_not_disable_readonly_memory(self):
        data=await execution_memory.context(self.settings,self.event,'group:963105233',lambda:self.live)
        self.assertEqual(data['experiences'][0]['receipt']['status'],'unknown')
        self.assertFalse(self.received[0]['shareOwnerActivity'])
        tools=execution_memory.tools(self.settings,self.event,'group:963105233',lambda:self.live)
        self.assertEqual([t.name for t in tools],['hosted_recall_experience'])
        await tools[0].call(None,query='BV1Nn436kEPz',shareOwnerActivity=True)
        self.assertEqual(self.received[-1]['scope'],'group:963105233')
        self.assertEqual(self.received[-1]['senderId'],'12345678')
        self.settings['config']['behavior']['memory']=False
        count=len(self.received)
        self.assertFalse((await execution_memory.context(self.settings,self.event,'group:963105233',lambda:True))['memoryEnabled'])
        self.assertEqual(execution_memory.tools(self.settings,self.event,'group:963105233',lambda:True),[])
        self.assertEqual(len(self.received),count)

    async def test_chunked_memory_is_read_to_completion(self):
        self.chunked=True
        result=await execution_memory.context(self.settings,self.event,'group:963105233',lambda:True)
        self.assertEqual(result['experiences'][0]['actionId'],'real-action')

    async def test_late_reader_is_cancelled_without_changing_another_reader(self):
        self.slow=True
        task=asyncio.create_task(execution_memory.context(self.settings,self.event,'group:963105233',lambda:self.live))
        await self.entered.wait();self.live=False;self.resume.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.slow=False
        newer=await execution_memory.context(self.settings,self.event,'private:12345678',lambda:True)
        self.assertEqual(newer['experiences'][0]['actionId'],'real-action')
        self.assertTrue(self.received[-1]['shareOwnerActivity'])

    async def test_oversize_is_unknown_availability_not_fake_empty_history(self):
        self.large=True
        data=await execution_memory.context(self.settings,self.event,'group:963105233',lambda:True)
        self.assertEqual(data['status'],'unavailable');self.assertNotIn('experiences',data)
        self.bridge['revision']=3;self.write();count=len(self.received)
        self.assertEqual((await execution_memory.context(self.settings,self.event,'group:963105233',lambda:True))['reason'],'memory_bridge_unavailable')
        self.assertEqual(len(self.received),count)

    async def test_every_foreground_tool_records_its_trusted_origin(self):
        self.bridge['operations']=[{'operation':'like','write':True,'scope':'like.write','args':{'type':'object','properties':{}}}];self.write()
        tool=bilibili_tools.tools(self.settings,self.event,lambda:True,[])[0]
        data=json.loads(await tool.call(None,bvid='BV1Nn436kEPz',value=True))
        self.assertEqual(data['receipt']['status'],'confirmed')
        self.assertEqual(self.received[0]['origin'],{'scope':'group:963105233','eventId':'incoming-event','objective':'我说自己是别人的主人'})
        self.assertEqual(self.received[0]['senderId'],'12345678')

    async def test_local_protocol_receipts_share_a_source_without_reconstructing_private_text(self):
        import sqlite3
        from bot_pipeline import BotPipeline,experience_context
        from qq_actions import initialize
        with sqlite3.connect(':memory:') as db:
            BotPipeline(db);initialize(db)
            for event,scope in [('own','private:12345678'),('group','group:963105233'),('collision','group:963105233'),('collision','group:44444444')]:
                db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,'12345678',?,1,'message','commit','sent','')",(scope,event))
            for event,status in [('own','attempted'),('group','confirmed'),('collision','confirmed'),('legacy-without-source','confirmed')]:
                db.execute('INSERT INTO qq_action_attempts VALUES(?,?,?,?)',(event,'signature',1,status))
            db.commit();store=NS(db=db,autonomous_experience={})
            group=experience_context(store,'group:963105233',{})['protocol']
            self.assertEqual([r['actionId'] for r in group],['qq:group:signature'])
            own=experience_context(store,'private:12345678',{})['protocol']
            self.assertEqual(own[0]['receipt']['status'],'unknown')
            self.assertEqual(len(experience_context(store,'self',{})['protocol']),2)
            self.assertNotIn('protocol',experience_context(store,'self',{'memory':False}))


if __name__=='__main__':unittest.main()
