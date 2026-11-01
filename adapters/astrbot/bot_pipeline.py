"""Shared work identity, ingress ownership and bounded stage evidence.

Capabilities add context/tools/output inside this work. They do not create a
second ingress, invent a sender, or own the QQ transport or a bot's database.
"""
from dataclasses import dataclass,field
import math,time


STAGES=('ingress','observe','admission','context','model_tools','output_plan','dispatch','commit')


class ContextBuilder:
    """One assembly point; a capability cannot replace identity or a peer's data."""
    def __init__(self,identity,origin):
        self._data={'identity':dict(identity),'origin':origin};self._owners={'identity':'platform','origin':'platform'}
    def contribute(self,capability,values):
        for key in values:
            if key in ('identity','origin'):raise ValueError('Platform context cannot be replaced')
            if key in self._owners and self._owners[key]!=capability:raise ValueError('Pipeline context field already owned')
        self._data.update(values)
        self._owners.update({key:capability for key in values})
    def build(self):return dict(self._data)


@dataclass(frozen=True)
class OutputPlan:
    messages:tuple
    delays:tuple

    def __post_init__(self):
        if not 1<=len(self.messages)<=4 or len(self.messages)!=len(self.delays):raise ValueError('Invalid output plan')
        if any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in self.delays):raise ValueError('Invalid output delay')


@dataclass
class Work:
    bot_id:str
    scope:str
    subject:str
    self_id:str
    event_id:str
    source:str
    source_at:float
    lease:dict=field(repr=False)
    started:float=field(default_factory=time.monotonic)
    trace:list=field(default_factory=list)
    status:str='received'
    reason:str=''
    ledger_owner:bool=False
    attempted:int=0
    confirmed:int=0
    planned_messages:int=0
    confirmed_messages:int=0

    @property
    def observes_input(self):return self.source=='message'

    def stage(self,name):
        if name not in STAGES:raise ValueError('Unknown bot pipeline stage')
        if not self.trace or self.trace[-1]['stage']!=name:
            self.trace.append({'stage':name,'ms':round((time.monotonic()-self.started)*1000)})
            self.trace[:]=self.trace[-16:]

    def stop(self,reason,status='blocked'):
        self.reason=reason;self.status=status


class BotPipeline:
    def __init__(self,db):
        self.db=db
        db.execute('''CREATE TABLE IF NOT EXISTS pipeline_events(scope TEXT,subject TEXT,event TEXT,at REAL,source TEXT,stage TEXT,status TEXT,reason TEXT,attempted INTEGER DEFAULT 0,confirmed INTEGER DEFAULT 0,PRIMARY KEY(scope,subject,event))''')
        db.execute("UPDATE pipeline_events SET status=CASE WHEN status='sending' THEN 'unknown' ELSE 'interrupted' END,reason='runtime_restarted' WHERE status IN ('received','sending')");db.commit()

    def bind(self,event,lease):
        group=str(event.get_group_id() or '');subject=str(event.get_sender_id());self_id=str(event.get_self_id())
        scope='group:'+group if group else 'private:'+subject
        source='deferred_group' if event.get_extra('_hosted_deferred_group') else 'owner_contact' if event.get_extra('_hosted_owner_contact') else 'scheduled_contact' if event.get_extra('_hosted_initiative',False) else 'message'
        message=event.message_obj
        work=Work(str(lease.get('botId') or event.get_platform_id()),scope,subject,self_id,str(getattr(message,'message_id','') or ''),source,float(getattr(message,'timestamp',0) or 0),lease)
        event.set_extra('_hosted_pipeline',work);work.stage('ingress')
        if subject==self_id:
            work.stop('self_message');return work
        if source=='deferred_group':
            row=self.db.execute('SELECT status FROM pipeline_events WHERE scope=? AND subject=? AND event=?',(scope,subject,work.event_id)).fetchone()
            if row and row[0] in ('sending','sent','partial','unknown'):work.stop('deferred_output_already_attempted','duplicate')
            return work
        if not work.event_id:return work
        now=time.time()
        with self.db:
            work.ledger_owner=bool(self.db.execute("INSERT OR IGNORE INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,?,?,?,?,'ingress','received','')",(scope,subject,work.event_id,now,source)).rowcount)
            self.db.execute('DELETE FROM pipeline_events WHERE at<?',(now-86400,))
            self.db.execute('DELETE FROM pipeline_events WHERE rowid NOT IN (SELECT rowid FROM pipeline_events ORDER BY at DESC LIMIT 2048)')
        if not work.ledger_owner:work.stop('duplicate_input','duplicate')
        return work

    def sending(self,work):
        work.stage('dispatch');work.attempted+=1
        # Persist before the network boundary. A crash or timeout cannot turn
        # an unknown external outcome into permission to repeat the request.
        with self.db:
            self.db.execute("UPDATE pipeline_events SET stage='dispatch',status='sending',attempted=? WHERE scope=? AND subject=? AND event=?",(work.attempted,work.scope,work.subject,work.event_id))

    def confirmed(self,work):work.confirmed+=1

    def finish(self,work,metrics):
        if work.attempted>work.confirmed:
            work.status='partial' if work.confirmed else 'unknown'
            if not work.reason:work.reason='unconfirmed_protocol_outcome'
        elif work.status=='received':
            work.status='partial' if work.confirmed_messages<work.planned_messages and work.confirmed else 'sent' if work.confirmed else 'silent'
            if not work.confirmed and not work.reason:work.reason='no_output'
        elif work.confirmed and work.status not in ('sent','duplicate'):work.status='partial'
        value={'source':work.source,'stage':work.trace[-1]['stage'],'status':work.status,'reason':work.reason,'protocolAttempts':work.attempted,'protocolConfirmed':work.confirmed,'plannedMessages':work.planned_messages,'confirmedMessages':work.confirmed_messages,'stages':work.trace}
        metrics['lastPipeline']=value
        if work.status=='duplicate' or work.source=='message' and not work.ledger_owner:return
        with self.db:
            self.db.execute('UPDATE pipeline_events SET stage=?,status=?,reason=?,attempted=?,confirmed=? WHERE scope=? AND subject=? AND event=?',(value['stage'],work.status,work.reason,work.attempted,work.confirmed,work.scope,work.subject,work.event_id))


def stage(event,name):
    work=event.get_extra('_hosted_pipeline')
    if work:work.stage(name)


def stop(event,reason,status='blocked'):
    work=event.get_extra('_hosted_pipeline_selected') or event.get_extra('_hosted_pipeline')
    if work:work.stop(reason,status)
