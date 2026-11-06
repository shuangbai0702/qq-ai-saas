"""Persistent outer transport with legal 35-effective-cycle compression permits.

The outer orientation/planning wire is disposable transport, never memory or a
task store. Unlike the legacy in-memory AppendHistory it survives restarts and
never silently rebuilds: idle time, request counts and ordinary summary writes
no longer reset history. Compression requires a checkpoint minted by the bot's
own backend after 35 effective complete planning cycles, consumed once per
phase, recorded in the same transaction as the snapshot it produces. Forgetting
or a changed trusted system retires the journal fail-closed until such a
checkpoint exists; capacity exhaustion fails loudly instead of rebuilding.
"""
import copy,hashlib,json,time
from dataclasses import dataclass

class OuterJournalDenied(RuntimeError):pass
class OuterCapacityBlocked(RuntimeError):pass
class OuterRecoveryUnavailable(RuntimeError):pass

MAX_OUTER_SNAPSHOT_BYTES=16*1024*1024
# Same transport bounds as the legacy decision journals; only their failure
# mode changes (explicit block instead of a silent reset).
from decision_history import history_boundary,MAX_HISTORY_CHARS,MAX_ADDED_CHARS,MAX_RETAINED_TEXT_CHARS,encode

def checkpoint_digest(scope_key,checkpoint):
    value={'scope':scope_key,'checkpointId':checkpoint.get('checkpointId'),'throughSeq':checkpoint.get('throughSeq'),
           'cycleIds':checkpoint.get('cycleIds')}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()).hexdigest()

def valid_checkpoint(scope_key,checkpoint):
    if not isinstance(checkpoint,dict):return False
    identifier=checkpoint.get('checkpointId');seq=checkpoint.get('throughSeq');cycles=checkpoint.get('cycleIds')
    if not isinstance(identifier,str) or not identifier or type(seq) is not int or seq<35 or seq%35:return False
    if not isinstance(cycles,list) or len(cycles)!=35 or len({str(c) for c in cycles})!=35:return False
    return checkpoint.get('digest')==checkpoint_digest(scope_key,checkpoint)

@dataclass
class OuterPrepared:
    serial:int
    scope:object
    system:str
    retirement:object
    contexts:list
    prompt:str
    records:dict
    at:float
    base_chars:int
    turns:int
    view:dict
    runtime:list
    reset_reason:str
    permit:object=None
    @property
    def reset_detail(self):return '' if self.reset_reason in ('','initial_or_cleared') else self.reset_reason

