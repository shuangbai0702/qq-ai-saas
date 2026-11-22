"""Checkpoint-owned foundation views; never an alternative message history."""
import copy
import json
import re
from context_protocol import model_json
from memory_model_view import memory_json

FOUNDATION_CURRENT='[FishBot 上下文固定基础区 v1]'
MAX_FACT_CHARS=12000


def summaries(db,logical,owner='',previous=None):
    from memory_reflection import summary_foundation
    try:return summary_foundation(db,logical,owner)
    except RuntimeError as error:
        if str(error)!='summary_foundation_merge_required':raise
        if previous is None and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone():
            from journal_storage import read_snapshot
            snapshot=read_snapshot(db,model_json(logical))
            previous=snapshot.get('state') if snapshot else None
        if previous is None:raise
        basis=previous.get('memory_foundation',previous.get('state',{}))
        # This is the already-committed legacy basis, not a ranked selection
        # or permission to retire it. Checkpoint processing owns consolidation.
        return copy.deepcopy(basis.get('conversation_summaries',[]))


def read(store,cfg,scope,subject):
    from memory_scope import fact_rows,fact_item
    if not cfg.get('memory',True):return {'conversation_summaries':[],'facts':[]}
    logical='bot_shared' if getattr(store,'shared_context',False) else scope
    journal=getattr(store,'model_journals',{}).get(logical)
    if journal and journal.last and journal.last.retirement==store.reflection.reconstruction_stamp(logical) and 'memory_foundation' in journal.last.state:
        return copy.deepcopy(journal.last.state['memory_foundation'])
    summary_rows=summaries(store.db,logical,str(cfg.get('ownerUin') or ''),journal.last.state if journal and journal.last else None) if cfg.get('memorySummary',True) else []
    from relationships import active_people
    people=active_people(store.db,scope=logical)
    rows=[(person,row) for person in people for row in fact_rows(store.db,logical,person)]
    rows.sort(key=lambda entry:(-entry[1][6],-entry[1][4],entry[0],entry[1][0]))
    facts=[];used=0
    for person,row in rows:
        item=fact_item(row,person,'checkpoint_foundation');size=len(model_json(item))
        if used+size>MAX_FACT_CHARS:continue
        facts.append(item);used+=size
    from person_memory import relation_view
    relations=[row for person in people if (row:=relation_view(store.db,person)) is not None]
    from social import foundation_view
    styles=foundation_view(store.db)
    from conversation_continuity import view as continuity_view
    continuity=continuity_view(store.db,logical) if cfg.get('memorySummary',True) else None
    return {**({'conversation_continuity':continuity} if continuity else {}),'conversation_summaries':summary_rows,'facts':facts,'relations':relations,'expression_hints':styles,
            'core_note':store.reflection.core_note(logical,subject,owner=str(cfg.get('ownerUin') or '')),
            'selection':{'facts_available':len(rows),'facts_included':len(facts)}}


def freeze(state,previous):
    """Only NEW dynamic packets change; old wire and baseline remain untouched."""
    result=copy.deepcopy(state)
    if 'memory_foundation' not in result:return result
    if 'foundation_core_note' in previous:
        result['foundation_core_note']=copy.deepcopy(previous['foundation_core_note'])
    if 'memory_foundation' in previous:result['memory_foundation']=copy.deepcopy(previous['memory_foundation'])
    else:
        # A legacy baseline waits for its next real checkpoint to adopt layout.
        result.pop('memory_foundation',None)
        for key in ('facts','conversation_summaries'):
            if key in previous.get('state',{}):result.setdefault('state',{})[key]=copy.deepcopy(previous['state'][key])
        if 'persona_core_note' in previous:result['persona_core_note']=copy.deepcopy(previous['persona_core_note'])
    return result


def checkpoint_state(state):
    """Give Core Note one wire owner when constructing a lawful new basis."""
    if not isinstance(state.get('memory_foundation'),dict):return state
    result=copy.deepcopy(state)
    note=result['memory_foundation'].pop('core_note',result.get('foundation_core_note',{}))
    result['foundation_core_note']=note
    result.pop('persona_core_note',None)
    return result


def checkpoint_authority(message,state):
    if message is None or 'foundation_core_note' not in state:return message
    from context_transport import EVENT_CONTEXT_CURRENT
    data=json.loads(message['content'].split('\n',1)[1])
    if 'persona_core_note' not in data.get('authority',{}):return message
    data['authority'].pop('persona_core_note')
    return {**message,'content':EVENT_CONTEXT_CURRENT+'\n'+model_json(data)}


