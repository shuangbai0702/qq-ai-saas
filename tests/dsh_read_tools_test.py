"""Original-tool reads, source semantics and Work ownership, without services."""
import asyncio,hashlib,json,jsonschema,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path('adapters/astrbot').resolve()))
sys.path.insert(0,str(Path('references/astrbot').resolve()))
import dsh_read_tools,execution_memory,qq_favorite_tools
from bot_pipeline import BotPipeline
from qq_favorites import description_id


class ReadBridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings={'root':self.temp.name,'botId':'bot','tenantId':'tenant','generation':1,'revision':1,'config':{'behavior':{}}}
        self.db=sqlite3.connect(':memory:');BotPipeline(self.db)
        self.work=SimpleNamespace(bot_id='bot',scope='private:12345',subject='12345',event_id='event')
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,?,?,?,'message','model','received','')",(self.work.scope,self.work.subject,self.work.event_id,time.time()));self.db.commit()
        self.extra={'_hosted_pipeline':self.work,'_hosted_lease':{'generation':1}}
        self.live=True;self.ready=asyncio.Event();self.release=asyncio.Event();self.delay=False;self.fetches=0
        self.row={'md5':'a'*32,'resId':'verified','url':'https://example.invalid/image'}
        async def login():return {'user_id':'99999'}
        async def details(**kwargs):
            self.fetches+=1;self.ready.set()
            if self.delay:await self.release.wait()
            return [self.row]
        self.event=SimpleNamespace(get_extra=lambda key,*default:self.extra.get(key,default[0] if default else None),
                                   get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_group_id=lambda:'',
                                   bot=SimpleNamespace(get_login_info=login,fetch_custom_face_detail=details))
        self.context=SimpleNamespace(context=SimpleNamespace(event=self.event))
    def tearDown(self):self.db.close();self.temp.cleanup()
    def catalog(self):return dsh_read_tools.catalog(self.settings,self.event,lambda:self.live,self.db,self.context)
    async def execute(self,name,args=None,social=None):return await dsh_read_tools.execute(self.settings,self.event,lambda:self.live,self.db,self.context,name,{} if args is None else args,social)
    def pending(self):
        self.catalog()
        self.db.execute("INSERT INTO qq_favorite_writes(account,md5,scope,source,state,at) VALUES(?,?,?,?,?,?)",('99999','a'*32,self.work.scope,'current','unknown',time.time()));self.db.commit()
    def receipt(self):return self.db.execute('SELECT state FROM qq_favorite_writes').fetchone()[0]
    async def test_original_catalog_and_denied_write_arguments(self):
        descriptors=self.catalog();self.assertEqual({x['id'] for x in descriptors},{'hosted_recall_experience','hosted_qq_favorites.sources','hosted_qq_favorites.list'})
        recall=next(x for x in descriptors if x['id']=='hosted_recall_experience')
        self.assertEqual(recall['definition']['parameters'],execution_memory.tools(self.settings,self.event,self.work.scope,lambda:True)[0].parameters)
        for name in ('hosted_qq_favorites.save','hosted_qq_favorites.prepare'):
            with self.assertRaisesRegex(ValueError,'unavailable'):await self.execute(name)
        with self.assertRaises(jsonschema.ValidationError):await self.execute('hosted_qq_favorites.list',{'operation':'save'})
        self.assertEqual(self.fetches,0)
        sources=await self.execute('hosted_qq_favorites.sources')
        self.assertEqual(sources['data']['source_ids'],['current']);self.assertEqual(sources['receipt']['status'],'not_applicable')
        self.assertEqual(self.fetches,0)
    async def test_canonical_semantics_and_verified_readback(self):
        self.pending()
        folder=Path(self.temp.name)/'qq-favorites';folder.mkdir()
        (folder/'index.json').write_text(json.dumps({'account':'99999','items':[{'id':description_id(self.row),'meaning':'开心','avoid':'严肃话题','rawText':'原始文本','enabled':True,'manual':True}]}),encoding='utf8')
        result=await self.execute('hosted_qq_favorites.list')
        self.assertEqual(self.receipt(),'confirmed')
        item=result['data']['items'][0];self.assertEqual(item['meaning'],'开心');self.assertEqual(item['avoid'],'严肃话题')
        self.assertEqual(item['rawText'],'原始文本');self.assertFalse(item['selection_disabled']);self.assertTrue(item['saved_from_this_conversation'])
        self.assertEqual(result['receipt']['status'],'not_applicable')
    async def test_finished_parent_during_read_cannot_settle_unknown(self):
        self.pending();self.delay=True
        task=asyncio.create_task(self.execute('hosted_qq_favorites.list'))
        await self.ready.wait();self.db.execute("UPDATE pipeline_events SET status='completed'");self.db.commit();self.release.set()
        with self.assertRaisesRegex(ValueError,'parent_stale'):await task
        self.assertEqual(self.receipt(),'unknown')
    async def test_wrong_context_sender_and_stale_work_rejected(self):
        self.context.context.event=object()
        with self.assertRaisesRegex(ValueError,'event_mismatch'):self.catalog()
        self.context.context.event=self.event;self.event.get_sender_id=lambda:'different'
        with self.assertRaisesRegex(ValueError,'identity_mismatch'):self.catalog()
        self.event.get_sender_id=lambda:'12345';self.live=False
        with self.assertRaises(asyncio.CancelledError):self.catalog()
    async def test_real_account_mismatch_keeps_unknown_and_never_fetches(self):
        self.pending()
        async def wrong_login():return {'user_id':'88888'}
        self.event.bot.get_login_info=wrong_login
        result=await self.execute('hosted_qq_favorites.list')
        self.assertEqual(result['receipt']['status'],'rejected')
        self.assertEqual(self.receipt(),'unknown');self.assertEqual(self.fetches,0)
    async def test_recall_keeps_original_scope_sender_and_share_policy(self):
        async def recall(settings,event,scope,current,query,limit,share,identity):
            self.assertIs(event,self.event);self.assertEqual(scope,self.work.scope)
            self.assertTrue(current());self.assertEqual(query,'BV1');self.assertTrue(share);self.assertIsNone(identity)
            return {'experiences':[{'receipt':{'status':'unknown'}}],'visibility':'public_activity_only'}
        with patch.object(execution_memory,'recall',recall):
            result=await self.execute('hosted_recall_experience',{'query':'BV1','shareOwnerActivity':True})
        self.assertEqual(result['data']['visibility'],'public_activity_only');self.assertEqual(result['data']['experiences'][0]['receipt']['status'],'unknown')
        self.settings['config']['behavior']['memory']=False
        with self.assertRaisesRegex(ValueError,'unavailable'):await self.execute('hosted_recall_experience')
    async def test_readonly_descriptions_versions_and_live_configuration(self):
        for item in self.catalog():
            definition=item['definition']
            expected=hashlib.sha256(json.dumps(definition,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.assertEqual(item['version'],expected)
            if item['capability']=='favorite_audit':
                self.assertNotIn('save',definition['description']);self.assertNotIn('prepare',definition['description'])
        self.settings['config']['behavior'].update(memory=False,qqFavoriteSaveEnabled=False,memeEnabled=False)
        self.assertEqual(self.catalog(),[])
        for identifier in ('hosted_recall_experience','hosted_qq_favorites.sources','hosted_qq_favorites.list'):
            with self.assertRaisesRegex(ValueError,'unavailable'):await self.execute(identifier)
    async def test_inactive_original_tool_not_exposed(self):
        tool=execution_memory.tools(self.settings,self.event,self.work.scope,lambda:True)[0]
        tool.active=False
        with patch.object(execution_memory,'tools',return_value=[tool]):
            self.assertNotIn(tool.name,{item['id'] for item in self.catalog()})
            with self.assertRaisesRegex(ValueError,'unavailable'):await self.execute(tool.name)
    async def test_main_projection_keeps_expression_and_rejects_guessed_reads(self):
        calls=[]
        from astrbot.core.agent.tool import FunctionTool
        class Original(FunctionTool):
            hosted_write_operations=frozenset({'save'})
            async def call(self,context,**arguments):
                self.assertion_context=context;calls.append(arguments);return json.dumps({'status':'prepared' if arguments['operation']=='prepare' else 'unknown'})
        schema=qq_favorite_tools.tools(self.event,self.settings['config'],self.db,self.settings['root'],lambda:True)[0].parameters
        original=Original(name='hosted_qq_favorites',description='original',parameters=schema)
        projected=dsh_read_tools.main_favorite_tool(original)
        self.assertEqual(projected.parameters['properties']['operation']['enum'],['prepare','save'])
        self.assertEqual(original.parameters['properties']['operation']['enum'],['sources','list','prepare','save'])
        self.assertEqual(projected.hosted_write_operations,frozenset({'save'}))
        for operation in ('sources','list','unknown'):
            result=json.loads(await projected.call(self.context,operation=operation))
            self.assertEqual(result['receipt']['status'],'rejected')
        self.assertEqual(calls,[])
        for arguments in ({'operation':'prepare','favorite_handle':'qf_'+'a'*20},{'operation':'save','source_id':'current'}):
            await projected.call(self.context,**arguments)
            self.assertEqual(calls[-1],arguments);self.assertIs(original.assertion_context,self.context)
    async def test_material_catalog_reuses_scoped_approved_catalog_without_admin_or_bytes(self):
        from social import SocialStore
        from sticker_collection import StickerCollector
        social=SocialStore.__new__(SocialStore)
        social.db=self.db;social.root=Path(self.temp.name);social.closed=False;social.stickers=StickerCollector(self.db)
        self.settings['config']['behavior']['memePacks']=[]
        for identifier,scope,status,meaning in [('approved-here',self.work.scope,'approved','当前已审核'),('pending-here',self.work.scope,'pending','管理员待审核'),('approved-foreign','group:55555','approved','他群私密素材')]:
            self.db.execute('INSERT INTO sticker_inbox VALUES(?,?,?,?,?,?,?,?,?)',(identifier,scope,'subject','event',status,meaning,time.time(),1,b'private-image'))
        reactions=social.root/'reactions';reactions.mkdir()
        (reactions/'ok.png').write_bytes(b'fixture-image')
        (reactions/'ok.json').write_text(json.dumps({'id':'ok','meaning':'本地开心','avoid':'严肃场景'}),encoding='utf8')
        descriptors=dsh_read_tools.catalog(self.settings,self.event,lambda:self.live,self.db,self.context,social)
        self.assertIn('hosted_material_catalog',{row['id'] for row in descriptors})
        before=self.db.total_changes
        result=await self.execute('hosted_material_catalog',social=social)
        self.assertEqual(self.db.total_changes,before)
        data=result['data'];self.assertEqual({item['id'] for item in data['items']},{'approved-here','ok'})
        self.assertFalse(data['review_inbox']['available']);self.assertEqual(data['scope'],self.work.scope)
        encoded=json.dumps(data,ensure_ascii=False)
        for denied in ('private-image',str(reactions),'他群私密素材','管理员待审核','subject','event','png','path'):
            self.assertNotIn(denied,encoded)
        with self.assertRaises(jsonschema.ValidationError):await self.execute('hosted_material_catalog',{'scope':'group:55555'},social)
        self.settings['config']['behavior']['memeEnabled']=False
        with self.assertRaisesRegex(ValueError,'unavailable'):await self.execute('hosted_material_catalog',social=social)
    async def test_material_catalog_rejects_other_bot_social_store(self):
        social=SimpleNamespace(closed=False,db=self.db,root=Path(self.temp.name)/'foreign')
        with self.assertRaisesRegex(ValueError,'owner_mismatch'):await self.execute('hosted_material_catalog',social=social)


if __name__=='__main__':unittest.main()
