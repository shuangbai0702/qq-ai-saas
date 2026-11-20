"""Authenticated web edits to the existing Core Note, never dynamic history."""
import copy
import hashlib
import json
import time


def fingerprint(note):
    return hashlib.sha256(json.dumps(note,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def note(db):
    row=db.execute('SELECT text,scopes,people,updated,source_verification FROM memory_core_notes WHERE slot=1').fetchone()
    return {'text':row[0],'source_scopes':json.loads(row[1]),'subjects':json.loads(row[2]),'updated':row[3],'source_verification':row[4]} if row else {'text':'','source_scopes':[],'subjects':[],'updated':0}


def edit(db,data):
    actor=data.get('actorId')
    if not isinstance(actor,str) or not actor:return {'error':'缺少真实用户操作来源','status':403}
    value=data.get('text')
    if not isinstance(value,str) or len(value)>300:return {'error':'核心记忆最多300字','status':400}
    before=note(db)
    if data.get('expectedVersion')!=fingerprint(before):return {'error':'核心记忆已变化，请刷新后重试','status':409}
    if value==before['text']:return {'ok':True,'unchanged':True}
    columns={r[1] for r in db.execute('PRAGMA table_info(memory_core_notes)')}
    if 'user_edit' not in columns:db.execute('ALTER TABLE memory_core_notes ADD COLUMN user_edit TEXT')
    previous=db.execute('SELECT user_edit FROM memory_core_notes WHERE slot=1').fetchone()
    proof=json.loads(previous[0]) if previous and previous[0] else {}
    origins=list(dict.fromkeys([*proof.get('origins',[]),fingerprint(before)]))
    updated=max(time.time(),before['updated']+0.000001)
    after={**before,'text':value,'updated':updated,'source_verification':'user_edited'}
    if not before['text'] and not before['source_scopes']:after['source_scopes']=['bot_shared']
    proof={'actorId':actor,'target':fingerprint(after),'origins':origins}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone():
        from journal_storage import read_snapshot
        from types import SimpleNamespace
        from readonly_journal import ContextPrefixViolation
        snapshot=read_snapshot(db,json.dumps('bot_shared'))
        if snapshot:
            try:project(SimpleNamespace(**snapshot),{'proof':proof,'note':after})
            except ContextPrefixViolation as error:return {'error':'当前核心记忆区域无法精确定位：'+str(error),'status':409}
    db.execute("INSERT INTO memory_core_notes(slot,text,scopes,people,sources,updated,schema_version,source_verification,user_edit) VALUES(1,?,?,?,'[]',?,2,'user_edited',?) ON CONFLICT(slot) DO UPDATE SET text=excluded.text,scopes=excluded.scopes,updated=excluded.updated,source_verification=excluded.source_verification,user_edit=excluded.user_edit",
        (value,json.dumps(after['source_scopes']),json.dumps(after['subjects']),updated,json.dumps(proof,separators=(',',':'))))
    return {'ok':True,'updatedRegion':'core_note','version':fingerprint(after)}


def read(db):
    if 'user_edit' not in {r[1] for r in db.execute('PRAGMA table_info(memory_core_notes)')}:return None
    row=db.execute('SELECT user_edit FROM memory_core_notes WHERE slot=1').fetchone()
    if not row or not row[0]:return None
    proof=json.loads(row[0]);current=note(db)
    if not proof.get('actorId') or proof.get('target')!=fingerprint(current):raise ValueError('web_core_note_provenance_mismatch')
    return {'proof':proof,'note':current}


def project(snapshot,change):
    if snapshot is None or change is None:return snapshot
    # The initialized empty shell has never submitted a prefix. Its first
    # ordinary request constructs the foundation from the authoritative row.
    if getattr(snapshot,'turns',None)==0 and not snapshot.base and not snapshot.wire:return snapshot
    from context_foundation import FOUNDATION_CURRENT
    from readonly_journal import ContextPrefixViolation
    current=change['note'];proof=change['proof']
    if not proof.get('actorId') or proof.get('target')!=fingerprint(current):raise ContextPrefixViolation('web_core_note_provenance_mismatch')
    state=copy.deepcopy(snapshot.state)
    previous=state.get('foundation_core_note')
    if previous is None:previous=state.get('memory_foundation',{}).get('core_note')
    if not isinstance(previous,dict):raise ContextPrefixViolation('web_core_note_layout_unrecognized')
    if previous==current:return snapshot
    if fingerprint(previous) not in proof.get('origins',[]):raise ContextPrefixViolation('web_core_note_origin_mismatch')
    def patch(messages,required):
        result=copy.deepcopy(messages);matches=[]
        for index,message in enumerate(result):
            content=message.get('content')
            if message.get('role')!='user' or not isinstance(content,str) or not content.startswith(FOUNDATION_CURRENT+'\n'):continue
            packet=json.loads(content.split('\n',1)[1])
            if packet.get('region')!='core_note':continue
            if packet.get('core_note')!=previous:raise ContextPrefixViolation('web_core_note_wire_mismatch')
            matches.append(index);packet['core_note']=copy.deepcopy(current)
            from memory_model_view import memory_json
            result[index]={**message,'content':FOUNDATION_CURRENT+'\n'+memory_json(packet)}
        if not matches and not previous.get('text'):
            from context_foundation import arrange
            from readonly_journal import system_head
            packets=arrange([],{'memory_foundation':{},'foundation_core_note':current})
            if packets:
                position=system_head(result);result[position:position]=packets
                matches.append(position)
        if len(matches)>1 or required and len(matches)!=1:raise ContextPrefixViolation('web_core_note_region_missing_or_ambiguous')
        return result
    base=patch(snapshot.base,False);wire=patch(snapshot.wire,True)
    if 'foundation_core_note' in state:state['foundation_core_note']=copy.deepcopy(current)
    if 'core_note' in state.get('memory_foundation',{}):state['memory_foundation']['core_note']=copy.deepcopy(current)
    result=copy.copy(snapshot);result.base=base;result.wire=wire;result.state=state
    return result
