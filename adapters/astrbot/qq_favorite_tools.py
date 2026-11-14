"""Real QQ favorites through the current OneBot, with durable uncertain writes."""
from tool_outcome import rejected
import asyncio,hashlib,json,time,logging,os,re,stat,tempfile
from pathlib import Path
from sticker_collection import download,png_thumbnail
from bot_pipeline import delivery_scope
import quote_reply
from qq_favorites import descriptions,description_id

MAX_FAVORITE_BYTES=8*1048576
SEND_CACHE_BYTES=32*1048576
SEND_CACHE_ITEMS=16
logger=logging.getLogger(__name__)

def validate_image(raw):return png_thumbnail(raw,max_bytes=MAX_FAVORITE_BYTES,max_pixels=4194304)

def cached_original(file,digest):
    try:
        info=file.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_FAVORITE_BYTES:return None
        with file.open('rb') as stream:raw=stream.read(MAX_FAVORITE_BYTES+1)
        if len(raw)>MAX_FAVORITE_BYTES or hashlib.md5(raw).hexdigest()!=digest:return None
        validate_image(raw)
        return raw
    except (OSError,ValueError):return None


def cache_original(folder,name,raw):
    """Disposable per-bot send cache; never prune uploads or account data."""
    temporary=None
    try:
        if folder.is_symlink():return
        folder.mkdir(parents=True,exist_ok=True)
        entries=[]
        for file in folder.iterdir():
            if not re.fullmatch(r'\d{5,20}_[a-f0-9]{32}\.image',file.name) or file.is_symlink():continue
            info=file.stat()
            if stat.S_ISREG(info.st_mode):entries.append((info.st_mtime,file,info.st_size))
        retained=[item for item in entries if item[1].name!=name]
        retained.sort(key=lambda item:item[0]);total=sum(item[2] for item in retained)
        while retained and (len(retained)>=SEND_CACHE_ITEMS or total+len(raw)>SEND_CACHE_BYTES):
            _,file,size=retained.pop(0);file.unlink();total-=size
        fd,temporary=tempfile.mkstemp(prefix='.qqf-send-',dir=folder)
        with os.fdopen(fd,'wb') as stream:stream.write(raw)
        os.replace(temporary,folder/name);temporary=None
    except OSError:pass # A cache failure must not prevent a valid image reply.
    finally:
        if temporary:
            try:Path(temporary).unlink(missing_ok=True)
            except OSError:pass


async def prepare_original(row,root,account,current):
    digest=str(row.get('md5','')).lower()
    if not re.fullmatch(r'[a-f0-9]{32}',digest) or not re.fullmatch(r'\d{5,20}',account):raise ValueError('unsupported_favorite_identity')
    folder=Path(root)/'qq-favorites'
    # Only the current QQ's verified detail row grants use of these bytes;
    # neither model text nor a QQ-reported filesystem path is accepted.
    if not current():raise ValueError('配置或执行身份已变化')
    if folder.is_symlink():raise ValueError('unsupported_favorite_cache_path')
    uploads=folder/'uploads'
    if not uploads.is_symlink():
        raw=cached_original(uploads/(digest+'.image'),digest)
        if raw is not None:return raw,'saved_original'
    cache=folder/'send-cache';name=account+'_'+digest+'.image'
    if not cache.is_symlink():
        file=cache/name;raw=cached_original(file,digest)
        if raw is not None:
            try:file.touch()
            except OSError:pass
            return raw,'send_cache'
    raw=await download(str(row.get('url') or ''),max_bytes=MAX_FAVORITE_BYTES)
    if not current():raise ValueError('配置或执行身份已变化')
    if hashlib.md5(raw).hexdigest()!=digest:raise ValueError('unsupported_favorite_content_mismatch')
    validate_image(raw);cache_original(cache,name,raw)
    return raw,'qq_download'


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS qq_favorite_writes(account TEXT,md5 TEXT,scope TEXT,source TEXT,state TEXT,res_id TEXT,at REAL,PRIMARY KEY(account,md5))')
    columns={r[1] for r in db.execute('PRAGMA table_info(qq_favorite_writes)')}
    for name,kind in (('action','TEXT'),('sequence','INTEGER'),('checks','INTEGER NOT NULL DEFAULT 0'),('next_check','REAL NOT NULL DEFAULT 0'),('last_error',"TEXT NOT NULL DEFAULT ''")):
        if name not in columns:db.execute('ALTER TABLE qq_favorite_writes ADD COLUMN '+name+' '+kind)

