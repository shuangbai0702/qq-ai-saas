"""Bounded image assets observed in real public Bilibili tool results.

The model selects handles, never URLs/paths. Each bot's existing SQLite store
owns the candidates and normalized assets; no background downloader or timer.
"""
import asyncio,base64,hashlib,json,time
from urllib.parse import urlsplit

PUBLIC_OPERATIONS={'search','popular','ranking','video_info','related','user_profile','user_videos','collections','collection_videos'}
MAX_DOWNLOAD=2097152
_slots=asyncio.Semaphore(1)


def image_url(value):
    if not isinstance(value,str) or len(value)>2048:return None
    if value.startswith('//'):value='https:'+value
    try:
        u=urlsplit(value);host=(u.hostname or '').lower()
        if u.scheme not in ('http','https') or u.username or u.password or u.port not in (None,80 if u.scheme=='http' else 443) or u.fragment:return None
        if not any(host==d or host.endswith('.'+d) for d in ('hdslb.com','biliimg.com')):return None
        if not u.path or u.path=='/':return None
    except ValueError:return None
    return u._replace(scheme='https',netloc=host).geturl()


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS bili_image_assets(handle TEXT PRIMARY KEY,work TEXT,revision TEXT,url TEXT,source TEXT,created REAL,png BLOB)')


def prune(db,now):
    db.execute('DELETE FROM bili_image_assets WHERE created<?',(now-3600,))
    db.execute('DELETE FROM bili_image_assets WHERE handle NOT IN (SELECT handle FROM bili_image_assets ORDER BY created DESC,rowid DESC LIMIT 32)')
    db.execute('UPDATE bili_image_assets SET png=NULL WHERE handle NOT IN (SELECT handle FROM bili_image_assets WHERE png IS NOT NULL ORDER BY created DESC,rowid DESC LIMIT 8)')


def record(db,result,operation,work,revision):
    if operation not in PUBLIC_OPERATIONS or result.get('execution')!='completed':return []
    initialize(db);now=time.time();found=[];seen=set()
    def visit(value,depth=0):
        if depth>6 or len(found)>=16:return
        if isinstance(value,list):
            for item in value[:30]:visit(item,depth+1)
        elif isinstance(value,dict):
            for key,item in list(value.items())[:60]:
                if len(found)>=16:break
                url=image_url(item) if key in ('cover','pic','face') else None
                if url and url not in seen:
                    seen.add(url);handle='bi_'+hashlib.sha256(json.dumps([work,revision,url],ensure_ascii=False).encode()).hexdigest()[:24]
                    source={'operation':operation,'actionId':result.get('actionId'),'title':str(value.get('title') or value.get('name') or '')[:120],'kind':key}
                    db.execute('INSERT OR IGNORE INTO bili_image_assets VALUES(?,?,?,?,?,?,NULL)',(handle,work,revision,url,json.dumps(source,ensure_ascii=False),now))
                    found.append({'handle':handle,**source})
                elif isinstance(item,(dict,list)):visit(item,depth+1)
    visit(result.get('data'));prune(db,now);db.commit();return found


async def fetch(url):
    async with _slots:return await _fetch(url)


async def _fetch(url):
    import aiohttp
    url=image_url(url)
    if not url:raise ValueError('untrusted_image_origin')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),trust_env=False) as client:
        async with client.get(url,allow_redirects=False,headers={'Referer':'https://www.bilibili.com/','User-Agent':'Mozilla/5.0'}) as response:
            if response.status!=200:raise ValueError('image_http_'+str(response.status))
            if (response.content_length or 0)>MAX_DOWNLOAD:raise ValueError('image_too_large')
            raw=bytearray()
            async for chunk in response.content.iter_chunked(32768):
                if len(raw)+len(chunk)>MAX_DOWNLOAD:raise ValueError('image_too_large')
                raw.extend(chunk)
            return bytes(raw)


def avatar_file(db,handle,revision):
    initialize(db)
    row=db.execute('SELECT png FROM bili_image_assets WHERE handle=? AND revision=? AND created>?',(handle,revision,time.time()-3600)).fetchone()
    if not row or not row[0]:raise ValueError('image_not_prepared_or_expired')
    return 'base64://'+base64.b64encode(row[0]).decode('ascii')


