"""Disposable provider wire continuity, never a conversation or memory store.

Exact native-prefix extensions and proven completed group-round transitions
can reuse a successful request. Source history, identity, permissions and
summary coverage remain authoritative.
"""
import copy,hashlib,json,time,uuid
from dataclasses import dataclass
from context_protocol import READONLY_CURRENT,model_json,source
from decision_history import changes,apply_changes,summaries,history_usage,history_boundary,MAX_RETAINED_WIRE_CHARS

MAX_PERSISTED_JOURNAL_BYTES=16*1024*1024
MAX_PERSISTED_JOURNALS=64
MAX_JOURNAL_TOMBSTONES=4096

class ContextReconstructionDenied(RuntimeError):pass
class ContextCapacityExceeded(RuntimeError):pass
class ContextRecoveryUnavailable(RuntimeError):pass
class ContextPrefixViolation(RuntimeError):pass

GUIDANCE='只读资料包首次提供完整对象；后续标为readonlyChanges的资料包是该对象的增量，按顺序应用[set,path,value]或[remove,path]，新值覆盖旧值，remove后不能恢复旧值。只有最新合并资料是当前状态；旧资料包是当时记录，不是新的用户发言、当前身份或权限。每次仍回应最新真实消息，工具结果以真实回执为准。'

