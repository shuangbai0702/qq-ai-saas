"""Provenance for the existing pending messages and confirmed dialogue ledger.

No second transcript: this table contains only bounded protocol metadata. Text
stays in memory_pending / dialogue_feedback and is read through one timeline.
Names are display data, never an identity or permission assertion.
"""
import json,re
import hashlib


def bind_model_input(db,logical,scope,event,subject,message):
    """Bind the protocol input before SDK enrichment; no user text copied."""
    from context_protocol import model_json
    key=model_json([scope,str(event)])
    row=db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='model'",(logical,key)).fetchone()
    value=json.loads(row[0]) if row else {'source_scope':scope,'event_id':str(event),'sender_id':str(subject),'outputs':[]}
    signature=hashlib.sha256(model_json(message).encode()).hexdigest()
    if value.get('sender_id')!=str(subject) or value.get('input_digest',signature)!=signature:raise ValueError('shared_context_protocol_input_changed')
    value['input_digest']=signature
    db.execute("INSERT INTO memory_message_sources VALUES(?,?,'model',?) ON CONFLICT(scope,event,direction) DO UPDATE SET data=excluded.data",(logical,key,model_json(value)))
    return signature


def bind_model_output(db,logical,scope,event,subject,messages,index):
    """Ownership metadata for an exact draft/tool message, never a receipt.

    Reuse the provenance ledger. No second transcript and no inferred owner
    from the nearest user in the shared message sequence.
    """
    from context_protocol import model_json,source
    message=messages[index]
    anchor_index=next((i for i in range(index-1,-1,-1) if source(messages[i])),None)
    if anchor_index is None:raise ValueError('model_output_anchor_missing')
    anchor=source(messages[anchor_index])
    location={'anchor':model_json([anchor['scope'],str(anchor['event_id'])]),'offset':index-anchor_index}
    if not isinstance(message,dict) or message.get('role') not in ('assistant','tool'):raise ValueError('model_output_required')
    key=model_json([scope,str(event)]) if logical=='bot_shared' else str(event)
    row=db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='model'",(logical,key)).fetchone()
    value=json.loads(row[0]) if row else {'source_scope':scope,'event_id':str(event),'sender_id':str(subject),'outputs':[]}
    if any(value.get(k)!=v for k,v in (('source_scope',scope),('event_id',str(event)),('sender_id',str(subject)))):raise ValueError('model_output_owner_changed')
    signature=hashlib.sha256(model_json(message).encode()).hexdigest()
    binding={**location,'digest':signature}
    if binding not in value['outputs']:value['outputs'].append(binding)
    db.execute("INSERT INTO memory_message_sources VALUES(?,?, 'model',?) ON CONFLICT(scope,event,direction) DO UPDATE SET data=excluded.data",(logical,key,model_json(value)))


def settle_model_output(db,work):
    """Persist terminal Work evidence next to its draft ownership metadata."""
    from context_protocol import model_json
    key=model_json([work.scope,str(work.event_id)])
    row=db.execute("SELECT data FROM memory_message_sources WHERE scope='bot_shared' AND event=? AND direction='model'",(key,)).fetchone()
    value=json.loads(row[0]) if row else {'source_scope':work.scope,'event_id':str(work.event_id),'sender_id':str(work.subject),'outputs':[]}
    if value.get('sender_id')!=str(work.subject):raise ValueError('model_settlement_owner_changed')
    value['settlement']={'status':work.status,'attempted':work.attempted,'confirmed':work.confirmed,
                         'planned':work.planned_messages,'confirmed_messages':work.confirmed_messages}
    db.execute("INSERT INTO memory_message_sources VALUES('bot_shared',?,'model',?) ON CONFLICT(scope,event,direction) DO UPDATE SET data=excluded.data",(key,model_json(value)))


def model_input_settlement(db,logical,event):
    row=db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='model'",(logical,event)).fetchone()
    return json.loads(row[0]).get('settlement') if row else None


