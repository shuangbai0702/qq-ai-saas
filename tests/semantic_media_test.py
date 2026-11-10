"""Real file release, durable retries and independent event ownership."""
import ast,asyncio,base64,io,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
import semantic_media as media
import semantic_batch


def batch_state(db,checkpoint,state='retired'):
    semantic_batch.initialize(db)
    db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,?,?,NULL)',
               (checkpoint,'bot_shared','digest','[]',0,state,0))


class OwnedOriginals(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name).resolve()
        self.db=sqlite3.connect(self.root/'behavior.sqlite');media.initialize(self.db);self.db.commit()
        self.file=self.root/'media_image_original.png';self.file.write_bytes(b'real original image')
        self.image='data:image/png;base64,bW9kZWwgYnl0ZXM='

    def tearDown(self):self.db.close();self.folder.cleanup()

    def bind(self,scope='bot_shared',event='event-A',file=None):
        file=file or self.file
        media.register_owned(self.db,[media.owned_record(file,self.root,[self.image])],
                             [{'scope':scope,'event':event,'images':[self.image]}]);self.db.commit()

    def release(self,scope='bot_shared',event='event-A',checkpoint=1,state='retired'):
        self.db.execute('BEGIN IMMEDIATE')
        result=media.retire_batch(self.db,[self.image],checkpoint,[media.event_key(scope,event)])
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_semantic_batches'").fetchone() or not self.db.execute('SELECT 1 FROM memory_semantic_batches WHERE checkpoint_id=?',(checkpoint,)).fetchone():
            batch_state(self.db,checkpoint,state)
        self.db.commit();return result

    def test_committed_summary_retains_original_until_journal_retirement(self):
        self.bind();self.release(state='committed')
        self.db.close();self.db=sqlite3.connect(self.root/'behavior.sqlite')
        with patch.object(Path,'unlink') as unlink:
            result=media.drain(self.db)
        unlink.assert_not_called()
        self.assertEqual((result['deleted'],result['pending'],result['protected']),(0,1,1))
        self.assertTrue(self.file.exists())
        with self.db:self.db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=1")
        self.assertEqual(media.drain(self.db)['deleted'],1);self.assertFalse(self.file.exists())

    def test_all_shared_file_batches_must_retire_before_unlink(self):
        self.bind('group:1','A');self.bind('private:2','B')
        self.release('group:1','A',1,state='committed');self.release('private:2','B',2)
        self.assertEqual(media.drain(self.db)['protected'],1);self.assertTrue(self.file.exists())
        with self.db:self.db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=1")
        self.assertEqual(media.drain(self.db)['deleted'],1)

    def test_missing_journal_batch_retains_original(self):
        self.bind();self.db.execute('BEGIN IMMEDIATE')
        media.retire_batch(self.db,[self.image],1,[media.event_key('bot_shared','event-A')]);self.db.commit()
        self.assertEqual(media.drain(self.db)['protected'],1);self.assertTrue(self.file.exists())
        with self.db:batch_state(self.db,2)
        self.assertEqual(media.drain(self.db)['protected'],1);self.assertTrue(self.file.exists())

    def test_resized_material_releases_all_original_event_refs(self):
        self.bind();self.db.execute('BEGIN IMMEDIATE')
        media.retire_batch(self.db,['data:image/png;base64,cmVzaXplZA=='],1,[media.event_key('bot_shared','event-A')])
        batch_state(self.db,1);self.db.commit()
        self.assertEqual(self.db.execute('SELECT released FROM semantic_media_refs').fetchone()[0],1)
        self.assertEqual(media.drain(self.db)['deleted'],1);self.assertFalse(self.file.exists())

    def test_committed_batch_deletes_real_file_and_retries_idempotently(self):
        self.bind();self.assertEqual(self.release()['untracked'],0)
        self.assertTrue(self.file.exists())
        self.assertEqual(media.drain(self.db)['deleted'],1);self.assertFalse(self.file.exists())
        self.assertEqual(media.drain(self.db)['deleted'],0)
        self.release();self.assertEqual(media.drain(self.db)['deleted'],0)

    def test_failed_summary_transaction_does_not_release(self):
        self.bind();self.db.execute('BEGIN IMMEDIATE')
        media.retire_batch(self.db,[self.image],1,[media.event_key('bot_shared','event-A')])
        self.db.rollback();self.assertEqual(media.drain(self.db)['deleted'],0);self.assertTrue(self.file.exists())

    def test_new_event_cross_scope_protects_same_file_until_final_owner(self):
        self.bind('group:1','same-id');self.bind('private:2','same-id')
        self.release('group:1','same-id');self.assertEqual(media.drain(self.db)['deleted'],0)
        self.assertTrue(self.file.exists());self.release('private:2','same-id',2)
        self.assertEqual(media.drain(self.db)['deleted'],1)

    def test_pending_delete_is_cancelled_by_new_material_reference(self):
        self.bind();self.release()
        media.register_owned(self.db,[],[{'scope':'group:3','event':'new-work','images':[self.image]}]);self.db.commit()
        self.assertEqual(media.drain(self.db)['deleted'],0);self.assertTrue(self.file.exists())
        self.release('group:3','new-work',2);self.assertEqual(media.drain(self.db)['deleted'],1)

    def test_stale_journal_cannot_reacquire_retired_event(self):
        self.bind();self.release()
        media.register_owned(self.db,[],[{'scope':'bot_shared','event':'event-A','images':[self.image]}]);self.db.commit()
        self.assertEqual(media.drain(self.db)['deleted'],1)

    def test_crash_after_commit_recovers_release(self):
        self.bind();self.release();self.db.close();self.db=sqlite3.connect(self.root/'behavior.sqlite')
        self.assertEqual(media.drain(self.db)['deleted'],1);self.assertFalse(self.file.exists())

    def test_unlink_failure_remains_visible_and_retryable(self):
        self.bind();self.release()
        with patch.object(Path,'unlink',side_effect=PermissionError('busy')):
            result=media.drain(self.db)
        self.assertEqual((result['errors'],result['pending']),(1,1));self.assertTrue(self.file.exists())
        self.assertIn('PermissionError',self.db.execute('SELECT error FROM semantic_media_owned').fetchone()[0])
        self.assertEqual(media.drain(self.db)['deleted'],1)

    def test_path_replacement_is_never_deleted(self):
        self.bind();self.release();self.file.write_bytes(b'another caller replacement')
        result=media.drain(self.db);self.assertEqual(result['errors'],1);self.assertTrue(self.file.exists())

    def test_other_bot_and_untracked_historical_file_are_untouched(self):
        self.bind();other=self.root/'other-bot';other.mkdir();other_file=other/self.file.name;other_file.write_bytes(b'other bot')
        self.release();media.drain(self.db);self.assertTrue(other_file.exists())
        self.db.execute('BEGIN IMMEDIATE')
        result=media.retire_batch(self.db,['data:image/png;base64,b2xk'],2,[media.event_key('bot_shared','old')])
        self.db.commit();self.assertEqual(result['untracked'],1);self.assertTrue(other_file.exists())

    def test_persistent_path_outside_temp_cannot_grant_ownership(self):
        child=self.root/'persistent';child.mkdir();file=child/'sticker.png';file.write_bytes(b'favorite')
        with self.assertRaises(ValueError):media.owned_record(file,self.root,[self.image])
        self.assertTrue(file.exists())

    def test_release_and_drain_require_opposite_transaction_boundaries(self):
        self.bind()
        with self.assertRaisesRegex(ValueError,'requires_summary_transaction'):
            media.retire_batch(self.db,[self.image],1,[media.event_key('bot_shared','event-A')])
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaisesRegex(ValueError,'requires_committed_batch'):media.drain(self.db)
        self.db.rollback()


class SDKOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_real_resolver_records_only_owned_source_and_batch_deletes_it(self):
        try:
            from astrbot.core.platform.astr_message_event import current_message_event
            from astrbot.core.utils import image_input,media_utils,astrbot_path
        except ImportError:self.skipTest('requires project AstrBot environment')
        from types import SimpleNamespace
        from PIL import Image
        import model_images
        extras={'_hosted_lease':object()}
        event=SimpleNamespace(get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.__setitem__(k,v))
        model_images.install_sdk_preparation();token=current_message_event.set(event)
        try:
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();raw=io.BytesIO()
                with Image.new('RGB',(1024,512),'red') as picture:picture.save(raw,format='PNG')
                value='data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()
                with patch.object(media_utils,'get_astrbot_temp_path',return_value=str(root)),patch.object(astrbot_path,'get_astrbot_temp_path',return_value=str(root)):
                    prepared=await image_input.prepare_model_image(value,max_size=1280,output_dir=root)
                records=extras['_hosted_media_originals'];self.assertEqual(len(records),1)
                original=Path(records[0]['path']);self.assertTrue(original.exists());self.assertNotEqual(str(original),prepared[0])
                with Image.open(prepared[0]) as picture:mime=Image.MIME[picture.format]
                model='data:'+mime+';base64,'+base64.b64encode(Path(prepared[0]).read_bytes()).decode()
                db=sqlite3.connect(':memory:')
                try:
                    media.register_owned(db,records,[{'scope':'bot_shared','event':'event','images':[model]}]);db.commit()
                    db.execute('BEGIN IMMEDIATE');media.retire_batch(db,[model],1,[media.event_key('bot_shared','event')]);batch_state(db,1);db.commit()
                    self.assertEqual(media.drain(db)['deleted'],1);self.assertFalse(original.exists())
                    self.assertTrue(Path(prepared[0]).exists()) # SDK retains its working-file owner.
                finally:db.close()
                extras['_hosted_media_originals']=[]
                borrowed=root/'persistent-sticker.png';borrowed.write_bytes(raw.getvalue())
                await image_input.prepare_model_image(str(borrowed),max_size=1280,output_dir=root)
                self.assertEqual(extras['_hosted_media_originals'],[]);self.assertTrue(borrowed.exists())
        finally:current_message_event.reset(token)


