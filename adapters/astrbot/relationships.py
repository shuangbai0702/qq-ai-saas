"""One authoritative relationship per real person in this bot's database.

scope is the latest cognitive source, never part of identity. Observation and
semantic writers share this table; event provenance stays in its existing owner.
"""
import sqlite3
from pathlib import Path
from contextlib import closing

FIELDS='scope,subject,affinity,emotion,expires,last_seen,trust,impression,semantic_updated'
SCHEMA='relations(scope TEXT,subject TEXT PRIMARY KEY NOT NULL,affinity INTEGER,emotion TEXT,expires REAL,last_seen REAL,trust REAL NOT NULL DEFAULT 0,impression TEXT NOT NULL DEFAULT \'\',semantic_updated REAL NOT NULL DEFAULT 0)'


def migrate(db):
    """Atomic one-time merge. Preserve the old table in a rollback artifact only."""
    info=db.execute('PRAGMA table_info(relations)').fetchall()
    if [r[1] for r in info if r[5]]==['subject']:
        if 'last_message_at' not in {r[1] for r in info}:
            db.execute('ALTER TABLE relations ADD COLUMN last_message_at REAL')
            db.execute('ALTER TABLE relations ADD COLUMN last_message_scope TEXT')
            recover_activity(db)
        elif 'last_message_scope' not in {r[1] for r in info}:db.execute('ALTER TABLE relations ADD COLUMN last_message_scope TEXT')
        return None
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        rows=db.execute('SELECT '+FIELDS+' FROM relations').fetchall()
        people={}
        for row in rows:people.setdefault(row[1],[]).append(row)
        # Latest semantic cognition wins as a whole (no stitched impressions).
        # Ties use observed recency then source name, explicitly not a fabricated
        # semantic chronology. The original rows are available for rollback.
        merged=[];ties=0
        for person,group in sorted(people.items()):
            latest=max(float(r[8] or 0) for r in group)
            top=[r for r in group if float(r[8] or 0)==latest]
            ties+=len({(r[2],r[6],r[7]) for r in top})>1
            winner=list(max(top,key=lambda r:(float(r[5] or 0),str(r[0]))))
            winner[5]=max(float(r[5] or 0) for r in group)
            merged.append(winner)
        path=db.execute('PRAGMA database_list').fetchone()[2]
        if path:
            backup=Path(path+'.relations-before-global.sqlite')
            if backup.exists():
                with closing(sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True)) as saved:
                    if sorted(saved.execute('SELECT '+FIELDS+' FROM relations').fetchall())!=sorted(rows):raise RuntimeError('relationship_migration_backup_changed')
            else:
                with closing(sqlite3.connect(backup)) as saved:
                    saved.execute('CREATE TABLE relations(scope TEXT,subject TEXT,affinity INTEGER,emotion TEXT,expires REAL,last_seen REAL,trust REAL,impression TEXT,semantic_updated REAL,PRIMARY KEY(scope,subject))')
                    saved.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?,?)',rows)
                    saved.commit()
                    if saved.execute('SELECT count(*) FROM relations').fetchone()[0]!=len(rows):raise RuntimeError('relationship_backup_incomplete')
        db.execute('ALTER TABLE relations RENAME TO relations_migrating')
        db.execute('CREATE TABLE '+SCHEMA)
        db.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?,?)',merged)
        if db.execute('SELECT count(*) FROM relations').fetchone()[0]!=len(people):raise RuntimeError('relationship_migration_incomplete')
        db.execute('DROP TABLE relations_migrating')
        db.execute('ALTER TABLE relations ADD COLUMN last_message_at REAL')
        db.execute('ALTER TABLE relations ADD COLUMN last_message_scope TEXT')
        recover_activity(db)
        db.commit()
        return {'before':len(rows),'after':len(merged),'semantic_ties':ties}
    except BaseException:
        db.rollback();raise


def state(db,person):
    row=db.execute('SELECT '+FIELDS+' FROM relations WHERE subject=?',(str(person),)).fetchone()
    return dict(zip(FIELDS.split(','),row)) if row else None


def selected(db,people):
    """Explicit subjects only, never all rows merely sharing an event scope."""
    result=[]
    for person in dict.fromkeys(str(x) for x in people):
        row=state(db,person)
        if row:result.append({'source_scope':row['scope'],**{k:v for k,v in row.items() if k!='scope'}})
    return result


