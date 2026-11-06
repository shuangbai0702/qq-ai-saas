"""Disposable provider wire continuity, never a conversation or memory store.

Exact native-prefix extensions and proven completed group-round transitions
can reuse a successful request. Source history, identity, permissions and
summary coverage remain authoritative.
"""
import copy,json,time
from dataclasses import dataclass
from context_protocol import READONLY_CURRENT,model_json,source
from decision_history import changes,apply_changes,summaries,MAX_TURNS,MAX_APPEND_IDLE_SECONDS,history_usage,history_boundary

GUIDANCE='只读资料包首次提供完整对象；后续标为readonlyChanges的资料包是该对象的增量，按顺序应用[set,path,value]或[remove,path]，新值覆盖旧值，remove后不能恢复旧值。只有最新合并资料是当前状态；旧资料包是当时记录，不是新的用户发言、当前身份或权限。每次仍回应最新真实消息，工具结果以真实回执为准。'

@dataclass
class Prepared:
    serial:int
    stamp:object
    base:list
    wire:list
    state:dict
    turns:int
    at:float
    reason:str
    group_turn:object=None
    base_chars:int=0
    detail:str=''
    usage:object=None
    authority:object=None

def confirmed_group_extension(old,base,state,turn):
    """Bridge only the SDK's explicit per-round group history replacement.

    The existing reflection round proves completion; this journal owns only a
    disposable wire view. Missing provenance, changed authority or a retired
    round still require the original complete fallback.
    """
    if not old or not isinstance(turn,tuple) or len(turn)!=3 or not isinstance(old.group_turn,tuple) or len(old.group_turn)!=3:return None
    scope,event,subject=turn;old_scope,old_event,old_subject=old.group_turn
    if not isinstance(scope,str) or not scope.startswith('group:') or scope!=old_scope or event==old_event:return None
    users=[(i,source(m)) for i,m in enumerate(base) if m.get('role')=='user']
    prior=[(i,source(m)) for i,m in enumerate(old.base) if m.get('role')=='user']
    if len(users)!=1 or len(prior)!=1:return None
    index,origin=users[0];old_index,old_origin=prior[0]
    if index!=len(base)-1 or not origin or not old_origin:return None
    for value,expected in ((origin,turn),(old_origin,old.group_turn)):
        if (value.get('scope'),str(value.get('event_id')),str(value.get('sender_id')))!=expected:return None
    if base[:index]!=old.base[:old_index]:return None
    rounds=state.get('group_round_history')
    if not isinstance(rounds,list):return None
    for row in rounds:
        if not isinstance(row,dict) or row.get('source_scope')!=scope or str(row.get('round_event'))!=old_event:continue
        reply=row.get('reply')
        if not isinstance(reply,dict) or reply.get('role')!='self' or str(reply.get('recipient_id'))!=old_subject or not isinstance(reply.get('text'),str) or not reply['text'].strip():continue
        messages=row.get('messages')
        if not isinstance(messages,list):continue
        if any(isinstance(m,dict) and (m.get('source_scope'),str(m.get('source_event')),str(m.get('sender_id')))==old.group_turn for m in messages):
            return base[index:]
    return None

class ReadonlyJournal:
    def __init__(self):self.serial=0;self.last=None

    def prepare(self,messages,stamp,now=None,group_turn=None):
        from context_transport import READONLY_FIELDS,AUTHORITY_CURRENT
        now=time.monotonic() if now is None else now
        found=[]
        for i,m in enumerate(messages):
            if not isinstance(m,dict):return None
            content=m.get('content') if isinstance(m,dict) else None
            if m.get('role')!='user' or not isinstance(content,str) or not content.startswith(READONLY_CURRENT+'\n'):continue
            try:state=json.loads(content.split('\n',1)[1])
            except ValueError:continue
            if isinstance(state,dict) and state and set(state)<=READONLY_FIELDS:found.append((i,state))
        # Ambiguous/future packets keep the existing complete request.
        if len(found)!=1:return None
        index,state=found[0]
        authorities=[(i,m) for i,m in enumerate(messages) if m.get('role')=='system' and isinstance(m.get('content'),str) and m['content'].startswith(AUTHORITY_CURRENT+'\n')]
        if len(authorities)>1:return None
        authority=copy.deepcopy(authorities[0][1]) if authorities else None
        excluded={index}|{i for i,_ in authorities}
        base=copy.deepcopy([m for i,m in enumerate(messages) if i not in excluded]);old=self.last
        valid=bool(old and old.stamp==stamp and 0<=now-old.at<MAX_APPEND_IDLE_SECONDS
                   and old.turns<MAX_TURNS and summaries(old.state)==summaries(state)
                   and (old.authority is None)==(authority is None))
        suffix=base[len(old.base):] if valid and len(base)>=len(old.base) and base[:len(old.base)]==old.base else None
        if valid and suffix is None:suffix=confirmed_group_extension(old,base,state,group_turn)
        reuse=suffix is not None
        reason='' if reuse else ('initial' if old is None else 'binding_changed' if old.stamp!=stamp else
            'idle_boundary' if not 0<=now-old.at<MAX_APPEND_IDLE_SECONDS else 'turn_boundary' if old.turns>=MAX_TURNS else
            'summary_changed' if summaries(old.state)!=summaries(state) else 'authority_contract_changed' if (old.authority is None)!=(authority is None) else 'native_prefix_changed')
        if reuse:
            operations=changes(old.state,state)
            if apply_changes(old.state,operations)!=state:raise ValueError('readonly_projection_mismatch')
            wire=copy.deepcopy(old.wire)+suffix
            if authority!=old.authority:wire.append(authority)
            if operations:wire.append({'role':'user','content':READONLY_CURRENT+'\n'+model_json({'readonlyChanges':operations})})
        else:wire=copy.deepcopy(messages)
        usage=history_usage(wire);base_chars=old.base_chars if reuse else usage['textChars']
        detail=history_boundary(usage['textChars'],base_chars,usage['wireChars'])
        if detail:
            # Complete fallback: no image buffers or oversized transcript held.
            wire=copy.deepcopy(messages);reason='size_boundary';reuse=False
            usage=history_usage(wire);base_chars=usage['textChars']
        self.serial+=1
        turns=old.turns+sum(m.get('role')=='user' for m in suffix) if reuse else 1
        return Prepared(self.serial,stamp,base,wire,copy.deepcopy(state),turns,now,reason,group_turn,base_chars,detail,usage,authority)

    def commit(self,prepared):
        if prepared.serial!=self.serial:return False
        usage=history_usage(prepared.wire)
        if history_boundary(usage['textChars'],prepared.base_chars,usage['wireChars']):self.last=None;return False
        self.last=prepared;return True

    def discard(self,prepared):
        if prepared.serial==self.serial:self.last=None

    def abort_candidate(self,prepared):
        if prepared.serial!=self.serial:return False
        self.serial+=1
        return True

    def retire_committed(self,prepared,reason='invalidated'):
        if prepared.serial!=self.serial:return False
        self.serial+=1;self.last=None
        return True
