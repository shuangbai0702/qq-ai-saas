"""Lossless new-event deltas and stable catalogs, using the pinned real SDK."""
import base64,copy,io,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from context_protocol import SOURCE,model_json
from context_transport import EVENT_CONTEXT_CURRENT,READONLY_CURRENT
from readonly_journal import ReadonlyJournal
from decision_history import apply_changes
import agreement_state,bilibili_tools,shared_context,model_images
from bot_pipeline import Work


def user(scope,event,text='hello'):
    return {'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':'12345678','event_id':event,'at':1})+'\n'+text}


class EventDeltas(unittest.TestCase):
    def test_35_appends_and_restart_restore_exact_event_contract(self):
        db=sqlite3.connect(':memory:');j=ReadonlyJournal(db,'bot_shared')
        base=[{'role':'system','content':'fixed bytes'}];old=[];restored=None;full_bytes=0
        for turn in range(35):
            if turn:j=ReadonlyJournal(db,'bot_shared')
            event={'authority':{'identity':{'sender_id':str(12345678+turn),'owner':turn%2==0},'permissions':{'write':turn%2==0}},
                   'policy':'必要稳定权限和人物规则。'*400,'currentGuidance':'只处理当前来源，不改历史。'*60,
                   'meaning':'platform evidence'}
            if turn==17:event['authority']['newCapability']={'write':False}
            base.append(user('private:12345678',str(turn)))
            packet={'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json(event)}
            p=j.prepare(base+[packet],('bot',),strict=True,compact_policy=True)
            self.assertEqual(p.wire[:len(old)],old)
            for msg in p.wire[len(old):]:
                c=msg.get('content')
                if isinstance(c,str) and c.startswith(EVENT_CONTEXT_CURRENT+'\n'):
                    value=json.loads(c.split('\n',1)[1])
                    if 'eventChanges' in value:restored=apply_changes(restored,value['eventChanges'])
                    else:
                        if 'policy' not in value and restored:value['policy']=restored['policy']
                        restored=value
            self.assertEqual(restored,event,'all permission/removal/speaker changes must survive')
            self.assertEqual(p.authority,packet)
            self.assertTrue(j.commit(p));old=copy.deepcopy(p.wire);full_bytes+=len(packet['content'])
            if turn==1:
                call={'role':'assistant','content':None,'tool_calls':[{'id':'t','type':'function','function':{'name':'read','arguments':'{}'}}]}
                base.extend([call,{'role':'tool','tool_call_id':'t','content':'real result'}])
        used=sum(len(m['content']) for m in old if isinstance(m.get('content'),str) and m['content'].startswith(EVENT_CONTEXT_CURRENT+'\n'))
        self.assertLess(used,full_bytes//4)
        db.close()

    def test_sdk_old_media_is_not_reprocessed_or_used_as_history(self):
        old={'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/gif;base64,OLD_COMMITTED_BYTES'}}]}
        event=user('private:12345678','new')
        packet={'role':'user','content':READONLY_CURRENT+'\n{}'}
        messages=[{'role':'system','content':'fixed'},old,packet,event,{'role':'assistant','content':None,'tool_calls':[]}, {'role':'tool','tool_call_id':'t','content':'result'}]
        before=copy.deepcopy(messages)
        selected=shared_context.current_view(messages,'private:12345678','new')
        self.assertNotIn(old,selected);self.assertEqual(selected,[messages[0],packet,*messages[3:]])
        self.assertEqual(messages,before)


class Catalogs(unittest.IsolatedAsyncioTestCase):
    async def test_agreement_catalog_stable_but_nonowner_call_rejected(self):
        cfg={'config':{'behavior':{'ownerUin':'12345678'}}};db=sqlite3.connect(':memory:')
        owner=Work('bot','private:12345678','12345678','99999999','a','message',1,{})
        member=Work('bot','group:55555','22345678','99999999','b','message',1,{})
        event=lambda work:NS(get_extra=lambda k:work if k=='_hosted_pipeline' else None)
        a=agreement_state.tools(cfg,event(owner),db,owner.scope,lambda:True)
        member_event=event(member)
        b=agreement_state.tools(cfg,member_event,db,member.scope,lambda:True)
        self.assertEqual([t.name for t in a],[t.name for t in b]);self.assertEqual(a[0].parameters,b[0].parameters)
        reply=json.loads(await b[0].call(NS(context=NS(event=member_event)),action='propose',topic='bad'))
        self.assertEqual(reply['error'],'owner_agreement_required')
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='memory_agreements'").fetchone()[0],0)
        db.close()

    async def test_bilibili_catalog_stable_and_live_owner_recheck_before_network(self):
        bridge={'settingsRevision':1,'operations':[{'operation':'like','write':True,'scope':'like.write','args':{'type':'object','properties':{}}}]}
        cfg={'config':{'behavior':{'ownerUin':'12345678'}}}
        event=lambda who:NS(get_sender_id=lambda:who,get_extra=lambda k:None)
        with patch('bilibili_tools.manifest',return_value=bridge):
            a=bilibili_tools.tools(cfg,event('12345678'),lambda:True,[])
            b=bilibili_tools.tools(cfg,event('22345678'),lambda:True,[])
            self.assertEqual([t.name for t in a],[t.name for t in b]);self.assertEqual(a[0].parameters,b[0].parameters)
            with patch('bilibili_tools.aiohttp.ClientSession',side_effect=AssertionError('no network before authorization')):
                denied=json.loads(await b[0].call(None))
            self.assertEqual(denied['receipt']['status'],'rejected');self.assertEqual(denied['error'],'owner_required')
        with patch('bilibili_tools.manifest',return_value={**bridge,'operations':[]}):
            reply=json.loads(await a[0].call(None));self.assertEqual(reply['error'],'capability_unavailable')

    async def test_remote_animation_has_motion_notice_and_original_unchanged(self):
        from PIL import Image
        pictures=[Image.new('RGB',(80,40),c) for c in ['red','blue']];out=io.BytesIO()
        pictures[0].save(out,format='GIF',save_all=True,append_images=pictures[1:]);raw=out.getvalue()
        for p in pictures:p.close()
        async def download(url,path,**kw):path.write_bytes(raw)
        request=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://example.test/animation'}}]}];before=copy.deepcopy(request)
        with patch('media_egress.download_file',download):result=await model_images.constrain_messages(request)
        self.assertIn('分帧',result[0]['content'][0]['text']);self.assertTrue(result[0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;'))
        self.assertEqual(request,before)


if __name__=='__main__':unittest.main()
