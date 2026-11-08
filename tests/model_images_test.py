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
        for size in [(1920,1080),(1080,1920),(3000,100),(384,200),(32,20)]:
            raw=encoded(size);limited=images.limit_data_url(raw)
            with decode(limited) as picture:
                self.assertLessEqual(max(picture.size),384)
                factor=min(1,384/max(size));self.assertLessEqual(abs(picture.width-size[0]*factor),1);self.assertLessEqual(abs(picture.height-size[1]*factor),1)
                if max(size)<=384:self.assertEqual(picture.size,size);self.assertEqual(limited,raw)

    def test_transparency_preserved(self):
        with decode(images.limit_data_url(encoded(mode='RGBA'))) as picture:self.assertIn('A',picture.getbands())

    def test_exif_rotation_does_not_bypass_size(self):
        exif=Image.Exif();exif[274]=6
        with decode(images.limit_data_url(encoded((1600,800),format='JPEG',exif=exif))) as picture:self.assertEqual(picture.size,(192,384))

    def test_animation_motion_is_sampled_even_below_edge_limit(self):
        frames=[Image.new('RGB',(640,320),color) for color in ['red','green','blue']];out=io.BytesIO();frames[0].save(out,format='GIF',save_all=True,append_images=frames[1:],duration=[100,200,300],loop=2)
        for frame in frames:frame.close()
        raw='data:image/gif;base64,'+base64.b64encode(out.getvalue()).decode()
        original=out.getvalue()
        details={}
        with decode(images.limit_data_url(raw,details)) as picture:
            self.assertEqual(picture.size,(384,192));self.assertEqual(getattr(picture,'n_frames',1),1)
            # All three chronological colors survive in the first montage row.
            for x,color in [(64,'red'),(192,'green'),(320,'blue')]:
                rgb=picture.getpixel((x,32));self.assertGreater(rgb[['red','green','blue'].index(color)],100)
        self.assertTrue(details['animated']);self.assertEqual(out.getvalue(),original)
        small=[Image.new('RGB',(60,30),c) for c in ['red','blue']];buf=io.BytesIO()
        small[0].save(buf,format='GIF',save_all=True,append_images=small[1:])
        for frame in small:frame.close()
        with decode(images.limit_data_url('data:image/gif;base64,'+base64.b64encode(buf.getvalue()).decode())) as picture:
            self.assertEqual(picture.size,(180,90));self.assertEqual(picture.format,'JPEG')

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
    async def test_current_history_and_tool_images_all_share_boundary(self):
        value=encoded();raw=[{'role':r,'content':[{'type':'text','text':'source'},{'type':'image_url','image_url':{'url':value,'detail':'low'}}]} for r in ['user','assistant','tool']];before=copy.deepcopy(raw)
        result=await images.constrain_messages(raw);self.assertEqual(raw,before)
        for message in result:
            self.assertEqual(message['content'][0]['text'],'source')
            with decode(message['content'][1]['image_url']['url']) as picture:self.assertEqual(max(picture.size),384)
        self.assertEqual(await images.constrain_messages(result),result)

    async def test_failed_images_are_explicit_not_silently_sent(self):
        result=await images.constrain_messages([{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}},{'type':'text','text':'看看这个'}]}])
        self.assertIn('图片未传入',result[0]['content'][0]['text']);self.assertEqual(result[0]['content'][1]['text'],'看看这个')

    async def test_remote_uses_existing_safe_downloader_and_cleans_temp(self):
        value=encoded();paths=[]
        async def download(url,path,**kwargs):paths.append(path);path.write_bytes(base64.b64decode(value.split(',',1)[1]))
        with patch('media_egress.download_file',download):result=await images.constrain_messages([{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://public.example/a'}}]}])
        self.assertTrue(paths);self.assertTrue(all(not p.exists() for p in paths))
        with decode(result[0]['content'][0]['image_url']['url']) as picture:self.assertEqual(max(picture.size),384)

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
