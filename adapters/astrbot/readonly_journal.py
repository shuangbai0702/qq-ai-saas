"""Authoritative submitted provider-wire continuity and crash recovery.

SDK windows supply new events, never authority to replace a committed prefix.
Source identity, permissions, protocol receipts and legal summary coverage keep
their own business authority; this Journal owns the exact submitted sequence.
"""
import copy,hashlib,json,os,time
import journal_storage
from dataclasses import dataclass,replace
from context_protocol import READONLY_CURRENT,model_json,source
from memory_model_view import memory_json
from decision_history import changes,apply_changes,summaries,history_usage,history_boundary,MAX_RETAINED_WIRE_CHARS,MAX_RETAINED_TEXT_CHARS

MAX_PERSISTED_JOURNAL_BYTES=16*1024*1024
MAX_CHECKPOINT_ARCHIVE_BYTES=64*1024*1024
MAX_PERSISTED_JOURNALS=64
MAX_JOURNAL_TOMBSTONES=4096

class ContextReconstructionDenied(RuntimeError):pass
class ContextCapacityExceeded(RuntimeError):
    """A rejected NEW candidate never authorizes mutation of submitted history."""
    def __init__(self,message,*,usage=None):
        self.capacity=capacity_evidence(usage) if usage else {}
        super().__init__(message+((' capacity='+model_json(self.capacity)) if self.capacity else ''))
class ContextRecoveryUnavailable(RuntimeError):pass
class ContextPrefixViolation(RuntimeError):pass


def capacity_evidence(usage):
    text=usage['textChars'];wire=usage['wireChars']
    ratio=max(text/MAX_RETAINED_TEXT_CHARS,wire/MAX_RETAINED_WIRE_CHARS)
    return {'state':'exhausted' if ratio>=1 else 'warning' if ratio>=0.8 else 'ready',
            'textChars':text,'wireChars':wire,
            'textLimit':MAX_RETAINED_TEXT_CHARS,'wireLimit':MAX_RETAINED_WIRE_CHARS,
            'textHeadroom':max(0,MAX_RETAINED_TEXT_CHARS-text),
            'wireHeadroom':max(0,MAX_RETAINED_WIRE_CHARS-wire),
            'recovery':'authorized_admin_force_compress',
            'historyRewriteAllowed':False}

# Pinned AstrBot utils/image_input.py adds this exact temporary TextPart to a
# new montage, but deliberately omits it from saved native conversation data.
# Compare the input views only; never remove it from already committed wire.
_SDK_MONTAGE_NOTICE='<system_notice>\nImages labeled as animation montages contain frames in reading order. Treat them as animations; do not mention the conversion or frame layout.\n</system_notice>'

def same_native_message(before,after):
    if before==after:return True
    if not isinstance(before,dict) or not isinstance(after,dict):return False
    if before.get('role')!='user' or source(before) is None or source(before)!=source(after):return False
    if {k:v for k,v in before.items() if k!='content'}!={k:v for k,v in after.items() if k!='content'}:return False
    left=before.get('content');right=after.get('content')
    if not isinstance(left,list) or not isinstance(right,list):return False
    # Pinned SDK message rehydration adds optional image_url.id=None.
    # Compare only that absent/null bookkeeping field equivalently; URLs,
    # media bytes, detail, other keys and order still have to match exactly.
    # The reuse branch sends old.wire unchanged, never these normalized views.
    def image_view(parts):
        result=[]
        for part in parts:
            if isinstance(part,dict) and part.get('type')=='image_url' and isinstance(part.get('image_url'),dict) and part['image_url'].get('id') is None:
                part={**part,'image_url':{k:v for k,v in part['image_url'].items() if k!='id'}}
            result.append(part)
        return result
    left=image_view(left);right=image_view(right)
    images=[p for p in left if isinstance(p,dict) and p.get('type')=='image_url']
    if not images or images!=[p for p in right if isinstance(p,dict) and p.get('type')=='image_url']:return False
    notice={'type':'text','text':_SDK_MONTAGE_NOTICE}
    return [p for p in left if p!=notice]==[p for p in right if p!=notice]

def same_native_prefix(before,after):
    return len(before)==len(after) and all(same_native_message(a,b) for a,b in zip(before,after))

def system_head(messages):
    head=0
    while head<len(messages) and isinstance(messages[head],dict) and messages[head].get('role') in ('system','developer'):head+=1
    return head

def semantic_retire_projection(messages,permit):
    """Remove exact batch member blocks; never infer membership from time."""
    if not permit or permit.get('semanticBatch') is not True:return copy.deepcopy(messages)
    from semantic_batch import event_key
    members=set(permit.get('batchMembers',[]));scope=permit.get('scope')
    if not isinstance(scope,str) or not members:raise ContextPrefixViolation('semantic_batch_members_missing')
    result=[];dropping=False
    for message in messages:
        if isinstance(message,dict) and message.get('role')=='user':
            origin=source(message)
            event=model_json([origin['scope'],str(origin['event_id'])]) if origin and scope=='bot_shared' else str(origin['event_id']) if origin else None
            dropping=bool(origin and (scope=='bot_shared' or origin['scope']==scope) and event_key(scope,event) in members)
        if not dropping or isinstance(message,dict) and message.get('role') in ('system','developer'):
            result.append(copy.deepcopy(message))
    return result

def tail_extension(old_base,new_messages):
    """Extend the committed non-System tail exactly; System bytes defer.

    A pending System, tool or serialization change must never strand already
    committed history. Until a licensed reconstruction swaps the baseline,
    requests continue on the committed System vector and append only real new
    tail messages. Comparison is positional after the committed System block,
    byte-exact over every remaining message.
    """
    if not old_base or not isinstance(new_messages,list):return None
    head=system_head(old_base)
    new_head=system_head(new_messages)
    if len(new_messages)<=new_head or len(new_messages)-new_head<len(old_base)-head:return None
    tail_old=old_base[head:];tail_new=new_messages[new_head:]
    if not same_native_prefix(tail_new[:len(tail_old)],tail_old):return None
    return tail_new[len(tail_old):]

GUIDANCE='只读资料包首次提供完整对象；后续标为readonlyChanges的资料包是该对象的增量，按顺序应用[set,path,value]或[remove,path]，新值覆盖旧值，remove后不能恢复旧值。只有最新合并资料是当前状态；旧资料包是当时记录，不是新的用户发言、当前身份或权限。每次仍回应最新真实消息，工具结果以真实回执为准。'

def reading_b_enabled():
    # Rollback affects only future packets. Committed B definitions and wire
    # remain visible and decodable, including when this switch is disabled.
    return os.environ.get('FISHBOT_CONTEXT_READING_B','1').lower() not in ('0','false','off')

def reading_pool(wire):
    """Require visible definitions/bases even after restart or write rollback."""
    from context_reading import PROTOCOL_MESSAGE,extract_pool,has_b_payload
    from context_transport import EVENT_CONTEXT_CURRENT
    defined=readonly_basis=event_basis=False
    for message in wire:
        if message==PROTOCOL_MESSAGE:defined=True;continue
        content=message.get('content')
        if message.get('role')!='user' or not isinstance(content,str):continue
        if content.startswith(READONLY_CURRENT+'\n'):
            packet=json.loads(content.split('\n',1)[1])
            root=('readonlyChanges' not in packet and not has_b_payload(packet)) or any(
                op[0]=='set' and op[1]==[] for op in packet.get('readonlyChanges',[]))
            if has_b_payload(packet) and (not defined or not (readonly_basis or root)):
                raise ValueError('reading_definition_or_baseline_missing')
            readonly_basis=readonly_basis or root
        elif content.startswith(EVENT_CONTEXT_CURRENT+'\n'):
            packet=json.loads(content.split('\n',1)[1])
            if 'eventChanges' in packet:
                if 'meaning' not in packet and (not defined or not event_basis):
                    raise ValueError('event_definition_or_baseline_missing')
            elif {'authority','currentGuidance','meaning'}<=set(packet):event_basis=True
    return extract_pool(wire)