class CaptureAdmission(unittest.IsolatedAsyncioTestCase):
    async def test_real_prepare_admits_both_journal_modes_before_resize_await(self):
        from types import SimpleNamespace as NS
        from context_protocol import SOURCE
        from summary_material import snapshots
        tree=ast.parse((ROOT/'adapters/astrbot/behavior.py').read_text(encoding='utf-8'))
        prepare=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='prepare_chat_payload')
        index=next(i for i,n in enumerate(prepare.body) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Await) and isinstance(n.value.value,ast.Call) and isinstance(n.value.value.func,ast.Name) and n.value.value.func.id=='constrain_messages')
        # Execute the production admission block and its actual following await.
        wrapper=ast.AsyncFunctionDef(name='capture',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='projected')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=prepare.body[index-1:index+1],decorator_list=[])
        module=ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[]))
        for shared in (False,True):
            with self.subTest(shared=shared),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();file=root/'original.png';file.write_bytes(b'original')
                db=sqlite3.connect(':memory:');value='data:image/png;base64,b3JpZ2luYWw='
                scope='bot_shared' if shared else 'group:1234'
                old=json.dumps(['group:1234','old'],separators=(',',':')) if shared else 'old'
                media.register_owned(db,[media.owned_record(file,root,[value])],[{'scope':scope,'event':old,'images':[value]}]);db.commit()
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    media.retire_batch(db,[value],1,[media.event_key(scope,old)])
                    batch_state(db,1)
                extras={'_hosted_pipeline':NS(scope='group:1234',event_id='new')}
                event=NS(get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.__setitem__(k,v))
                entered=asyncio.Event();finish=asyncio.Event()
                async def resize(*args):entered.set();await finish.wait();return args[0]
                projected=[{'role':'user','content':[{'type':'text','text':SOURCE+json.dumps({'scope':'group:1234','sender_id':'123456','event_id':'new'})+'\nimage'},
                                                  {'type':'image_url','image_url':{'url':value}}]}]
                environment={'owned':NS(db=db,shared_context=shared),'memory_capture':not shared,'foreground':True,
                             'delivery_current':lambda e:True,'image_event':event,'snapshots':snapshots,'work':extras['_hosted_pipeline'],
                             'projected':projected,'constrain_messages':resize,'image_memo':{},'retained_media':None,'asyncio':asyncio}
                exec(compile(module,'behavior_media_admission','exec'),environment)
                task=asyncio.create_task(environment['capture'](projected))
                try:
                    await asyncio.wait_for(entered.wait(),3)
                    self.assertFalse(db.in_transaction)
                    self.assertEqual(media.drain(db)['deleted'],0);self.assertTrue(file.exists())
                finally:finish.set();await task;db.close()


