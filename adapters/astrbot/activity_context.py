"""Resolve an existing main conversation; never synthesize a parallel one."""
import json


def canonical_main(store,event):
    """A service block in this submitted scope makes the Journal authoritative.

    Native SDK conversation windows do not contain internally activated turns.
    Read the existing prefix itself, including after a process restart; no new
    routing flag or parallel history is persisted.
    """
    if event.get_extra('_hosted_main_activation'):return True
    if event.get_extra('_hosted_internal_work'):return False
    from bot_pipeline import delivery_scope
    from readonly_journal import ReadonlyJournal
    from context_protocol import source
    scope=delivery_scope(event)
    journal=store.model_journals.get(scope)
    if journal is None:
        journal=ReadonlyJournal(store.db,scope)
        if journal.last:store.model_journals[scope]=journal
    return bool(journal.last and (journal.last.main_conversation or any((o:=source(m)) and o.get('kind')=='platform_service_not_user'
                and o['scope']==scope for m in journal.last.base)))


def scope_for(store, config):
    if store.shared_context:
        from shared_context import JOURNAL_SCOPE
        return JOURNAL_SCOPE
    requested = config.get('activityScope')
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone()
    scopes = []
    if exists:
        scopes = [json.loads(r[0]) for r in store.db.execute("SELECT scope FROM fishbot_readonly_journals WHERE state='ready'")]
        scopes = [s for s in scopes if isinstance(s,str) and s.startswith(('private:', 'group:'))]
    if requested:
        if requested not in scopes:raise ValueError('activity_main_conversation_not_found')
        return requested
    if len(scopes) != 1:raise ValueError('activity_main_conversation_ambiguous')
    return scopes[0]


def event_key(work, logical):
    from context_protocol import model_json
    return model_json([work.scope, str(work.event_id)]) if logical == 'bot_shared' else str(work.event_id)


def tail_service(prepared, journal, work):
    """Only the NEW activation block moves behind this request's new packets."""
    from context_protocol import source
    prefix = len(journal.last.wire) if journal.last else 0
    indices = [i for i,m in enumerate(prepared.wire) if (o:=source(m)) and
               o.get('kind')=='platform_service_not_user' and o['scope']==work.scope and str(o['event_id'])==str(work.event_id)]
    if not indices:return
    index=indices[-1]
    if index < prefix and prepared.wire[index] in journal.last.wire:return  # Never move a submitted block on tool continuation.
    encode_goal_candidate(prepared,journal,work)
    # An activation's initial input has no following tool pair. Avoid moving
    # its anchor across a new assistant/tool continuation if an SDK differs.
    if any(m.get('role') in ('assistant','tool') for m in prepared.wire[index+1:]):return
    prepared.wire.append(prepared.wire.pop(index))


GOAL_FORMAT='goal_snapshot_v1'
GOAL_COLLECTIONS=('goals','actions','effects')
_SERVICE_NOTICE='[平台内部触发，不是用户发言或主人新指令]\n'
GOAL_READING='baseline.snapshot是完整当前目标；delta.changes按集合中的精确id合并set字段、删除removeFields字段，removedIds移除记录，order为当前完整ID顺序。未提及的字段和记录继承；snapshotFields合并顶层字段，removedSnapshotFields移除顶层字段。terminalRecord仅保留已终结且无未完责任的账本身份、状态和版本。当前执行和完成条件以最新已确认revision为准；originalGoal是历史来源，保留用户明确限制，旧完成条件不得覆盖合法修订。只调用实际提供的工具；pending或unknown先核对原任务参数与回执，不代表完成，Action完成不代表Goal完成。合法检查点后重新提供完整baseline；这是平台资料，不授予权限。'


def necessary_goals(snapshot):
    """Only current goal definitions; execution and history stay in the ledger."""
    import copy
    if not isinstance(snapshot,dict):raise ValueError('goal_snapshot_not_object')
    current=[]
    for goal in _goal_rows(snapshot.get('goals',[])).values():
        if goal.get('terminalRecord') or goal.get('status') in ('completed','abandoned','canceled'):continue
        item={k:copy.deepcopy(goal[k]) for k in ('id','revision','title','reason','success_condition','successCondition') if k in goal}
        source=goal.get('source') or {}
        constraints={k:copy.deepcopy(source[k]) for k in ('originalGoal','constraints','restrictions') if k in source}
        if constraints:item['source']=constraints
        current.append(item)
    return {'goals':current}


