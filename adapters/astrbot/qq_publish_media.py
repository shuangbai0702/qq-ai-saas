"""Qzone attachment preparation shared by every OneBot protocol backend.

Only real event/quote sources enter this path. Original bytes are staged for
one dispatch and removed by its caller; no model URLs, media pool or memory.
"""
import asyncio
import re
from pathlib import Path
from sticker_collection import download,decoded_image

MAX_IMAGE_BYTES=8*1048576
MAX_TOTAL_BYTES=24*1048576


def validate_original(raw):
    with decoded_image(raw,max_bytes=MAX_IMAGE_BYTES,max_pixels=16777216) as source:source.load()


async def originals(event,db,settings,sources,current):
    if not isinstance(sources,list) or len(sources)>9 or any(not isinstance(s,str) for s in sources) or len(set(sources))!=len(sources):raise ValueError('图片来源应为最多9个不重复的可信句柄。')
    if not sources:return []
    if not settings or not Path(settings.get('root','')).is_absolute():raise ValueError('缺少当前机器人图片绑定。')
    from astrbot.core.message.components import Image,Reply
    import quote_reply
    lease=event.get_extra('_hosted_lease') or settings
    urls=[]
    for source in sources:
        if not isinstance(source,str):raise ValueError('图片来源无效。')
        if source=='current':
            components=list(event.get_messages())
            images=[c for c in components if isinstance(c,Image)]
            if not images:
                images=[image for c in components if isinstance(c,Reply) for image in (c.chain or []) if isinstance(image,Image)]
            if images:
                urls.extend(image.url or image.file for image in images)
            else:
                # Reuse the existing scoped source owner; don't replay history
                # or invent another collection of recent attachments.
                rows=[r for r in quote_reply.media_catalog(db,event,lease) if r['sender_id']==str(event.get_sender_id())]
                latest=[r for r in rows if rows and r['message_id']==rows[0]['message_id']]
                if len(latest)!=1:raise ValueError('当前没有唯一附图，请用已有图片来源句柄明确选择。')
                value=await quote_reply.resolve_media(db,event,lease,latest[0]['handle'],current)
                urls.append(value.get('url') or value.get('file'))
        elif re.fullmatch(r'qm_[a-f0-9]{20}',source):
            value=await quote_reply.resolve_media(db,event,lease,source,current)
            urls.append(value.get('url') or value.get('file'))
        else:raise ValueError('仅支持current或已有可信图片句柄，不能传URL或路径。')
        if not current():raise ValueError('机器人配置或执行身份已变化。')
    if not urls or len(urls)>9:raise ValueError('每条说说需明确选择1至9张图片。')
    total=0;decode_slot=asyncio.Semaphore(1)
    async def read(url):
        nonlocal total
        if not current():raise ValueError('机器人配置或执行身份已变化。')
        raw=await download(url,max_bytes=MAX_IMAGE_BYTES)
        if not current():raise ValueError('机器人配置或执行身份已变化。')
        total+=len(raw)
        if total>MAX_TOTAL_BYTES:raise ValueError('图片总大小超过24MiB。')
        # Decode/validate off the event loop, but publish the original bytes.
        async with decode_slot:
            decode=asyncio.create_task(asyncio.to_thread(validate_original,raw))
            try:await asyncio.shield(decode)
            except asyncio.CancelledError:
                # Decoder threads cannot be canceled. Drain this bounded read
                # before releasing its slot or finishing preparation cleanup.
                await asyncio.gather(decode,return_exceptions=True)
                raise
        if not current():raise ValueError('机器人配置或执行身份已变化。')
        return raw
    # Existing downloader owns the two-slot network bound. Cancel and drain all
    # preparation children on failure; none may outlive the owning tool call.
    pending=[asyncio.create_task(read(url)) for url in urls]
    try:
        result=await asyncio.wait_for(asyncio.gather(*pending),12)
        return result
    finally:
        for task in pending:
            if not task.done():task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
