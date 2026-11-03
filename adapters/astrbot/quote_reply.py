"""Trusted, bounded reply references. The model never supplies a message ID."""
import asyncio,hashlib,json,time
from bot_pipeline import delivery_scope

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS pipeline_quote_sources(handle TEXT PRIMARY KEY,scope TEXT,sender TEXT,message TEXT,at REAL,self_id TEXT,generation INTEGER,preview TEXT)')
    if 'media' not in {r[1] for r in db.execute('PRAGMA table_info(pipeline_quote_sources)')}:
        db.execute("ALTER TABLE pipeline_quote_sources ADD COLUMN media TEXT NOT NULL DEFAULT '[]'")

def observe(db,event,lease,scope):
    initialize(db)
    value=getattr(event.message_obj,'message_id',None)
    if type(value) not in (str,int) or not str(value).strip() or str(value)=='0' or str(event.get_sender_id())==str(event.get_self_id()):return
    now=time.time();sender=str(event.get_sender_id());account=str(event.get_self_id());generation=lease.get('generation',0)
    handle='qr_'+hashlib.sha256(json.dumps([lease.get('botId'),account,generation,scope,sender,str(value)]).encode()).hexdigest()[:20]
    preview=event.get_message_str()[:120] or '[图片或其他非文本消息]'
    native=getattr(event.message_obj,'raw_message',{});segments=native.get('message',[]) if isinstance(native,dict) else []
    media=[{'url':str(x['data'].get('url') or '')[:4096],'file':str(x['data'].get('file') or '')[:200]} for x in segments if isinstance(x,dict) and x.get('type') in ('image','mface') and isinstance(x.get('data'),dict)][:3] if isinstance(segments,list) else []
    db.execute('INSERT OR IGNORE INTO pipeline_quote_sources(handle,scope,sender,message,at,self_id,generation,preview,media) VALUES(?,?,?,?,?,?,?,?,?)',(handle,scope,sender,str(value),now,account,generation,preview,json.dumps(media)))
    db.execute('DELETE FROM pipeline_quote_sources WHERE at<? OR rowid NOT IN (SELECT rowid FROM pipeline_quote_sources ORDER BY at DESC LIMIT 200)',(now-86400,));db.commit()

def catalog(db,event,lease):
    initialize(db)
    scope=delivery_scope(event)
    return [{'handle':r[0],'sender_id':r[1],'preview':r[2],'at':r[3]} for r in db.execute('SELECT handle,sender,preview,at FROM pipeline_quote_sources WHERE scope=? AND self_id=? AND generation=? AND at>? ORDER BY at DESC LIMIT 12',(scope,str(event.get_self_id()),lease.get('generation',0),time.time()-86400))]

def selected(db,event,lease):
    handle=event.get_extra('_hosted_quote_handle')
    if not handle:return None
    rows=catalog(db,event,lease)
    if not any(r['handle']==handle for r in rows):return None
    row=db.execute('SELECT message,sender FROM pipeline_quote_sources WHERE handle=?',(handle,)).fetchone()
    return row

def media_catalog(db,event,lease):
    initialize(db);result=[]
    rows=db.execute("SELECT handle,sender,message,at,media FROM pipeline_quote_sources WHERE scope=? AND self_id=? AND generation=? AND at>? AND media<>'[]' ORDER BY at DESC LIMIT 12",(delivery_scope(event),str(event.get_self_id()),lease.get('generation',0),time.time()-900))
    for ref,sender,message,at,media in rows:
        for index,_ in enumerate(json.loads(media)):
            result.append({'handle':'qm_'+hashlib.sha256((ref+'|'+str(index)).encode()).hexdigest()[:20],'sender_id':sender,'at':at,'message_id':message,'_reference':ref,'_index':index})
    return result[:12]

def media_reference(db,event,lease):
    source=event.get_extra('_hosted_media_handle')
    return next((r for r in media_catalog(db,event,lease) if r['handle']==source),None)

async def resolve_media(db,event,lease,handle,current):
    source=next((r for r in media_catalog(db,event,lease) if r['handle']==handle),None)
    if not source or not current():raise ValueError('media_source_expired')
    event.set_extra('_hosted_media_handle',handle)
    try:
        value=await asyncio.wait_for(event.bot.get_msg(message_id=int(source['message_id'])),3)
        if isinstance(value,dict) and isinstance(value.get('data'),dict):value=value['data']
        if not isinstance(value,dict) or not current() or media_reference(db,event,lease)!=source:raise ValueError('media_source_expired')
        if str(value.get('message_id'))!=source['message_id'] or str((value.get('sender') or {}).get('user_id'))!=source['sender_id'] or str(value.get('group_id') or '')!=str(event.get_group_id() or ''):raise ValueError('media_source_mismatch')
        media=[s['data'] for s in value.get('message',[]) if isinstance(s,dict) and s.get('type') in ('image','mface') and isinstance(s.get('data'),dict)]
        if source['_index']>=len(media):raise ValueError('media_source_unavailable')
        return media[source['_index']]
    finally:event.set_extra('_hosted_media_handle',None)

async def resolve(db,event,lease,current):
    row=selected(db,event,lease)
    if not row or not current():return None
    # A catalog entry is an observed source, not proof it still exists. Recheck
    # at the delivery boundary so withdrawn messages degrade to an ordinary reply.
    try:
        message=await asyncio.wait_for(event.bot.get_msg(message_id=int(row[0])),2)
        if isinstance(message,dict) and isinstance(message.get('data'),dict):message=message['data']
        if not isinstance(message,dict) or not current():return None
        sender=str((message.get('sender') or {}).get('user_id',message.get('user_id','')))
        group=str(message.get('group_id') or '')
        if str(message.get('message_id'))!=row[0] or sender!=row[1] or group!=str(event.get_group_id() or ''):return None
        return row if selected(db,event,lease)==row else None
    except Exception:return None

def tools(event,cfg,db,lease,current):
    if not (cfg.get('behavior') or {}).get('quoteReplyEnabled',True):return []
    from astrbot.core.agent.tool import FunctionTool
    candidates=catalog(db,event,lease) if hasattr(event,'get_self_id') else []
    parameter={'type':'string','maxLength':24,'description':'从本次真实消息候选选择handle；留空重新读取候选，不接受消息编号。'}
    if hasattr(event,'get_self_id'):parameter['enum']=['']+[r['handle'] for r in candidates]
    class Quote(FunctionTool):
        async def call(self,context,handle=''):
            if not current() or context.context.event is not event:return '引用已失效。'
            rows=catalog(db,event,lease)
            if not handle:return json.dumps({'messages':rows,'meaning':'真实来源句柄；只可引用当前会话，图片也可以引用。'},ensure_ascii=False)
            if not any(r['handle']==handle for r in rows):return json.dumps({'status':'invalid_reference','messages':rows,'meaning':'未设置引用；只能从当前真实候选选择handle，不能把消息编号当handle。'},ensure_ascii=False)
            event.set_extra('_hosted_quote_handle',handle)
            return '已为本轮首条回复设置真实引用；尚未发送，最终结果以QQ消息回执为准。'
    return [Quote(name='hosted_quote_reply',description='选择并真实引用当前会话的一条别人消息回复。省略handle可查看近期可信句柄，指定handle设置本轮首条引用；不是发送工具，不得自行填写消息编号。当前可信候选：'+json.dumps(candidates,ensure_ascii=False),parameters={'type':'object','properties':{'handle':parameter},'additionalProperties':False})]