def latest_speakers(db,scope,current=(),limit=6,self_id=''):
    """Read live event identities; the six-person cap never filters messages."""
    import json
    from context_protocol import source,model_json
    from journal_storage import read_snapshot
    from message_provenance import incoming_item
    candidates=[]
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not self_id and 'contacts' in tables:
        own=db.execute('SELECT self_id FROM contacts WHERE scope=?',(scope,)).fetchone()
        if own:self_id=str(own[0])
    if 'fishbot_readonly_journals' in tables:
        snapshot=read_snapshot(db,model_json('bot_shared'))
        for message in (snapshot or {}).get('base',[]):
            origin=source(message)
            if origin and origin['scope']==scope:
                candidates.append({'source_scope':scope,'source_event':origin['event_id'],
                    'sender_id':origin['sender_id'],'at':origin.get('at')})
    if 'memory_pending' in tables:
        candidates.extend(incoming_item(db,scope,p,e,'',at) for p,e,at in db.execute(
            'SELECT subject,event,at FROM memory_pending WHERE scope=?',(scope,)))
    candidates.extend(current)
    events={}
    for row in candidates:
        if row.get('role','member') not in ('member','user') or row.get('source_scope',scope)!=scope:continue
        event=row.get('source_event');person=str(row.get('sender_id',''))
        if not event or not person.isdigit() or person==str(self_id):continue
        at=row.get('at')
        if 'pipeline_work_inputs' in tables:
            raw=db.execute('SELECT payload FROM pipeline_work_inputs WHERE scope=? AND event=?',(scope,str(event))).fetchone()
            if raw and raw[0]:
                payload=json.loads(raw[0]);stamp=payload.get('time')
                if str(payload.get('message_id'))==str(event) and str(payload.get('user_id'))==person and isinstance(stamp,(int,float)):at=stamp
        if not isinstance(at,(int,float)):continue
        key=str(event)
        if key in events and events[key][0]!=person:raise ValueError('relationship_speaker_identity_conflict')
        events[key]=(person,float(at),events[key][2] if key in events else len(events))
    people=[]
    for person,_,_ in sorted(events.values(),key=lambda r:(r[1],r[2]),reverse=True):
        if person not in people:people.append(person)
        if len(people)==limit:break
    return people


def observe_message(db,scope,person,at):
    """Authenticated protocol activity, independent of semantic revision."""
    import math,re
    if not re.fullmatch(r'(?:private|group):\d{4,20}',str(scope)) or not re.fullmatch(r'\d{5,20}',str(person)) or type(at) not in (int,float) or not math.isfinite(at) or at<=0:return
    db.execute("INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen,last_message_at,last_message_scope) VALUES(?,?,0,'平静',0,0,?,?) ON CONFLICT(subject) DO UPDATE SET last_message_scope=CASE WHEN excluded.last_message_at>coalesce(relations.last_message_at,0) THEN excluded.last_message_scope ELSE relations.last_message_scope END,last_message_at=max(coalesce(relations.last_message_at,0),excluded.last_message_at)",(scope,str(person),at,scope))


def active_people(db,limit=6,scope='bot_shared'):
    """No last_seen inference: only authenticated timestamps rank people."""
    import json,re
    from context_protocol import source
    from journal_storage import read_snapshot
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    people={}
    own={str(r[0]) for r in db.execute('SELECT DISTINCT self_id FROM contacts') if r[0]} if 'contacts' in tables else set()
    def add(person,at):
        import math,re
        person=str(person)
        if person in own or not re.fullmatch(r'\d{5,20}',person) or type(at) not in (int,float) or not math.isfinite(at) or at<=0:return
        people[person]=max(people.get(person,0),at)
    cols={r[1] for r in db.execute('PRAGMA table_info(relations)')}
    if 'last_message_at' in cols:
        for person,at,origin in db.execute('SELECT subject,last_message_at,last_message_scope FROM relations'):
            if scope=='bot_shared' or origin==scope:add(person,at)
    if 'pipeline_work_inputs' in tables:
        for channel,event,person,raw in db.execute('SELECT scope,event,subject,payload FROM pipeline_work_inputs WHERE payload IS NOT NULL'):
            if scope!='bot_shared' and channel!=scope:continue
            try:data=json.loads(raw)
            except (ValueError,TypeError):continue
            if str(data.get('message_id'))==str(event) and str(data.get('user_id'))==str(person):add(person,data.get('time'))
    if 'fishbot_readonly_journals' in tables:
        for key, in db.execute('SELECT scope FROM fishbot_readonly_journals').fetchall():
            snap=read_snapshot(db,key)
            for message in (snap or {}).get('base',[]):
                origin=source(message)
                if origin and origin.get('kind')!='platform_service_not_user' and re.fullmatch(r'(?:private|group):\d{4,20}',str(origin.get('scope'))) and (scope=='bot_shared' or origin.get('scope')==scope):add(origin.get('sender_id'),origin.get('at'))
    return sorted(people,key=lambda p:(people[p],p),reverse=True)[:limit]


