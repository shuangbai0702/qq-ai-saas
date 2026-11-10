"""Isolated public-image acquisition and avatar permission/late-result tests."""
import asyncio,base64,io,json,os,sqlite3,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
import bilibili_images as images,qq_actions


async def main():
    from PIL import Image
    raw=io.BytesIO();Image.new('RGB',(80,40),'blue').save(raw,format='PNG');png=raw.getvalue()
    with sqlite3.connect(':memory:') as db,sqlite3.connect(':memory:') as other:
        revision='1:2';settings={'generation':1,'revision':2,'config':{'behavior':{'qqProfile':True,'ownerUin':'12345678'}}}
        result={'execution':'completed','actionId':'read-1','data':{'videos':[{'title':'public cover','cover':'https://i0.hdslb.com/bfs/archive/abc.jpg'},{'cover':'https://i0.hdslb.com.evil.test/a.png'},{'pic':'http://i0.hdslb.com/bfs/archive/abc.jpg'}]}}
        found=images.record(db,result,'search','work-1',revision);assert len(found)==1;handle=found[0]['handle']
        assert images.image_url('http://i0.hdslb.com/a.png')=='https://i0.hdslb.com/a.png'
        assert images.record(db,result,'feed','work-1',revision)==[],'personalized/private sources became public assets'
        assert images.record(db,{**result,'execution':'failed'},'search','work-1',revision)==[]
        for url in ['https://localhost/a','https://i0.hdslb.com@127.0.0.1/a','file:///tmp/a','https://i0.hdslb.com:80/a','https://i0.hdslb.com/a#x','https://i0.hdslb.com:bad/a']:
            assert images.image_url(url) is None,url
        current=True;calls=[];extras={'_hosted_internal_work':{'id':'work-1','kind':'research','principal':'autonomous-service'},'_hosted_autonomous_action':{'kind':'research'}}
        async def login():return {'user_id':'90000000'}
        async def avatar(**kwargs):calls.append(kwargs);return {}
        event=NS(get_group_id=lambda:'',get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.__setitem__(k,v),get_sender_id=lambda:'0',get_self_id=lambda:'90000000',get_messages=lambda:[],message_obj=NS(message_id='stable-work-1'),bot=NS(get_login_info=login,set_qq_avatar=avatar))
        context=NS(context=NS(event=event));bridge={'settingsRevision':1,'allowAutonomous':True,'operations':[{'operation':'search'}]}
        with patch('bilibili_tools.manifest',side_effect=lambda _:json.loads(json.dumps(bridge))):
            import bilibili_tools
            future={**bridge,'operations':[{'operation':'future_feature','scope':'public.read','args':{'type':'object','properties':{}}}]}
            with patch('bilibili_tools.manifest',return_value=future):
                registered=bilibili_tools.tools(settings,event,lambda:True,[],extras['_hosted_internal_work'])
                assert [t.name for t in registered]==['bilibili_future_feature'],'authorized registry operation silently omitted because a prose description was missing'
            tool=images.tools(settings,event,db,lambda:current,extras['_hosted_internal_work'])[0]
            async def download(url):return png
            with patch.object(images,'fetch',download):
                prepared=await tool.call(context,operation='prepare',handle=handle)
            data=json.loads(prepared.content[0].text)
            assert data['status']=='prepared' and prepared.content[1].type=='image'
            assert base64.b64decode(prepared.content[1].data)==png,'small model preview changed source bytes'
            with Image.open(io.BytesIO(db.execute('SELECT png FROM bili_image_assets WHERE handle=?',(handle,)).fetchone()[0])) as avatar_asset:
                assert avatar_asset.size==(80,40)
            avatar_before=db.execute('SELECT png FROM bili_image_assets WHERE handle=?',(handle,)).fetchone()[0]
            for edge,expected in [(1025,512),(1024,328),(512,512),(511,256),(256,256)]:
                original=io.BytesIO();Image.new('RGB',(edge,200),'green').save(original,format='PNG')
                async def tier_download(url):return original.getvalue()
                with patch.object(images,'fetch',tier_download):preview=await tool.call(context,operation='prepare',handle=handle)
                with Image.open(io.BytesIO(base64.b64decode(preview.content[1].data))) as model_copy:assert max(model_copy.size)==expected
                assert db.execute('SELECT png FROM bili_image_assets WHERE handle=?',(handle,)).fetchone()[0]==avatar_before,'model preview modified QQ avatar asset'
            qqtool=next(t for t in qq_actions.tools(event,settings['config'],db,lambda:current,settings) if t.name=='hosted_qq_avatar')
            assert '已确认该操作' in await qqtool.call(context,image_handle=handle)
            assert len(calls)==1 and calls[0]['file'].startswith('base64://')
            assert '不会重复执行' in await qqtool.call(context,image_handle=handle) and len(calls)==1
            foreign=images.tools(settings,event,other,lambda:True,extras['_hosted_internal_work'])[0]
            assert json.loads(await foreign.call(context,operation='prepare',handle=handle))['code']=='image_handle_not_in_work'
            try:images.avatar_file(db,handle,'1:3');assert False
            except ValueError:pass
            settings['config']['behavior']['qqProfile']=False
            assert qq_actions.tools(event,settings['config'],db,lambda:True,settings)==[]
            settings['config']['behavior']['qqProfile']=True
            db.execute('UPDATE bili_image_assets SET png=NULL');db.commit()
            started=asyncio.Event();release=asyncio.Event()
            async def late_download(url):started.set();await release.wait();return png
            with patch.object(images,'fetch',late_download):
                task=asyncio.create_task(tool.call(context,operation='prepare',handle=handle));await started.wait();current=False;release.set()
                try:await task;assert False
                except asyncio.CancelledError:pass
            assert db.execute('SELECT png FROM bili_image_assets').fetchone()[0] is None,'stale download committed an asset'
            current=True
            with patch.object(images,'fetch',download):
                bridge['settingsRevision']=2
                assert json.loads(await tool.call(context,operation='prepare',handle=handle))['code']=='configuration_changed'
                bridge['settingsRevision']=1
            try:await tool.call(NS(context=NS(event=NS())),operation='list');assert False
            except asyncio.CancelledError:pass
        # Bounded disk rows and decoded cache; expiration cannot be refreshed
        # by merely listing/reusing a prepared asset.
        for n in range(45):
            images.record(db,{'execution':'completed','data':{'pic':f'https://i0.hdslb.com/{n}.png'}},'video_info',f'w-{n}',revision)
        assert db.execute('SELECT count(*) FROM bili_image_assets').fetchone()[0]<=32
        db.execute('UPDATE bili_image_assets SET png=?',(png,));images.prune(db,time.time());db.commit()
        assert db.execute('SELECT count(*) FROM bili_image_assets WHERE png IS NOT NULL').fetchone()[0]<=8
        db.execute('UPDATE bili_image_assets SET created=?',(time.time()-3601,));images.prune(db,time.time());assert db.execute('SELECT count(*) FROM bili_image_assets').fetchone()[0]==0
    print('PASS image provenance, bounded media, real-tool image result, avatar binding/idempotency, tenant/revision/permission and late-download fences')


if __name__=='__main__':asyncio.run(main())