def reading_baseline(messages,state):
    """Only a fresh/licensed baseline reaches here; never pass old.wire."""
    from context_foundation import arrange
    wire=arrange(messages,state)
    if not reading_b_enabled():return wire
    from context_reading import PROTOCOL_MESSAGE,encode_readonly,decode_readonly
    for i,message in enumerate(wire):
        content=message.get('content')
        if message.get('role')!='user' or not isinstance(content,str) or not content.startswith(READONLY_CURRENT+'\n'):continue
        data=json.loads(content.split('\n',1)[1])
        # The tested initial actions/time remain complete. Only candidate
        # definitions need a self-contained version pool in a new baseline.
        if not isinstance(data.get('social'),dict) or not isinstance(data['social'].get('meme_candidates'),list):continue
        packet=encode_readonly({},data,[['set',[],data]],{})
        if decode_readonly({},packet,{})!=data:raise ValueError('reading_baseline_mismatch')
        wire[i]={**message,'content':READONLY_CURRENT+'\n'+memory_json(packet)}
        wire.insert(i,copy.deepcopy(PROTOCOL_MESSAGE))
        break
    return wire

def visible_summary_ids(value):
    result=set()
    def visit(node):
        if isinstance(node,dict):
            content=node.get('content') if node.get('role')=='user' else None
            from context_foundation import FOUNDATION_CURRENT
            if isinstance(content,str) and content.startswith((READONLY_CURRENT+'\n',FOUNDATION_CURRENT+'\n')):
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
    validation_dependency:object=None
    # Bounded, content-free diagnostic: which committed-prefix-preserving
    # deferral applied to this candidate ('system_change' or
    # 'coverage_retirement'). Never persisted in the journal snapshot.
    retirement_deferred:str=''
    # R1 license binding: set when the stage transaction consumed this
    # candidate's reconstruction permit. A consumed permit is never settled
    # again, refunded or re-issued, whatever happens to the candidate after
    # the durable stage.
    reconstruction_consumed:bool=False
    # Local routing provenance, never a Provider field or another transcript.
    # Survives legal retirement of the service block that established it.
    main_conversation:bool=False

def confirmed_group_extension(old,base,state,turn,db=None):
    """Bridge only the SDK's explicit per-round group history replacement.

    The existing reflection round proves completion; this journal owns only a
    disposable wire view. Missing provenance, changed authority or a retired
    round still require the original complete fallback.
    """
    if not old or not isinstance(turn,tuple) or len(turn)!=3 or not isinstance(old.group_turn,tuple) or len(old.group_turn)!=3:return None
    scope,event,subject=turn[:3];old_scope,old_event,old_subject=old.group_turn[:3]
    if not isinstance(scope,str) or not scope.startswith('group:') or scope!=old_scope or event==old_event:return None
    service_sources=('scheduled_contact','owner_contact','tool_continuation')
    def service_work(value):
        if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_events'").fetchone():return False
        row=db.execute('SELECT source FROM pipeline_events WHERE scope=? AND event=? AND subject=?',value).fetchone()
        return bool(row and row[0] in service_sources)
    service=service_work(turn)
    old_service=service_work(old.group_turn)
    users=[(i,source(m)) for i,m in enumerate(base) if m.get('role')=='user']
    prior=[(i,source(m)) for i,m in enumerate(old.base) if m.get('role')=='user']
    if len(users)!=1 or not prior:return None
    index,origin=users[0];old_index,old_origin=prior[-1]
    if index!=len(base)-1:return None
    for value,expected,is_service in ((origin,turn,service),(old_origin,old.group_turn,old_service)):
        if is_service:
            # Existing ingress ledger proves the service origin; no authority
            # is inferred from model/user text or a synthetic sender label.
            if value is not None:return None
        elif not value or (value.get('scope'),str(value.get('event_id')),str(value.get('sender_id')))!=expected[:3]:return None
    if base[:index]!=old.base[:system_head(old.base)]:return None
    if old_service and db is not None:
        from message_provenance import get
        proof=get(db,scope,old_event,'outgoing')
        if proof.get('receipt')=='confirmed' and proof.get('complete') is True and proof.get('message_ids'):
            return base[index:]
        return None
    rounds=state.get('group_round_history')
    if not isinstance(rounds,list):return None
    for row in rounds:
        if not isinstance(row,dict) or row.get('source_scope')!=scope or str(row.get('round_event'))!=old_event:continue
        reply=row.get('reply')
        if not isinstance(reply,dict) or reply.get('role')!='self' or str(reply.get('recipient_id'))!=old_subject or not isinstance(reply.get('text'),str) or not reply['text'].strip():continue
        messages=row.get('messages')
        if not isinstance(messages,list):continue
        if any(isinstance(m,dict) and (m.get('source_scope'),str(m.get('source_event')),str(m.get('sender_id')))==old.group_turn[:3] for m in messages):
            return base[index:]
    return None

def confirmed_native_window_extension(old, base):
    """Retain wire in full when the SDK presents a shorter native window.

    Only an exact tail/prefix overlap containing a sourced event is evidence.
    Repeated prose or replaced media cannot prove it. System changes defer:
    the caller reuses committed wire, including its original System vector.
    This adapts the SDK view; it never truncates the committed wire.
    """
    if not old:return None
    prior=old.base[system_head(old.base):];current=base[system_head(base):]
    if any(m.get('role') in ('system','developer') for m in current):return None
    for size in range(min(len(prior),len(current)),0,-1):
        overlap=prior[-size:]
        if not same_native_prefix(overlap,current[:size]):continue
        origins=[source(m) for m in overlap if m.get('role')=='user']
        if not any(isinstance(o,dict) and o.get('scope') and o.get('event_id') is not None for o in origins):continue
        # A shorter match must not replay a known event later in the tail.
        known={(o['scope'],str(o['event_id'])) for m in prior if (o:=source(m))}
        if any((o['scope'],str(o['event_id'])) in known for m in current[size:] if (o:=source(m))):continue
        # The next item must start a new real event or preserve ToolLoop
        # adjacency through the exact overlapping assistant/tool messages.
        return current[size:]
    return None

def fresh_event_extension(old_base,new_messages):
    """Append only provably new tail turns when the native window has no
    overlap at all (a worker restart lost the in-memory SDK conversation).

    The committed journal stays authoritative. The tail must start at a
    sourced user event absent from the committed base, and no sourced event
    in the whole tail may already be known: one known event anywhere means
    the view partially overlaps history in an order that cannot be proven,
    which still requires a licensed reconstruction. Appending here adds
    real, protocol-identified messages the journal never committed; it
    never rewrites the committed prefix.
    """
    if not old_base or not isinstance(new_messages,list):return None
    known=set()
    for message in old_base:
        origin=source(message)
        if origin:known.add(str(origin['event_id']))
    tail=new_messages[system_head(new_messages):]
    if not tail or not isinstance(tail[0],dict):return None
    origin=source(tail[0])
    if not origin or str(origin['event_id']) in known:return None
    for message in tail[1:]:
        later=source(message) if isinstance(message,dict) else None
        if later and str(later['event_id']) in known:return None
    return tail

def uncommitted_window_head_extension(old,raw_base,db=None):
    """Adopt provably delivered rounds the committed journal never kept.

    A successful round whose journal candidate was aborted (for example by a
    summary dependency that changed mid-request) leaves its user turn and
    reply in the SDK conversation but nothing in the journal. Every later
    request then fails the native-window overlap forever and the scope
    deadlocks, because refused rounds can never earn the license a rebuild
    needs. When the window head consists of complete sourced rounds absent
    from the base and the per-event delivery ledger proves they were really
    sent and confirmed, they are appended once as real history; the first
    known event must still chain the rest of the window exactly onto the
    committed tail. The committed prefix stays byte-exact and no license,
    round count or retirement is implied.
    """
    if not old or not isinstance(raw_base,list) or db is None:return None
    known={str(source(m)['event_id']) for m in old.base if (s:=source(m))}
    tail=raw_base[system_head(raw_base):]
    try:tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception:return None
    if 'pipeline_events' not in tables:return None
    head=[];taken=0
    while taken<len(tail):
        message=tail[taken]
        if not isinstance(message,dict) or message.get('role')!='user':break
        origin=source(message)
        if not origin or str(origin['event_id']) in known:break
        end=next((j for j in range(taken+1,len(tail)) if isinstance(tail[j],dict) and tail[j].get('role')=='user'),len(tail))
        block=tail[taken:end]
        if not any(isinstance(m,dict) and m.get('role')=='assistant' and not m.get('tool_calls') for m in block):break
        row=db.execute("SELECT status,confirmed FROM pipeline_events WHERE scope=? AND event=?",(str(origin.get('scope')),str(origin['event_id']))).fetchone()
        if not row or row[0] not in ('sent','partial') or not row[1]:break
        head.extend(block);taken=end
    if not head:return None
    rest=tail[taken:]
    if not rest:return head
    prior=old.base[system_head(old.base):]
    if len(rest)>len(prior):return None
    if not same_native_prefix(prior[-len(rest):],rest):return None
    return head