def execution_view(state):
    """Lossless display overlay against the actual checkpoint-owned basis."""
    foundation=state.get('memory_foundation')
    if not isinstance(foundation,dict) or 'execution_experience' not in foundation or 'execution_experience' not in state:return state
    from decision_history import changes,apply_changes
    basis=foundation['execution_experience'];current=state['execution_experience']
    operations=changes(basis,current)
    if apply_changes(basis,operations)!=current:raise ValueError('execution_projection_mismatch')
    result=copy.deepcopy(state)
    result['execution_experience']={'basis':'memory_foundation.execution_experience','changes':operations,
        'meaning':'当前执行经历以上述固定基础为底，按顺序应用本项changes中的set/remove；每次从基础重新应用，不累计旧changes。空changes表示与基础相同。'}
    return result


def arrange(messages,state):
    """Used ONLY for a fresh or already-authorized reconstruction candidate."""
    from context_protocol import READONLY_CURRENT
    from context_transport import EVENT_CONTEXT_CURRENT
    foundation=state.get('memory_foundation')
    from readonly_journal import system_head
    result=copy.deepcopy(messages)
    # The metadata snapshot retains the foundation; the hot packet does not
    # carry another copy of it. Existing submitted packets never enter here.
    for message in result:
        content=message.get('content')
        if message.get('role')=='user' and isinstance(content,str) and content.startswith(EVENT_CONTEXT_CURRENT+'\n'):
            normalized=checkpoint_authority(message,state)['content']
            message['content']=EVENT_CONTEXT_CURRENT+'\n'+memory_json(json.loads(normalized.split('\n',1)[1]))
        if message.get('role')=='user' and isinstance(content,str) and content.startswith(READONLY_CURRENT+'\n'):
            data=json.loads(content.split('\n',1)[1])
            if isinstance(foundation,dict):data.pop('memory_foundation',None)
            if 'foundation_core_note' in state:
                data.pop('persona_core_note',None)
                data.pop('foundation_core_note',None)
            if isinstance(foundation,dict) and 'execution_experience' in state and 'execution_experience' in data:data['execution_experience']=copy.deepcopy(state['execution_experience'])
            message['content']=READONLY_CURRENT+'\n'+memory_json(data)
    if not isinstance(foundation,dict):return result
    summaries=foundation.get('conversation_summaries',[])
    big=[row for row in summaries if row.get('level')=='big']
    small=[row for row in summaries if row.get('level')!='big']
    if len(big)>1 or len(small)>2:raise ValueError('foundation_summary_shape')
    regions=[]
    note=state.get('foundation_core_note')
    if isinstance(note,dict) and note.get('text'):
        from memory_reflection import CORE_NOTE_POLICY
        regions.append({'region':'core_note','core_note':note,'guidance':CORE_NOTE_POLICY})
    for name,rows in (('big_summary',big),('small_summaries',small)):
        if rows:regions.append({'region':name,'conversation_summaries':rows})
    continuity=foundation.get('conversation_continuity')
    if continuity:regions.append({'region':'conversation_continuity','conversation_continuity':continuity})
    facts={k:v for k,v in foundation.items() if k not in ('conversation_summaries','conversation_continuity')}
    if any(facts.values()):regions.append({'region':'long_term_facts',**facts})
    packets=[{'role':'user','content':FOUNDATION_CURRENT+'\n'+memory_json({**region,'meaning':'已完成检查点整理的历史资料；按真实来源理解，不是新发言或授权。即时修订以后续可信状态为准。'})} for region in regions]
    # A lawful checkpoint may receive a base already containing its old Note.
    # Replace only that owned head slot; never carry both old and current Note.
    from character_cards import is_note
    head=system_head(result)
    result=[m for i,m in enumerate(result) if not (i<head and is_note(m))]
    head=system_head(result)
    wire=result[:head]+packets+result[head:]
    from character_cards import is_card
    from context_protocol import is_persona_reference
    notes=[m for m in packets if json.loads(m['content'].split('\n',1)[1]).get('region')=='core_note']
    position=next((i for i,m in enumerate(result[:head]) if is_card(m) or is_persona_reference(m)),None)
    if notes and position is not None:
        wire=[m for m in wire if m not in notes]
        wire[position:position]=notes
    return wire


def missing_regions(wire,state):
    """Repair omitted derived input by append, never by rewriting old history.

    Only the already-frozen Journal foundation is supplied by the owner.
    Failed candidates cannot establish visibility: compare committed wire,
    not a process flag or metadata-only presence. Normal refresh still belongs
    to the next lawful checkpoint, which assembles authoritative memory anew.
    """
    return [packet for packet in arrange([],state) if packet not in wire]