class ProtocolOwnership(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from types import SimpleNamespace as NS
        from PIL import Image
        self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name).resolve()
        self.file=self.root/'qq/profile/123456/nt_qq/nt_data/Pic/2026-11/Ori/image.png'
        self.file.parent.mkdir(parents=True)
        raw=io.BytesIO()
        with Image.new('RGB',(64,32),'blue') as image:image.save(raw,format='PNG')
        self.bytes=raw.getvalue();self.file.write_bytes(self.bytes)
        self.image='data:image/png;base64,'+base64.b64encode(self.bytes).decode()
        self.extras={'_hosted_lease':{'root':str(self.root)},'_hosted_pipeline':NS(scope='group:1',event_id='work1')}
        self.event=NS(get_extra=lambda k,d=None:self.extras.get(k,d),set_extra=lambda k,v:self.extras.__setitem__(k,v),
                      get_self_id=lambda:'123456',bot=NS(get_image=AsyncMock(return_value={'file':str(self.file),'file_size':str(len(self.bytes))})),
                      message_obj=NS(raw_message={'self_id':123456,'message':[{'type':'image','data':{'file':'inbound-image-id','file_size':len(self.bytes)}}]}))
        self.materials=[{'scope':'bot_shared','event':'["group:1","work1"]','images':[self.image]}]
        self.db=sqlite3.connect(':memory:');media.initialize(self.db);self.db.commit()

    def tearDown(self):self.db.close();self.folder.cleanup()

    async def bind(self):
        await media.collect_protocol_originals(self.event,lambda:True)
        media.register_event(self.db,self.event,self.materials);self.db.commit()

    def release(self,checkpoint=1):
        self.db.execute('BEGIN IMMEDIATE')
        media.retire_batch(self.db,[self.image],checkpoint,[media.event_key('bot_shared',self.materials[0]['event'])])
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_semantic_batches'").fetchone() or not self.db.execute('SELECT 1 FROM memory_semantic_batches WHERE checkpoint_id=?',(checkpoint,)).fetchone():
            batch_state(self.db,checkpoint)
        self.db.commit();return media.drain(self.db)

    async def test_onebot_real_file_reply_binds_and_deletes_only_account_image_cache(self):
        await self.bind();self.event.bot.get_image.assert_awaited_once_with(file='inbound-image-id')
        self.assertEqual(self.release()['deleted'],1);self.assertFalse(self.file.exists())

    async def test_protocol_new_owner_is_durable_before_resize_yields(self):
        await self.bind()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            media.retire_batch(self.db,[self.image],1,[media.event_key('bot_shared',self.materials[0]['event'])])
            batch_state(self.db,1)
        self.extras['_hosted_media_protocol_checked']=False;self.extras['_hosted_pipeline'].event_id='work2'
        self.materials[0]['event']='["group:1","work2"]'
        entered=asyncio.Event();finish=asyncio.Event()
        resized='data:image/png;base64,cmVzaXplZA=='
        async def resize(value):entered.set();await finish.wait();return resized
        with patch('model_images._limited',side_effect=resize):
            task=asyncio.create_task(media.collect_protocol_originals(self.event,lambda:True,self.db,shared=True))
            try:
                await asyncio.wait_for(entered.wait(),3);self.assertFalse(self.db.in_transaction)
                self.assertEqual(media.drain(self.db)['deleted'],0);self.assertTrue(self.file.exists())
            finally:finish.set();await task
        self.materials[0]['images']=[resized]
        media.register_event(self.db,self.event,self.materials);self.db.commit()
        self.assertEqual(self.release(2)['deleted'],1);self.assertFalse(self.file.exists())

    async def test_protocol_admission_does_not_commit_another_transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaisesRegex(ValueError,'media_admission_requires_idle_transaction'):
            await media.collect_protocol_originals(self.event,lambda:True,self.db,shared=True)
        self.assertTrue(self.db.in_transaction);self.event.bot.get_image.assert_not_awaited()
        self.db.rollback()

    async def test_downloaded_cache_path_can_be_reused_by_a_later_batch(self):
        await self.bind();self.release();self.file.write_bytes(self.bytes)
        self.extras['_hosted_media_protocol_checked']=False;self.extras['_hosted_pipeline'].event_id='work2'
        self.materials[0]['event']='["group:1","work2"]'
        await self.bind();self.assertEqual(self.release(2)['deleted'],1)

    async def test_other_account_profile_and_login_database_are_rejected(self):
        for tail in ('qq/profile/654321/nt_qq/nt_data/Pic/image.png','qq/profile/123456/nt_qq/nt_db/identity.db','qq-favorites/uploads/image.png'):
            file=self.root/tail;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(self.bytes)
            self.event.bot.get_image.return_value={'file':str(file)};self.extras['_hosted_media_protocol_checked']=False
            await self.bind();self.assertEqual(self.db.execute('SELECT count(*) FROM semantic_media_owned').fetchone()[0],0)
            self.assertEqual(self.release()['protocol_unresolved'],1);self.assertTrue(file.exists())

    async def test_invalid_mime_and_size_cannot_grant_ownership(self):
        self.file.write_bytes(b'not an image');self.event.bot.get_image.return_value={'file':str(self.file)}
        await self.bind();self.assertEqual(self.release()['deleted'],0);self.assertTrue(self.file.exists())
        self.assertEqual(self.db.execute('SELECT count(*) FROM semantic_media_protocol_issues').fetchone()[0],1)

    async def test_url_only_reply_is_unresolved_and_no_path_is_guessed(self):
        self.event.bot.get_image.return_value={'url':'https://qpic.cn/image.png'}
        await self.bind();result=self.release();self.assertEqual(result['deleted'],0)
        self.assertEqual(result['protocol_unresolved'],1);self.assertTrue(self.file.exists())

    async def test_protocol_image_must_match_exact_model_material(self):
        self.materials[0]['images']=['data:image/png;base64,another-image']
        await self.bind();self.assertEqual(self.db.execute('SELECT count(*) FROM semantic_media_owned').fetchone()[0],0)
        self.assertEqual(self.release()['protocol_unresolved'],1);self.assertTrue(self.file.exists())

    async def test_stale_get_image_response_does_not_publish_or_delete(self):
        entered=asyncio.Event();finish=asyncio.Event();current=True
        async def response(**params):
            entered.set();await finish.wait();return {'file':str(self.file)}
        self.event.bot.get_image.side_effect=response
        task=asyncio.create_task(media.collect_protocol_originals(self.event,lambda:current))
        await entered.wait();current=False;finish.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertNotIn('_hosted_media_originals',self.extras);self.assertTrue(self.file.exists())


if __name__=='__main__':unittest.main()