def window_reload_extension(old,raw_base,db=None):
    """Align a reloaded SDK window without changing the committed wire.

    The SDK may omit unanswered user turns retained after an unknown model
    submission, and retain older history before the journal's baseline. A
    delivery status cannot prove that a model never received a turn. Omitted
    turns therefore stay in old.wire and old.base, including their exact media.
    Require ordered, unchanged shared source events and an exact native suffix
    ending at the retained baseline. Append only the genuinely new tail; never
    replay a known event or infer a completed round/reconstruction license.
    """
    if not old or not isinstance(raw_base,list):return None
    prior=old.base[system_head(old.base):]
    current=raw_base[system_head(raw_base):]
    if not current or not prior:return None
    def identity(message):
        o=source(message)
        return (o['scope'],str(o['sender_id']),str(o['event_id'])) if o else None
    known={}
    for i,m in enumerate(prior):
        key=identity(m)
        if key is not None:known.setdefault(key,[]).append(i)
    present=set();last=-1
    for m in current:
        key=identity(m)
        if key not in known:continue
        positions=known[key]
        if len(positions)!=1:return None
        index=positions[0]
        if index<=last or not same_native_message(prior[index],m):return None
        present.add(key);last=index
    if not present:return None
    keep=len(prior)
    while keep>0:
        m=prior[keep-1]
        if not (isinstance(m,dict) and m.get('role')=='user'):break
        key=identity(m)
        if key is None or key in present:break
        keep-=1
    if keep<=0:return None
    # Start at a sourced user, never at an arbitrary equal assistant/tool text.
    # Shared older events may precede this suffix after an SDK window reload;
    # the ordered identity check above prevents a known-event reorder.
    for begin in range(keep):
        if identity(prior[begin]) is None:continue
        target=prior[begin:keep]
        for start in range(0,len(current)-len(target)+1):
            if not same_native_prefix(target,current[start:start+len(target)]):continue
            tail=current[start+len(target):]
            if not tail or not any(identity(m) for m in tail):continue
            if any(identity(m) in known for m in tail):continue
            return tail
    return None

