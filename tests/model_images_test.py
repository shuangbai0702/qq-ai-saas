"""Actual encoded dimensions, animation, safety and canceled codec ownership."""
import asyncio,base64,copy,io,json,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'adapters/astrbot'))
import model_images as images


def encoded(size=(1920,1080),mode='RGB',format='PNG',**kwargs):
    picture=Image.new(mode,size,'red');output=io.BytesIO();picture.save(output,format=format,**kwargs);picture.close()
    return 'data:image/'+('jpeg' if format=='JPEG' else format.lower())+';base64,'+base64.b64encode(output.getvalue()).decode()


def decode(value):return Image.open(io.BytesIO(base64.b64decode(value.split(',',1)[1])))


class Sizes(unittest.TestCase):
    def test_landscape_portrait_panorama_and_exact_limit(self):
        for size in [(1920,1080),(1080,1920),(3000,100),(1025,500),(1024,500),(513,250),(512,250),(511,250),(257,120),(256,120),(255,120),(32,20)]:
            raw=encoded(size);limited=images.limit_data_url(raw)
            with decode(limited) as picture:
                self.assertEqual(max(picture.size),images.target_edge(max(size)))
                factor=min(1,images.target_edge(max(size))/max(size));self.assertLessEqual(abs(picture.width-size[0]*factor),1);self.assertLessEqual(abs(picture.height-size[1]*factor),1)
                if max(size)<=256 or max(size)==512:self.assertEqual(picture.size,size);self.assertEqual(limited,raw)

    def test_transparency_preserved(self):
        with decode(images.limit_data_url(encoded(mode='RGBA'))) as picture:self.assertIn('A',picture.getbands())

    def test_exif_rotation_does_not_bypass_size(self):
        exif=Image.Exif();exif[274]=6
        with decode(images.limit_data_url(encoded((1600,800),format='JPEG',exif=exif))) as picture:self.assertEqual(picture.size,(256,512))

    def test_animation_motion_is_sampled_even_below_edge_limit(self):
        frames=[Image.new('RGB',(640,320),color) for color in ['red','green','blue']];out=io.BytesIO();frames[0].save(out,format='GIF',save_all=True,append_images=frames[1:],duration=[100,200,300],loop=2)
        for frame in frames:frame.close()
        raw='data:image/gif;base64,'+base64.b64encode(out.getvalue()).decode()
        original=out.getvalue()
        details={}
        with decode(images.limit_data_url(raw,details)) as picture:
            self.assertEqual(picture.size,(328,164));self.assertEqual(getattr(picture,'n_frames',1),1)
            # All three chronological colors survive in the first montage row.
            for x,color in [(54,'red'),(164,'green'),(273,'blue')]:
                rgb=picture.getpixel((x,32));self.assertGreater(rgb[['red','green','blue'].index(color)],100)
        self.assertTrue(details['animated']);self.assertEqual(out.getvalue(),original)
        small=[Image.new('RGB',(60,30),c) for c in ['red','blue']];buf=io.BytesIO()
        small[0].save(buf,format='GIF',save_all=True,append_images=small[1:])
        for frame in small:frame.close()
        with decode(images.limit_data_url('data:image/gif;base64,'+base64.b64encode(buf.getvalue()).decode())) as picture:
            self.assertEqual(picture.size,(60,30));self.assertEqual(picture.format,'JPEG')

    def test_real_gif_source_boundaries_cap_final_montage_not_each_cell(self):
        for edge,expected in [(1025,512),(1024,328),(512,512),(511,256),(256,256)]:
            frames=[Image.new('RGB',(edge,128),color) for color in ('red','green','blue')]
            output=io.BytesIO();frames[0].save(output,format='GIF',save_all=True,append_images=frames[1:],duration=100,loop=0)
            for frame in frames:frame.close()
            original=output.getvalue();details={}
            with decode(images.limit_data_url('data:image/gif;base64,'+base64.b64encode(original).decode(),details)) as montage:
                self.assertTrue(details['animated']);self.assertEqual(getattr(montage,'n_frames',1),1)
                if edge>256:self.assertEqual(max(montage.size),expected)
                else:self.assertLessEqual(max(montage.size),expected)
                # A 3x3 sheet retains the input aspect ratio; one output pixel
                # of rounding is allowed, rather than scaling each tile to cap.
                self.assertLessEqual(abs(montage.height-montage.width*128/edge),1)
            self.assertEqual(output.getvalue(),original)

    def test_invalid_and_over_budget_inputs_are_not_forwarded(self):
        for value in ['data:image/png;base64,AAAA','file:///secret','data:image/svg+xml;base64,AAAA']:
            with self.assertRaises(Exception):images.limit_data_url(value)
        with patch.dict(images.POLICY,maxSourcePixels=100):
            with self.assertRaises(ValueError):images.limit_data_url(encoded((20,20)))

    def test_shared_annotation_preview_is_also_at_most_384(self):
        import favorite_media
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'input.gif';output=Path(folder)/'preview.jpg'
            frames=[Image.new('RGB',(256,128),c) for c in ['red','green','blue']];frames[0].save(file,save_all=True,append_images=frames[1:],format='GIF')
            for frame in frames:frame.close()
            favorite_media.preview(file,output)
            with Image.open(output) as picture:self.assertLessEqual(max(picture.size),384)


