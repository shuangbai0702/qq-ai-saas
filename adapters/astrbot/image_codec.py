"""Bounded image downscaling and single-slot codec semaphore."""
import asyncio,base64,io,json,re,warnings
from pathlib import Path

POLICY=json.loads((Path(__file__).resolve().parents[2]/'templates/model-media.json').read_text(encoding='utf-8'))
MAX_EDGE=POLICY['maxEdge']
if type(MAX_EDGE) is not int or not 1<=MAX_EDGE<=384:raise ValueError('invalid_model_image_limit')
_loop=None
_semaphore=None


def target_edge(longest):
    """Choose once from original dimensions; never upscale small sources."""
    if longest>1024:return 512
    if longest>=512:return 256
    if longest>256:return 192
    if longest>=128:return 128
    return longest


def limit_data_url(value,details=None,legacy=False):
    """Bound a new model image; preserve still pixels and sample animation motion."""
    from PIL import Image,ImageOps
    if not isinstance(value,str) or len(value)>POLICY['maxSourceBytes']*4//3+256:raise ValueError('model_image_size')
    match=re.fullmatch(r'data:image/(png|jpe?g|gif|webp|bmp|tiff|avif);base64,([A-Za-z0-9+/=\r\n]+)',value,re.I)
    if not match:raise ValueError('model_image_format')
    raw=base64.b64decode(re.sub(r'\s','',match[2]),validate=True)
    if len(raw)>POLICY['maxSourceBytes']:raise ValueError('model_image_size')
    if details is not None:details['animated']=False
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            count=getattr(image,'n_frames',1);pixels=image.width*image.height
            edge=MAX_EDGE if legacy else target_edge(max(image.size))
            if pixels>POLICY['maxSourcePixels'] or count>POLICY['maxAnimationFrames'] or pixels*count>POLICY['maxAnimationPixels']:raise ValueError('model_image_pixels')
            if count>1:
                from astrbot.core.utils.media_utils import _extract_animation_montage_sync
                with ImageOps.exif_transpose(image) as oriented:display_size=oriented.size
                data=_extract_animation_montage_sync(image,edge)
                if not legacy:
                    scale=edge/max(display_size)
                    size=tuple(max(1,round(dimension*scale)) for dimension in display_size)
                    with Image.open(io.BytesIO(data)) as montage:
                        if montage.size!=size:
                            with montage.resize(size,Image.Resampling.LANCZOS) as adjusted:
                                output=io.BytesIO();adjusted.save(output,format='JPEG',quality=80);data=output.getvalue()
                if details is not None:details['animated']=True
                return 'data:image/jpeg;base64,'+base64.b64encode(data).decode('ascii')
            if max(image.size)<=edge and match[1].lower() in ('png','jpg','jpeg','gif','webp'):return value
            output=io.BytesIO()
            thumb=ImageOps.exif_transpose(image);thumb.thumbnail((edge,edge),Image.Resampling.LANCZOS)
            if 'A' in thumb.getbands() or not legacy and 'transparency' in thumb.info:thumb.save(output,format='PNG');mime='png'
            else:
                rgb=thumb.convert('RGB');rgb.save(output,format='JPEG',quality=80);rgb.close();mime='jpeg'
            thumb.close()
            data=output.getvalue()
            if len(data)>POLICY['maxSourceBytes']:raise ValueError('model_image_output_size')
            return 'data:image/'+mime+';base64,'+base64.b64encode(data).decode('ascii')


async def _limited(value,details=None,legacy=False):
    args=(value,details,True) if legacy else (value,) if details is None else (value,details)
    return await _run_codec(limit_data_url,*args)


async def _run_codec(function,*args):
    """Share the existing single codec slot, including cancelled waiters."""
    global _loop,_semaphore
    loop=asyncio.get_running_loop()
    if _loop is not loop:_loop=loop;_semaphore=asyncio.Semaphore(1)
    async with _semaphore:
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:await asyncio.shield(task)
                except asyncio.CancelledError:continue
                except Exception:break
            if not task.cancelled():task.exception()
            raise
