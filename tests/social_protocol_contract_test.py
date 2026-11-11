"""Protocol fixtures exercise real tool and storage code; no actual QQ claims."""
import asyncio,hashlib,io,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
import quote_reply,qq_favorite_tools,sticker_collection,group_actions,base64
from data_admin import run as admin
from embedding_turn import EmbeddingTurn
import embedding_turn

class SocialContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.st=BehaviorStore(self.path);self.extra={};self.sent=[];self.favorite_rows=[];self.confirm=True
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(3,3),'blue').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(3,3),'red')],duration=100,loop=0);self.raw=out.getvalue()
        async def login():return {'user_id':900001}
        async def details(**_):return list(self.favorite_rows)
        async def add(**kw):
            self.sent.append(kw)
            self.assertEqual(Path(kw['file']).read_bytes(),self.raw)
            if self.confirm:self.favorite_rows.append({'md5':hashlib.md5(self.raw).hexdigest(),'resId':'actual-fixture-resid','url':'https://qpic.cn/fixture.gif'})
            return {}
        self.event=NS(bot=NS(get_login_info=login,fetch_custom_face_detail=details,add_custom_face=add),message_obj=NS(message_id=42,raw_message={'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture.gif','sub_type':1}}]}),get_sender_id=lambda:'12345678',get_self_id=lambda:'900001',get_group_id=lambda:'55555',get_message_str=lambda:'这张表情',get_extra=lambda k,d=None:self.extra.get(k,d),set_extra=lambda k,v:self.extra.update({k:v}))
        self.lease={'botId':'owned-bot','generation':7};self.context=NS(context=NS(event=self.event))
    def tearDown(self):self.st.close();self.tmp.cleanup()
    async def test_dynamic_candidates_do_not_change_tool_schemas_or_authorize_foreign_handles(self):
        from astrbot.core.agent.tool import ToolSet
        import inner_tools
        self.extra['_hosted_lease']=self.lease
        self.extra['_hosted_context']='{"identity":{"sender_id":"12345678"},"state":{"x":1}}'
        cfg={'behavior':{}}
        def schema():
            return ToolSet(tools=quote_reply.tools(self.event,cfg,self.st.db,self.lease,lambda:True)+qq_favorite_tools.tools(self.event,cfg,self.st.db,self.tmp.name,lambda:True)).get_func_desc_openai_style()
        quote_reply.observe(self.st.db,self.event,self.lease,'group:55555')
        before=schema();first=quote_reply.catalog(self.st.db,self.event,self.lease)
        self.event.message_obj.message_id=43
        quote_reply.observe(self.st.db,self.event,self.lease,'group:55555')
        after=schema();second=quote_reply.catalog(self.st.db,self.event,self.lease)
        self.assertNotEqual(first,second);self.assertEqual(before,after)
        self.extra['_hosted_tool_context']={'quote_reply':second,'qq_favorites':qq_favorite_tools.context(self.event,self.st.db)}
        context=json.loads(inner_tools.request_context(self.event))
        self.assertCountEqual(context['tool_context']['quote_reply'],second) # Context serialization is chronological; tool catalog is newest-first.
        self.assertEqual(context['state'],{'x':1})
        tool=quote_reply.tools(self.event,cfg,self.st.db,self.lease,lambda:True)[0]
        denied=json.loads(await tool.call(self.context,'qr_'+'f'*20))
        self.assertEqual(denied['status'],'invalid_reference');self.assertNotIn('_hosted_quote_handle',self.extra)
        # A stable schema does not remove the actual scope/generation checks.
        self.lease['generation']=8
        denied=json.loads(await tool.call(self.context,first[0]['handle']))
        self.assertEqual(denied['status'],'invalid_reference')

    async def test_favorite_list_uses_same_semantics_and_disable_state_as_automatic_candidates(self):
        row={'md5':'a'*32,'resId':'fixture-resid','url':'https://qpic.cn/fixture'};self.favorite_rows.append(row)
        folder=Path(self.tmp.name)/'qq-favorites';folder.mkdir()
        identifier='qqf_'+hashlib.sha256(row['md5'].encode()).hexdigest()[:16]
        (folder/'index.json').write_text(json.dumps({'account':'900001','enabled':True,'items':[{'id':identifier,'meaning':'向对方挥手打招呼','avoid':'不用于嘲笑','enabled':False}]}),encoding='utf8')
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        listed=json.loads(await tool.call(self.context,operation='list'))['items'][0]
        self.assertEqual(listed['meaning'],'向对方挥手打招呼');self.assertEqual(listed['avoid'],'不用于嘲笑');self.assertTrue(listed['selection_disabled'])
        result=json.loads(await tool.call(self.context,operation='prepare',favorite_handle=listed['handle']))
        self.assertEqual(result['status'],'disabled_favorite');self.assertNotIn('_hosted_tool_sticker',self.extra)
    async def test_prepare_is_idempotent_per_work_and_never_resends_after_dispatch(self):
        row={'md5':'a'*32,'resId':'fixture-resid','url':'https://qpic.cn/fixture'};self.favorite_rows.append(row)
        other={'md5':'b'*32,'resId':'fixture-resid-2','url':'https://qpic.cn/fixture2'};self.favorite_rows.append(other)
        folder=Path(self.tmp.name)/'qq-favorites';folder.mkdir(exist_ok=True)
        identifier='qqf_'+hashlib.sha256(row['md5'].encode()).hexdigest()[:16]
        second='qqf_'+hashlib.sha256(other['md5'].encode()).hexdigest()[:16]
        (folder/'index.json').write_text(json.dumps({'account':'900001','enabled':True,'items':[
            {'id':identifier,'file':identifier+'.gif','meaning':'挥手','avoid':'严肃','enabled':True,'semantic':{'confidence':'clear'}},
            {'id':second,'file':second+'.gif','meaning':'点头','avoid':'哀伤','enabled':True,'semantic':{'confidence':'clear'}}]}),encoding='utf8')
        tool=qq_favorite_tools.tools(self.event,{'behavior':{'memeEnabled':True}},self.st.db,self.tmp.name,lambda:True)[0]
        items=json.loads(await tool.call(self.context,operation='list'))['items']
        first_handle=next(i['handle'] for i in items if i['meaning']=='挥手')
        second_handle=next(i['handle'] for i in items if i['meaning']=='点头')
        with patch.object(qq_favorite_tools,'prepare_original',return_value=(self.raw,'send_cache')) as prepared:
            result=await tool.call(self.context,operation='prepare',favorite_handle=first_handle)
            self.assertIn('已选定真实QQ收藏',result);self.assertEqual(prepared.call_count,1)
            sticker=self.extra['_hosted_tool_sticker'];self.assertEqual(sticker['digest'],hashlib.md5(self.raw).hexdigest())
            again=await tool.call(self.context,operation='prepare',favorite_handle=first_handle)
            self.assertEqual(json.loads(again)['status'],'already_prepared');self.assertEqual(prepared.call_count,1,'a repeated prepare must not download the same item again')
            # A different attachment may replace the pending one before dispatch.
            replaced=await tool.call(self.context,operation='prepare',favorite_handle=second_handle)
            self.assertIn('已选定真实QQ收藏',replaced);self.assertEqual(prepared.call_count,2)
            self.assertEqual(self.extra['_hosted_tool_sticker']['id'],second_handle)
            # Once the round was dispatched (or its ACK is unknown) nothing is
            # downloaded or re-attached again in the same Work.
            self.extra['_hosted_meme_sent']=True
            blocked=await tool.call(self.context,operation='prepare',favorite_handle=first_handle)
            self.assertEqual(json.loads(blocked)['status'],'already_sent');self.assertEqual(prepared.call_count,2)
    async def test_raw_qq_text_is_never_selection_semantics(self):
        row={'md5':'c'*32,'modifyWord':'来自QQ的文字','resId':'r3'};self.favorite_rows.append(row)
        folder=Path(self.tmp.name)/'qq-favorites';folder.mkdir(exist_ok=True)
        identifier='qqf_'+hashlib.sha256(row['md5'].encode()).hexdigest()[:16]
        (folder/'index.json').write_text(json.dumps({'account':'900001','enabled':True,'items':[
            {'id':identifier,'file':identifier+'.gif','rawText':'来自QQ的文字','meaning':'','avoid':'','enabled':False,'semanticStatus':'raw_text'}]}),encoding='utf8')
        tool=qq_favorite_tools.tools(self.event,{'behavior':{'memeEnabled':True}},self.st.db,self.tmp.name,lambda:True)[0]
        listed=json.loads(await tool.call(self.context,operation='list'))['items'][0]
        self.assertEqual(listed['meaning'],'','QQ raw text must not be presented as a usable meaning')
        self.assertEqual(listed['rawText'],'来自QQ的文字')
        self.assertTrue(listed['selection_disabled'])
        result=json.loads(await tool.call(self.context,operation='prepare',favorite_handle=listed['handle']))
        self.assertEqual(result['status'],'disabled_favorite')
        self.assertNotIn('_hosted_tool_sticker',self.extra)
        evidence=qq_favorite_tools.descriptions(self.tmp.name,'900001')[identifier]
        self.assertEqual(evidence['rawText'],'来自QQ的文字');self.assertEqual(evidence['semanticStatus'],'raw_text')

    async def test_favorite_requires_readback_and_preserves_original_animation(self):
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        self.assertEqual(qq_favorite_tools.context(self.event,self.st.db)['source_ids'],['current'])
        self.assertIn('favorite_handle',tool.parameters['properties'])
        with patch.object(qq_favorite_tools,'download',return_value=self.raw):
            result=await tool.call(self.context,operation='save');self.assertIn('真实收藏成功',result)
            saved=json.loads(result)['item'];self.assertTrue(saved['saved_from_this_conversation']);self.assertGreater(saved['saved_at'],0)
            listed=json.loads(await tool.call(self.context,operation='list'))['items'];self.assertEqual(listed,[saved])
            again=await tool.call(self.context,operation='save');self.assertIn('无需重复添加',again)
            await tool.call(self.context,operation='prepare',favorite_handle='qf_'+hashlib.sha256(b'900001|actual-fixture-resid').hexdigest()[:20])
        self.assertEqual(len(self.sent),1);self.assertEqual(self.extra['_hosted_tool_sticker']['bytes'],self.raw)
        self.assertEqual(self.st.db.execute('SELECT state FROM qq_favorite_writes').fetchone()[0],'confirmed')
        self.extra['_hosted_meme_allowed']=False;self.extra['_hosted_meme_gate']={'reason':'cooldown','retryAfterSeconds':18}
        blocked=json.loads(await tool.call(self.context,operation='prepare',favorite_handle=saved['handle']))
        self.assertEqual(blocked['reason'],'cooldown');self.assertEqual(blocked['retryAfterSeconds'],18)
        self.event.get_group_id=lambda:'66666'
        foreign=json.loads(await tool.call(self.context,operation='list'))['items'][0]
        self.assertFalse(foreign['saved_from_this_conversation']);self.assertIsNone(foreign['saved_at'])
    async def test_preparing_saved_original_uses_no_second_cdn_download(self):
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        with patch.object(qq_favorite_tools,'download',return_value=self.raw) as fetch:
            saved=json.loads(await tool.call(self.context,operation='save'))
        self.assertEqual(fetch.await_count,1)
        with patch.object(qq_favorite_tools,'download',side_effect=AssertionError('unexpected download')) as fetch:
            await tool.call(self.context,operation='prepare',favorite_handle=saved['item']['handle'])
        self.assertEqual(fetch.await_count,0)
        self.assertEqual(self.extra['_hosted_tool_sticker']['source'],'saved_original')
        self.assertEqual(self.extra['_hosted_tool_sticker']['bytes'],self.raw)
        self.assertEqual(len(self.sent),1)
    async def test_favorite_send_cache_survives_new_tool_and_remains_account_bound(self):
        row={'md5':hashlib.md5(self.raw).hexdigest().upper(),'resId':'remote-existing','url':'https://qpic.cn/original.gif'}
        self.favorite_rows.append(row)
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        selected=json.loads(await tool.call(self.context,operation='list'))['items'][0]['handle']
        with patch.object(qq_favorite_tools,'download',return_value=self.raw) as fetch:
            await tool.call(self.context,operation='prepare',favorite_handle=selected)
            self.assertEqual(self.extra['_hosted_tool_sticker']['source'],'qq_download')
            recreated=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
            repeat=json.loads(await recreated.call(self.context,operation='prepare',favorite_handle=selected))
            self.assertEqual(repeat['status'],'already_prepared','a repeated prepare of the same Work attachment is already_prepared')
            self.assertEqual(fetch.await_count,1,'the repeated prepare never downloads again')
            self.assertEqual(self.extra['_hosted_tool_sticker']['source'],'qq_download','the pending attachment is not replaced')
            # The send-cache hit path is still available to a later Work and
            # never crosses accounts.
            _,cache_source=await qq_favorite_tools.prepare_original(row,self.tmp.name,'900001',lambda:True)
            self.assertEqual(cache_source,'send_cache')
            _,source=await qq_favorite_tools.prepare_original(row,self.tmp.name,'900002',lambda:True)
            self.assertEqual(source,'qq_download')
            # Another actual account cannot select this account's send-cache
            # path, even if QQ happens to list the same image digest there.
        self.assertEqual(self.extra['_hosted_tool_sticker']['bytes'],self.raw)
    async def test_favorite_cache_verifies_content_and_never_writes_after_unbind(self):
        digest=hashlib.md5(self.raw).hexdigest();row={'md5':digest,'url':'https://qpic.cn/original.gif'}
        uploads=Path(self.tmp.name)/'qq-favorites/uploads';uploads.mkdir(parents=True)
        original=uploads/(digest+'.image');original.write_bytes(b'damaged original')
        with patch.object(qq_favorite_tools,'download',return_value=self.raw) as fetch:
            raw,source=await qq_favorite_tools.prepare_original(row,self.tmp.name,'900001',lambda:True)
        self.assertEqual(raw,self.raw);self.assertEqual(source,'qq_download');self.assertEqual(fetch.await_count,1)
        self.assertEqual(original.read_bytes(),b'damaged original')
        with patch.object(qq_favorite_tools,'download',return_value=self.raw+b'wrong'):
            with self.assertRaisesRegex(ValueError,'content_mismatch'):
                await qq_favorite_tools.prepare_original(row,self.tmp.name,'900002',lambda:True)
        valid=True
        async def unbind(*args,**kwargs):
            nonlocal valid
            valid=False;return self.raw
        with patch.object(qq_favorite_tools,'download',side_effect=unbind):
            with self.assertRaises(ValueError):await qq_favorite_tools.prepare_original(row,self.tmp.name,'900003',lambda:valid)
        self.assertFalse((uploads.parent/'send-cache'/('900003_'+digest+'.image')).exists())
    async def test_send_cache_prunes_only_disposable_bytes_and_honors_bounds(self):
        folder=Path(self.tmp.name)/'qq-favorites/send-cache';folder.mkdir(parents=True)
        preserved=folder/'operator-note.txt';preserved.write_text('keep')
        upload=folder.parent/'uploads';upload.mkdir();(upload/'saved.image').write_bytes(b'account data')
        for n in range(4):
            raw=(str(n)*4).encode();name='900001_'+hashlib.md5(raw).hexdigest()+'.image'
            with patch.object(qq_favorite_tools,'SEND_CACHE_BYTES',8),patch.object(qq_favorite_tools,'SEND_CACHE_ITEMS',2):
                qq_favorite_tools.cache_original(folder,name,raw)
        files=list(folder.glob('*.image'));self.assertEqual(len(files),2)
        self.assertLessEqual(sum(file.stat().st_size for file in files),8)
        self.assertEqual((upload/'saved.image').read_bytes(),b'account data');self.assertEqual(preserved.read_text(),'keep')
        self.assertFalse(list(folder.glob('.qqf-send-*')))
    async def test_unknown_favorite_never_reuploads_and_foreign_material_is_rejected(self):
        self.confirm=False;tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        with patch.object(qq_favorite_tools,'download',return_value=self.raw):
            self.assertIn('未确认',await tool.call(self.context,operation='save'))
            self.assertIn('不会重复上传',await tool.call(self.context,operation='save'))
        self.assertEqual(len(self.sent),1)
        self.assertIn('不属于当前会话',await tool.call(self.context,operation='save',source_id='cap_foreign'))
    async def test_lost_add_response_is_confirmed_by_readback_without_second_upload(self):
        original=self.event.bot.add_custom_face
        async def lost(**kw):await original(**kw);raise TimeoutError('private transport detail')
        self.event.bot.add_custom_face=lost
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        with patch.object(qq_favorite_tools,'download',return_value=self.raw):result=json.loads(await tool.call(self.context,operation='save'))
        self.assertEqual(result['status'],'confirmed');self.assertEqual(len(self.sent),1)
        self.assertNotIn('private transport detail',json.dumps(result))
        self.assertEqual(self.st.db.execute('SELECT state,last_error FROM qq_favorite_writes').fetchone(),('confirmed',''))
    async def test_readback_failure_after_write_can_be_recovered_in_same_turn(self):
        calls=0
        async def detail(**_):
            nonlocal calls
            calls+=1
            if calls==2:raise ConnectionError('private URL and key')
            return list(self.favorite_rows)
        self.event.bot.fetch_custom_face_detail=detail
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        with patch.object(qq_favorite_tools,'download',return_value=self.raw):result=json.loads(await tool.call(self.context,operation='save'))
        self.assertEqual(result['status'],'confirmed');self.assertEqual(len(self.sent),1);self.assertEqual(calls,3)
    async def test_late_visible_favorite_list_settles_foreground_without_reupload(self):
        self.confirm=False
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        with patch.object(qq_favorite_tools,'download',return_value=self.raw):result=json.loads(await tool.call(self.context,operation='save'))
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['code'],'favorite_confirmation_pending')
        digest=hashlib.md5(self.raw).hexdigest();self.favorite_rows.append({'md5':digest.upper(),'resId':'late-actual-resid'})
        listed=json.loads(await tool.call(self.context,operation='list'))['items']
        self.assertTrue(listed[0]['saved_from_this_conversation']);self.assertEqual(len(self.sent),1)
        self.assertEqual(self.st.db.execute('SELECT state,res_id FROM qq_favorite_writes').fetchone(),('confirmed','late-actual-resid'))
    async def test_pending_rechecks_survive_restart_and_apply_read_only_backoff(self):
        digest=hashlib.md5(self.raw).hexdigest()
        self.st.db.execute("INSERT INTO qq_favorite_writes(account,md5,scope,source,state,res_id,at) VALUES('900001',?,'group:55555','current','unknown','',?)",(digest,time.time()));self.st.db.commit()
        first=await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001',None,lambda:True)
        self.assertTrue(first['checked']);self.assertFalse(first['confirmed'])
        self.st.close();self.st=BehaviorStore(self.path)
        self.assertFalse((await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001',None,lambda:True))['checked'])
        self.st.db.execute('UPDATE qq_favorite_writes SET next_check=0');self.st.db.commit()
        self.favorite_rows.append({'md5':digest,'resId':'read-after-restart'})
        result=await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001',None,lambda:True)
        self.assertIn(digest,result['confirmed']);self.assertEqual(self.sent,[])
        self.assertEqual(self.st.db.execute('SELECT state,checks FROM qq_favorite_writes').fetchone(),('confirmed',2))
    async def test_readback_never_settles_changed_identity_or_stale_binding(self):
        digest=hashlib.md5(self.raw).hexdigest();self.favorite_rows.append({'md5':digest,'resId':'identity-resid'})
        self.st.db.execute("INSERT INTO qq_favorite_writes(account,md5,scope,source,state,res_id,at) VALUES('900001',?,'group:55555','current','unknown','',?)",(digest,time.time()));self.st.db.commit()
        async def other():return {'user_id':900002}
        self.event.bot.get_login_info=other
        self.assertFalse((await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001',None,lambda:True))['confirmed'])
        self.st.db.execute('UPDATE qq_favorite_writes SET next_check=0');self.st.db.commit();valid=True
        async def login():return {'user_id':900001}
        async def changed(**_):
            nonlocal valid
            valid=False;return list(self.favorite_rows)
        self.event.bot.get_login_info=login;self.event.bot.fetch_custom_face_detail=changed
        self.assertFalse((await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001',None,lambda:valid))['confirmed'])
        self.assertEqual(self.st.db.execute('SELECT state FROM qq_favorite_writes').fetchone()[0],'unknown');self.assertEqual(self.sent,[])
    async def test_action_receipt_reconciles_only_matching_account_without_replay(self):
        digest=hashlib.md5(self.raw).hexdigest();self.favorite_rows.append({'md5':digest,'resId':'action-proof'})
        for account in ('900001','900002'):
            self.st.db.execute("INSERT INTO qq_favorite_writes(account,md5,scope,source,state,res_id,at,action,sequence) VALUES(?,?,'group:55555','current','unknown','',?,'work-1',0)",(account,digest,time.time()))
        self.st.db.execute("INSERT INTO pipeline_action_effects VALUES('work-1',0,'add_custom_face','self','dispatching','',?)",(time.time(),));self.st.db.commit()
        result=await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001','work-1',lambda:True)
        self.assertIn(digest,result['confirmed']);self.assertEqual(self.sent,[])
        self.assertEqual(self.st.db.execute("SELECT state FROM qq_favorite_writes WHERE account='900002'").fetchone()[0],'unknown')
        self.assertEqual(self.st.db.execute('SELECT state,receipt FROM pipeline_action_effects').fetchone(),('confirmed','qq_favorite:'+digest+':action-proof'))
    async def test_existing_global_caretaker_closes_foreground_unknown_without_model(self):
        from runtime_care import RuntimeCare
        digest=hashlib.md5(self.raw).hexdigest();self.favorite_rows.append({'md5':digest,'resId':'caretaker-proof'})
        self.st.db.execute("INSERT INTO qq_favorite_writes(account,md5,scope,source,state,res_id,at) VALUES('900001',?,'group:55555','current','unknown','',?)",(digest,time.time()));self.st.db.commit()
        care=RuntimeCare({'owned':self.st})
        async def check():
            await qq_favorite_tools.reconcile(self.st.db,self.event.bot,'900001',None,lambda:True);care.close()
        care.favorite_reconciler=check
        async def tick(_):pass
        with patch('runtime_care.asyncio.sleep',new=tick):await care.run()
        self.assertEqual(self.st.db.execute('SELECT state FROM qq_favorite_writes').fetchone()[0],'confirmed');self.assertEqual(self.sent,[])
    async def test_binding_removed_after_upload_never_writes_closed_store_or_rechecks_old_qq(self):
        original=self.event.bot.add_custom_face;valid=True;reads=0
        async def detail(**_):
            nonlocal reads
            reads+=1;return list(self.favorite_rows)
        async def detached(**kw):
            nonlocal valid
            await original(**kw);valid=False;self.st.close()
        self.event.bot.add_custom_face=detached;self.event.bot.fetch_custom_face_detail=detail
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:valid)[0]
        try:
            with patch.object(qq_favorite_tools,'download',return_value=self.raw):result=json.loads(await tool.call(self.context,operation='save'))
            self.assertEqual(result['status'],'unknown');self.assertEqual(len(self.sent),1);self.assertEqual(reads,1)
        finally:self.st=BehaviorStore(self.path)
        self.assertEqual(self.st.db.execute('SELECT state FROM qq_favorite_writes').fetchone()[0],'unknown')
    async def test_following_text_can_save_previous_image_but_never_other_sender_or_scope(self):
        self.extra['_hosted_lease']=self.lease
        quote_reply.observe(self.st.db,self.event,self.lease,'group:55555')
        source=quote_reply.media_catalog(self.st.db,self.event,self.lease)[0]
        native={'message_id':42,'group_id':55555,'sender':{'user_id':12345678},'message':self.event.message_obj.raw_message['message']}
        async def get_msg(**kw):self.assertEqual(kw['message_id'],42);return native
        self.event.bot.get_msg=get_msg;self.event.message_obj=NS(message_id=43,raw_message={'message':[]})
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        self.assertIn(source['handle'],qq_favorite_tools.context(self.event,self.st.db)['source_ids'])
        with patch.object(qq_favorite_tools,'download',return_value=self.raw):
            self.assertEqual(json.loads(await tool.call(self.context,operation='save'))['status'],'confirmed')
        self.event.get_sender_id=lambda:'87654321'
        self.assertEqual(json.loads(await tool.call(self.context,operation='save'))['code'],'media_source_ambiguous_or_missing')
        self.event.get_group_id=lambda:'66666'
        self.assertEqual(quote_reply.media_catalog(self.st.db,self.event,self.lease),[])
        self.event.get_group_id=lambda:'55555';self.lease['generation']=8
        self.assertEqual(quote_reply.media_catalog(self.st.db,self.event,self.lease),[])
        self.event.message_obj.raw_message={'message':[{'type':'image','data':{'url':'https://qpic.cn/one.gif'}},{'type':'image','data':{'url':'https://qpic.cn/two.gif'}}]}
        self.assertEqual(json.loads(await tool.call(self.context,operation='save'))['code'],'media_source_ambiguous_or_missing')
        self.assertEqual(len(self.sent),1)
    async def test_large_original_is_bounded_and_pre_submission_failure_is_not_unknown(self):
        large=self.raw+b'\0'*(2*1048576)
        with self.assertRaisesRegex(ValueError,'media_too_large'):sticker_collection.png_thumbnail(large)
        self.assertTrue(qq_favorite_tools.validate_image(large).startswith(b'\x89PNG'))
        tool=qq_favorite_tools.tools(self.event,{'behavior':{}},self.st.db,self.tmp.name,lambda:True)[0]
        with patch.object(qq_favorite_tools,'download',side_effect=ValueError('media_too_large')):
            result=json.loads(await tool.call(self.context,operation='save'))
        self.assertEqual(result['status'],'blocked');self.assertFalse(result['submitted']);self.assertEqual(self.sent,[])
    async def test_prepared_favorite_goes_to_authorized_group_once_without_private_attachment(self):
        self.event.get_group_id=lambda:'';self.event.get_message_str=lambda:'把这张发群里'
        async def groups():return [{'group_id':55555}]
        async def member(**kw):return {'user_id':kw['user_id']}
        async def send(**kw):self.sent.append(kw);return {'message_id':321}
        self.event.bot.get_group_list=groups;self.event.bot.get_group_member_info=member;self.event.bot.send_group_msg=send
        self.extra['_hosted_tool_sticker']={'id':'qf_test','bytes':self.raw}
        cfg={'groups':['55555'],'behavior':{'ownerUin':'12345678','memeEnabled':True}}
        result=json.loads(await group_actions.send(self.event,cfg,self.st.db,'55555','', '',lambda:True,lambda *args:None,True,self.st.social))
        self.assertEqual(result['status'],'confirmed');self.assertTrue(result['image'])
        self.assertEqual(base64.b64decode(self.sent[0]['message'][0]['data']['file'][9:]),self.raw)
        self.assertEqual(self.sent[0]['message'][0]['data']['sub_type'],1)
        self.assertIsNone(self.extra['_hosted_tool_sticker']);self.assertTrue(self.extra['_hosted_meme_sent'])
        await group_actions.send(self.event,cfg,self.st.db,'55555','后续', '',lambda:True,lambda *args:None)
        self.assertEqual(len(self.sent),1)
        self.event.message_obj.message_id='uncertain-group-image';cfg['behavior']['memeCooldownSeconds']=0
        self.extra['_hosted_tool_sticker']={'id':'qf_test','bytes':self.raw}
        async def uncertain(**kw):self.sent.append(kw);raise TimeoutError()
        self.event.bot.send_group_msg=uncertain
        self.assertIn('未确认',await group_actions.send(self.event,cfg,self.st.db,'55555','', '',lambda:True,lambda *args:None,True,self.st.social))
        self.assertIsNone(self.extra['_hosted_tool_sticker'])
        await group_actions.send(self.event,cfg,self.st.db,'55555','重复', '',lambda:True,lambda *args:None)
        self.assertEqual(len(self.sent),2)
    async def test_captured_gif_sends_original_while_preview_stays_png(self):
        raw={'message_id':42,'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture.gif','sub_type':1}}]}
        with patch.object(sticker_collection,'download',return_value=self.raw):
            self.st.social.stickers.observe(raw,'group:55555','12345678','900001',{'stickerCollect':True,'stickerGroups':['55555']},lambda:True,{},fetch=sticker_collection.download)
            await self.st.social.stickers.task
        row=self.st.db.execute('SELECT id,png FROM sticker_inbox').fetchone();self.assertTrue(bytes(row[1]).startswith(b'\x89PNG'))
        self.st.db.execute("UPDATE sticker_inbox SET status='approved' WHERE id=?",(row[0],));self.st.db.commit()
        self.assertEqual(self.st.social.stickers.image(row[0],'group:55555'),self.raw)
        candidates=self.st.social.context('group:55555','发图',{'memeEnabled':True,'memePacks':['blue-fat-fish']})['meme_candidates']
        self.assertTrue(any(i['source']=='platform_pack' and i['pack']=='blue-fat-fish' for i in candidates))
        collected=next(i for i in self.st.social.catalog('group:55555') if i['id']==row[0]);self.assertEqual(collected['source'],'collected');self.assertEqual(collected['source_scope'],'group:55555')
        self.assertFalse(any(i['source']=='collected' for i in self.st.social.catalog('group:66666')))
        self.st.db.execute("UPDATE sticker_inbox SET status='rejected' WHERE id=?",(row[0],));self.st.db.commit();self.assertEqual(self.st.db.execute('SELECT count(*) FROM sticker_originals').fetchone()[0],0)
    async def test_quote_ids_are_scope_account_and_generation_bound(self):
        quote_reply.observe(self.st.db,self.event,self.lease,'group:55555');first=quote_reply.catalog(self.st.db,self.event,self.lease)[0]['handle']
        tool=quote_reply.tools(self.event,{'behavior':{}},self.st.db,self.lease,lambda:True)[0]
        self.assertIn(first,[r['handle'] for r in quote_reply.catalog(self.st.db,self.event,self.lease)])
        await tool.call(self.context,handle=first);self.assertEqual(quote_reply.selected(self.st.db,self.event,self.lease),('42','12345678'))
        async def existing(**_):return {'message_id':42,'group_id':55555,'sender':{'user_id':12345678}}
        self.event.bot.get_msg=existing
        self.assertEqual(await quote_reply.resolve(self.st.db,self.event,self.lease,lambda:True),('42','12345678'))
        async def withdrawn(**_):raise ValueError('message withdrawn')
        self.event.bot.get_msg=withdrawn;self.assertIsNone(await quote_reply.resolve(self.st.db,self.event,self.lease,lambda:True))
        self.event.get_group_id=lambda:'66666';quote_reply.observe(self.st.db,self.event,self.lease,'group:66666')
        self.assertIsNone(quote_reply.selected(self.st.db,self.event,self.lease));self.assertNotEqual(first,quote_reply.catalog(self.st.db,self.event,self.lease)[0]['handle'])
        self.assertEqual(quote_reply.catalog(self.st.db,self.event,{**self.lease,'generation':8}),[])
    async def test_native_mface_can_be_collected_without_an_ai_reply(self):
        raw={'message_id':43,'message':[{'type':'mface','data':{'url':'https://gxh.vip.qq.com/fixture.gif'}}]}
        with patch.object(sticker_collection,'download',return_value=self.raw):
            self.st.social.stickers.observe(raw,'group:55555','12345678','900001',{'stickerCollect':True,'stickerGroups':['55555']},lambda:True,{},fetch=sticker_collection.download)
            await self.st.social.stickers.task
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM sticker_originals').fetchone()[0],1)
    async def test_data_pages_and_restored_rhythm_keep_real_records(self):
        now=time.time()
        self.st.db.executemany('INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen) VALUES(?,?,?,?,?,?)',[('group:55555',str(100000+i),i%4,'平静',0,now-i) for i in range(105)]);self.st.db.commit()
        one=admin(self.path,{'action':'list','page':0});two=admin(self.path,{'action':'list','page':1})
        self.assertEqual(one['pagination']['totals']['relations'],105);self.assertEqual(len(one['relations']),100);self.assertEqual(len(two['relations']),5)
        self.st.participation.outgoing('group:55555','第一条',now-80,subject='12345678',event='first');self.st.participation.outgoing('group:55555','第二条',now-20,subject='12345678',event='second')
        self.st.close();self.st=BehaviorStore(self.path)
        self.assertEqual(len(self.st.participation.scopes['group:55555']['sent']),2,'restored program fatigue retains real completed turns')
        self.assertNotIn('rhythm',self.st.participation.context('group:55555',now=now))
        self.assertTrue(self.st.participation.continuing('group:55555','接话','12345678',now,120))
        self.assertFalse(self.st.participation.continuing('group:66666','接话','12345678',now,120))
    async def test_fact_embedding_remains_available_without_summary_embedding_calls(self):
        now=time.time();calls=[]
        self.st.memory.remember('private:12345678','12345678','饮料','红茶','fact',now)
        self.st.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('private:12345678','喝茶的真实对话','[]',?)",(now,));self.st.db.commit()
        async def create(**kw):calls.append(kw['input']);return NS(data=[NS(index=i,embedding=[1.,0.]) for i,_ in enumerate(kw['input'])])
        client=NS(base_url='https://embedding.example/v1',embeddings=NS(create=create));client.with_options=lambda **_:client;provider=NS(client=client)
        cfg={'memory':True,'memorySemantic':True,'embeddingModel':'fixture','dailyEmbeddingLimit':100}
        async with EmbeddingTurn():
            await self.st.memory.semantic_recall('private:12345678','12345678','茶',now,cfg,provider,lambda:True)
            summaries=await self.st.reflection.semantic_recall('private:12345678','茶',cfg,provider,lambda:True,{},subject='12345678')
        self.assertEqual(sum(x=='茶' for inputs in calls for x in inputs),1);self.assertEqual(len(calls),1)
        self.assertEqual(summaries,self.st.reflection.foundation('private:12345678'))
        self.assertNotIn('喝茶的真实对话',[x for inputs in calls for x in inputs])
        self.assertEqual(self.st.reflection.foundation('private:12345678')[0]['summary'],'喝茶的真实对话')

    async def test_embedding_queue_expiry_does_not_charge_a_remote_request(self):
        provider=NS(client=NS(base_url='https://embedding.example/v1'));metrics={}
        with patch.object(embedding_turn,'slots',asyncio.Semaphore(0)):
            async with EmbeddingTurn(.01):
                with self.assertRaises(TimeoutError):
                    await embedding_turn.request(provider,'','fixture','query',[],self.st.db,{'dailyEmbeddingLimit':100},metrics)
        self.assertEqual(metrics,{})
        self.assertEqual(self.st.db.execute('SELECT coalesce(sum(count),0) FROM memory_embedding_budget').fetchone()[0],0)

    async def test_full_answer_replaces_confirmed_prefix_and_summary_delete_keeps_new_debt(self):
        r=self.st.reflection;scope='private:12345678'
        r.incoming(scope,'12345678','old','原问题');r.confirmed_prefix(scope,'12345678','old','第一条',r.epoch())
        r.record(scope,'12345678','old','原问题','第一条\n第二条')
        self.assertEqual(self.st.db.execute("SELECT answer FROM memory_pending WHERE event='old'").fetchone()[0],'第一条\n第二条')
        r.incoming(scope,'12345678','new','新问题');r.record(scope,'12345678','new','新问题','新回答')
        identifier=self.st.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,'旧摘要','["old"]',123)).lastrowid;self.st.db.commit()
        self.assertTrue(admin(self.path,{'action':'forget_summary','scope':scope,'id':identifier,'expectedUpdated':123},self.st.db)['ok'])
        self.assertEqual([r[0] for r in self.st.db.execute('SELECT event FROM memory_pending')],['new'])
        self.assertEqual([r[0] for r in self.st.db.execute('SELECT event FROM memory_rounds')],['new'])

if __name__=='__main__':unittest.main()