def settle(db,account,digest,res_id):
    proof='qq_favorite:'+digest+':'+str(res_id)
    row=db.execute('SELECT action,sequence FROM qq_favorite_writes WHERE account=? AND md5=?',(account,digest)).fetchone()
    db.execute("UPDATE qq_favorite_writes SET state='confirmed',res_id=?,next_check=0,last_error='' WHERE account=? AND md5=?",(str(res_id),account,digest))
    if row and row[0] and row[1] is not None:
        db.execute("UPDATE pipeline_action_effects SET state='confirmed',receipt=? WHERE action=? AND sequence=? AND method='add_custom_face' AND state='dispatching'",(proof,row[0],row[1]))
    db.commit();return proof

def due(db,account,action=None):
    where="state='unknown' AND next_check<=?";args=[time.time()]
    if account:where+=' AND account=?';args.append(account)
    if action is not None:where+=' AND action=?';args.append(action)
    return db.execute('SELECT account,md5,checks FROM qq_favorite_writes WHERE '+where+' ORDER BY next_check,at LIMIT 2',args).fetchall()

def settle_readback(db,account,rows):
    pending={r[0] for r in db.execute("SELECT md5 FROM qq_favorite_writes WHERE account=? AND state='unknown' LIMIT 200",(account,))}
    confirmed={}
    for row in rows[:200]:
        digest=str(row.get('md5','')).lower()
        if digest in pending and row.get('resId'):
            settle(db,account,digest,row['resId']);confirmed[digest]=row
    return confirmed

async def reconcile(db,bot,account,action,current):
    if not current():return {'checked':False,'confirmed':{}}
    pending=due(db,account,action)
    if not pending:return {'checked':False,'confirmed':{}}
    if not account:
        accounts={r[0] for r in pending}
        if len(accounts)!=1:return {'checked':False,'confirmed':{}}
        account=next(iter(accounts))
    # Persist read-only check backoff before awaiting; process restarts cannot
    # turn an uncertain upload into another upload or an aggressive poll loop.
    for _,digest,checks in pending:
        db.execute('UPDATE qq_favorite_writes SET checks=checks+1,next_check=? WHERE account=? AND md5=? AND state=\'unknown\'',(time.time()+min(300,30*2**min(4,checks)),account,digest))
    db.commit()
    try:
        login=await asyncio.wait_for(bot.get_login_info(),1.2)
        if not current() or str(login.get('user_id'))!=account:return {'checked':True,'confirmed':{}}
        rows=await asyncio.wait_for(bot.fetch_custom_face_detail(count=200),1.5)
        if not current() or not isinstance(rows,list):return {'checked':True,'confirmed':{}}
        return {'checked':True,'confirmed':settle_readback(db,account,rows)}
    except asyncio.CancelledError:raise
    except Exception as error:
        logger.warning('QQ favorite readback pending: %s',type(error).__name__)
        return {'checked':True,'confirmed':{}} # No write retry; uncertainty stays visible.

def context(event,db):
    """Fresh source data, separate from the stable tool schema; not authority."""
    initialize(db)
    lease=event.get_extra('_hosted_lease') or {}
    observed=quote_reply.media_catalog(db,event,lease) if all(hasattr(event,name) for name in ('get_group_id','get_sender_id','get_self_id')) else []
    source_ids=['current']+[r['handle'] for r in observed]
    if hasattr(event,'get_sender_id'):
        source_ids+=[r[0] for r in db.execute("SELECT id FROM sticker_inbox WHERE scope=? AND status='approved' ORDER BY created DESC LIMIT 30",(delivery_scope(event),))] if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sticker_inbox'").fetchone() else []
    media=[{k:v for k,v in r.items() if not k.startswith('_')} for r in observed]
    recent=[{'state':r[0],'md5':r[1],'at':r[2]} for r in db.execute('SELECT state,md5,at FROM qq_favorite_writes WHERE account=? AND scope=? ORDER BY at DESC LIMIT 3',(str(event.get_self_id()),delivery_scope(event)))] if hasattr(event,'get_self_id') else []
    return {'source_ids':source_ids,'media':media,'recent_writes':recent}