def recover_model_settlements(db,logical='bot_shared'):
    """Repair metadata only from exact terminal ledger/receipt identities.

    No adjacent-message owner inference, waiting->silent conversion, round
    creation or external operation replay. Unknown/partial outcomes stay held.
    """
    from context_protocol import model_json
    repaired=0
    for key,raw in db.execute("SELECT event,data FROM memory_message_sources WHERE scope=? AND direction='model'",(logical,)).fetchall():
        value=json.loads(raw)
        if value.get('settlement'):continue
        scope=value.get('source_scope');event=value.get('event_id');subject=value.get('sender_id')
        if key!=model_json([scope,str(event)]):continue
        row=db.execute('SELECT status,attempted,confirmed FROM pipeline_events WHERE scope=? AND subject=? AND event=?',(scope,subject,str(event))).fetchone()
        if not row:continue
        status,attempted,confirmed=row
        if status not in ('sent','silent','superseded','blocked','error','interrupted') or type(attempted) is not int or attempted!=confirmed:continue
        planned=0
        if status=='sent':
            receipt=get(db,scope,str(event),'outgoing')
            ids=receipt.get('message_ids')
            if receipt.get('receipt')!='confirmed' or receipt.get('complete') is not True or not isinstance(ids,list) or not ids or len(set(ids))!=len(ids) or not confirmed or confirmed<len(ids):continue
            planned=len(ids)
        elif attempted:continue  # No complete chat-plan evidence for a partial tail.
        value['settlement']={'status':status,'attempted':attempted,'confirmed':confirmed,'planned':planned,'confirmed_messages':planned}
        db.execute("UPDATE memory_message_sources SET data=? WHERE scope=? AND event=? AND direction='model'",(model_json(value),logical,key));repaired+=1
    return repaired


def prune_retired_model_owners(db,logical,messages):
    """Only at licensed retirement; never discard unresolved ownership."""
    from context_protocol import source,model_json
    retained={model_json([o['scope'],str(o['event_id'])]) for m in messages if (o:=source(m))}
    for event,raw in db.execute("SELECT event,data FROM memory_message_sources WHERE scope=? AND direction='model'",(logical,)).fetchall():
        value=json.loads(raw);settlement=value.get('settlement') or {}
        if event in retained or any(item['anchor'] in retained for item in value.get('outputs',[])):continue
        if settlement.get('status') not in ('sent','silent','superseded','blocked','error','interrupted') or settlement.get('attempted')!=settlement.get('confirmed'):continue
        if any(db.execute('SELECT 1 FROM '+table+' WHERE scope=? AND event=? LIMIT 1',(logical,event)).fetchone() for table in ('memory_hold_rounds','memory_round_material','memory_pending','memory_rounds')):continue
        db.execute("DELETE FROM memory_message_sources WHERE scope=? AND event=? AND direction='model'",(logical,event))


def model_output_owners(db,logical,messages):
    from context_protocol import model_json,source
    positions={};anchor=None;anchor_index=0
    for index,message in enumerate(messages):
        origin=source(message)
        if origin:
            anchor=model_json([origin['scope'],str(origin['event_id'])]);anchor_index=index
        elif anchor and message.get('role') in ('assistant','tool'):
            positions[(anchor,index-anchor_index)]=hashlib.sha256(model_json(message).encode()).hexdigest()
    owners={}
    for raw, in db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND direction='model'",(logical,)):
        value=json.loads(raw)
        for binding in value.get('outputs',[]):
            key=(binding['anchor'],binding['offset'])
            if positions.get(key)!=binding['digest']:continue
            owner={k:value[k] for k in ('source_scope','event_id','sender_id')}
            owner['settlement']=value.get('settlement')
            if key not in owners:owners[key]=owner
            elif owners[key]!=owner:owners[key]=None
    return owners


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
    allowed=('sender_id','sender_name','name_source','mentions','reply_to','message_ids','receipt','complete','responded_events','source_scope','confirmed_media_kinds')
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