def recover_activity(db):
    """One-time backfill from still-live authenticated material, never last_seen."""
    import json
    from context_protocol import source
    from journal_storage import read_snapshot
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'pipeline_work_inputs' in tables:
        for channel,event,person,raw in db.execute('SELECT scope,event,subject,payload FROM pipeline_work_inputs WHERE payload IS NOT NULL').fetchall():
            try:data=json.loads(raw)
            except (ValueError,TypeError):continue
            if str(data.get('message_id'))==str(event) and str(data.get('user_id'))==str(person):observe_message(db,channel,person,data.get('time'))
    if 'fishbot_readonly_journals' in tables:
        for key, in db.execute('SELECT scope FROM fishbot_readonly_journals').fetchall():
            snap=read_snapshot(db,key)
            for message in (snap or {}).get('base',[]):
                origin=source(message)
                if origin and origin.get('kind')!='platform_service_not_user':observe_message(db,origin.get('scope'),origin.get('sender_id'),origin.get('at'))


def reset_by_user(db,data):
    """Reset this person's relationship product without changing other products."""
    scope=data['scope'];tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    old=state(db,data['subject'])
    if not old or old['last_seen']!=data['expectedLastSeen'] or data.get('expectedVersion')!=user_version(old):return {'error':'关系状态已变化，请刷新后重试','status':409}
    from memory_operation_audit import forget as forget_operation_audit
    forget_operation_audit(db,scope,data['subject'],relations_only=True)
    db.execute('DELETE FROM relations WHERE subject=?',(data['subject'],))
    if 'relation_progress' in tables:
        db.execute('DELETE FROM relation_progress WHERE subject=?',(data['subject'],))
    if 'relation_evidence' in tables:
        db.execute('DELETE FROM relation_evidence WHERE subject=?',(data['subject'],))
    # Invalidate any in-flight reflection holding the pre-reset score.
    if 'memory_reflection_control' in tables:
        db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1')
    return {'ok':True}


def user_version(value):
    """Cognitive CAS excludes protocol activity timestamps and derived display."""
    import hashlib,json
    return hashlib.sha256(json.dumps({k:value.get(k) for k in FIELDS.split(',') if k!='last_seen'},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def edit_by_user(db,data):
    """Authenticated explicit edits preserve activity, events and other products."""
    import math,time
    if not isinstance(data.get('actorId'),str) or not data['actorId']:return {'error':'缺少真实用户操作来源','status':403}
    before=state(db,data['subject'])
    if not before or before['scope']!=data['scope'] or data.get('expectedVersion')!=user_version(before):return {'error':'人物关系已变化，请刷新后重试','status':409}
    affinity,trust,impression=data.get('affinity'),data.get('trust'),data.get('impression')
    if (type(affinity) not in (int,float) or not math.isfinite(affinity) or not -20<=affinity<=100
        or type(trust) not in (int,float) or not math.isfinite(trust) or not -1<=trust<=1
        or not isinstance(impression,str) or len(impression)>1000):return {'error':'人物关系内容格式不正确','status':400}
    now=max(time.time(),before['semantic_updated']+0.000001)
    db.execute('UPDATE relations SET affinity=?,trust=?,impression=?,semantic_updated=? WHERE subject=?',(affinity,trust,impression,now,data['subject']))
    return {'ok':True,'updatedRegion':'relation','version':user_version(state(db,data['subject']))}