def tools(event,cfg,db,root,current):
    b=cfg.get('behavior') or {}
    if not b.get('qqFavoriteSaveEnabled',True) and not b.get('memeEnabled',True):return []
    initialize(db)
    lease=event.get_extra('_hosted_lease') or {}
    from astrbot.core.agent.tool import FunctionTool
    def source_context():return context(event,db)
    source_ids=source_context()['source_ids']
    meanings={}
    async def details():
        nonlocal meanings
        if not current():raise ValueError('配置或执行身份已变化')
        login=await asyncio.wait_for(event.bot.get_login_info(),3)
        if not current() or str(login.get('user_id'))!=str(event.get_self_id()):raise ValueError('QQ身份已变化')
        rows=await asyncio.wait_for(event.bot.fetch_custom_face_detail(count=200),5)
        if not current() or not isinstance(rows,list):raise ValueError('QQ收藏协议不支持或返回无效')
        meanings=descriptions(root,str(event.get_self_id()))
        settle_readback(db,str(event.get_self_id()),rows)
        return rows[:200]
    def handle(row):return 'qf_'+hashlib.sha256((str(event.get_self_id())+'|'+str(row.get('resId') or row.get('md5'))).encode()).hexdigest()[:20]
    def item(row):
        saved=db.execute("SELECT scope,at FROM qq_favorite_writes WHERE account=? AND md5=? AND state='confirmed'",(str(event.get_self_id()),str(row.get('md5','')).lower())).fetchone()
        semantic=meanings.get(description_id(row))
        # The index is the only semantic authority. A stored meaning stays
        # visible even while the item is disabled; QQ raw text
        # (modifyWord/desc/ocrWord) is source evidence, never selection
        # semantics; an item without any index record reports
        # selection_disabled for automatic selection but keeps the explicit
        # prepare path (blanket-blocking unannotated favorites is not allowed).
        valid=bool(semantic and semantic.get('enabled') and semantic.get('meaning'))
        return {'handle':handle(row),'meaning':semantic['meaning'] if semantic else '',
                'avoid':semantic['avoid'] if semantic else '', 'rawText':semantic.get('rawText','') if semantic else '',
                'selection_disabled':not valid,
                'saved_from_this_conversation':bool(saved and saved[0]==delivery_scope(event)),
                'saved_at':saved[1] if saved and saved[0]==delivery_scope(event) else None}
    def confirmed(row,existing=False):
        proof=settle(db,str(event.get_self_id()),str(row.get('md5','')).lower(),row.get('resId') or '')
        verify=event.get_extra('_hosted_favorite_verifier')
        if verify:verify(proof)
        records=list(event.get_extra('_hosted_tool_results') or []);records.append({'tool':'hosted_qq_favorites','status':'returned','operation':'save','receipt':proof,'at':int(time.time()*1000)});event.set_extra('_hosted_tool_results',records[-8:])
        return json.dumps({'status':'confirmed','item':item(row),'meaning':('QQ收藏详情已确认已有此原图，无需重复添加。' if existing else 'QQ收藏详情已读回原图MD5，真实收藏成功。此操作保留原动图；可读取收藏并选择发表情。')},ensure_ascii=False)
    class Favorites(FunctionTool):
        @property
        def hosted_write_operations(self):return frozenset({'save'})
        async def call(self,context,operation,source_id='current',favorite_handle=''):
            if not current() or context.context.event is not event:return rejected('tool_preflight_rejected','未操作：配置或执行身份已变化。')
            submitted=False;phase='source';digest=None;account=str(event.get_self_id())
            try:
                if operation=='sources':
                    return json.dumps(source_context(),ensure_ascii=False)
                if operation=='list':
                    phase='list'
                    rows=await details()
                    return json.dumps({'items':[item(r) for r in rows],'meaning':'这是当前登录QQ的真实收藏；saved_from_this_conversation和saved_at来自实际收藏回执，可辨别刚才在本会话保存的图。无场景或来源证据时不要猜图。prepare设置本轮附图，尚未发送。'},ensure_ascii=False)
                if operation=='prepare':
                    phase='prepare'
                    # Dispatch consumes/removes the prepared attachment. The
                    # sent/unknown fence therefore cannot depend on it still
                    # being present (nor on which handle the model now picks).
                    if event.get_extra('_hosted_meme_sent',False):
                        return json.dumps({'status':'already_sent','meaning':'本轮已投递过收藏图；回执未知也不重复发送。'},ensure_ascii=False)
                    if not b.get('memeEnabled',True):return rejected('tool_preflight_rejected','未准备：聊天表情能力已关闭。')
                    if event.get_extra('_hosted_meme_allowed') is False:
                        gate=event.get_extra('_hosted_meme_gate') or {}
                        return json.dumps({'status':'unavailable',**gate,'meaning':'未准备：本轮表情发送条件不满足，请保留文字回复。'},ensure_ascii=False)
                    rows=await details();row=next((r for r in rows if handle(r)==favorite_handle),None)
                    if not row:return json.dumps({'status':'invalid_favorite','items':[item(r) for r in rows],'meaning':'未设置附图；prepare的favorite_handle必须来自真实收藏列表。'},ensure_ascii=False)
                    # Only an indexed record can veto preparation: a disabled
                    # manual preference, raw text, pending/uncertain/failed
                    # semantics. Unindexed favorites keep the legacy explicit
                    # prepare path; automatic semantic selection never sees them.
                    known=meanings.get(description_id(row))
                    if known and item(row)['selection_disabled']:return json.dumps({'status':'disabled_favorite','meaning':'该收藏已停用或语义尚未确认，未准备附图。'},ensure_ascii=False)
                    # One Work prepares one attachment. A repeat call for the same
                    # handle is already_prepared (no second download); a different
                    # handle may only replace the attachment before any dispatch,
                    # and a sent or unknown-ACK round never sends a second image.
                    prepared=event.get_extra('_hosted_tool_sticker')
                    if isinstance(prepared,dict) and prepared.get('id'):
                        if prepared.get('id')==favorite_handle:
                            return json.dumps({'status':'already_prepared','id':favorite_handle,'source':prepared.get('source'),'meaning':'本轮已选定该收藏，尚未投递；不重复下载，也不改选其他图。'},ensure_ascii=False)
                    raw,source=await prepare_original(row,root,account,current)
                    if not current():return rejected('tool_preflight_rejected','未准备：配置已变化。')
                    event.set_extra('_hosted_tool_sticker',{'id':favorite_handle,'bytes':raw,'source':source,'digest':hashlib.md5(raw).hexdigest()})
                    return '已选定真实QQ收藏，将随本轮回复投递一次；成功与否以消息回执为准。'
                if operation!='save' or not b.get('qqFavoriteSaveEnabled',True):return rejected('tool_preflight_rejected','未收藏：该操作未开放。')
                scope=delivery_scope(event)
                if source_id=='current':
                    native=getattr(event.message_obj,'raw_message',{})
                    segments=native.get('message',[]) if isinstance(native,dict) else []
                    images=[r['data'] for r in segments if isinstance(r,dict) and r.get('type') in ('image','mface') and isinstance(r.get('data'),dict)]
                    if len(images)>1:return json.dumps({'status':'blocked','code':'media_source_ambiguous_or_missing','allowedSources':source_ids,'meaning':'本条有多张图片，需要从可信图片候选指定source_id，不能默认收藏第一张。'},ensure_ascii=False)
                    source=images[0] if images else None
                    if not source:
                        recent=[r for r in quote_reply.media_catalog(db,event,lease) if r['sender_id']==str(event.get_sender_id())]
                        latest=[r for r in recent if recent and r['message_id']==recent[0]['message_id']]
                        if len(latest)!=1:return json.dumps({'status':'blocked','code':'media_source_ambiguous_or_missing','allowedSources':source_ids,'meaning':'本轮无图，最近收到的图片不唯一或已过期；需要从可信图片候选指定source_id。'},ensure_ascii=False)
                        source=await quote_reply.resolve_media(db,event,lease,latest[0]['handle'],current)
                    raw=await download(str(source.get('url') or ''),max_bytes=MAX_FAVORITE_BYTES);validate_image(raw)
                elif source_id.startswith('qm_'):
                    source=await quote_reply.resolve_media(db,event,lease,source_id,current)
                    raw=await download(str(source.get('url') or ''),max_bytes=MAX_FAVORITE_BYTES);validate_image(raw)
                else:
                    row=db.execute("SELECT coalesce(o.bytes,i.png) FROM sticker_inbox i LEFT JOIN sticker_originals o ON o.id=i.id WHERE i.id=? AND i.scope=? AND i.status='approved'",(source_id,scope)).fetchone()
                    if not row:return json.dumps({'status':'invalid_source','allowedSources':source_ids,'meaning':'未收藏：素材不属于当前会话或尚未审核；save保存本轮收到的图时source_id使用current。'},ensure_ascii=False)
                    raw=bytes(row[0]);validate_image(raw)
                phase='preflight_readback';digest=hashlib.md5(raw).hexdigest();rows=await details()
                match=next((r for r in rows if str(r.get('md5','')).lower()==digest),None)
                if match:
                    return confirmed(match,existing=True)
                old=db.execute('SELECT state FROM qq_favorite_writes WHERE account=? AND md5=?',(account,digest)).fetchone()
                if old:return '原收藏写入状态为'+old[0]+'；本次读回未确认，不会重复上传，请先在QQ检查。'
                if db.execute('SELECT count(*) FROM qq_favorite_writes').fetchone()[0]>=200:return rejected('tool_preflight_rejected','收藏执行记录已到有界上限，需要管理员检查历史状态。')
                directory=Path(root)/'qq-favorites'/'uploads';directory.mkdir(parents=True,exist_ok=True)
                file=directory/(digest+'.image');file.write_bytes(raw)
                if not current():
                    file.unlink(missing_ok=True);return rejected('tool_preflight_rejected','未收藏：提交前配置已变化。')
                db.execute("INSERT INTO qq_favorite_writes(account,md5,scope,source,state,res_id,at) VALUES(?,?,?,?,'unknown','',?)",(account,digest,scope,source_id,time.time()));db.commit()
                event.set_extra('_hosted_favorite_md5',digest)
                # Registered before the await. A lost response is never a retry.
                submitted=True;phase='submit'
                await asyncio.wait_for(event.bot.add_custom_face(file=str(file),is_origin=True),12)
                phase='confirmation_readback'
                rows=await details();match=next((r for r in rows if str(r.get('md5','')).lower()==digest),None)
                if not match:return json.dumps({'status':'unknown','code':'favorite_confirmation_pending','submitted':True,'phase':phase,'meaning':'请求已提交，结果未确认；后台会只读核对，不会重复上传。等待确认不等于失败，不能据此声称QQ或网络故障。'},ensure_ascii=False)
                return confirmed(match)
            except asyncio.CancelledError:raise
            except Exception as error:
                logger.warning('QQ favorite operation pending: phase=%s submitted=%s error=%s',phase,submitted,type(error).__name__)
                if submitted and current():
                    db.execute("UPDATE qq_favorite_writes SET last_error=? WHERE account=? AND md5=? AND state='unknown'",(phase+':'+type(error).__name__[:48],account,digest));db.commit()
                    checked=await reconcile(db,event.bot,account,None,current)
                    if current() and digest in checked['confirmed']:return confirmed(checked['confirmed'][digest])
                code='favorite_confirmation_pending' if submitted else str(error) if isinstance(error,ValueError) and str(error).startswith(('media_','unsupported_')) else 'favorite_'+phase+'_unavailable'
                return json.dumps({'status':'unknown' if submitted else 'blocked','code':code,'submitted':submitted,'phase':phase,'meaning':('请求已经发起，结果未确认；后台仅核查真实收藏，不会重复上传。等待确认不能解释为保存失败或QQ网络故障。' if submitted else '操作在提交前受阻，没有写入QQ收藏。')+'单张上限8MiB、首帧4百万像素，保留原动图。'},ensure_ascii=False)
    return [Favorites(name='hosted_qq_favorites',description='当前登录QQ的真实自定义表情：list读取收藏并核实历史未确认写入；prepare用list返回的favorite_handle选择收藏随本轮回复发送，也可由群发工具将已选图发往授权群；save用source_id保存收到的原图。current取本条图片，无图时只取当前发送者最近15分钟最新消息中唯一的图片；多图需选可信句柄。source_id与favorite_handle不能混用，都不是URL或路径。保存不是平台收图；按实际回执说明结果。unknown只表示待确认，不是失败或网络故障；confirmed才可报告成功。sources只读查询当前来源、近期图片及本会话收藏写入回执，不访问QQ网络；list查询真实收藏。执行时仍校验账号、会话与真实来源。',parameters={'type':'object','properties':{'operation':{'type':'string','enum':['sources','list','prepare','save']},'source_id':{'type':'string','maxLength':128,'description':'仅save使用；current或近期可信图片/已审核素材句柄。'},'favorite_handle':{'type':'string','pattern':'^qf_[a-f0-9]{20}$','description':'仅prepare使用；从list或save真实返回的handle选择。'}},'required':['operation'],'additionalProperties':False})]