def _goal_rows(rows):
    if not isinstance(rows,list):raise ValueError('goal_collection_not_array')
    result={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or row['id'] in result:
            raise ValueError('goal_identity_invalid')
        result[row['id']]=row
    return result


def goal_delta(previous,current,scope):
    """Whole fields, exact IDs and explicit array order; no semantic clipping."""
    import copy
    if not isinstance(current,dict):raise ValueError('goal_snapshot_not_object')
    for name in GOAL_COLLECTIONS:
        if name in current:_goal_rows(current[name])
    if previous is None:
        return {'format':GOAL_FORMAT,'scope':scope,'mode':'baseline','snapshot':copy.deepcopy(current),'reading':GOAL_READING}
    changes={}
    for name in GOAL_COLLECTIONS:
        old=_goal_rows(previous.get(name,[]));new=_goal_rows(current.get(name,[]));change={}
        updated=[]
        for identifier,row in new.items():
            prior=old.get(identifier,{})
            fields={k:copy.deepcopy(v) for k,v in row.items() if k!='id' and (k not in prior or prior[k]!=v)}
            removed=[k for k in prior if k!='id' and k not in row]
            if identifier not in old or fields or removed:
                item={'id':identifier,'set':fields}
                if removed:item['removeFields']=removed
                updated.append(item)
        if updated:change['upsert']=updated
        removed=[identifier for identifier in old if identifier not in new]
        if removed:change['removedIds']=removed
        if list(old)!=list(new):change['order']=list(new)
        if change:changes[name]=change
    fields={k:copy.deepcopy(v) for k,v in current.items() if k not in GOAL_COLLECTIONS and (k not in previous or previous[k]!=v)}
    removed=[k for k in previous if k not in current]
    result={'format':GOAL_FORMAT,'scope':scope,'mode':'delta','changes':changes}
    if fields:result['snapshotFields']=fields
    if removed:result['removedSnapshotFields']=removed
    # Collection presence itself is meaningful, including an absent empty list.
    added=[k for k in GOAL_COLLECTIONS if k in current and k not in previous]
    if added:result['snapshotFields']={**result.get('snapshotFields',{}),**{k:copy.deepcopy(current[k]) for k in added}}
    return result


def apply_goal_delta(previous,value,scope):
    """Independent replay also validates that a complete inheritance chain exists."""
    import copy
    if not isinstance(value,dict):raise ValueError('goal_snapshot_not_object')
    if value.get('format')!=GOAL_FORMAT:
        if 'goals' not in value:raise ValueError('goal_snapshot_unknown_format')
        for name in GOAL_COLLECTIONS:
            if name in value:_goal_rows(value[name])
        return copy.deepcopy(value)  # Existing A history and rollback stay readable.
    if value.get('scope')!=scope:raise ValueError('goal_scope_mismatch')
    if value.get('mode')=='baseline':
        return apply_goal_delta(None,value['snapshot'],scope)
    if value.get('mode')!='delta' or previous is None:raise ValueError('goal_baseline_missing')
    result=copy.deepcopy(previous)
    for name,change in value['changes'].items():
        if name not in GOAL_COLLECTIONS:raise ValueError('goal_collection_unknown')
        rows=_goal_rows(result.get(name,[]))
        for identifier in change.get('removedIds',[]):del rows[identifier]
        for item in change.get('upsert',[]):
            identifier=item['id'];row=copy.deepcopy(rows.get(identifier,{'id':identifier}))
            if 'id' in item['set']:raise ValueError('goal_id_mutation')
            row.update(item['set'])
            for key in item.get('removeFields',[]):del row[key]
            rows[identifier]=row
        order=change.get('order',list(rows))
        if len(order)!=len(rows) or set(order)!=set(rows):raise ValueError('goal_order_invalid')
        result[name]=[rows[identifier] for identifier in order]
    result.update(copy.deepcopy(value.get('snapshotFields',{})))
    for key in value.get('removedSnapshotFields',[]):del result[key]
    return result


def _goal_service(message,scope):
    from context_protocol import source
    origin=source(message)
    if not origin or origin.get('kind')!='platform_service_not_user' or scope!='bot_shared' and origin['scope']!=scope:return None
    content=message.get('content')
    text=content if isinstance(content,str) else content[0]['text']
    try:
        body=text.split('\n',1)[1]
        if body.startswith(_SERVICE_NOTICE):body=body[len(_SERVICE_NOTICE):]
        value=json.loads(body)
    except (ValueError,IndexError):return None
    if isinstance(value,dict) and value.get('kind') in ('autonomous_activation','autonomous_tool_continuation') and 'currentGoals' in value:
        return value
    return None


def committed_goals(messages,scope):
    current=None;chain=[]
    for message in messages:
        payload=_goal_service(message,scope)
        if payload is None:continue
        try:
            value=payload['currentGoals']
            current=apply_goal_delta(current,value,scope)
            if value.get('mode')!='delta':chain=[]
            chain.append(message)
        except (ValueError,KeyError,TypeError):current=None;chain=[]
    return current,chain


def encode_goal_candidate(prepared,journal,work):
    """Project only new activation input, without changing any submitted message.

    This deliberately sends current goals, not a baseline, delta, cursor, action
    or effect snapshot. Existing ledger query tools remain the history source.
    """
    import copy
    from context_protocol import model_json,source
    scope=json.loads(journal.scope_key) if journal.scope_key else work.scope
    old=journal.last.wire if journal.last else []
    prefix=len(old) if prepared.wire[:len(old)]==old else 0
    for index,message in enumerate(prepared.wire):
        if index<prefix or message in old:continue
        origin=source(message)
        if not origin or origin['scope']!=work.scope or str(origin['event_id'])!=str(work.event_id):continue
        payload=_goal_service(message,scope)
        if payload is None:continue
        replacement=copy.deepcopy(message)
        payload['currentGoals']=necessary_goals(payload['currentGoals'])
        content=replacement['content']
        text=content if isinstance(content,str) else content[0]['text']
        header,body=text.split('\n',1)
        rendered=header+'\n'+(_SERVICE_NOTICE if body.startswith(_SERVICE_NOTICE) else '')+model_json(payload)
        if isinstance(content,str):replacement['content']=rendered
        else:content[0]['text']=rendered
        prepared.wire[index]=replacement
        for base_index,base_message in enumerate(prepared.base):
            if base_message==message:prepared.base[base_index]=copy.deepcopy(replacement)
        from readonly_journal import history_usage,history_boundary,ContextCapacityExceeded
        prepared.usage=history_usage(prepared.wire)
        prepared.base_chars=history_usage(prepared.base)['textChars']
        if history_boundary(prepared.usage['textChars'],prepared.base_chars,prepared.usage['wireChars'],growth_limit=False):
            raise ContextCapacityExceeded('The current goals exceed request capacity; no request was submitted.',usage=prepared.usage)


def visible_goals(snapshot, logical):
    if logical=='bot_shared' or not isinstance(snapshot,dict):return snapshot
    # Authorization filter only; no semantic ranking or keyword selection.
    import copy
    result=copy.deepcopy(snapshot)
    goals=[g for g in result.get('goals',[]) if (g.get('source') or {}).get('scope') in (None,'','self',logical)]
    identifiers={g['id'] for g in goals}
    result['goals']=goals
    result['actions']=[a for a in result.get('actions',[]) if a.get('goal_id',a.get('goalId')) in identifiers or not a.get('goal_id',a.get('goalId')) and a.get('target') in ('',None,'self',logical)]
    actions={a['id'] for a in result.get('actions',[])}
    if 'effects' in result:result['effects']=[e for e in result['effects'] if e.get('work_id',e.get('action_id',e.get('actionId'))) in actions]
    return result