class PersistentOuterHistory:
    def __init__(self,db=None,scope_key=None,phase='orientation'):
        self.phase=phase if phase in ('orientation','planning') else 'orientation'
        self.db=db;self.scope_key=scope_key
        self.serial=0;self.history=[];self.records={};self.scope=None
        self.system=None;self.retirement=None;self.at=0;self.turns=0;self.base_chars=0
        self.view=None;self.runtime=None
        self.retired_reason='';self.recovery_error=None;self.pending_permit=None;self.pending_web_permit=None
        if db is not None and scope_key is not None:self._load()

    def _schema(self):
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS fishbot_outer_journals(
              scope TEXT NOT NULL,phase TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,
              payload TEXT,sha TEXT,updated REAL NOT NULL,PRIMARY KEY(scope,phase));
            CREATE TABLE IF NOT EXISTS fishbot_outer_permits(
              scope TEXT NOT NULL,checkpoint_id TEXT NOT NULL,phase TEXT NOT NULL,
              through_seq INTEGER NOT NULL,digest TEXT NOT NULL,consumed REAL NOT NULL,
              PRIMARY KEY(scope,checkpoint_id,phase));
            CREATE TABLE IF NOT EXISTS fishbot_outer_rebaselines(
              scope TEXT NOT NULL,permit_id TEXT NOT NULL,phase TEXT NOT NULL,at REAL NOT NULL,
              PRIMARY KEY(scope,permit_id,phase));
        ''')

    def _load(self):
        self._schema()
        row=self.db.execute('SELECT version,state,payload,sha FROM fishbot_outer_journals WHERE scope=? AND phase=?',(self.scope_key,self.phase)).fetchone()
        if not row:return
        version,state,payload,sha=row
        if version!=2 or state not in ('ready','retired') or not isinstance(payload,str) or not isinstance(sha,str) or hashlib.sha256(payload.encode()).hexdigest()!=sha or len(payload.encode())>MAX_OUTER_SNAPSHOT_BYTES:
            self.recovery_error='outer_journal_snapshot_integrity';return
        if state=='retired':self.retired_reason='persisted_retirement'
        try:value=json.loads(payload)
        except (TypeError,ValueError):self.recovery_error='outer_journal_snapshot_invalid';return
        required={'scope','phase','binding','system','view','runtime','records','history','turns','at','base_chars','retirement'}
        if not isinstance(value,dict) or set(value)!=required or value['scope']!=self.scope_key or value['phase']!=self.phase or not isinstance(value['history'],list) or not isinstance(value['records'],dict) or not isinstance(value['view'],dict) or type(value['turns']) is not int or not isinstance(value['at'],(int,float)) or any(not isinstance(m,dict) or not isinstance(m.get('content'),str) for m in value['history']):
            self.recovery_error='outer_journal_snapshot_invalid';return
        self.history=copy.deepcopy(value['history']);self.records=copy.deepcopy(value['records'])
        self.view=copy.deepcopy(value['view']);self.runtime=copy.deepcopy(value['runtime'])
        self.scope=value['binding'];self.system=value['system'];self.retirement=value['retirement']
        self.base_chars=value['base_chars'];self.turns=value['turns'];self.at=value['at']

    def _payload(self):
        return json.dumps({'scope':self.scope_key,'phase':self.phase,'binding':self.scope,'system':self.system,'view':self.view,'runtime':self.runtime,
            'records':self.records,'history':self.history,'turns':self.turns,'at':self.at,'base_chars':self.base_chars,
            'retirement':self.retirement},ensure_ascii=False,separators=(',',':'))

    def _persist(self,state='ready'):
        if self.db is None or self.scope_key is None:return True
        try:payload=self._payload()
        except (TypeError,ValueError):self.recovery_error='outer_journal_snapshot_serialize';return False
        if len(payload.encode())>MAX_OUTER_SNAPSHOT_BYTES:self.recovery_error='outer_journal_storage_capacity';return False
        sha=hashlib.sha256(payload.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("INSERT INTO fishbot_outer_journals(scope,phase,version,state,payload,sha,updated) VALUES(?,?,2,?,?,?,?) ON CONFLICT(scope,phase) DO UPDATE SET version=2,state=excluded.state,payload=excluded.payload,sha=excluded.sha,updated=excluded.updated",
                            (self.scope_key,self.phase,state,payload,sha,time.time()))
            if state=='ready' and self.pending_permit is not None:
                permit=self.pending_permit
                cursor=self.db.execute("INSERT OR IGNORE INTO fishbot_outer_permits(scope,checkpoint_id,phase,through_seq,digest,consumed) VALUES(?,?,?,?,?,?)",
                                       (self.scope_key,permit['checkpointId'],self.phase,permit['throughSeq'],permit['digest'],time.time()))
                if cursor.rowcount!=1:
                    self.db.rollback();self.recovery_error=None
                    return False
            if state=='ready' and self.pending_web_permit is not None:
                cursor=self.db.execute('INSERT OR IGNORE INTO fishbot_outer_rebaselines(scope,permit_id,phase,at) VALUES(?,?,?,?)',(self.scope_key,'rebaseline:'+str(self.pending_web_permit),self.phase,time.time()))
                if cursor.rowcount!=1:self.db.rollback();return False
            self.db.commit();self.pending_permit=None;self.pending_web_permit=None;return True
        except BaseException:
            self.db.rollback();raise

    def _permit_consumed(self,checkpoint_id):
        if self.db is None or self.scope_key is None:return False
        self._schema()
        return self.db.execute('SELECT 1 FROM fishbot_outer_permits WHERE scope=? AND checkpoint_id=? AND phase=?',(self.scope_key,checkpoint_id,self.phase)).fetchone() is not None

    def _web_permit_available(self,web_permit):
        """Only inspect here; consumption belongs to the snapshot transaction."""
        if self.db is None or self.scope_key is None:return False
        self._schema()
        return self.db.execute('SELECT 1 FROM fishbot_outer_rebaselines WHERE scope=? AND permit_id=? AND phase=?',(self.scope_key,'rebaseline:'+str(web_permit),self.phase)).fetchone() is None

    def blocked_reason(self,scope,system,retirement):
        """Why the committed outer prefix cannot continue; '' means it may."""
        if self.recovery_error:return 'journal_recovery_unavailable'
        if self.retired_reason:return 'journal_retired'
        if self.view is None:return ''
        if encode(self.scope)!=encode(scope) or self.system!=system:return 'binding_changed'
        if self.retirement!=retirement:return 'memory_epoch_changed'
        return ''

    def retire(self,reason='invalidated'):
        self.retired_reason=reason
        if self.view is not None:self._persist('retired')

    def clear(self):
        self.serial+=1;self.history=[];self.records={};self.scope=None
        self.system=None;self.retirement=None;self.at=0;self.turns=0;self.base_chars=0
        self.view=None;self.runtime=None;self.pending_permit=None;self.pending_web_permit=None

    def _assemble(self,reuse,view,runtime,active,instruction,retirement,scope,system,now):
        records=copy.deepcopy(self.records) if reuse else {};history=copy.deepcopy(self.history) if reuse else []
        added=[entry for key,entry in active.items() if key not in records]
        for key,entry in active.items():
            if key in records and records[key]!=entry:raise ValueError('context_record_collision')
        if added:history.append({'role':'user','content':encode({'contextRecords':added})})
        records.update(active)
        if reuse:
            from decision_history import changes,apply_changes
            operations=changes(self.view,view)
            if apply_changes(self.view,operations)!=view:raise ValueError('decision_state_projection_mismatch')
            if operations:history.append({'role':'user','content':encode({'contextChanges':operations})})
        else:history.append({'role':'user','content':encode(view)})
        if instruction:
            key=hashlib.sha256(instruction.encode()).hexdigest()[:24]
            definition='[平台阶段合同 '+key+']\n'+instruction
            if not any(m.get('role')=='user' and m.get('content')==definition for m in history):history.append({'role':'user','content':definition})
            history.append({'role':'user','content':'[本次平台阶段指令] 使用平台阶段合同 '+key+'；只按此合同完成本轮。'})
        return records,history

    def prepare(self,system,prompt,scope,now=None,retirement=None,instruction=None,permit=None,web_permit=None):
        from decision_context import split_snapshot,expand_snapshot,split_runtime,restore_runtime
        now=time.monotonic() if now is None else now
        snapshot=json.loads(prompt)
        if not isinstance(snapshot,dict):raise ValueError('decision_state_not_object')
        if instruction is not None and not isinstance(instruction,str):raise ValueError('decision_instruction_not_text')
        current,active=split_snapshot(snapshot);view,runtime=split_runtime(current)
        if expand_snapshot(restore_runtime(view,runtime),active)!=snapshot:raise ValueError('decision_context_projection_mismatch')
        retirement=self.retirement if retirement is None else retirement
        blocked=self.blocked_reason(scope,system,retirement)
        reuse=blocked==''
        permit_needed=bool(blocked)
        web_rebaseline=False
        if permit_needed:
            if blocked=='journal_recovery_unavailable':raise OuterRecoveryUnavailable('The committed outer %s journal cannot be restored; a 35-effective-cycle checkpoint is required before rebuilding it.'%self.phase)
            if self.view is not None and self.system!=system and not web_permit:
                raise OuterJournalDenied('An outer checkpoint cannot authorize a System change.')
            if permit is None or (self.view is not None and self.system!=system):
                # A real authenticated web-config save is the other legal
                # rebaseline license (same proof class as the foreground System
                # permit); it must never be synthesizable by the model.
                if not web_permit or self._permit_consumed('rebaseline:'+str(web_permit)):
                    raise OuterJournalDenied('Rebuilding the outer %s history is not authorized: %s requires a 35-effective-cycle checkpoint or an authenticated web-config rebaseline.'%(self.phase,blocked))
                if not self._web_permit_available(web_permit):
                    raise OuterJournalDenied('Outer rebaseline permit %s was already consumed for phase %s.'%(web_permit,self.phase))
                web_rebaseline=True
            elif not valid_checkpoint(self.scope_key,permit):
                raise OuterJournalDenied('Invalid outer checkpoint permit.')
            elif self._permit_consumed(permit['checkpointId']):
                raise OuterJournalDenied('Outer checkpoint %s was already consumed for phase %s.'%(permit['checkpointId'],self.phase))
        records,history=self._assemble(reuse,view,runtime,active,instruction,retirement,scope,system,now)
        chars=sum(len(row['content']) for row in history)
        base_chars=self.base_chars if reuse else chars
        detail=history_boundary(chars,base_chars)
        if detail and reuse:
            # Continued growth crossed the bounded budget: compression is legal
            # only through a checkpoint, never a silent reset.
            if permit is None or not valid_checkpoint(self.scope_key,permit) or self._permit_consumed(permit['checkpointId']):
                raise OuterCapacityBlocked('The outer %s transport reached %s; a 35-effective-cycle checkpoint is required before rebuilding.'%(self.phase,detail))
            reuse=False;records,history=self._assemble(False,view,runtime,active,instruction,retirement,scope,system,now)
            chars=sum(len(row['content']) for row in history);base_chars=chars
        if base_chars>MAX_RETAINED_TEXT_CHARS:
            # A single oversized active packet stays complete in one baseline.
            records={};view,runtime=split_runtime(snapshot);history=[{'role':'user','content':encode(view)}]
            if instruction:
                key=hashlib.sha256(instruction.encode()).hexdigest()[:24]
                history.append({'role':'user','content':'[平台阶段合同 '+key+']\n'+instruction});history.append({'role':'user','content':'[本次平台阶段指令] 使用平台阶段合同 '+key+'；只按此合同完成本轮。'})
            base_chars=sum(len(row['content']) for row in history)
            if base_chars>MAX_RETAINED_TEXT_CHARS:raise OuterCapacityBlocked('The current outer snapshot alone exceeds the bounded transport budget.')
        self.serial+=1
        self.pending_web_permit=web_permit if web_rebaseline else None
        self.pending_permit=dict(permit) if (not web_rebaseline and permit is not None and (permit_needed or (detail and not reuse))) else None
        return OuterPrepared(self.serial,scope,system,retirement,history,encode({'currentRuntime':runtime}),
                             records,now,base_chars,self.turns+1 if reuse else 1,copy.deepcopy(view),copy.deepcopy(runtime),
                             '' if reuse else (blocked or 'checkpoint_rebuild'))

    def commit(self,prepared,response):
        if prepared.serial!=self.serial:return False
        if not isinstance(json.loads(response),dict):raise ValueError('decision_result_not_object')
        history=copy.deepcopy(prepared.contexts)+[{'role':'user','content':prepared.prompt},{'role':'assistant','content':response}]
        if history_boundary(sum(len(row['content']) for row in history),prepared.base_chars):
            self.pending_permit=None;return False
        # Serialize the full post-commit snapshot before touching live state; a
        # persistence failure must leave the committed prefix exactly as it was.
        candidate={'scope':self.scope_key,'phase':self.phase,'binding':prepared.scope,'system':prepared.system,'view':copy.deepcopy(prepared.view),
            'runtime':copy.deepcopy(prepared.runtime),'records':copy.deepcopy(prepared.records),'history':history,
            'turns':prepared.turns,'at':prepared.at,'base_chars':prepared.base_chars,'retirement':copy.deepcopy(prepared.retirement)}
        try:payload=json.dumps(candidate,ensure_ascii=False,separators=(',',':'))
        except (TypeError,ValueError):self.recovery_error='outer_journal_snapshot_serialize';self.pending_permit=None;return False
        if len(payload.encode())>MAX_OUTER_SNAPSHOT_BYTES:self.recovery_error='outer_journal_storage_capacity';self.pending_permit=None;return False
        previous=(self.history,self.records,self.view,self.runtime,self.scope,self.system,self.retirement,self.at,self.turns,self.base_chars,self.retired_reason,self.recovery_error)
        self.history=history;self.records=copy.deepcopy(prepared.records);self.view=copy.deepcopy(prepared.view)
        self.runtime=copy.deepcopy(prepared.runtime);self.scope=prepared.scope;self.system=prepared.system
        self.retirement=copy.deepcopy(prepared.retirement);self.at=prepared.at
        self.base_chars=prepared.base_chars;self.turns=prepared.turns
        if not self._persist('ready'):
            persist_error=self.recovery_error or 'outer_journal_persist_unavailable'
            (self.history,self.records,self.view,self.runtime,self.scope,self.system,self.retirement,self.at,self.turns,self.base_chars,self.retired_reason,self.recovery_error)=previous
            self.recovery_error=persist_error
            return False
        self.retired_reason='';self.recovery_error=None;return True

    def abort_candidate(self,prepared):
        """Cancel only this uncommitted attempt; fence its late completion."""
        if prepared.serial!=self.serial:return False
        self.pending_permit=None;self.pending_web_permit=None;self.serial+=1
        return True

    def discard(self,prepared):self.abort_candidate(prepared)

    def retire_committed(self,prepared,reason='invalidated'):
        if prepared.serial!=self.serial:return False
        self.serial+=1;self.retire(reason);return True

    def seed_from(self,peer):
        """Copy a committed peer phase as this phase's starting transport.

        Both phases keep their own permit consumption; the copy only carries
        already-committed material.
        """
        if peer is None or peer.view is None or self.view is not None:return False
        self.history=copy.deepcopy(peer.history);self.records=copy.deepcopy(peer.records)
        self.view=copy.deepcopy(peer.view);self.runtime=copy.deepcopy(peer.runtime)
        self.scope=peer.scope;self.system=peer.system;self.retirement=copy.deepcopy(peer.retirement)
        self.at=peer.at;self.base_chars=peer.base_chars;self.turns=peer.turns
        return True
