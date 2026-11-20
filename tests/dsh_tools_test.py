"""Local bridge identity, schema, final-only and late-result boundaries. No model/QQ calls."""
import asyncio,hashlib,json,sqlite3,sys,tempfile,time,unittest
from unittest.mock import patch
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
        self.live=True;self.payloads=[];self.acks=[];self.ready=asyncio.Event();self.release=asyncio.Event();self.delay=False
        self.hold_eof=False;self.final_written=asyncio.Event()
        async def submit(request):
            payload=await request.json();self.payloads.append(payload);self.ready.set()
            if payload.get('imageAnalysis'):
                response=web.StreamResponse(headers={'Content-Type':'application/x-ndjson'})
                await response.prepare(request)
                await response.write(b'{"type":"tool","operation":"vision_images","id":"fixture-vision"}\n')
                await self.release.wait()
                await response.write((json.dumps({'type':'final','text':'原图结论'})+'\n').encode())
                return response
            if self.delay:await self.release.wait()
            if self.hold_eof:
                response=web.StreamResponse(headers={'Content-Type':'application/x-ndjson'})
                await response.prepare(request)
                await response.write(b'{"type":"final","text":"old conclusion"}\n')
                self.final_written.set()
                await self.release.wait()
                return response
            return web.Response(text=json.dumps({'type':'final','text':'真实结果\nhttps://example.org/'})+'\n',content_type='application/x-ndjson')
        async def callback(request):
            self.acks.append((request.headers.get('X-FishBot-Vision-Callback'),await request.json()))
            self.release.set();return web.Response()
        app=web.Application();app.router.add_post('/delegate',submit);app.router.add_post('/tool-result',callback);self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.bridge={'version':2,'mode':'production','botId':'bot','tenantId':'tenant','generation':1,'revision':1,'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/delegate','token':'a'*64}
        self.file=Path(self.temp.name)/'ai/dsh-bridge.json';self.file.parent.mkdir();self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        self.event=SimpleNamespace(get_extra=lambda key:self.work if key=='_hosted_pipeline' else None,get_sender_id=lambda:self.work.subject)
        self.context=SimpleNamespace(context=SimpleNamespace(event=self.event))
    async def asyncTearDown(self):self.release.set();await self.runner.cleanup();self.db.close();self.temp.cleanup()
    def tool(self):return dsh_tools.tools(self.settings,self.event,lambda:self.live,self.db,self.bridge)[0]
    async def test_config_change_after_final_before_eof_rejects_old_conclusion(self):
        self.hold_eof=True
        token=tool_action_id.set('late-final')
        try:
            task=asyncio.create_task(self.tool().call(self.context,goal='check'))
            await self.final_written.wait()
            self.file.write_text(json.dumps({**self.bridge,'revision':2}),encoding='utf8')
            self.release.set()
            result=await task
            self.assertNotIn('old conclusion',result)
            self.assertIn('未完成',result)
        finally:tool_action_id.reset(token)
    async def test_text_only_revision_keeps_in_flight_result_but_permission_change_does_not(self):
        self.hold_eof=True;self.bridge['authorityHash']='1'*64;self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        token=tool_action_id.set('text-only-final')
        try:
            task=asyncio.create_task(self.tool().call(self.context,goal='check'))
            await self.final_written.wait()
            self.file.write_text(json.dumps({**self.bridge,'revision':2,'token':'b'*64}),encoding='utf8')
            self.release.set();self.assertEqual(await task,'old conclusion')
            self.assertTrue(dsh_tools.bridge_current(self.settings,self.bridge))
            self.file.write_text(json.dumps({**self.bridge,'revision':3,'token':'c'*64,'authorityHash':'2'*64}),encoding='utf8')
            self.assertFalse(dsh_tools.bridge_current(self.settings,self.bridge))
        finally:tool_action_id.reset(token)
    async def test_exact_work_and_final_only(self):
        tool=self.tool();self.assertEqual(tool.name,'dsh_delegate');self.assertEqual(set(tool.parameters['properties']),{'goal','background','constraints','continue_task'})
        token=tool_action_id.set('real-call')
        try:result=await tool.call(self.context,goal='查证')
        finally:tool_action_id.reset(token)
        self.assertEqual(result,'真实结果\nhttps://example.org/')
        parent=self.payloads[0]['parent'];self.assertEqual(parent['origin_event'],'event');self.assertEqual(self.payloads[0]['delegationId'],'real-call')
        self.assertEqual(parent['work_id'],'foreground:'+hashlib.sha256(json.dumps(['bot',self.work.scope,'12345','event'],ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE source='tool'").fetchone()[0],0)
    async def test_vision_is_opt_in_and_sends_originals_only_via_callback(self):
        self.bridge['capabilities']=['image_analysis']
        self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        tool=self.tool();self.assertIn('image',tool.parameters['properties'])
        selection={'positions':[2]};originals=[{'mimeType':'image/png','width':320,'height':180,'data':'eA=='}]
        token=tool_action_id.set('vision-call')
        try:
            with patch('semantic_media.delegation_originals',return_value=None):
                self.assertIn('没有可用于',await tool.call(self.context,goal='查看细节',image=selection))
            self.assertEqual(self.payloads,[])
            with patch('semantic_media.delegation_originals',return_value=originals) as lookup:
                self.assertEqual(await tool.call(self.context,goal='查看细节',image=selection),'原图结论')
            lookup.assert_called_once_with(self.db,self.work.scope,self.work.scope,self.work.event_id,[2])
        finally:tool_action_id.reset(token)
        self.assertTrue(self.payloads[0]['imageAnalysis'])
        self.assertEqual(set(self.payloads[0]['input']),{'goal','background','constraints'})
        self.assertEqual(self.acks,[('fixture-vision',{'id':'fixture-vision','ok':True,'value':{'eventId':'event','positions':[2],'images':originals}})])

    async def test_foreign_manifest_and_finished_parent_rejected(self):
        self.file.write_text(json.dumps({**self.bridge,'tenantId':'other'}),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'manifest'):dsh_tools.manifest(self.settings)
        self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        self.db.execute("UPDATE pipeline_events SET status='completed'")
        token=tool_action_id.set('real-call')
        try:
            self.assertIn('委托执行未完成',await self.tool().call(self.context,goal='查证'))
        finally:tool_action_id.reset(token)
        self.assertEqual(self.payloads,[])
    async def test_late_success_is_not_committed_to_stale_work(self):
        self.delay=True;token=tool_action_id.set('real-call')
        try:task=asyncio.create_task(self.tool().call(self.context,goal='查证'))
        finally:tool_action_id.reset(token)
        await self.ready.wait();self.live=False;self.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
    async def test_actual_receipt_assembly_preserves_plain_final_and_failure_text(self):
        from tool_outcome import structured
        tool=structured(self.tool())
        token=tool_action_id.set('wrapped-real-call')
        try:
            result=await tool.call(self.context,goal='查证')
            self.assertEqual(result,'真实结果\nhttps://example.org/')
            with self.assertRaises(ValueError):json.loads(result)
            self.db.execute("UPDATE pipeline_events SET status='completed'")
            failure=await structured(self.tool()).call(self.context,goal='查证')
            self.assertIn('委托执行未完成',failure)
            with self.assertRaises(ValueError):json.loads(failure)
        finally:tool_action_id.reset(token)

    async def test_follow_up_forwards_only_reference_under_the_current_work(self):
        token=tool_action_id.set('new-follow-up-call')
        try:
            result=await self.tool().call(self.context,goal='进一步解释结果',continue_task='earlier-call')
            self.assertEqual(result,'真实结果\nhttps://example.org/')
            self.assertEqual(self.payloads[0]['delegationId'],'new-follow-up-call')
            self.assertEqual(self.payloads[0]['continueDelegationId'],'earlier-call')
            self.assertEqual(self.payloads[0]['parent']['origin_scope'],self.work.scope)
            with self.assertRaisesRegex(ValueError,'invalid_dsh_task_continuation'):
                await self.tool().run(self.context,goal='bad',continue_task='../bad',image={})
        finally:tool_action_id.reset(token)

if __name__=='__main__':unittest.main()
