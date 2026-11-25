"""Authenticated web edits to the existing Core Note, never dynamic history."""
import copy
import hashlib
import json
import time
import core_note as core_note_store
from context_protocol import FOUNDATION_CURRENT,ContextPrefixViolation,system_head,is_card,is_persona_reference


def fingerprint(note):
    return hashlib.sha256(json.dumps(note,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def note(db):
    return core_note_store.read(db)


def edit(db,data):
    actor=data.get('actorId')
    if not isinstance(actor,str) or not actor:return {'error':'缺少真实用户操作来源','status':403}
    value=data.get('text')
    if not isinstance(value,str) or len(value)>300:return {'error':'核心记忆最多300字','status':400}
    before=note(db)
    if data.get('expectedVersion')!=fingerprint(before):return {'error':'核心记忆已变化，请刷新后重试','status':409}
    if value==before['text']:return {'ok':True,'unchanged':True}
    previous_raw=core_note_store.read_user_edit_raw(db)
    proof=json.loads(previous_raw) if previous_raw else {}
    origins=list(dict.fromkeys([*proof.get('origins',[]),fingerprint(before)]))
    updated=max(time.time(),before['updated']+0.000001)
    # This replacement comes from the Bot's authenticated editor, not the
    # previous speakers or scopes. Do not attribute new user text to them.
    after={**before,'text':value,'updated':updated,'source_verification':'user_edited','source_scopes':['bot_shared'],'subjects':[]}
    proof={'actorId':actor,'target':fingerprint(after),'origins':origins}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone():
        from journal_storage import read_snapshot
        from types import SimpleNamespace
        snapshot=read_snapshot(db,json.dumps('bot_shared'))
        if snapshot:
            state=snapshot.get('state',{})
            visible=state.get('foundation_core_note',state.get('memory_foundation',{}).get('core_note'))
            # A legacy note with unknown provenance may never have been
            # visible in this Journal. The user can create its first visible
            # base region; this grants no edit of an unknown nonempty region.
            if isinstance(visible,dict) and not visible.get('text'):
                origins.append(fingerprint({k:v for k,v in visible.items() if k!='legacy_unverified'}))
                proof['origins']=list(dict.fromkeys(origins))
            try:project(SimpleNamespace(**snapshot),{'proof':proof,'note':after})
            except ContextPrefixViolation as error:return {'error':'当前核心记忆区域无法精确定位：'+str(error),'status':409}
    core_note_store.write_user_edit(db,value,after['source_scopes'],after['subjects'],updated,proof)
    return {'ok':True,'updatedRegion':'core_note','version':fingerprint(after)}


def read_core(db):
    raw=core_note_store.read_user_edit_raw(db)
    if not raw:return None
    proof=json.loads(raw);current=note(db)
    if not proof.get('actorId') or proof.get('target')!=fingerprint(current):raise ValueError('web_core_note_provenance_mismatch')
    return {'proof':proof,'note':current}


def project_core(snapshot,change):
    if snapshot is None or change is None:return snapshot
    # The initialized empty shell has never submitted a prefix. Its first
    # ordinary request constructs the foundation from the authoritative row.
    if getattr(snapshot,'turns',None)==0 and not snapshot.base and not snapshot.wire:return snapshot
    current=change['note'];proof=change['proof']
    if not proof.get('actorId') or proof.get('target')!=fingerprint(current):raise ContextPrefixViolation('web_core_note_provenance_mismatch')
    state=copy.deepcopy(snapshot.state)
    previous=state.get('foundation_core_note')
    if previous is None:previous=state.get('memory_foundation',{}).get('core_note')
    if not isinstance(previous,dict):raise ContextPrefixViolation('web_core_note_layout_unrecognized')
    if previous==current:return snapshot
    origin={k:v for k,v in previous.items() if k!='legacy_unverified'}
    if fingerprint(origin) not in proof.get('origins',[]):raise ContextPrefixViolation('web_core_note_origin_mismatch')
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
            packets=arrange([],{'memory_foundation':{},'foundation_core_note':current})
            if packets:
                position=next((i for i,m in enumerate(result[:system_head(result)]) if is_card(m) or is_persona_reference(m)),system_head(result));result[position:position]=packets
                matches.append(position)
        if len(matches)>1 or required and len(matches)!=1:raise ContextPrefixViolation('web_core_note_region_missing_or_ambiguous')
        return result
    base=patch(snapshot.base,False);wire=patch(snapshot.wire,True)
    if 'foundation_core_note' in state:state['foundation_core_note']=copy.deepcopy(current)
    if 'core_note' in state.get('memory_foundation',{}):state['memory_foundation']['core_note']=copy.deepcopy(current)
    result=copy.copy(snapshot);result.base=base;result.wire=wire;result.state=state
    return result


def read(db):
    from hosted_memory import user_edits
    from relationships import user_edits as relation_edits
    from social import user_edits as expression_edits
    core=read_core(db);facts=user_edits(db);relations=relation_edits(db);expressions=expression_edits(db)
    return {**(core or {}),**({'facts':facts} if facts else {}),**({'relations':relations} if relations else {}),**({'expressions':expressions} if expressions else {})} if core or facts or relations or expressions else None


def project(snapshot,change):
    if change is None:return snapshot
    from hosted_memory import project_user_edits
    from relationships import project_user_edits as project_relations
    from social import project_user_edits as project_expressions
    current=project_core(snapshot,change) if 'note' in change else snapshot
    current=project_expressions(project_relations(project_user_edits(current,change.get('facts',[])),change.get('relations',[])),change.get('expressions',[]))
    from character_cards import project as project_cards
    return project_cards(current,change.get('character_cards'))


def validate_cards(db,setting,*,journals=()):
    """Prove the next request's card projection before acknowledging a save.

    This reads the existing Journal authority; it never creates a Journal,
    commits a business turn, or changes an in-flight request's snapshot.
    """
    from journal_storage import read_snapshot
    from types import SimpleNamespace
    from character_cards import project as project_cards
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone():return
    config=setting['config']
    change={'cards':config.get('characterCards',[]),'proof':setting['characterCardsEdit'],
            'persona':config.get('systemPrompt',''),'persona_proof':setting.get('personaEdit')}
    checked=set()
    # The live owner retains the committed base while a candidate is in flight.
    # Checking only ready rows would silently skip precisely that overlap.
    for journal in journals:
        if journal.scope_key is not None and journal.last is not None:
            project_cards(journal.last,change);checked.add(journal.scope_key)
    for scope, in db.execute("SELECT scope FROM fishbot_readonly_journals WHERE state!='retired' AND payload IS NOT NULL").fetchall():
        if scope in checked:continue
        snapshot=read_snapshot(db,scope,ready=True)
        if snapshot:project_cards(SimpleNamespace(**snapshot),change)