def visible_summary_ids(value):
    result=set()
    def visit(node):
        if isinstance(node,dict):
            content=node.get('content') if node.get('role')=='user' else None
            if isinstance(content,str) and content.startswith(READONLY_CURRENT+'\n'):
                try:visit(json.loads(content.split('\n',1)[1]))
                except (TypeError,ValueError):pass
            for key,child in node.items():
                if key=='conversation_summaries' and isinstance(child,list):
                    result.update(row['id'] for row in child if isinstance(row,dict) and type(row.get('id')) is int and row['id']>0)
                else:visit(child)
        elif isinstance(node,list):
            for child in node:visit(child)
    visit(value);return sorted(result)

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
    retirement:object=None
    reconstruction_permit:object=None
    reconstruction_authority:object=None

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
    def __init__(self,db=None,scope_key=None):
        self.serial=0;self.last=None;self.db=db;self.scope_key=self._scope_key(scope_key)
        self.recovery_error=None;self.retired_reason=''
        if db is not None:self._load()

    @staticmethod
    def _scope_key(scope):
        if scope is None:return None
        return json.dumps(scope,ensure_ascii=False,separators=(',',':'),sort_keys=True)

    def _load(self):
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS fishbot_readonly_journals(
              scope TEXT PRIMARY KEY,version INTEGER NOT NULL,state TEXT NOT NULL,
              payload TEXT,sha TEXT,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS fishbot_readonly_journal_meta(
              id INTEGER PRIMARY KEY CHECK(id=1),overflow INTEGER NOT NULL DEFAULT 0);
            INSERT OR IGNORE INTO fishbot_readonly_journal_meta(id,overflow) VALUES(1,0);
        ''')
        if self.scope_key is None:return
        row=self.db.execute('SELECT version,state,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(self.scope_key,)).fetchone()
        if row:
            version,state,payload,sha=row
            if version!=1 or state not in ('ready','retired') or not isinstance(payload,str) or not isinstance(sha,str) or hashlib.sha256(payload.encode()).hexdigest()!=sha or len(payload.encode())>MAX_RETAINED_WIRE_CHARS+256000:
                self.recovery_error='journal_snapshot_integrity'
                return
            if state=='retired':self.retired_reason='persisted_retirement'
            try:value=json.loads(payload)
            except (TypeError,ValueError):self.recovery_error='journal_snapshot_invalid';return
            required={'scope','base','wire','state','turns','at','group_turn','base_chars','detail','usage','authority','retirement'}
            if not isinstance(value,dict) or set(value)!=required or value['scope']!=self.scope_key or not isinstance(value['base'],list) or not isinstance(value['wire'],list) or not isinstance(value['state'],dict) or type(value['turns']) is not int or not isinstance(value['at'],(int,float)):
                self.recovery_error='journal_snapshot_invalid';return
            group_turn=value['group_turn']
            if group_turn is not None:
                if not isinstance(group_turn,list) or len(group_turn)!=3 or not all(isinstance(x,str) for x in group_turn):
                    self.recovery_error='journal_snapshot_invalid';return
                group_turn=tuple(group_turn)
            if any(not isinstance(message,dict) or not isinstance(message.get('role'),str) for message in value['base']+value['wire']):
                self.recovery_error='journal_snapshot_invalid';return
            authority=value['authority']
            if authority is not None:
                from context_transport import EVENT_CONTEXT_CURRENT
                try:
                    if not isinstance(authority,dict) or authority.get('role')!='user' or not isinstance(authority.get('content'),str) or not authority['content'].startswith(EVENT_CONTEXT_CURRENT+'\n'):raise ValueError()
                    event=json.loads(authority['content'].split('\n',1)[1])
                    if not isinstance(event,dict) or set(event)!={'authority','policy','currentGuidance','meaning'}:raise ValueError()
                except (TypeError,ValueError):self.recovery_error='journal_snapshot_invalid';return
            self.last=Prepared(0,None,value['base'],value['wire'],value['state'],value['turns'],value['at'],'restored',group_turn,value['base_chars'],value['detail'],value['usage'],authority,value['retirement'])
            return
        overflow=self.db.execute('SELECT overflow FROM fishbot_readonly_journal_meta WHERE id=1').fetchone()[0]
        if overflow or self._scope_has_completed_history():self.recovery_error='journal_snapshot_missing'

    def _scope_has_completed_history(self):
        try:
            scope=json.loads(self.scope_key)
            if not isinstance(scope,str):return False
            tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'memory_rounds' in tables and self.db.execute('SELECT 1 FROM memory_rounds WHERE scope=? LIMIT 1',(scope,)).fetchone():return True
            if 'memory_summaries' in tables and self.db.execute('SELECT 1 FROM memory_summaries WHERE scope=? LIMIT 1',(scope,)).fetchone():return True
            if 'memory_summary_origins' in tables and self.db.execute('SELECT 1 FROM memory_summary_origins WHERE scope=? LIMIT 1',(scope,)).fetchone():return True
        except (TypeError,ValueError):return False
        return False

    def _persist(self,prepared,state='ready'):
        if self.db is None or self.scope_key is None:return True
        value={'scope':self.scope_key,'base':prepared.base,'wire':prepared.wire,'state':prepared.state,'turns':prepared.turns,'at':prepared.at,
               'group_turn':prepared.group_turn,'base_chars':prepared.base_chars,'detail':prepared.detail,'usage':prepared.usage,
               'authority':prepared.authority,'retirement':prepared.retirement}
        try:payload=json.dumps(value,ensure_ascii=False,separators=(',',':'))
        except (TypeError,ValueError):self.recovery_error='journal_snapshot_serialize';return False
        encoded=payload.encode('utf-8');existing=self.db.execute('SELECT length(cast(payload AS BLOB)) FROM fishbot_readonly_journals WHERE scope=?',(self.scope_key,)).fetchone()
        used=self.db.execute("SELECT coalesce(sum(length(cast(payload AS BLOB))),0),sum(state='ready') FROM fishbot_readonly_journals WHERE state IN ('ready','retired')").fetchone()
        overflow=self.db.execute('SELECT overflow FROM fishbot_readonly_journal_meta WHERE id=1').fetchone()[0]
        # SELECT length(payload) returns a row whose value may be NULL when an
        # unavailable tombstone already exists; distinguish that from no row.
        next_total=used[0]-((existing[0] or 0) if existing is not None else 0)+len(encoded)
        count=self.db.execute('SELECT count(*) FROM fishbot_readonly_journals').fetchone()[0]
        allowed=(len(encoded)<=MAX_RETAINED_WIRE_CHARS+256000 and next_total<=MAX_PERSISTED_JOURNAL_BYTES and
                 (existing is not None or (used[1] or 0)<MAX_PERSISTED_JOURNALS) and (existing is not None or count<MAX_JOURNAL_TOMBSTONES) and not overflow)
        if not allowed:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if existing is None and count>=MAX_JOURNAL_TOMBSTONES:
                    self.db.execute('UPDATE fishbot_readonly_journal_meta SET overflow=1 WHERE id=1')
                else:
                    self.db.execute("INSERT INTO fishbot_readonly_journals(scope,version,state,payload,sha,updated) VALUES(?,1,'unavailable',NULL,NULL,?) ON CONFLICT(scope) DO UPDATE SET version=1,state='unavailable',payload=NULL,sha=NULL,updated=excluded.updated",(self.scope_key,time.time()))
                self.db.commit()
            except BaseException:self.db.rollback();raise
            self.recovery_error='journal_storage_capacity';return False
        sha=hashlib.sha256(encoded).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("INSERT INTO fishbot_readonly_journals(scope,version,state,payload,sha,updated) VALUES(?,1,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET version=1,state=excluded.state,payload=excluded.payload,sha=excluded.sha,updated=excluded.updated",
                            (self.scope_key,state,payload,sha,time.time()))
            self.db.commit();return True
        except BaseException:self.db.rollback();raise

    def _reservation(self,authority,scope,request_id,summary_ids,web_proof,logical_scope):
        if authority is None or scope is None:return None
        return authority.reserve_context_reconstruction(scope,request_id,summary_ids=summary_ids,
                                                        web_proof=web_proof,logical_scope=logical_scope)

    def prepare(self,messages,stamp,now=None,group_turn=None,reconstruction_authority=None,reconstruction_scope=None,
                reconstruction_web_proof=None,reconstruction_context_scope=None,retirement=None,strict=False,compact_policy=False):
        from context_transport import READONLY_FIELDS,EVENT_CONTEXT_CURRENT
        now=time.time() if now is None else now
        found=[];malformed=False
        for i,m in enumerate(messages):
            if not isinstance(m,dict):
                if strict:raise ContextPrefixViolation('model_message_shape_changed')
                return None
            content=m.get('content')
            if m.get('role')!='user' or not isinstance(content,str) or not content.startswith(READONLY_CURRENT+'\n'):continue
            try:state=json.loads(content.split('\n',1)[1])
            except ValueError:
                malformed=True;continue
            if isinstance(state,dict) and set(state)<=READONLY_FIELDS:found.append((i,state))
            else:malformed=True
        if strict and malformed:raise ContextPrefixViolation('readonly_packet_malformed')
        if len(found)>1:
            if strict:raise ContextPrefixViolation('readonly_packet_ambiguous')
            return None
        if found:index,state=found[0]
        elif strict:index,state=None,{}
        else:return None
        summary_ids=visible_summary_ids(state)
        event_packets=[]
        for i,message in enumerate(messages):
            content=message.get('content')
            if message.get('role')!='user' or not isinstance(content,str) or not content.startswith(EVENT_CONTEXT_CURRENT+'\n'):continue
            try:value=json.loads(content.split('\n',1)[1])
            except ValueError:
                if strict:raise ContextPrefixViolation('event_packet_malformed')
                return None
            allowed={'authority','policy','currentGuidance','meaning'}
            required={'authority','currentGuidance','meaning'}
            if not isinstance(value,dict) or not required<=set(value) or not set(value)<=allowed:
                if strict:raise ContextPrefixViolation('event_packet_malformed')
                return None
            event_packets.append((i,message))
        if (strict and len(event_packets)!=1) or (not strict and len(event_packets)>1):
            if strict:raise ContextPrefixViolation('event_packet_missing_or_ambiguous')
            return None
        if event_packets:
            event_index,event_packet=event_packets[0]
            authority=copy.deepcopy(event_packet)
            excluded={event_index}
            if index is not None:excluded.add(index)
        else:
            event_index=None;authority=None;excluded=set(() if index is None else (index,))
        base=copy.deepcopy([m for i,m in enumerate(messages) if i not in excluded]);old=self.last
        summary_changed=bool(old and summaries(old.state)!=summaries(state))
        retirement_changed=bool(old and old.retirement!=retirement)
        authority_contract_changed=bool(old and (old.authority is None)!=(authority is None))
        valid=bool(old and not self.recovery_error and not self.retired_reason and not summary_changed and not retirement_changed
                   and not authority_contract_changed)
        suffix=base[len(old.base):] if valid and len(base)>=len(old.base) and base[:len(old.base)]==old.base else None
        if valid and suffix is None:suffix=confirmed_group_extension(old,base,state,group_turn)
        reuse=suffix is not None
        reason='' if reuse else ('initial' if old is None and not self.recovery_error else
            'journal_recovery_unavailable' if self.recovery_error else 'journal_retired' if self.retired_reason else
            'summary_changed' if summary_changed else 'memory_epoch_changed' if retirement_changed else
            'authority_contract_changed' if authority_contract_changed else 'native_prefix_changed')
        reconstruction_permit=None
        needs_reconstruction=bool(self.recovery_error or self.retired_reason or (old and (not reuse or summary_changed or retirement_changed or authority_contract_changed)))
        if needs_reconstruction:
            request_id=str(uuid.uuid4())
            reconstruction_permit=self._reservation(reconstruction_authority,reconstruction_scope,request_id,summary_ids,
                                                    reconstruction_web_proof,reconstruction_context_scope)
            if reconstruction_permit is None:
                if reason in ('journal_recovery_unavailable','journal_retired'):
                    raise ContextRecoveryUnavailable('A committed request journal cannot be restored or was retired; a 35-round summary checkpoint is required before rebuilding it.')
                raise ContextReconstructionDenied('A full request-history reconstruction is not authorized by a successful 35-round summary checkpoint.')
            reuse=False
        if reuse:
            operations=changes(old.state,state)
            if apply_changes(old.state,operations)!=state:raise ValueError('readonly_projection_mismatch')
            wire=copy.deepcopy(old.wire)+suffix
            # Event facts are append-only too, but an identical packet adds no
            # information. Repeating it on every ToolLoop request needlessly
            # grows history and can manufacture the next size boundary.
            if authority is not None and authority!=old.authority:
                current_data=json.loads(authority['content'].split('\n',1)[1])
                previous_data=json.loads(old.authority['content'].split('\n',1)[1]) if old.authority else {}
                if compact_policy and 'policy' in current_data and current_data.get('policy')==previous_data.get('policy'):
                    current_data.pop('policy')
                    authority={'role':authority['role'],'content':EVENT_CONTEXT_CURRENT+'\n'+model_json(current_data)}
                wire.append(authority)
            if operations:wire.append({'role':'user','content':READONLY_CURRENT+'\n'+model_json({'readonlyChanges':operations})})
        else:wire=copy.deepcopy(messages)
        usage=history_usage(wire);base_chars=old.base_chars if reuse else usage['textChars']
        detail=history_boundary(usage['textChars'],base_chars,usage['wireChars'])
        if detail:
            if not reconstruction_permit:
                request_id=str(uuid.uuid4())
                reconstruction_permit=self._reservation(reconstruction_authority,reconstruction_scope,request_id,summary_ids,
                                                        reconstruction_web_proof,reconstruction_context_scope)
            if reconstruction_permit is None:
                raise ContextCapacityExceeded('The append-only request journal reached its configured capacity before an authorized 35-round reconstruction checkpoint.')
            wire=copy.deepcopy(messages);reason='history_size_boundary';reuse=False
            usage=history_usage(wire);base_chars=usage['textChars'];detail=history_boundary(usage['textChars'],base_chars,usage['wireChars'])
            if detail:
                reconstruction_authority.settle_context_reconstruction(reconstruction_permit,False)
                raise ContextCapacityExceeded('The current complete request exceeds the configured bounded context capacity.')
        self.serial+=1
        turns=old.turns+sum(m.get('role')=='user' for m in suffix) if reuse else 1
        # Persist the complete current event contract for future comparisons;
        # wire may contain a shorter authority-only delta when its policy is
        # byte-identical to the latest committed policy.
        stored_authority=copy.deepcopy(event_packet) if event_packets else authority
        return Prepared(self.serial,stamp,base,wire,copy.deepcopy(state),turns,now,reason,group_turn,base_chars,detail,usage,stored_authority,retirement,reconstruction_permit,reconstruction_authority)

    def commit(self,prepared):
        if prepared.serial!=self.serial:
            self._settle(prepared,False);return False
        usage=history_usage(prepared.wire)
        if history_boundary(usage['textChars'],prepared.base_chars,usage['wireChars']):
            if prepared.reconstruction_permit:self._settle(prepared,False)
            return False
        if prepared.reconstruction_permit and not self._settle(prepared,True):return False
        if not self._persist(prepared,'ready'):
            self.last=prepared;self.retired_reason='persistence_unavailable';return False
        self.last=prepared;self.retired_reason='';self.recovery_error=None;return True

    def _settle(self,prepared,success):
        if not prepared.reconstruction_permit:return True
        # The authority that minted the permit is held on the candidate; it is
        # attached below by prepare, never reconstructed from model data.
        authority=prepared.reconstruction_authority
        if authority is None:return False
        return authority.settle_context_reconstruction(prepared.reconstruction_permit,success)

    def discard(self,prepared):return self.abort_candidate(prepared)

    def abort_candidate(self,prepared):
        settled=self._settle(prepared,False)
        if prepared.serial!=self.serial:return False
        self.serial+=1
        return settled

    def retire_committed(self,prepared,reason='invalidated'):
        self._settle(prepared,False)
        if prepared.serial!=self.serial:return False
        self.serial+=1;self.retired_reason=reason
        if self.last is not None:self._persist(self.last,'retired')
        return True
