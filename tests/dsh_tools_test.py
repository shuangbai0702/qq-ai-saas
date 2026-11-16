"""Local bridge identity, schema, final-only and late-result boundaries. No model/QQ calls."""
import asyncio,hashlib,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path('adapters/astrbot').resolve()))
sys.path.insert(0,str(Path('references/astrbot').resolve()))
from aiohttp import web
import dsh_tools,pipeline_tools
from bot_pipeline import BotPipeline
from group_actions import tool_action_id

class DshBridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.settings={'root':self.temp.name,'botId':'bot','tenantId':'tenant','generation':1,'revision':1}
        self.db=sqlite3.connect(':memory:');BotPipeline(self.db);pipeline_tools.initialize(self.db)
        self.work=SimpleNamespace(bot_id='bot',scope='private:12345',subject='12345',event_id='event')
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,?,?,?,'message','model','received','')",(self.work.scope,self.work.subject,self.work.event_id,time.time()));self.db.commit()
        self.live=True;self.payloads=[];self.ready=asyncio.Event();self.release=asyncio.Event();self.delay=False
        async def submit(request):
            self.payloads.append(await request.json());self.ready.set()
            if self.delay:await self.release.wait()
            return web.json_response({'result':'真实结果','evidence':['https://example.org/'],'unfinished':[],'limitations':[]})
        app=web.Application();app.router.add_post('/delegate',submit);self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.bridge={'version':1,'localPrototype':True,'botId':'bot','tenantId':'tenant','generation':1,'revision':1,'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/delegate','token':'a'*64}
        self.file=Path(self.temp.name)/'ai/dsh-bridge.json';self.file.parent.mkdir();self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        self.event=SimpleNamespace(get_extra=lambda key:self.work if key=='_hosted_pipeline' else None)
        self.context=SimpleNamespace(context=SimpleNamespace(event=self.event))
    async def asyncTearDown(self):self.release.set();await self.runner.cleanup();self.db.close();self.temp.cleanup()
    def tool(self):return dsh_tools.tools(self.settings,self.event,lambda:self.live,self.db,self.bridge)[0]
    async def test_exact_work_and_final_only(self):
        tool=self.tool();self.assertEqual(tool.name,'dsh_delegate');self.assertEqual(set(tool.parameters['properties']),{'goal','background','constraints'})
        token=tool_action_id.set('real-call')
        try:result=json.loads(await tool.call(self.context,goal='查证'))
        finally:tool_action_id.reset(token)
        self.assertEqual(set(result),{'result','evidence','unfinished','limitations'})
        parent=self.payloads[0]['parent'];self.assertEqual(parent['origin_event'],'event');self.assertEqual(self.payloads[0]['delegationId'],'real-call')
        self.assertEqual(parent['work_id'],'foreground:'+hashlib.sha256(json.dumps(['bot',self.work.scope,'12345','event'],ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE source='tool'").fetchone()[0],0)
    async def test_foreign_manifest_and_finished_parent_rejected(self):
        self.file.write_text(json.dumps({**self.bridge,'tenantId':'other'}),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'manifest'):dsh_tools.manifest(self.settings)
        self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        self.db.execute("UPDATE pipeline_events SET status='completed'")
        token=tool_action_id.set('real-call')
        try:
            with self.assertRaisesRegex(ValueError,'parent_stale'):await self.tool().call(self.context,goal='查证')
        finally:tool_action_id.reset(token)
        self.assertEqual(self.payloads,[])
    async def test_late_success_is_not_committed_to_stale_work(self):
        self.delay=True;token=tool_action_id.set('real-call')
        try:task=asyncio.create_task(self.tool().call(self.context,goal='查证'))
        finally:tool_action_id.reset(token)
        await self.ready.wait();self.live=False;self.release.set()
        with self.assertRaises(asyncio.CancelledError):await task

if __name__=='__main__':unittest.main()
