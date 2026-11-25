"""Qzone attachment preparation shared by every OneBot protocol backend.

Only explicit live-history sources enter this path. Original bytes are staged for
one dispatch and removed by its caller; no model URLs, media pool or memory.
"""
import asyncio
from pathlib import Path
from sticker_collection import download,decoded_image

MAX_IMAGE_BYTES=8*1048576
MAX_TOTAL_BYTES=24*1048576


def validate_original(raw):
    with decoded_image(raw,max_bytes=MAX_IMAGE_BYTES,max_pixels=16777216) as source:source.load()


async def originals(event,db,settings,sources,current):
    if not isinstance(sources,list) or not 1<=len(sources)<=9 or any(not isinstance(s,str) for s in sources) or len(set(sources))!=len(sources):raise ValueError('图片来源应为1至9个不重复的历史图片句柄。')
    if not settings or not Path(settings.get('root','')).is_absolute():raise ValueError('缺少当前机器人图片绑定。')
    from history_sources import original_url
    total=0;decode_slot=asyncio.Semaphore(1);source_slot=asyncio.Semaphore(2)
    async def read(source):
        nonlocal total
        if not current():raise ValueError('机器人配置或执行身份已变化。')
        async with source_slot:
            url=await original_url(settings,event,db,source,current)
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
    pending=[asyncio.create_task(read(source)) for source in sources]
    try:
        result=await asyncio.wait_for(asyncio.gather(*pending),12)
        return result
    finally:
        for task in pending:
            if not task.done():task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