def tools(settings,event,db,current,identity=None):
    from bilibili_tools import manifest
    bridge=manifest(settings)
    if not bridge or (identity and not bridge.get('allowAutonomous')):return []
    if not any(s.get('operation') in PUBLIC_OPERATIONS for s in bridge.get('operations',[])):return []
    from astrbot.core.agent.tool import FunctionTool
    initialize(db)
    revision=str(settings.get('generation'))+':'+str(settings.get('revision'))
    if identity:work=identity['id']
    else:
        bound=event.get_extra('_hosted_pipeline')
        if bound is None:return []
        work='foreground:'+hashlib.sha256(json.dumps([bound.bot_id,bound.scope,bound.subject,bound.event_id],ensure_ascii=False).encode()).hexdigest()
    class Images(FunctionTool):
        async def call(self,context,operation,handle=''):
            if context.context.event is not event or not current():raise asyncio.CancelledError('stale_image_work')
            live=manifest(settings)
            if not live or live.get('settingsRevision')!=bridge.get('settingsRevision'):return json.dumps({'status':'rejected','code':'configuration_changed'})
            prune(db,time.time());db.commit()
            if operation=='list':
                rows=db.execute('SELECT handle,source,png IS NOT NULL FROM bili_image_assets WHERE work=? AND revision=? ORDER BY created DESC LIMIT 16',(work,revision)).fetchall()
                return json.dumps({'images':[{'handle':r[0],'source':json.loads(r[1]),'prepared':bool(r[2])} for r in rows],'meaning':'真实公开工具返回的图片候选；没有候选时先调用视频搜索、详情或UP资料工具，不接受任意URL。'},ensure_ascii=False)
            if operation!='prepare':return json.dumps({'status':'rejected','code':'invalid_operation'})
            row=db.execute('SELECT url,png FROM bili_image_assets WHERE handle=? AND work=? AND revision=?',(handle,work,revision)).fetchone()
            if not row:return json.dumps({'status':'rejected','code':'image_handle_not_in_work'})
            try:
                # Avatar PNG keeps its existing contract. The ephemeral model
                # preview must use downloaded source dimensions, even on reuse.
                raw=await fetch(row[0])
                if not current():raise asyncio.CancelledError('stale_image_download')
                from model_images import constrain_messages,request_memo
                from PIL import Image
                import io
                with Image.open(io.BytesIO(raw)) as source:mime=Image.MIME.get(source.format,'')
                preview=await constrain_messages([{'role':'tool','content':[{'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')}}]}],request_memo(event))
                visual=next((p for p in preview[0]['content'] if p.get('type')=='image_url'),None)
                if visual is None:raise ValueError('model_image_unavailable')
                model_mime,model_data=visual['image_url']['url'].split(';base64,',1)
                if not current():raise asyncio.CancelledError('stale_image_decode')
                live=manifest(settings)
                if not live or live.get('settingsRevision')!=bridge.get('settingsRevision'):raise asyncio.CancelledError('image_configuration_changed')
                png=row[1]
                if not row[1]:
                    from sticker_collection import png_thumbnail
                    png=png_thumbnail(raw,max_bytes=MAX_DOWNLOAD,max_pixels=4194304)
                    if not current():raise asyncio.CancelledError('stale_image_decode')
                    db.execute('UPDATE bili_image_assets SET png=? WHERE handle=? AND work=? AND revision=?',(png,handle,work,revision));prune(db,time.time());db.commit()
                from mcp.types import CallToolResult,TextContent,ImageContent
                notices=''.join(p.get('text','') for p in preview[0]['content'] if p.get('type')=='text')
                return CallToolResult(content=[TextContent(type='text',text=json.dumps({'status':'prepared','handle':handle,'meaning':'图片已真实下载并校验，可观察此缩略图，再决定是否将handle交给hosted_qq_avatar；尚未换头像。'+notices},ensure_ascii=False)),ImageContent(type='image',data=model_data,mimeType=model_mime[5:])])
            except asyncio.CancelledError:raise
            except Exception as error:return json.dumps({'status':'rejected','code':'image_prepare_failed','reason':type(error).__name__})
    return [Images(name='hosted_bilibili_image',description='从本轮B站真实搜索、视频详情或UP资料得到的公开封面/头像候选中选图。list列出可信handle；prepare下载并校验一张图片，最多2MiB、最多4百万像素，规范为512像素PNG，缓存1小时。不能下载完整视频或任意地址；prepared不是已更换QQ头像，换头像需调用hosted_qq_avatar并以其回执为准。',parameters={'type':'object','properties':{'operation':{'type':'string','enum':['list','prepare']},'handle':{'type':'string','pattern':'^bi_[a-f0-9]{24}$'}},'required':['operation'],'additionalProperties':False})]
