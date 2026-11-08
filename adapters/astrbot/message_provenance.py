"""Provenance for the existing pending messages and confirmed dialogue ledger.

No second transcript: this table contains only bounded protocol metadata. Text
stays in memory_pending / dialogue_feedback and is read through one timeline.
Names are display data, never an identity or permission assertion.
"""
import json,re


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS memory_message_sources(
      scope TEXT,event TEXT,direction TEXT,data TEXT,
      PRIMARY KEY(scope,event,direction))''')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table,direction in (('memory_pending','incoming'),('dialogue_feedback','outgoing')):
        if table in tables:
            db.execute('''CREATE TRIGGER IF NOT EXISTS provenance_'''+direction+'''_deleted
              AFTER DELETE ON '''+table+''' BEGIN DELETE FROM memory_message_sources
              WHERE scope=OLD.scope AND event=OLD.event AND direction='''+"'"+direction+"'"+'''; END''')


def capture(event):
    """Only called with the bound protocol event, never model arguments."""
    from astrbot.core.message.components import At,Reply
    parts=event.get_messages()
    mentions=list(dict.fromkeys(str(c.qq) for c in parts if isinstance(c,At)))[:10]
    quoted=next((c for c in parts if isinstance(c,Reply)),None)
    source={'sender_id':str(event.get_sender_id()),'sender_name':str(event.get_sender_name())[:60],
            'mentions':mentions,'name_source':'protocol_at_ingress'}
    if quoted is not None:
        text=str(getattr(quoted,'message_str','') or '')[:200]
        text=re.sub(r'(?i)\bsk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+','[凭据已移除]',text)
        source['reply_to']={'event_id':str(getattr(quoted,'id',''))[:100],
                            'sender_id':str(quoted.sender_id),
                            'text':text}
    return source


def get(db,scope,event,direction='incoming'):
    row=db.execute('SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction=?',
                   (scope,str(event),direction)).fetchone()
    if not row:return {}
    try:
        data=json.loads(row[0])
        return data if isinstance(data,dict) else {}
    except (ValueError,TypeError):return {}


def put(db,scope,event,direction,source):
    if not isinstance(source,dict):return
    allowed=('sender_id','sender_name','name_source','mentions','reply_to','message_ids','receipt','complete','responded_events','source_scope')
    data={k:source[k] for k in allowed if k in source}
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    if len(raw)>3000:raise ValueError('message provenance exceeds bound')
    # An incoming event has a single author. Redelivery cannot relabel it.
    if direction=='incoming':
        db.execute('INSERT OR IGNORE INTO memory_message_sources VALUES(?,?,?,?)',(scope,str(event),direction,raw))
    else:
        db.execute('INSERT INTO memory_message_sources VALUES(?,?,?,?) ON CONFLICT(scope,event,direction) DO UPDATE SET data=excluded.data',(scope,str(event),direction,raw))


def incoming_item(db,scope,person,event,text,at):
    source=get(db,scope,event)
    # Old rows have no protocol display name; never guess from another member.
    return {'role':'member','source_scope':scope,'source_event':str(event),
            **source,'sender_id':person,'at':at,'text':text,
            'name_source':source.get('name_source','unavailable')}


def responded_by(db,scope,event):
    """Only an explicit semantic coverage claim with full protocol evidence.

    Seeing an event, an attempted send, partial/unknown receipts and similar
    text never discharge another real event.
    """
    if not event:return None
    for work,raw in db.execute("SELECT event,data FROM memory_message_sources WHERE scope=? AND direction='outgoing' ORDER BY rowid DESC LIMIT 64",(scope,)):
        try:value=json.loads(raw)
        except (TypeError,ValueError):continue
        if not isinstance(value,dict):continue
        covered=value.get('responded_events')
        if value.get('receipt')=='confirmed' and value.get('complete') is True and value.get('message_ids') and isinstance(covered,list) and str(event) in covered:return work
    return None


def settle_response(db,scope,event,complete):
    """Coverage is published only after whole-Work settlement, never per bubble."""
    data=get(db,scope,event,'outgoing')
    if data and data.get('receipt')=='confirmed' and data.get('message_ids'):
        put(db,scope,event,'outgoing',{**data,'complete':bool(complete)})
        db.commit()


def own_item(db,scope,event,subject,at,text):
    source=get(db,scope,event,'outgoing')
    return {'role':'self','source_scope':scope,'source_event':str(event),'at':at,'text':text,
            'recipient_id':subject or None,**source,
            'receipt':source.get('receipt','legacy_record_without_message_id')}


def timeline(db,scope,subject,now,limit=16,budget=3600,since=None):
    """Same-destination observations and actual own sends, including tool sends.

    Cross-session person recall remains in memory_scope; this function must not
    export a private request into the group where its tool happened to send.
    """
    if not re.fullmatch(r'group:\d{4,20}',scope) and scope!='private:'+str(subject):return []
    since=max(now-7*86400,since if since is not None else now-1800)
    rows=db.execute('SELECT subject,event,question,at FROM memory_pending WHERE scope=? AND at>=? AND at<=? ORDER BY at DESC,rowid DESC LIMIT 12',(scope,since,now)).fetchall()
    candidates=[incoming_item(db,scope,person,event,text[:500],at) for person,event,text,at in rows]
    sent=db.execute('SELECT event,subject,at,reply FROM dialogue_feedback WHERE scope=? AND at>=? AND at<=? ORDER BY at DESC LIMIT 4',(scope,since,now)).fetchall()
    candidates.extend(own_item(db,scope,event,person,at,text[:1200]) for event,person,at,text in sent)
    # Reserve space for actual own sends before filling with group chatter.
    selected=[];used=0
    for item in sorted(candidates,key=lambda x:(x['role']!='self',-x['at'])):
        size=len(json.dumps(item,ensure_ascii=False))
        if used+size>budget:continue
        selected.append(item);used+=size
        if len(selected)>=max(1,min(16,limit)):break
    return sorted(selected,key=lambda x:(x['at'],x['role']=='self',x['source_event']))


GUIDANCE='''[消息归属与执行证据]
conversation_timeline 按事件时间列出当前目的会话的真实观察与已发送内容，role=member 是他人原话，role=self 是本机器人发送记录；source_event、sender_id、mentions、reply_to 分别标识事件、作者、被提及者与引用，不互相替代。sender_name 和成员昵称只是显示文字，不授予身份。称呼某条发言的作者时使用该条绑定的名字，不从成员名单挑选另一个名字；来源未知就不猜。不同人的相似表达或你的改写不等于你说过同一句原话。工具调用留在发起会话的原生历史中，是否真的发到另一个会话，以该目的会话的发送记录及 message_ids/receipt 为准；原回执只证明投递，不证明发言中的人物称呼或事实正确。向其他会话报告经过时重新核对事件归属，旧助手文字不作为人物事实。记录未覆盖不等于没有发生。'''