class Requests(unittest.IsolatedAsyncioTestCase):
    async def test_same_shape_retained_images_all_stay_exact(self):
        def message(value):return {'role':'user','content':[{'type':'text','text':'same caption'},{'type':'image_url','image_url':{'url':value}}]}
        sources=[encoded((1600,900)),encoded((1600,800))]
        retained=[message(images.limit_data_url(value,legacy=True)) for value in sources]
        before=copy.deepcopy(retained)
        for history in [retained,[message(value) for value in sources]]:
            new=[message(encoded((edge,200))) for edge in (1025,1024,512,511,256)]
            result=await images.constrain_messages(history+new,{},retained)
            self.assertEqual(result[:len(retained)],before)
            for item,edge in zip(result[len(retained):],(512,328,512,256,256)):
                with decode(item['content'][-1]['image_url']['url']) as picture:self.assertEqual(max(picture.size),edge)
        self.assertEqual(retained,before)

    async def test_nonshared_history_keeps_exact_media_and_appends_new_input(self):
        from readonly_journal import ReadonlyJournal
        from context_protocol import event_message,READONLY_CURRENT,model_json
        from context_transport import EVENT_CONTEXT_CURRENT
        old_source=encoded((1600,900));old_copy=images.limit_data_url(old_source,legacy=True)
        def event(identifier,value):return event_message({'scope':'private:12345678','event_id':identifier,'sender_id':'12345678','at':123},[{'type':'text','text':'real input'},{'type':'image_url','image_url':{'url':value}}])
        def request(messages):return [{'role':'system','content':'fixed'},{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})},{'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{},'policy':'fixed','currentGuidance':'','meaning':'platform'})}]+messages
        journal=ReadonlyJournal();old=event('old',old_copy)
        initial=journal.prepare(request([old]),'fixed',strict=True);journal.commit(initial);before=copy.deepcopy(journal.last.wire)
        for old_input in [old, event('old',old_source)]:
            current=event('new',encoded((1024,600)))
            prepared_view=await images.constrain_messages(request([old_input,current]),{},journal.last.base)
            result=journal.prepare(prepared_view,'fixed',strict=True)
            self.assertEqual(result.wire[:len(before)],before)
            with decode(result.wire[-1]['content'][-1]['image_url']['url']) as picture:self.assertEqual(max(picture.size),328)
            self.assertEqual(journal.last.wire,before)

    async def test_submitted_legacy_media_stays_exact_before_checkpoint(self):
        import sqlite3
        import shared_context as shared
        from readonly_journal import ReadonlyJournal
        from context_protocol import event_message,READONLY_CURRENT,model_json
        from context_transport import EVENT_CONTEXT_CURRENT
        def request(message):
            return [{'role':'system','content':'fixed'},
                {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})},
                {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{},'policy':'fixed','currentGuidance':'','meaning':'platform'})},message]
        def event(identifier,value):
            return event_message({'scope':'private:12345678','event_id':identifier,'sender_id':'12345678','at':123},[{'type':'text','text':'real input'},{'type':'image_url','image_url':{'url':value}}])
        from contextlib import closing
        with tempfile.TemporaryDirectory() as folder,closing(sqlite3.connect(Path(folder)/'journal.sqlite')) as db:
            journal=ReadonlyJournal(db,shared.JOURNAL_SCOPE)
            old=event('old',encoded((384,200)))
            initial=journal.prepare(request(old),'fixed',strict=True);journal.commit(initial)
            old_wire=copy.deepcopy(journal.last.wire)
            journal=ReadonlyJournal(db,shared.JOURNAL_SCOPE)
            current=event('new',encoded((1024,600)));memo={}
            new_view=shared.current_view(request(old)+[current],'private:12345678','new')
            with patch('model_images._limited',wraps=images._limited) as codec:
                constrained=await images.constrain_messages(new_view,memo)
                composed,_=shared.compose(journal,constrained,'private:12345678','new',[constrained[-1]])
                prepared=journal.prepare(composed,'fixed',strict=True)
            self.assertEqual(codec.call_count,1)
            self.assertEqual(prepared.wire[:len(old_wire)],old_wire)
            self.assertEqual(journal.last.wire,old_wire)

    async def test_source_tier_is_applied_once_across_request_projections(self):
        raw=[{'role':'user','content':[{'type':'image_url','image_url':{'url':encoded((1024,600))}}]}]
        memo={};first=await images.constrain_messages(raw,memo)
        second=await images.constrain_messages(first,memo)
        self.assertEqual(first,second)
        with decode(second[0]['content'][0]['image_url']['url']) as picture:self.assertEqual(max(picture.size),328)

    async def test_sdk_current_image_uses_original_tiers_before_provider(self):
        from astrbot.core.platform.astr_message_event import current_message_event
        from astrbot.core.utils import image_input
        from types import SimpleNamespace as NS
        extras={'_hosted_lease':object()}
        event=NS(get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.__setitem__(k,v))
        images.install_sdk_preparation();token=current_message_event.set(event)
        try:
            with tempfile.TemporaryDirectory() as folder:
                for size,target in [((1025,600),512),((1024,600),328),((512,300),512),((511,300),256),((256,100),256)]:
                    file=Path(folder)/'original.png';file.write_bytes(base64.b64decode(encoded(size).split(',',1)[1]))
                    result=await image_input.prepare_model_image(str(file),max_size=1280,output_dir=Path(folder))
                    with Image.open(result[0]) as picture:
                        self.assertEqual(max(picture.size),target);mime=Image.MIME[picture.format]
                    value='data:'+mime+';base64,'+base64.b64encode(Path(result[0]).read_bytes()).decode()
                    after=await images.constrain_messages([{'role':'user','content':[{'type':'image_url','image_url':{'url':value}}]}],images.request_memo(event))
                    with decode(after[0]['content'][0]['image_url']['url']) as picture:self.assertEqual(max(picture.size),target)
        finally:current_message_event.reset(token)

    async def test_current_history_and_tool_images_all_share_boundary(self):
        value=encoded();raw=[{'role':r,'content':[{'type':'text','text':'source'},{'type':'image_url','image_url':{'url':value,'detail':'low'}}]} for r in ['user','assistant','tool']];before=copy.deepcopy(raw)
        memo={};result=await images.constrain_messages(raw,memo);self.assertEqual(raw,before)
        for message in result:
            self.assertEqual(message['content'][0]['text'],'source')
            with decode(message['content'][1]['image_url']['url']) as picture:self.assertEqual(max(picture.size),512)
        self.assertEqual(await images.constrain_messages(result,memo),result)

    async def test_failed_images_are_explicit_not_silently_sent(self):
        result=await images.constrain_messages([{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}},{'type':'text','text':'看看这个'}]}])
        self.assertIn('图片未传入',result[0]['content'][0]['text']);self.assertEqual(result[0]['content'][1]['text'],'看看这个')

    async def test_remote_uses_existing_safe_downloader_and_cleans_temp(self):
        value=encoded();paths=[]
        async def download(url,path,**kwargs):paths.append(path);path.write_bytes(base64.b64decode(value.split(',',1)[1]))
        with patch('media_egress.download_file',download):result=await images.constrain_messages([{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://public.example/a'}}]}])
        self.assertTrue(paths);self.assertTrue(all(not p.exists() for p in paths))
        with decode(result[0]['content'][0]['image_url']['url']) as picture:self.assertEqual(max(picture.size),512)

    async def test_cancel_keeps_codec_slot_until_thread_exits(self):
        entered=threading.Event();release=threading.Event();second=threading.Event()
        def block(value):
            if value=='one':entered.set();release.wait(5)
            else:second.set()
            return value
        with patch('model_images.limit_data_url',block):
            first=asyncio.create_task(images._limited('one'));await asyncio.to_thread(entered.wait,2);self.assertTrue(entered.is_set())
            first.cancel();other=asyncio.create_task(images._limited('two'));await asyncio.sleep(.02);self.assertFalse(second.is_set());self.assertFalse(first.done())
            first.cancel();release.set()
            with self.assertRaises(asyncio.CancelledError):await first
            self.assertEqual(await other,'two');self.assertTrue(second.is_set())


if __name__=='__main__':unittest.main()
