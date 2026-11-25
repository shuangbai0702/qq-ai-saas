"""Typed activation provenance in the existing round/Work ledgers.

No transcript or second counter. The provenance retires with its round.
"""
import json
import time


def metadata(db, scope, event):
    row = db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='activation'", (scope, str(event))).fetchone()
    return json.loads(row[0]) if row else None


_SERVICE_SOURCES = ('autonomous', 'owner_contact', 'scheduled_contact')


def material_subject(db,scope,event,subject):
    """Service round.subject is the recipient; capture.subject is the speaker.

    Normalize only that proven bookkeeping distinction. A third principal is
    still a conflict, and ordinary dialogue retains its exact author checks.
    """
    data=metadata(db,scope,event)
    if not data or data.get('round_kind')!='service_dialogue':return str(subject)
    owner=db.execute("SELECT subject FROM pipeline_events WHERE scope=? AND event=? AND source IN ('autonomous','owner_contact','scheduled_contact')",(data.get('source_scope'),data.get('owner_work'))).fetchall()
    import message_provenance
    incoming=message_provenance.get(db,scope,event) or {}
    speaker=str(incoming.get('sender_id',''))
    if len(owner)!=1 or not speaker or incoming.get('source_scope',data.get('source_scope'))!=data.get('source_scope'):
        raise ValueError('semantic_service_speaker_unproven')
    if str(subject) not in (str(owner[0][0]),speaker):raise ValueError('semantic_collection_sender_conflict')
    return speaker


def annotate_service(db, scope, event, work, *, source_scope):
    owner=db.execute("SELECT scope FROM pipeline_events WHERE scope=? AND event=? AND source IN ('autonomous','owner_contact','scheduled_contact')",(source_scope,work)).fetchone()
    if not owner or owner[0]!=source_scope:raise ValueError('service_dialogue_owner_missing')
    data={'round_kind':'service_dialogue','owner_work':work,'source_scope':source_scope}
    previous=metadata(db,scope,event)
    if previous is not None and previous!=data:raise ValueError('service_dialogue_owner_changed')
    if previous is None and db.execute('SELECT 1 FROM memory_rounds WHERE scope=? AND event=?',(scope,str(event))).fetchone():raise ValueError('service_dialogue_event_collision')
    import message_provenance
    message_provenance.put_activation(db,scope,event,data)


def record(db, scope, event, work, *, source_scope, subject, at=None):
    """Caller owns the Journal staging transaction; never commit separately."""
    owner = db.execute("SELECT scope,status FROM pipeline_events WHERE event=? AND source='autonomous'", (work,)).fetchone()
    if not owner or owner[0] != source_scope:
        raise ValueError('activation_owner_missing')
    data = {'round_kind': 'autonomous_activation', 'owner_work': work, 'source_scope': source_scope}
    previous = metadata(db, scope, event)
    if previous is not None and previous != data:
        raise ValueError('activation_owner_changed')
    if previous is None and db.execute('SELECT 1 FROM memory_rounds WHERE scope=? AND event=?', (scope, str(event))).fetchone():
        raise ValueError('activation_event_collision')
    import message_provenance
    message_provenance.put_activation(db, scope, event, data)
    db.execute('INSERT OR IGNORE INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)', (scope, subject, str(event), '', '', time.time() if at is None else at))


def valid(db, scope, row):
    data = metadata(db, scope, row[2])
    if not data:
        return bool(isinstance(row[1], str) and row[1] and isinstance(row[2], str) and row[2] and isinstance(row[3], str) and row[3].strip() and isinstance(row[4], str) and row[4].strip() and isinstance(row[5], (int, float)))
    sources = ("'autonomous'",) if data.get('round_kind') == 'autonomous_activation' else ("'autonomous'", "'owner_contact'", "'scheduled_contact'")
    owner = db.execute(f"SELECT scope FROM pipeline_events WHERE scope=? AND event=? AND source IN ({','.join(sources)})", (data.get('source_scope'), data['owner_work'])).fetchone()
    return bool(data.get('round_kind') in ('autonomous_activation','service_dialogue') and owner and owner[0] == data['source_scope'] and isinstance(row[5], (int, float)) and (data['round_kind']=='autonomous_activation' or bool(isinstance(row[3], str) and row[3].strip() and isinstance(row[4], str) and row[4].strip())))


def settled(db, scope, rows):
    for row in rows:
        data = metadata(db, scope, row[2])
        if data:
            sources = ("'autonomous'",) if data.get('round_kind') == 'autonomous_activation' else ("'autonomous'", "'owner_contact'", "'scheduled_contact'")
            owner = db.execute(f"SELECT status FROM pipeline_events WHERE scope=? AND event=? AND source IN ({','.join(sources)})", (data.get('source_scope'), data['owner_work'])).fetchone()
            if not owner or owner[0] in ('received','sending'):
                return False
    return True


def split_material(db, scope, rows):
    dialogue, activities = [], []
    for row in rows:
        data = metadata(db, scope, row['event'])
        if data:
            sources=('autonomous',) if data.get('round_kind')=='autonomous_activation' else _SERVICE_SOURCES
            owner=db.execute("SELECT scope FROM pipeline_events WHERE scope=? AND event=? AND source IN ("+','.join('?' for _ in sources)+")",(data.get('source_scope'),data.get('owner_work'),*sources)).fetchone()
            if data.get('round_kind') not in ('autonomous_activation','service_dialogue') or not owner:
                raise ValueError('semantic_activation_owner_missing')
            # Keep the actual evidence, but never call a service a human author.
            activities.append({'event': row['event'], 'kind': data['round_kind'],
                               'source_scope': data['source_scope'], 'material': row,
                               'meaning': '平台主动激活及真实活动材料；不是用户发言，生成文字不证明QQ发送。'})
        else:
            dialogue.append(row)
    return dialogue, activities
