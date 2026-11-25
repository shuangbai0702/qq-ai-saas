"""Actual DSH QQ bindings retain Work authority, shared execution and receipts."""
import json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from history_sources_test import seed,png
from context_protocol import event_message
from bot_pipeline import BotPipeline
import dsh_qq_tools,pipeline_tools,qq_publish_media


class DshQqTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        BotPipeline(self.db);pipeline_tools.initialize(self.db)
        self.scope='private:12345';self.live=True;self.calls=[];self.raw=png()
        self.settings={'root':folder.name,'botId':'bot','tenantId':'tenant','generation':1,'revision':1,
                       'config':{'behavior':{'qzone':True,'ownerUin':'12345'}}}
        work=NS(bot_id='bot',scope=self.scope,subject='12345',event_id='103')
        self.extra={'_hosted_pipeline':work,'_hosted_lease':self.settings,'_hosted_journal_scope':'bot_shared'}
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,?,?,?,'message','model','received','')",(self.scope,'12345','103',time.time()));self.db.commit()
        async def publish(**args):self.calls.append([Path(name).read_bytes() for name in args.get('images',[])]);return {'tid':'fixture'}
        self.event=NS(get_extra=lambda k,d=None:self.extra.get(k,d),get_sender_id=lambda:'12345',get_group_id=lambda:'',get_self_id=lambda:'99999',get_message_str=lambda:'按之前那张澪的图发空间',message_obj=NS(message_id='103'),
                      bot=NS(get_login_info=AsyncMock(return_value={'user_id':99999}),send_qzone_msg=publish,
                             get_msg=AsyncMock(return_value={'message_id':100,'sender':{'user_id':12345},'message':[{'type':'image','data':{'url':'https://qpic.cn/original'}}]})))
        self.context=NS(context=NS(event=self.event))
        import base64
        seed(self.db,[event_message({'scope':self.scope,'sender_id':'12345','event_id':'100','at':'2026-11-01'},
             [{'type':'text','text':'这是澪'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(self.raw).decode()}}]),
             event_message({'scope':self.scope,'sender_id':'12345','event_id':'103'},'把之前那张配文发空间')])

    def catalog(self):return dsh_qq_tools.catalog(self.settings,self.event,lambda:self.live,self.db,self.context)
    async def execute(self,name,args):return await dsh_qq_tools.execute(self.settings,self.event,lambda:self.live,self.db,self.context,name,args)
    async def materialize(self,reference):return await dsh_qq_tools.materialize(self.settings,self.event,lambda:self.live,self.db,self.context,reference)

    async def test_private_history_see_image_and_publish_use_original_executor_once(self):
        catalog={item['id']:item for item in self.catalog()}
        self.assertEqual(catalog['hosted_qq_publish']['effect'],'write');self.assertEqual(catalog['qq_history_image']['effect'],'read')
        page=await self.execute('qq_history_search',{'images_only':True})
        self.assertNotIn('这是澪',json.dumps(page));self.assertIn('这是澪',(await self.materialize(page))['text'])
        handle=json.loads((await self.materialize(page))['text'])['entries'][0]['images'][0]['source_id'];image=await self.execute('qq_history_image',{'source_id':handle})
        self.assertNotIn('base64',json.dumps(image));self.assertEqual(len((await self.materialize(image))['images']),1)
        before=self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchall()
        with patch.object(qq_publish_media,'download',AsyncMock(return_value=self.raw)):
            result=await self.execute('hosted_qq_publish',{'content':'澪好可爱','image_sources':[handle]})
            again=await self.execute('hosted_qq_publish',{'content':'澪好可爱','image_sources':[handle]})
        self.assertEqual(result['receipt']['status'],'confirmed');self.assertEqual(again['receipt']['status'],'confirmed')
        self.assertEqual(self.calls,[[self.raw]]);self.assertEqual(self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchall(),before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_events').fetchone()[0],1)

    async def test_original_authorization_switches_and_schema_are_not_widened(self):
        self.settings['config']['behavior']['ownerUin']='67890'
        result=await self.execute('hosted_qq_publish',{'content':'无权'})
        self.assertEqual(result['receipt']['status'],'rejected');self.assertEqual(self.calls,[])
        import jsonschema
        for args in ({'group_id':'55555'},{'before':''},{'limit':11},{'scope':'private:67890'}):
            with self.assertRaises(jsonschema.ValidationError):await self.execute('qq_history_search',args)
        self.settings['config']['behavior']['qzone']=False
        self.settings['config']['delegatedCapabilities']={'groupHistory':False}
        self.assertEqual(self.catalog(),[])
        with self.assertRaises(ValueError):await self.materialize({'kind':'qq_history_page'})

    async def test_actual_python_ndjson_bridge_routes_history_image_and_original_publish(self):
        import asyncio
        from aiohttp import web
        import dsh_tools
        from group_actions import tool_action_id
        waiters={};acks=[]
        async def answer(request):
            value=await request.json();acks.append(value);waiters[value['id']].set_result(value)
            return web.json_response({})
        async def delegate(request):
            payload=await request.json();self.assertEqual(payload['input'],{'goal':'找之前的澪图配文发空间','background':'','constraints':[]})
            response=web.StreamResponse(headers={'Content-Type':'application/x-ndjson'});await response.prepare(request)
            async def rpc(operation,**fields):
                identifier=str(len(waiters));waiters[identifier]=asyncio.get_running_loop().create_future()
                await response.write((json.dumps({'type':'tool','id':identifier,'operation':operation,**fields})+'\n').encode())
                ack=await asyncio.wait_for(waiters[identifier],3);self.assertTrue(ack['ok'],ack);return ack['value']
            names={row['id'] for row in await rpc('discover')}
            self.assertTrue({'qq_history_search','qq_history_image','hosted_qq_publish'}<=names)
            page=await rpc('execute',tool='qq_history_search',args={'images_only':True})
            self.assertNotIn('这是澪',json.dumps(page));self.assertNotIn('event_id',json.dumps(page))
            content=await rpc('history_content',reference=page);self.assertIn('这是澪',content['text'])
            source=json.loads(content['text'])['entries'][0]['images'][0]['source_id']
            image=await rpc('execute',tool='qq_history_image',args={'source_id':source})
            picture=await rpc('history_content',reference=image);self.assertEqual(len(picture['images']),1)
            receipt=await rpc('execute',tool='hosted_qq_publish',args={'content':'澪好可爱','image_sources':[source]})
            self.assertEqual(receipt['receipt']['status'],'confirmed')
            await response.write((json.dumps({'type':'final','text':'已确认发布带图说说。'})+'\n').encode());return response
        app=web.Application();app.router.add_post('/delegate',delegate);app.router.add_post('/tool-result',answer)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        try:
            bridge={'version':2,'mode':'local',**{key:self.settings[key] for key in ('botId','tenantId','generation','revision')},
                    'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/delegate','token':'a'*64}
            file=Path(self.settings['root'])/'ai/dsh-bridge.json';file.parent.mkdir();file.write_text(json.dumps(bridge),encoding='utf8')
            token=tool_action_id.set('main-delegation-call')
            try:
                with patch.object(qq_publish_media,'download',AsyncMock(return_value=self.raw)):
                    result=await dsh_tools.tools(self.settings,self.event,lambda:self.live,self.db,bridge)[0].run(self.context,goal='找之前的澪图配文发空间')
            finally:tool_action_id.reset(token)
            self.assertEqual(result,'已确认发布带图说说。');self.assertNotIn('qh_',result);self.assertNotIn('base64',result)
            self.assertEqual(self.calls,[[self.raw]]);self.assertEqual(len(acks),6)
        finally:await runner.cleanup()

    async def test_stale_and_replaced_event_cannot_borrow_the_tool(self):
        # A Work can remain current for analysis while its external-action
        # permit was revoked. Delegation must retain the original effect gate.
        denied=await dsh_qq_tools.execute(self.settings,self.event,lambda:True,self.db,self.context,'hosted_qq_publish',{'content':'禁止外部动作'},effect_current=lambda:False)
        self.assertEqual(denied['receipt']['status'],'rejected');self.event.bot.get_login_info.assert_not_awaited()
        other=NS(context=NS(event=NS()))
        import asyncio
        with self.assertRaises(asyncio.CancelledError):dsh_qq_tools.catalog(self.settings,self.event,lambda:True,self.db,other)
        self.live=False
        with self.assertRaises(asyncio.CancelledError):await self.execute('qq_history_search',{})
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
