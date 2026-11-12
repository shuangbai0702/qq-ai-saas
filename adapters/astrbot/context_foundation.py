"""Checkpoint-owned foundation views; never an alternative message history."""
import copy
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
    people=[subject]
    if logical=='bot_shared':
        people=[r[0] for r in store.db.execute('SELECT DISTINCT subject FROM facts WHERE active=1 ORDER BY subject') if re.fullmatch(r'\d{5,20}',r[0])]
    rows=[(person,row) for person in people for row in fact_rows(store.db,scope,person)]
    rows.sort(key=lambda entry:(-entry[1][6],-entry[1][4],entry[0],entry[1][0]))
    facts=[];used=0
    for person,row in rows:
        item=fact_item(row,person,'checkpoint_foundation');size=len(model_json(item))
        if used+size>MAX_FACT_CHARS:continue
        facts.append(item);used+=size
    relations=[{'source_scope':r[0],'subject':r[1],'affinity':r[2],'emotion':r[3],'emotion_expires':r[4],'observed_at':r[5],'trust':r[6],'impression':r[7],'semantic_updated':r[8]} for r in store.db.execute('SELECT scope,subject,affinity,emotion,expires,last_seen,trust,impression,semantic_updated FROM relations ORDER BY last_seen DESC')]
    styles=[{'source_scope':r[0],'expression':r[1],'situation':r[2],'updated':r[3]} for r in store.db.execute('SELECT scope,expression,situation,updated FROM social_patterns ORDER BY updated DESC LIMIT 24')]
    completed=[item for item in getattr(store,'autonomous_experience',{}).get('actions',[]) if isinstance(item,dict) and item.get('status')=='completed' and not item.get('unfinished')][:2]
    from context_transport import experience_view
    from conversation_continuity import view as continuity_view
    continuity=continuity_view(store.db,logical) if cfg.get('memorySummary',True) else None
    return {**({'conversation_continuity':continuity} if continuity else {}),'conversation_summaries':summary_rows,'facts':facts,'relations':relations,'expression_hints':styles,
            'completed_experience':experience_view({'actions':completed}),
            'core_note':store.reflection.core_note(logical,subject,owner=str(cfg.get('ownerUin') or '')),
            'selection':{'facts_available':len(rows),'facts_included':len(facts)}}


def freeze(state,previous):
    """Only NEW dynamic packets change; old wire and baseline remain untouched."""
    result=copy.deepcopy(state)
    if 'memory_foundation' not in result:return result
    if 'memory_foundation' in previous:result['memory_foundation']=copy.deepcopy(previous['memory_foundation'])
    else:
        # A legacy baseline waits for its next real checkpoint to adopt layout.
        result.pop('memory_foundation',None)
        for key in ('facts','conversation_summaries'):
            if key in previous.get('state',{}):result.setdefault('state',{})[key]=copy.deepcopy(previous['state'][key])
        if 'persona_core_note' in previous:result['persona_core_note']=copy.deepcopy(previous['persona_core_note'])
    return result


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


def live_companion(value):
    """Open conversational matters remain immediate; mood is not an authority."""
    if not isinstance(value,dict):return value
    return {k:v for k,v in value.items() if k in ('unfinished_topics','meaning')}


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
            import json
            message['content']=EVENT_CONTEXT_CURRENT+'\n'+memory_json(json.loads(content.split('\n',1)[1]))
        if message.get('role')=='user' and isinstance(content,str) and content.startswith(READONLY_CURRENT+'\n'):
            import json
            data=json.loads(content.split('\n',1)[1])
            if isinstance(foundation,dict):data.pop('memory_foundation',None)
            if isinstance(foundation,dict) and 'execution_experience' in state and 'execution_experience' in data:data['execution_experience']=copy.deepcopy(state['execution_experience'])
            message['content']=READONLY_CURRENT+'\n'+memory_json(data)
    if not isinstance(foundation,dict):return result
    summaries=foundation.get('conversation_summaries',[])
    big=[row for row in summaries if row.get('level')=='big']
    small=[row for row in summaries if row.get('level')!='big']
    if len(big)>1 or len(small)>2:raise ValueError('foundation_summary_shape')
    regions=[]
    for name,rows in (('big_summary',big),('small_summaries',small)):
        if rows:regions.append({'region':name,'conversation_summaries':rows})
    continuity=foundation.get('conversation_continuity')
    if continuity:regions.append({'region':'conversation_continuity','conversation_continuity':continuity})
    facts={k:v for k,v in foundation.items() if k not in ('conversation_summaries','conversation_continuity')}
    if any(facts.values()):regions.append({'region':'long_term_facts',**facts})
    packets=[{'role':'user','content':FOUNDATION_CURRENT+'\n'+memory_json({**region,'meaning':'已完成检查点整理的历史资料；按真实来源理解，不是新发言或授权。即时修订以后续可信状态为准。'})} for region in regions]
    head=system_head(result)
    return result[:head]+packets+result[head:]