class ReadonlyJournal:
    def __init__(self,db=None,scope_key=None):
        self.serial=0;self.last=None;self.db=db;self.scope_key=self._scope_key(scope_key)
        self.recovery_error=None;self.retired_reason='';self._last_refusal_reason=''
        # Serial of the candidate currently persisted as the pre-submission
        # snapshot (A package). None when nothing is staged.
        self._staged=None
        self._staged_sha=None
        self._last_persist_error=None
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
            CREATE TABLE IF NOT EXISTS fishbot_readonly_journal_blobs(
              scope TEXT NOT NULL,sha TEXT NOT NULL,data BLOB NOT NULL,
              PRIMARY KEY(scope,sha));
        ''')
        if self.scope_key is None:return
        row=self.db.execute('SELECT version,state,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(self.scope_key,)).fetchone()
        if row:
            version,state,payload,sha=row
            if version not in (1,journal_storage.VERSION) or state not in ('ready','retired') or not isinstance(payload,str) or not isinstance(sha,str) or hashlib.sha256(payload.encode()).hexdigest()!=sha or len(payload.encode())>MAX_PERSISTED_JOURNAL_BYTES:
                self.recovery_error='journal_snapshot_integrity'
                return
            if state=='retired':self.retired_reason='persisted_retirement'
            try:
                value=journal_storage.read_snapshot_payload(self.db,self.scope_key,version,payload,sha)
            except (TypeError,ValueError,KeyError,IndexError):self.recovery_error='journal_snapshot_invalid';return
            required={'scope','base','wire','state','turns','at','group_turn','base_chars','detail','usage','authority','retirement'}
            if not isinstance(value,dict) or set(value)-{'main_conversation'}!=required or type(value.get('main_conversation',False)) is not bool or value['scope']!=self.scope_key or not isinstance(value['base'],list) or not isinstance(value['wire'],list) or not isinstance(value['state'],dict) or type(value['turns']) is not int or not isinstance(value['at'],(int,float)):
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
            try:reading_pool(value['wire'])
            except (TypeError,ValueError,KeyError,IndexError):
                self.recovery_error='journal_reading_definition_invalid';return
            self.last=Prepared(0,None,value['base'],value['wire'],value['state'],value['turns'],value['at'],'restored',group_turn,value['base_chars'],value['detail'],value['usage'],authority,value['retirement'])
            self.last.main_conversation=value.get('main_conversation',False)
            return
        overflow=self.db.execute('SELECT overflow FROM fishbot_readonly_journal_meta WHERE id=1').fetchone()[0]
        # An operator-enrolled legacy scope can establish exactly one new
        # baseline. Enrollment is explicit deployment work, never a restart
        # fallback and never inferred from an old summary or request body.
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cutover=None
        if 'fishbot_readonly_cutover' in tables:
            cutover=self.db.execute("SELECT state FROM fishbot_readonly_cutover WHERE scope=?",(self.scope_key,)).fetchone()
        if not overflow and cutover and cutover[0]=='available':return
        # A separately authorized operator closure is a NEW logical session,
        # never a 35-round reconstruction or reuse of an old enrollment.
        if not overflow and 'fishbot_operator_session_archives' in tables and self.db.execute("SELECT 1 FROM fishbot_operator_session_archives WHERE scope=? AND new_session_state='available'",(self.scope_key,)).fetchone():return
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

    def _snapshot_parts(self,prepared):
        value={'scope':self.scope_key,'base':prepared.base,'wire':prepared.wire,'state':prepared.state,'turns':prepared.turns,'at':prepared.at,
               'group_turn':prepared.group_turn,'base_chars':prepared.base_chars,'detail':prepared.detail,'usage':prepared.usage,
               'authority':prepared.authority,'retirement':prepared.retirement}
        if prepared.main_conversation:value['main_conversation']=True
        return journal_storage.encode(value)

    def _snapshot(self,prepared):return self._snapshot_parts(prepared)[0]

    def _persist(self,prepared,state='ready',settle_permit=False,consume_permit=False,tombstone=True,settle_cutover=True,model_origin=None,on_stage=None):
        if self.db is None or self.scope_key is None:return True
        try:payload,blobs=self._snapshot_parts(prepared)
        except (TypeError,ValueError):self.recovery_error='journal_snapshot_serialize';return False
        encoded=payload.encode('utf-8');sha=hashlib.sha256(encoded).hexdigest()
        # Capacity accounting and snapshot/permit commit share the same write
        # lock, including separate connections serving different scopes.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Blob and referencing snapshot share one transaction. Never GC:
            # older protected snapshots/operator archives may retain references.
            for digest,raw in blobs.items():
                row=self.db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(self.scope_key,digest)).fetchone()
                if row is not None and row[0]!=raw:raise ValueError('journal_blob_integrity')
            blob_total=self.db.execute('SELECT coalesce(sum(length(data)),0) FROM fishbot_readonly_journal_blobs').fetchone()[0]
            if consume_permit and prepared.reconstruction_permit.get('semanticBatch'):
                blob_total-=journal_storage.prune_unreferenced_blobs(self.db,self.scope_key,
                    replacement=(journal_storage.VERSION,payload),dry_run=True)
            new_blobs=[(digest,raw) for digest,raw in blobs.items() if not self.db.execute('SELECT 1 FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(self.scope_key,digest)).fetchone()]
            existing=self.db.execute('SELECT length(cast(payload AS BLOB)) FROM fishbot_readonly_journals WHERE scope=?',(self.scope_key,)).fetchone()
            used=self.db.execute("SELECT coalesce(sum(length(cast(payload AS BLOB))),0),sum(state='ready') FROM fishbot_readonly_journals WHERE state IN ('ready','retired')").fetchone()
            overflow=self.db.execute('SELECT overflow FROM fishbot_readonly_journal_meta WHERE id=1').fetchone()[0]
            # SELECT length(payload) returns a row whose value may be NULL when an
            # unavailable tombstone already exists; distinguish that from no row.
            next_total=used[0]-((existing[0] or 0) if existing is not None else 0)+len(encoded)+blob_total+sum(len(raw) for _,raw in new_blobs)
            archive_overflow=False;archive_required=False
            if consume_permit and prepared.reconstruction_permit.get('kind')=='summary_checkpoint' and not prepared.reconstruction_permit.get('semanticBatch'):
                has_exceptions=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_retirement_exceptions'").fetchone()
                if has_exceptions and self.last:
                    def event_ids(messages):
                        return {model_json([o['scope'],str(o['event_id'])]) for m in messages if (o:=source(m))}
                    retiring=event_ids(self.last.base)-event_ids(prepared.base)
                    archive_required=any(self.db.execute("SELECT 1 FROM memory_retirement_exceptions WHERE scope=? AND event=? AND checkpoint_id<=? AND state='offered'",(json.loads(self.scope_key),event,prepared.reconstruction_permit['id'])).fetchone() for event in retiring)
                has_archives=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_checkpoint_archives'").fetchone()
                archive_bytes=self.db.execute('SELECT coalesce(sum(length(cast(payload AS BLOB))),0) FROM memory_checkpoint_archives').fetchone()[0] if has_archives else 0
                archive_overflow=archive_required and archive_bytes+((existing[0] or 0) if existing else 0)>MAX_CHECKPOINT_ARCHIVE_BYTES
            count=self.db.execute('SELECT count(*) FROM fishbot_readonly_journals').fetchone()[0]
            # Existing snapshots may progress under their actual row/byte
            # limits. Preserve overflow as missing-scope recovery evidence;
            # clearing it could mistake lost history for a new conversation.
            # Account for UTF-8 snapshot bytes and all retained scoped blobs.
            # A wire-character limit is not its storage-byte limit.
            # The existing per-bot total still bounds every row and all scopes.
            allowed=(next_total<=MAX_PERSISTED_JOURNAL_BYTES and
                     (existing is not None or (used[1] or 0)<MAX_PERSISTED_JOURNALS) and (existing is not None or count<MAX_JOURNAL_TOMBSTONES) and not archive_overflow)
            if not allowed:
                if not tombstone:self.db.rollback()
                else:
                    if existing is None and count>=MAX_JOURNAL_TOMBSTONES:
                        self.db.execute('UPDATE fishbot_readonly_journal_meta SET overflow=1 WHERE id=1')
                    elif existing is not None and existing[0]:
                        pass # Preserve the last recoverable committed prefix.
                    else:
                        self.db.execute("INSERT INTO fishbot_readonly_journals(scope,version,state,payload,sha,updated) VALUES(?,1,'unavailable',NULL,NULL,?) ON CONFLICT(scope) DO UPDATE SET version=1,state='unavailable',payload=NULL,sha=NULL,updated=excluded.updated",(self.scope_key,time.time()))
                    self.db.commit()
                self.recovery_error='checkpoint_archive_capacity' if archive_overflow else 'journal_storage_capacity';self._last_persist_error=self.recovery_error;return False
            if archive_required:
                # Immutable recovery archive, not another active history. Blob
                # references keep their original bytes; no historical rewrite.
                # Only exceptional legacy retirement needs this full snapshot;
                # ordinary proven checkpoints must not accumulate another log.
                self.db.execute('''CREATE TABLE IF NOT EXISTS memory_checkpoint_archives(
                  checkpoint_id INTEGER PRIMARY KEY,scope TEXT NOT NULL,version INTEGER,
                  state TEXT,payload TEXT,sha TEXT,archived REAL NOT NULL)''')
                self.db.execute('''INSERT INTO memory_checkpoint_archives
                  SELECT ?,scope,version,state,payload,sha,? FROM fishbot_readonly_journals WHERE scope=?''',
                  (prepared.reconstruction_permit['id'],time.time(),self.scope_key))
            for digest,raw in new_blobs:
                self.db.execute('INSERT INTO fishbot_readonly_journal_blobs(scope,sha,data) VALUES(?,?,?)',(self.scope_key,digest,raw))
            self.db.execute("INSERT INTO fishbot_readonly_journals(scope,version,state,payload,sha,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET version=excluded.version,state=excluded.state,payload=excluded.payload,sha=excluded.sha,updated=excluded.updated",
                            (self.scope_key,journal_storage.VERSION,state,payload,sha,time.time()))
            tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if settle_cutover and 'fishbot_readonly_cutover' in tables:
                self.db.execute("UPDATE fishbot_readonly_cutover SET state='consumed' WHERE scope=? AND state='available'",(self.scope_key,))
            if 'fishbot_operator_session_archives' in tables:
                # The first durable stage uses this explicit session grant.
                # A crash/unknown response cannot offer it to another baseline.
                self.db.execute("UPDATE fishbot_operator_session_archives SET new_session_state='consumed' WHERE scope=? AND new_session_state='available'",(self.scope_key,))
            if settle_permit and not self._settle(prepared,True,in_transaction=True):
                self.db.rollback();return False
            if consume_permit and not prepared.reconstruction_authority.settle_context_reconstruction(prepared.reconstruction_permit,True,in_transaction=True):
                self.db.rollback();return False
            if consume_permit and prepared.reconstruction_permit.get('kind')=='summary_checkpoint':
                self.db.execute('''CREATE TABLE IF NOT EXISTS memory_checkpoint_retirements(
                  checkpoint_id INTEGER PRIMARY KEY,scope TEXT,request_id TEXT,
                  retired_events TEXT,retained_events TEXT,before_sha TEXT,after_sha TEXT,created REAL)''')
                def events(messages):
                    return {model_json([o['scope'],str(o['event_id'])]) for m in messages if (o:=source(m))}
                before=events(self.last.base) if self.last else set();after=events(prepared.base)
                semantic=prepared.reconstruction_permit.get('semanticBatch') is True
                self.db.execute('INSERT INTO memory_checkpoint_retirements VALUES(?,?,?,?,?,?,?,?)',
                    (prepared.reconstruction_permit['id'],json.loads(self.scope_key),prepared.reconstruction_permit['requestId'],
                     model_json({'batchDigest':prepared.reconstruction_permit['batchDigest'],'count':len(before-after)}) if semantic else model_json(sorted(before-after)),
                     model_json({'count':len(before&after)}) if semantic else model_json(sorted(before&after)),
                     hashlib.sha256(model_json(self.last.wire if self.last else []).encode()).hexdigest(),
                     hashlib.sha256(model_json(prepared.wire).encode()).hexdigest(),time.time()))
                if not semantic and 'memory_retirement_exceptions' in tables:
                    for event in before-after:
                        changed=self.db.execute("UPDATE memory_retirement_exceptions SET state='retired' WHERE scope=? AND event=? AND checkpoint_id<=? AND state='offered'",(json.loads(self.scope_key),event,prepared.reconstruction_permit['id'])).rowcount
                        if changed:self.db.execute('DELETE FROM memory_hold_rounds WHERE scope=? AND event=?',(json.loads(self.scope_key),event))
                prepared.reconstruction_authority.checkpoint_issue(json.loads(self.scope_key),prepared.reconstruction_permit['id'],'retirement',
                    {'retired_events':len(before-after),'retained_events':len(before&after),'result':'partial' if before&after and before-after else 'no_history_retired' if not before-after else 'retired',
                     'retirement_evidence':'memory_checkpoint_retirements','request_id':prepared.reconstruction_permit['requestId']},resolved=semantic or not bool(before&after))
                if semantic:
                    if 'memory_semantic_batches' in tables:
                        changed=self.db.execute("UPDATE memory_semantic_batches SET state='retired',completed=? WHERE checkpoint_id=? AND scope=? AND digest=? AND state IN ('committed','retired')",
                            (time.time(),prepared.reconstruction_permit['id'],json.loads(self.scope_key),prepared.reconstruction_permit['batchDigest'])).rowcount
                        if changed!=1:raise ContextPrefixViolation('semantic_batch_commit_changed')
                        from conversation_continuity import view as continuity_view,retire_previous
                        expected=continuity_view(self.db,json.loads(self.scope_key),checkpoint_id=prepared.reconstruction_permit['id'])
                        if prepared.state.get('memory_foundation',{}).get('conversation_continuity')!=expected:
                            raise ContextPrefixViolation('semantic_continuity_checkpoint_mismatch')
                        retire_previous(self.db,json.loads(self.scope_key),prepared.reconstruction_permit['id'])
                    journal_storage.prune_unreferenced_blobs(self.db,self.scope_key)
            if model_origin is not None:
                from message_provenance import bind_model_output
                bind_model_output(self.db,json.loads(self.scope_key),*model_origin,prepared.base,len(prepared.base)-1)
            if consume_permit and json.loads(self.scope_key)=='bot_shared' and 'memory_message_sources' in tables:
                from message_provenance import prune_retired_model_owners
                prune_retired_model_owners(self.db,'bot_shared',prepared.base)
            if on_stage is not None:on_stage()
            self.db.commit();return True
        except BaseException:self.db.rollback();raise

    def stage_candidate(self,prepared,on_stage=None):
        """Persist the exact outgoing candidate before any HTTP submission.

        Once a request may leave the process, the next request must never fall
        back to a prefix without this candidate's tail (A package). A storage
        refusal keeps the previous row and guard state untouched; the caller
        must stop before HTTP submission when this returns False.

        R1 license binding: when the durable stage succeeds, this candidate's
        reconstruction permit is consumed inside the same transaction. From
        that moment the 35-round license belongs to this exact candidate — a
        later abort, failure or process death may discard the candidate but
        never returns the license to ``available`` for a different baseline.
        A stage refusal consumes nothing (nothing was submitted).
        """
        if self.db is None or self.scope_key is None:return True
        if prepared.serial!=self.serial:return False
        if self._staged==prepared.serial:
            if hashlib.sha256(self._snapshot(prepared).encode()).hexdigest()!=self._staged_sha:
                raise ContextPrefixViolation('A durably staged request candidate was mutated.')
            return True
        consume=bool(prepared.reconstruction_permit and not prepared.reconstruction_consumed
                     and prepared.reconstruction_authority is not None
                     and getattr(prepared.reconstruction_authority,'db',None) is self.db)
        saved=(self.recovery_error,self.retired_reason)
        ok=self._persist(prepared,'ready',consume_permit=consume,tombstone=False,settle_cutover=False,on_stage=on_stage)
        self.recovery_error,self.retired_reason=saved
        if not ok:return False
        self._staged=prepared.serial
        self._staged_sha=hashlib.sha256(self._snapshot(prepared).encode()).hexdigest()
        if consume:
            prepared.reconstruction_consumed=True
        elif prepared.reconstruction_permit and not prepared.reconstruction_consumed:
            # Cross-database authority (test harnesses): consume after the
            # durable stage. A crash between both steps leaves the permit
            # reserved with a dead owner, which stays conservatively isolated.
            if prepared.reconstruction_authority.settle_context_reconstruction(prepared.reconstruction_permit,True):
                prepared.reconstruction_consumed=True
            else:
                self._staged=None
                if self.last is not None:self._persist(self.last,'ready',tombstone=False,settle_cutover=False)
                return False
        return True

    def _reservation(self,authority,scope,request_id,summary_ids):
        if authority is None or scope is None:return None
        return authority.reserve_context_reconstruction(scope,request_id,summary_ids=summary_ids)

    def _validate_partial_retirement(self,permit,authority,scope,old,base):
        if permit and permit.get('semanticBatch'):
            retained=semantic_retire_projection(old.base,permit) if old else []
            # A semantic checkpoint retires its batch only; all other native
            # blocks remain exact, including unfinished/unknown Work.
            cursor=system_head(base)
            for message in retained[system_head(retained):]:
                while cursor<len(base) and model_json(base[cursor])!=model_json(message):cursor+=1
                if cursor==len(base):
                    authority.settle_context_reconstruction(permit,False)
                    raise ContextPrefixViolation('semantic_checkpoint_retained_material_missing')
                cursor+=1
            return
        if not permit or not (permit.get('partialRetirement') or scope=='bot_shared' and permit.get('kind')=='summary_checkpoint'):return
        from summary_material import project_history,coverage
        proofs={e:p for e,p in coverage(self.db,scope).items() if (p.get('version')==3 or not permit.get('partialRetirement') and p.get('version')!=4) or p.get('version')==4 and 0<p.get('policy_checkpoint',0)<=permit.get('id',0)}
        retained=project_history(old.base,proofs,scope,'',protect_latest=False) if old else []
        cursor=0
        for message in retained:
            while cursor<len(base) and model_json(base[cursor])!=model_json(message):cursor+=1
            if cursor==len(base):
                authority.settle_context_reconstruction(permit,False)
                raise ContextPrefixViolation('partial_checkpoint_retained_material_missing')
            cursor+=1

    def refusal_evidence(self):
        """Bounded, content-free journal state for typed guard diagnostics."""
        old=self.last
        if old is None:return {'journalState':'empty','recoveryError':self.recovery_error or '','refusal':self._last_refusal_reason,'turns':0}
        usage=history_usage(old.wire)
        return {'journalState':'retired:'+self.retired_reason if self.retired_reason else 'ready',
                'recoveryError':self.recovery_error or '','refusal':self._last_refusal_reason,
                'view':getattr(self,'_last_view_evidence',{}),'baseChars':old.base_chars,'capacity':capacity_evidence(usage),
                'persistenceError':self._last_persist_error,
                'textChars':usage['textChars'],'wireChars':usage['wireChars'],'turns':old.turns,
                'addedTextHeadroom':max(0,MAX_RETAINED_TEXT_CHARS-usage['textChars']),
                'textHeadroom':max(0,MAX_RETAINED_TEXT_CHARS-usage['textChars']),
                'wireHeadroom':max(0,MAX_RETAINED_WIRE_CHARS-usage['wireChars'])}

    def checkpoint_system_boundary(self,scope):
        """Only the unsubmitted replacement inside a committed retirement."""
        old=self.last
        if old is None or self.db is None or not scope:return False
        head=system_head(old.base)
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return bool(head and head==len(old.base) and
            {'memory_semantic_batches','memory_reconstruction_checkpoints'}<=tables and
            self.db.execute("""SELECT 1 FROM memory_semantic_batches b
              JOIN memory_reconstruction_checkpoints c ON c.id=b.checkpoint_id
              WHERE b.scope=? AND b.state='retired' AND c.state='consumed'
                AND c.created<=? AND b.completed>=? LIMIT 1""",(scope,old.at,old.at)).fetchone())

    def prepare(self,messages,stamp,now=None,group_turn=None,reconstruction_authority=None,reconstruction_scope=None,
                retirement=None,strict=False,compact_policy=False,guarded=True,retained_validator=None,
                unprojected=None,coverage_view=None,system_transition_authorized=False):
        from context_transport import READONLY_FIELDS,EVENT_CONTEXT_CURRENT
        now=time.time() if now is None else now
        # SDK native windows can rehydrate a retired batch after restart.
        # Content-free membership tombstones fence that resurrection before
        # native-prefix comparison; no Coverage or timestamp inference.
        if self.db is not None and self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_semantic_batches'").fetchone():
            # Internal Work journals use an ephemeral tuple as their owner key;
            # it is not a conversation scope or a SQLite parameter. They still
            # apply the bot's shared tombstones to real sourced chat blocks.
            scope=reconstruction_scope if isinstance(reconstruction_scope,str) else json.loads(self.scope_key) if self.scope_key is not None else None
            by_scope={}
            for batch_scope,raw in self.db.execute("SELECT scope,members FROM memory_semantic_batches WHERE scope IN (?,'bot_shared') AND state='retired'",(scope,)):
                by_scope.setdefault(batch_scope,set()).update(json.loads(raw))
            for batch_scope,members in by_scope.items():
                retired={'semanticBatch':True,'scope':batch_scope,'batchMembers':sorted(members)}
                messages=semantic_retire_projection(messages,retired)
                if isinstance(unprojected,list):unprojected=semantic_retire_projection(unprojected,retired)
                if isinstance(coverage_view,list):coverage_view=semantic_retire_projection(coverage_view,retired)
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
        if old and system_transition_authorized and self.db is not None and reconstruction_scope:
            new_head=system_head(base)
            boundary=self.checkpoint_system_boundary(reconstruction_scope)
            if boundary and old.base!=base[:new_head]:
                old=replace(old,base=copy.deepcopy(base[:new_head]),
                    wire=copy.deepcopy(base[:new_head])+copy.deepcopy(old.wire[system_head(old.wire):]))
        summary_changed=bool(old and summaries(old.state)!=summaries(state))
        retirement_changed=bool(old and old.retirement!=retirement)
        can_defer_retirement=bool(retirement_changed and guarded and strict and self.db is not None
            and getattr(reconstruction_authority,'db',None) is self.db
            and getattr(reconstruction_authority,'can_defer_context_retirement',lambda *_:False)(reconstruction_scope,old.retirement))
        authority_contract_changed=bool(old and (old.authority is None)!=(authority is None))
        audience_changed=bool(old and retained_validator is not None and not retained_validator(old.wire))
        valid=bool(old and not self.recovery_error and not self.retired_reason and (guarded or not summary_changed) and (not retirement_changed or can_defer_retirement)
                   and not authority_contract_changed and not audience_changed)
        # A licensed reconstruction may swap the committed System and retire
        # covered history in one candidate. Without a permit the committed
        # prefix is the contract: System bytes and covered-round retirement
        # both defer, and the request continues on the committed wire with
        # only real new tail messages appended.
        suffix=None;retirement_deferred='';raw_base=base
        if isinstance(unprojected,list):
            raw_base=[]
            for m in unprojected:
                c=m.get('content') if isinstance(m,dict) else None
                if isinstance(m,dict) and m.get('role')=='user' and isinstance(c,str) and (c.startswith(READONLY_CURRENT+'\n') or c.startswith(EVENT_CONTEXT_CURRENT+'\n')):continue
                raw_base.append(m)
        # Projection is not authority to hide new events, or to reinterpret a
        # known event under a different sender/scope. Check the honest SDK view
        # before ANY overlap/reload fallback; the committed wire stays intact.
        if valid and old:
            # Shared chat IDs can collide across QQ channels. Legacy keys keep
            # their existing strict event-identity comparison unchanged.
            shared=self.scope_key=='"bot_shared"'
            identity=lambda o:(o['scope'],str(o['event_id'])) if shared else str(o['event_id'])
            known={identity(o):(o,m) for m in old.base if (o:=source(m))}
            projected={model_json(o) for m in base if (o:=source(m))}
            # A restored SDK window can reintroduce older, already summarized
            # blocks BETWEEN retained anchors. They were never in old.base;
            # requiring them in the projection strands an otherwise unchanged
            # Journal. Recheck full block coverage from this scope's database,
            # and only exempt blocks before the last committed anchor. This
            # does not retire or change a single message in old.wire.
            historical_covered=set()
            last_anchor=max((i for i,m in enumerate(raw_base) if (o:=source(m)) and identity(o) in known),default=-1)
            if self.db is not None and reconstruction_scope and any(
                    i<last_anchor and (o:=source(m)) and identity(o) not in known and model_json(o) not in projected
                    for i,m in enumerate(raw_base)):
                from summary_material import coverage,project_history
                # Coverage fingerprints were captured before image downscaling.
                # Recheck complete blocks on that original view, paired in order
                # with the constrained view. Only image URL bytes may differ;
                # author, text, replies, tools and every other field must agree.
                proof_base=raw_base
                if isinstance(coverage_view,list):
                    original=[]
                    for m in coverage_view:
                        c=m.get('content') if isinstance(m,dict) else None
                        if isinstance(m,dict) and m.get('role')=='user' and isinstance(c,str) and (c.startswith(READONLY_CURRENT+'\n') or c.startswith(EVENT_CONTEXT_CURRENT+'\n')):continue
                        original.append(m)
                    original=raw_base[:system_head(raw_base)]+original[system_head(original):]
                    def without_image_bytes(message):
                        message=copy.deepcopy(message)
                        if isinstance(message,dict) and isinstance(message.get('content'),list):
                            for part in message['content']:
                                if isinstance(part,dict) and part.get('type')=='image_url' and isinstance(part.get('image_url'),dict):
                                    part['image_url']['url']='[image bytes compared by provider projection]'
                        return message
                    if len(original)==len(raw_base) and all(without_image_bytes(a)==without_image_bytes(b) for a,b in zip(original,raw_base)):
                        proof_base=original
                checked=project_history(proof_base,coverage(self.db,reconstruction_scope),reconstruction_scope,
                                        next((str(o['event_id']) for m in reversed(raw_base) if (o:=source(m))),''))
                kept={model_json(o) for m in checked if (o:=source(m))}
                historical_covered={model_json(o) for i,m in enumerate(raw_base)
                                    if i<last_anchor and (o:=source(m)) and model_json(o) not in kept}
            anchored=False
            for m in raw_base:
                o=source(m)
                if not o:continue
                prior=known.get(identity(o))
                if prior is not None:
                    anchored=True
                    if o!=prior[0] or not same_native_message(prior[1],m):valid=False;break
                elif anchored and model_json(o) not in projected and model_json(o) not in historical_covered:
                    valid=False;break
        if valid and old is not None:
            suffix=tail_extension(old.base,base)
            if suffix is not None and base[:system_head(old.base)]!=old.base[:system_head(old.base)]:
                retirement_deferred='system_change'
            if suffix is None and isinstance(unprojected,list):
                # The full native view carries this request's own platform
                # packets; they are excluded so the deferred tail is pure
                # conversation and the reuse branch keeps appending packet
                # deltas exactly once.
                raw_excluded=set()
                for i,m in enumerate(unprojected):
                    if not isinstance(m,dict) or m.get('role')!='user':continue
                    c=m.get('content')
                    if isinstance(c,str) and (c.startswith(READONLY_CURRENT+'\n') or c.startswith(EVENT_CONTEXT_CURRENT+'\n')):raw_excluded.add(i)
                raw_base=[m for i,m in enumerate(unprojected) if i not in raw_excluded]
                suffix=tail_extension(old.base,raw_base)
                if suffix is not None:retirement_deferred='coverage_retirement'
        if valid and suffix is None:suffix=confirmed_group_extension(old,base,state,group_turn,self.db)
        if valid and suffix is None and strict:
            # Use the honest full native view when coverage has projected out
            # earlier blocks. SDK turn limits and pending System changes may
            # happen together; neither authorizes rewriting committed wire.
            suffix=confirmed_native_window_extension(old,base)
            if suffix is not None and raw_base is not base:
                # Coverage may remove older SDK blocks before the overlap.
                # It must never hide any uncommitted message after that anchor:
                # the full native suffix, including its last committed item,
                # must agree exactly before we append on unchanged old.wire.
                anchored=[old.base[-1]]+list(suffix)
                if not same_native_prefix(raw_base[-len(anchored):],anchored):suffix=None
            if suffix is None:suffix=confirmed_native_window_extension(old,raw_base)
            if suffix is not None:retirement_deferred='native_window_overlap'
        if valid and suffix is None and strict:
            # The honest full native view owns the fallback when provided: a
            # projected view whose preceding blocks were dropped by coverage
            # must never seed an append that skips uncommitted real turns.
            # Use the same packet-free native view as the coverage fallback.
            # The SDK inserts the platform packets before the new prompt;
            # passing that decorated view makes its first user unsourced.
            suffix=fresh_event_extension(old.base,raw_base)
            if suffix is not None:retirement_deferred='fresh_native_window'
        if valid and suffix is None and strict:
            # Complete, delivery-proven rounds the journal never kept (an
            # aborted success) sit at the window head and would deadlock the
            # scope forever; adopt them once as appended real history.
            suffix=uncommitted_window_head_extension(old,raw_base,self.db)
            if suffix is not None:retirement_deferred='uncommitted_window_head'
        if valid and suffix is None and strict:
            # The SDK may retain older history and omit unanswered turns,
            # including possibly submitted ones. Keep them in the old wire.
            suffix=window_reload_extension(old,raw_base,self.db)
            if suffix is not None:retirement_deferred='window_reload_extension'
        # Content-free evidence distinguishes a failed native overlap from
        # retirement/authority fencing; summary changes alone are not a cause.
        origins={str(o['event_id']) for m in (old.base if old else []) if (o:=source(m))}
        tail=raw_base[system_head(raw_base):]
        self._last_view_evidence={'valid':valid,'retirementChanged':retirement_changed,
            'authorityChanged':authority_contract_changed,'nativeMessages':len(raw_base),
            'tailShape':[{'role':m.get('role') if isinstance(m,dict) else 'invalid',
                'sourced':bool(source(m)) if isinstance(m,dict) else False,
                'known':bool((o:=source(m)) and str(o['event_id']) in origins) if isinstance(m,dict) else False}
                for m in tail[:4]]}
        if valid and suffix is None and old:
            prior=old.base[system_head(old.base):]
            for i,(a,b) in enumerate(zip(prior,tail)):
                if same_native_message(a,b):continue
                def shape(m):
                    content=m.get('content')
                    return {'role':m.get('role'),'keys':sorted(m),'contentType':type(content).__name__,
                            'parts':[p.get('type') if isinstance(p,dict) else 'invalid' for p in content] if isinstance(content,list) else [],
                            'chars':len(model_json(content))}
                self._last_view_evidence['mismatch']={'index':i,'old':shape(a),'new':shape(b),
                    'sameSource':source(a)==source(b),'sameContent':a.get('content')==b.get('content')}
                break
        reuse=suffix is not None
        reason='' if reuse else ('initial' if old is None and not self.recovery_error else
            'journal_recovery_unavailable' if self.recovery_error else 'journal_retired' if self.retired_reason else
            'memory_audience_changed' if audience_changed else 'summary_changed' if summary_changed else 'memory_epoch_changed' if retirement_changed else
            'authority_contract_changed' if authority_contract_changed else 'native_prefix_changed')
        self._last_refusal_reason='' if reuse else reason
        reconstruction_permit=None
        needs_reconstruction=bool(self.recovery_error or self.retired_reason or (old and (not reuse or (summary_changed and not guarded) or retirement_changed or authority_contract_changed)))
        # R1: a web-config save no longer authorizes any journal rebuild —
        # not even a first baseline. A scope with committed history keeps its
        # committed System vector and wire until this scope's own licensed
        # 35-round reconstruction; a fresh scope builds its first baseline
        # without any license because there is nothing to retire.
        if needs_reconstruction and guarded:
            from system_contract import request_identity
            request_id=request_identity()
            reconstruction_permit=self._reservation(reconstruction_authority,reconstruction_scope,request_id,summary_ids)
            if reconstruction_permit is None:
                if reuse and can_defer_retirement:
                    # Keep the exact committed prefix and old retirement stamp.
                    # Only a genuine future permit may retire it; audience,
                    # epoch, native-tail and capacity checks still apply.
                    retirement=old.retirement;retirement_deferred='checkpoint_retirement_deferred'
                elif reason in ('journal_recovery_unavailable','journal_retired'):
                    raise ContextRecoveryUnavailable('A committed request journal cannot be restored or was retired; a 35-round summary checkpoint is required before rebuilding it.')
                else:raise ContextReconstructionDenied('A full request-history reconstruction is not authorized by a successful 35-round summary checkpoint.')
            else:
                reuse=False
        if reconstruction_permit and reconstruction_permit.get('semanticBatch'):
            base=semantic_retire_projection(base,reconstruction_permit)
            messages=semantic_retire_projection(messages,reconstruction_permit)
        if reuse:
            from context_foundation import freeze,execution_view
            state=execution_view(freeze(state,old.state))
            operations=changes(old.state,state)
            if apply_changes(old.state,operations)!=state:raise ValueError('readonly_projection_mismatch')
            # Legacy semantic retirement persisted metadata but omitted its
            # first full readonly packet. Never edit that submitted prefix:
            # append one explicit current-object reset before more deltas.
            has_basis=False
            for message in old.wire:
                content=message.get('content')
                if not isinstance(content,str) or not content.startswith(READONLY_CURRENT+'\n'):continue
                packet=json.loads(content.split('\n',1)[1])
                if 'readonlyChanges' not in packet or any(op[0]=='set' and op[1]==[] for op in packet.get('readonlyChanges',[])):
                    has_basis=True;break
            if state and not has_basis:
                reset=copy.deepcopy(state)
                reset.pop('memory_foundation',None) # Already visible as fixed regions.
                operations=[['set',[],reset]]
            if retirement_deferred:
                # The stored base must stay byte-consistent with the committed
                # wire: deferred System/retirement changes are not adopted.
                base=old.base+list(suffix)
            wire=copy.deepcopy(old.wire)+suffix
            # Old cleanup versions could retain a complete foundation only in
            # metadata. Append genuinely missing regions through this same
            # staged writer; never alter the committed prefix or reset rounds.
            from context_foundation import missing_regions
            wire.extend(missing_regions(old.wire,state))
            use_b=reading_b_enabled()
            if use_b:
                from context_reading import PROTOCOL_MESSAGE,encode_readonly,decode_readonly
                pool=reading_pool(old.wire)
            def define_reading():
                if use_b and PROTOCOL_MESSAGE not in wire:wire.append(copy.deepcopy(PROTOCOL_MESSAGE))
            # Event facts are append-only too, but an identical packet adds no
            # information. Repeating it on every ToolLoop request needlessly
            # grows history and can manufacture the next size boundary.
            if authority is not None and authority!=old.authority:
                current_data=json.loads(authority['content'].split('\n',1)[1])
                previous_data=json.loads(old.authority['content'].split('\n',1)[1]) if old.authority else {}
                # A checkpoint may leave only a contract-presence placeholder
                # in metadata. It is not a model-visible event baseline.
                event_basis=any(isinstance(m.get('content'),str) and m.get('role')=='user'
                    and m['content'].startswith(EVENT_CONTEXT_CURRENT+'\n')
                    and 'eventChanges' not in json.loads(m['content'].split('\n',1)[1])
                    for m in old.wire)
                if compact_policy and old.authority and (not use_b or event_basis):
                    delta=changes(previous_data,current_data)
                    if apply_changes(previous_data,delta)!=current_data:raise ValueError('event_projection_mismatch')
                    compact={'eventChanges':delta}
                    if not use_b:compact['meaning']='仅按set/remove路径更新上一份平台事件资料；未变字段沿用，不改历史、不授予权限。'
                    # Keep the full latest snapshot in durable metadata. Only
                    # the NEW wire packet may use a smaller lossless delta.
                    fallback=copy.deepcopy(current_data)
                    if fallback.get('policy')==previous_data.get('policy'):fallback.pop('policy',None)
                    if len(model_json(compact))<len(model_json(fallback)):
                        define_reading()
                        authority={'role':authority['role'],'content':EVENT_CONTEXT_CURRENT+'\n'+model_json(compact)}
                    else:authority={'role':authority['role'],'content':EVENT_CONTEXT_CURRENT+'\n'+model_json(fallback)}
                wire.append({**authority,'content':EVENT_CONTEXT_CURRENT+'\n'+memory_json(json.loads(authority['content'].split('\n',1)[1]))})
            if operations:
                packet={'readonlyChanges':operations}
                if use_b:
                    # The legacy missing-hot-basis repair intentionally omits
                    # the separately visible fixed foundation from its root.
                    # Verify that exact wire view; durable metadata stays full.
                    wire_state=apply_changes(old.state,operations)
                    packet=encode_readonly(old.state,wire_state,operations,pool)
                    if decode_readonly(old.state,packet,copy.deepcopy(pool))!=wire_state:raise ValueError('reading_increment_mismatch')
                    if packet!={'readonlyChanges':operations}:define_reading()
                wire.append({'role':'user','content':READONLY_CURRENT+'\n'+memory_json(packet)})
        else:
            from context_foundation import execution_view
            state=execution_view(state)
            wire=reading_baseline(messages,state)
        usage=history_usage(wire);base_chars=old.base_chars if reuse else usage['textChars']
        # A foreground journal cannot rebuild at a cache-growth threshold.
        # Existing absolute text/wire limits still require a real permit.
        detail=history_boundary(usage['textChars'],base_chars,usage['wireChars'],growth_limit=False)
        if detail:
            if not reconstruction_permit and guarded:
                from system_contract import request_identity
                request_id=request_identity()
                reconstruction_permit=self._reservation(reconstruction_authority,reconstruction_scope,request_id,summary_ids)
            if reconstruction_permit is None and guarded:
                raise ContextCapacityExceeded('The append-only request journal reached its configured capacity before an authorized 35-round reconstruction checkpoint. Existing history is unchanged; an authenticated administrator may request compression.',usage=usage)
            if reconstruction_permit and reconstruction_permit.get('semanticBatch'):
                base=semantic_retire_projection(base,reconstruction_permit)
                messages=semantic_retire_projection(messages,reconstruction_permit)
            wire=reading_baseline(messages,state);reason='history_size_boundary';reuse=False
            usage=history_usage(wire);base_chars=usage['textChars'];detail=history_boundary(usage['textChars'],base_chars,usage['wireChars'],growth_limit=False)
            if detail:
                if reconstruction_permit:reconstruction_authority.settle_context_reconstruction(reconstruction_permit,False)
                raise ContextCapacityExceeded('The current complete request exceeds the configured bounded context capacity. Reject oversized NEW material before submission; never trim committed messages.',usage=usage)
        # Both retirement-driven and capacity-driven reservations must keep
        # every unproven native block byte-for-byte, including media/tools.
        self._validate_partial_retirement(reconstruction_permit,reconstruction_authority,reconstruction_scope,old,base)
        self.serial+=1
        turns=old.turns+sum(m.get('role')=='user' for m in suffix) if reuse else 1
        # Persist the complete current event contract for future comparisons;
        # wire may contain a shorter authority-only delta when its policy is
        # byte-identical to the latest committed policy.
        stored_authority=copy.deepcopy(event_packet) if event_packets else authority
        candidate=Prepared(self.serial,stamp,base,wire,copy.deepcopy(state),turns,now,reason,group_turn,base_chars,detail,usage,stored_authority,retirement,reconstruction_permit,reconstruction_authority,None,retirement_deferred,False)
        candidate.main_conversation=bool(old and old.main_conversation)
        return candidate

    def retire_semantic_batch(self,permit,authority,state,retirement=None,event_authority=None):
        """Immediately rebuild a settled Journal from a committed summary.

        The owner supplies freshly assembled state, never the old readonly
        packet. An in-flight staged candidate must settle before this call.
        This records no provider submission and no external delivery receipt.
        """
        if not permit or permit.get('semanticBatch') is not True:raise ValueError('semantic_batch_permit_required')
        if self.last is None or self._staged is not None or self.recovery_error or self.retired_reason:return False
        if self.db is None or getattr(authority,'db',None) is not self.db:return False
        if permit.get('scope')!=json.loads(self.scope_key):raise ContextPrefixViolation('semantic_batch_scope_changed')
        from context_foundation import execution_view
        base=semantic_retire_projection(self.last.base,permit)
        state=execution_view(copy.deepcopy(state))
        messages=base+([copy.deepcopy(event_authority)] if event_authority is not None else [])
        messages.append({'role':'user','content':READONLY_CURRENT+'\n'+model_json(state)})
        wire=reading_baseline(messages,state);usage=history_usage(wire)
        if history_boundary(usage['textChars'],usage['textChars'],usage['wireChars'],growth_limit=False):
            raise ContextCapacityExceeded('The complete lawful reconstruction candidate exceeds Journal capacity; the existing Journal and checkpoint remain unchanged.',usage=usage)
        self.serial+=1
        stored_authority=copy.deepcopy(event_authority)
        if stored_authority is None and self.last.authority is not None:
            # Keep only the transport contract's presence. The retired event's
            # sender/ID packet must not survive, and the next real event may
            # append its own current authority without earning another permit.
            from context_transport import EVENT_CONTEXT_CURRENT
            stored_authority={'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({
                'authority':{},'policy':{},'currentGuidance':'','meaning':'已完成语义检查点；下一真实平台事件提供当前身份与现场。'})}
        candidate=Prepared(self.serial,self.last.stamp,base,wire,state,1,time.time(),'semantic_batch_retirement',
            None,usage['textChars'],'',usage,stored_authority,retirement,permit,authority)
        candidate.main_conversation=self.last.main_conversation
        from semantic_batch import purge
        if not self.stage_candidate(candidate,on_stage=lambda:purge(self.db,permit['id'])):return False
        return self._accept_staged(candidate)

    def commit(self,prepared):
        if prepared.serial!=self.serial:
            self._settle(prepared,False);return False
        usage=history_usage(prepared.wire)
        if history_boundary(usage['textChars'],prepared.base_chars,usage['wireChars'],growth_limit=False):
            self._settle(prepared,False)
            return False
        return self._commit_candidate(prepared,submitted=False)

    def _commit_candidate(self,prepared,*,submitted):
        """One durable adoption path; unknown submission cannot refund a permit."""
        if self._accept_staged(prepared):return True
        atomic=bool(prepared.reconstruction_permit and not prepared.reconstruction_consumed and self.db is not None and getattr(prepared.reconstruction_authority,'db',None) is self.db)
        if prepared.reconstruction_permit and not prepared.reconstruction_consumed and not atomic and not self._settle(prepared,True):return False
        semantic=bool(atomic and prepared.reconstruction_permit.get('semanticBatch'))
        if not self._persist(prepared,'ready',settle_permit=atomic and not semantic,consume_permit=semantic):
            if atomic:self._settle(prepared,submitted)
            # Even if persistence fails, retain the conservative in-memory
            # prefix and fence further requests until recovery succeeds.
            self.last=prepared;self.retired_reason='persistence_unavailable';self._staged=None;return False
        self.last=prepared;self.retired_reason='';self.recovery_error=None;self._staged=None;return True

    def append_completion(self,message,model_origin=None):
        """Append a new terminal response before another channel can continue.

        No existing message is reserialized or projected. A response is not a
        QQ receipt and earns no business round; delivery settles separately.
        """
        if self.last is None or self.recovery_error or self.retired_reason:
            raise ContextRecoveryUnavailable('Cannot append a completion without its durable request.')
        if not isinstance(message,dict) or message.get('role')!='assistant' or message.get('tool_calls'):
            raise ValueError('journal_terminal_completion_required')
        import dataclasses
        candidate=dataclasses.replace(self.last,base=copy.deepcopy(self.last.base)+[copy.deepcopy(message)],
                                     wire=copy.deepcopy(self.last.wire)+[copy.deepcopy(message)])
        usage=history_usage(candidate.wire)
        candidate.usage=usage
        if history_boundary(usage['textChars'],candidate.base_chars,usage['wireChars'],growth_limit=False):
            raise ContextCapacityExceeded('The terminal completion exceeds Journal capacity.')
        if not self._persist(candidate,'ready',settle_cutover=False,model_origin=model_origin):
            raise ContextRecoveryUnavailable('The terminal completion could not be persisted.')
        self.last=candidate
        return True

    def retain_submitted(self,prepared):
        """Keep the candidate whose submission reached (or may have reached)
        the provider as the new committed baseline.

        A typed failure after ``request_not_sent`` must never shrink the
        committed prefix back below a wire that was possibly received (A
        package). Retaining implies no business round, no delivery receipt and
        no license credit; the reconstruction permit, if any, is consumed
        rather than returned to available, because the licensed request may
        have been received and reusing it on another baseline is forbidden.
        A permit already consumed by the stage transaction stays consumed.
        """
        if prepared.serial!=self.serial:return False
        return self._commit_candidate(prepared,submitted=True)

    def _accept_staged(self,prepared):
        # The complete wire was committed BEFORE HTTP, together with its
        # one-use permit. A second identical UPDATE after the response adds
        # no durability and can incorrectly retire a safely staged Journal
        # when the disk fills between send and receipt. This applies equally
        # to success and possibly-submitted failure, never creates a round.
        if self.db is None or self._staged!=prepared.serial or not self._staged_sha:return False
        if hashlib.sha256(self._snapshot(prepared).encode()).hexdigest()!=self._staged_sha:
            raise ContextPrefixViolation('A durably staged request candidate was mutated.')
        self.last=prepared;self.retired_reason='';self.recovery_error=None
        self._staged=None;self._staged_sha=None;self._last_persist_error=None
        return True

    def _settle(self,prepared,success,in_transaction=False):
        if not prepared.reconstruction_permit or prepared.reconstruction_consumed:return True
        # The authority that minted the permit is held on the candidate; it is
        # attached below by prepare, never reconstructed from model data.
        authority=prepared.reconstruction_authority
        if authority is None:return False
        if in_transaction:return authority.settle_context_reconstruction(prepared.reconstruction_permit,success,in_transaction=True)
        return authority.settle_context_reconstruction(prepared.reconstruction_permit,success)

    def abort_candidate(self,prepared):
        settled=self._settle(prepared,False)
        if prepared.serial!=self.serial:
            # The staged row (if any) belongs to a newer candidate; never
            # restore an older snapshot over it.
            return False
        # A provably unsent candidate may have been staged before submission;
        # restore the previously committed snapshot so an unsent request
        # leaves no trace in the persisted journal.
        if self._staged==prepared.serial:
            if self.last is None and self.db is not None:
                self._accept_staged(prepared)
                self.serial+=1
                return settled
            if self.db is not None and self.scope_key is not None and self.last is not None:
                saved=(self.recovery_error,self.retired_reason)
                try:restored=self._persist(self.last,'ready',settle_permit=False,tombstone=False,settle_cutover=False)
                except Exception:restored=False
                finally:self.recovery_error,self.retired_reason=saved
                if not restored:
                    # The staged candidate is still the durable conservative
                    # baseline. Keep memory aligned with it; do not pretend a
                    # failed rollback removed a possibly-submitted prefix.
                    self._accept_staged(prepared)
                    self._last_persist_error='journal_abort_restore_failed'
                    self.serial+=1
                    return False
            self._staged=None;self._staged_sha=None
        self.serial+=1
        return settled

    def retire_committed(self,prepared,reason='invalidated'):
        self._settle(prepared,False)
        if prepared.serial!=self.serial:return False
        self._staged=None;self._staged_sha=None
        self.serial+=1;self.retired_reason=reason
        if self.last is not None:self._persist(self.last,'retired')
        return True
