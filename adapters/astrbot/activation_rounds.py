"""Typed activation provenance in the existing round/Work ledgers.

No transcript or second counter. The provenance retires with its round.
"""
import json
import time


def metadata(db, scope, event):
    row = db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='activation'", (scope, str(event))).fetchone()
    return json.loads(row[0]) if row else None


def annotate_service(db, scope, event, work, *, source_scope):
    owner=db.execute("SELECT scope FROM pipeline_events WHERE event=? AND source='autonomous'",(work,)).fetchone()
    if not owner or owner[0]!=source_scope:raise ValueError('service_dialogue_owner_missing')
    data={'round_kind':'service_dialogue','owner_work':work,'source_scope':source_scope}
    previous=metadata(db,scope,event)
    if previous is not None and previous!=data:raise ValueError('service_dialogue_owner_changed')
    if previous is None and db.execute('SELECT 1 FROM memory_rounds WHERE scope=? AND event=?',(scope,str(event))).fetchone():raise ValueError('service_dialogue_event_collision')
    db.execute("INSERT OR IGNORE INTO memory_message_sources VALUES(?,?,'activation',?)",(scope,str(event),json.dumps(data,separators=(',',':'))))


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
    db.execute("INSERT OR IGNORE INTO memory_message_sources VALUES(?,?,'activation',?)", (scope, str(event), json.dumps(data, separators=(',', ':'))))
    db.execute('INSERT OR IGNORE INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)', (scope, subject, str(event), '', '', time.time() if at is None else at))


def valid(db, scope, row):
    data = metadata(db, scope, row[2])
    if not data:
        return bool(isinstance(row[1], str) and row[1] and isinstance(row[2], str) and row[2] and isinstance(row[3], str) and row[3].strip() and isinstance(row[4], str) and row[4].strip() and isinstance(row[5], (int, float)))
    owner = db.execute("SELECT scope FROM pipeline_events WHERE event=? AND source='autonomous'", (data['owner_work'],)).fetchone()
    return bool(data.get('round_kind') in ('autonomous_activation','service_dialogue') and owner and owner[0] == data['source_scope'] and isinstance(row[5], (int, float)) and (data['round_kind']=='autonomous_activation' or bool(row[3].strip() and row[4].strip())))


def settled(db, scope, rows):
    for row in rows:
        data = metadata(db, scope, row[2])
        if data:
            owner = db.execute("SELECT status FROM pipeline_events WHERE event=? AND source='autonomous'", (data['owner_work'],)).fetchone()
            if not owner or owner[0] in ('received','sending'):
                return False
    return True


def split_material(db, scope, rows):
    dialogue, activities = [], []
    for row in rows:
        data = metadata(db, scope, row['event'])
        if data:
            # Keep the actual evidence, but never call a service a human author.
            activities.append({'event': row['event'], 'kind': data['round_kind'],
                               'source_scope': data['source_scope'], 'material': row,
                               'meaning': '平台主动激活及真实活动材料；不是用户发言，生成文字不证明QQ发送。'})
        else:
            dialogue.append(row)
    return dialogue, activities
