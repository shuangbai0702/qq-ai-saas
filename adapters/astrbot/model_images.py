"""Bounded model-only image copies. QQ media and persistent originals stay intact."""
import asyncio,base64,io,json,re,tempfile,warnings
from pathlib import Path

POLICY=json.loads((Path(__file__).resolve().parents[2]/'templates/model-media.json').read_text(encoding='utf-8'))
MAX_EDGE=POLICY['maxEdge']
if type(MAX_EDGE) is not int or not 1<=MAX_EDGE<=384:raise ValueError('invalid_model_image_limit')
_loop=None
_semaphore=None


def limit_data_url(value,details=None):
    """Bound a new model image; preserve still pixels and sample animation motion."""
    from PIL import Image,ImageOps
    if not isinstance(value,str) or len(value)>POLICY['maxSourceBytes']*4//3+256:raise ValueError('model_image_size')
    match=re.fullmatch(r'data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)',value,re.I)
    if not match:raise ValueError('model_image_format')
    raw=base64.b64decode(re.sub(r'\s','',match[2]),validate=True)
    if len(raw)>POLICY['maxSourceBytes']:raise ValueError('model_image_size')
    if details is not None:details['animated']=False
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            count=getattr(image,'n_frames',1);pixels=image.width*image.height
            if pixels>POLICY['maxSourcePixels'] or count>POLICY['maxAnimationFrames'] or pixels*count>POLICY['maxAnimationPixels']:raise ValueError('model_image_pixels')
            if count>1:
                # Reuse the pinned SDK's visual contract: a bounded 3x3
                # chronological montage, not hundreds of encoded frames.
                # QQ originals remain animated; only new model copies change.
                from astrbot.core.utils.media_utils import _extract_animation_montage_sync
                data=_extract_animation_montage_sync(image,MAX_EDGE)
                if details is not None:details['animated']=True
                return 'data:image/jpeg;base64,'+base64.b64encode(data).decode('ascii')
            if max(image.size)<=MAX_EDGE:return value
            output=io.BytesIO()
            thumb=ImageOps.exif_transpose(image);thumb.thumbnail((MAX_EDGE,MAX_EDGE),Image.Resampling.LANCZOS)
            if 'A' in thumb.getbands():thumb.save(output,format='PNG');mime='png'
            else:
                rgb=thumb.convert('RGB');rgb.save(output,format='JPEG',quality=80);rgb.close();mime='jpeg'
            thumb.close()
            data=output.getvalue()
            if len(data)>POLICY['maxSourceBytes']:raise ValueError('model_image_output_size')
            return 'data:image/'+mime+';base64,'+base64.b64encode(data).decode('ascii')


async def _limited(value,details=None):
    global _loop,_semaphore
    loop=asyncio.get_running_loop()
    if _loop is not loop:_loop=loop;_semaphore=asyncio.Semaphore(1)
    async with _semaphore:
        task=asyncio.create_task(asyncio.to_thread(limit_data_url,value) if details is None else asyncio.to_thread(limit_data_url,value,details))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling a waiter cannot stop a codec thread. Keep its one
            # resource slot owned until it exits, even on repeated cancellation.
            while not task.done():
                try:await asyncio.shield(task)
                except asyncio.CancelledError:continue
                except Exception:break
            if not task.cancelled():task.exception()
            raise


async def constrain_messages(messages):
    """Prepare only never-submitted image inputs; Journal wire bypasses this."""
    if not isinstance(messages,list):return messages
    result=[];memo={}
    for original in messages:
        if not isinstance(original,dict) or not isinstance(original.get('content'),list):result.append(original);continue
        message=dict(original);parts=[]
        for part in original['content']:
            if not isinstance(part,dict) or part.get('type')!='image_url':parts.append(part);continue
            location=part.get('image_url');value=location.get('url') if isinstance(location,dict) else None
            try:
                if not isinstance(value,str):raise ValueError('model_image_url')
                if value not in memo:
                    details={}
                    if value.startswith('data:'):limited=await _limited(value,details)
                    else:
                        from media_egress import download_file
                        with tempfile.TemporaryDirectory(prefix='fishbot-model-image-') as folder:
                            path=Path(folder)/'source'
                            await download_file(value,path,max_bytes=POLICY['maxSourceBytes'])
                            raw=path.read_bytes()
                            # Detect MIME from bytes; do not trust remote extensions.
                            from PIL import Image
                            with Image.open(io.BytesIO(raw)) as image:mime=Image.MIME.get(image.format,'')
                            limited=await _limited('data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii'),details)
                    memo[value]=(limited,details.get('animated',False))
                if memo[value][1]:
                    parts.append({'type':'text','text':'[动画已按时间顺序采样为3×3分帧预览；只据可见帧理解动作，不代表全部帧。]'})
                parts.append({**part,'image_url':{**part['image_url'],'url':memo[value][0]}})
            except asyncio.CancelledError:raise
            except Exception:parts.append({'type':'text','text':'[图片未传入：无法在尺寸或安全限制内处理；不要推测图片内容。]'})
        message['content']=parts;result.append(message)
    return result
