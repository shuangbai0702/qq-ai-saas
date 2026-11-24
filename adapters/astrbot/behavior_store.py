"""Per-bot SQLite store lifecycle, scope turn locking, and compression admission."""
import asyncio,sqlite3,time
from contextlib import asynccontextmanager
from pathlib import Path
import autonomy
import bot_pipeline
import companion_state
import group_actions
import group_freshness
import initiative
import periodic_guidance
import qq_favorite_tools
import relationships
import shared_context
from companion_schedule import Planner
from hosted_memory import ScopedMemory
from memory_reflection import Reflection
from participation import Participation
from runtime_care import Ambient
from social import SocialStore
from system_contract import SystemContract


class BehaviorStore:
    def __init__(self,path,*,runtime_config=None):
        self.root=Path(path).parent
        self.model_journals={}
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL;')
        if runtime_config is not None:shared_context.activate_empty(self.db,runtime_config)
        self.shared_context=shared_context.active(self.db)
        self.shared_group_limit=self.db.execute('SELECT group_limit FROM fishbot_shared_context WHERE id=1').fetchone()[0] if self.shared_context else 2
        self.model_writer=shared_context.Writer()
        if self.shared_context:shared_context.initialize(self.db)
        self.system_contract=SystemContract(self.db)
        self.relationship_migration=relationships.initialize(self.db)
        initiative.initialize(self.db)
        companion_state.initialize(self.db)
        autonomy.recover_unstarted(self.db)
        self.memory=ScopedMemory(self.db)
        self.reflection=Reflection(self.db)
        self.reflection.shared_context=self.shared_context
        self.reflection.memory=self.memory
        # Retire the unused BM25 prototype and its raw-text derived index.
        # Authoritative facts, corrections and checkpoint foundations stay put.
        secure_delete=self.db.execute('PRAGMA secure_delete').fetchone()[0]
        try:
            self.db.execute('PRAGMA secure_delete=ON')
            self.db.execute('SAVEPOINT retire_candidate_index')
            try:
                for trigger in ('search_fact_changed','search_fact_deleted','search_summary_deleted','search_summary_changed'):
                    self.db.execute('DROP TRIGGER IF EXISTS '+trigger)
                self.db.execute('DROP TABLE IF EXISTS memory_search')
            except BaseException:
                self.db.execute('ROLLBACK TO retire_candidate_index');raise
            finally:self.db.execute('RELEASE retire_candidate_index')
        finally:self.db.execute('PRAGMA secure_delete='+str(secure_delete))
        companion_state.bootstrap_members(self.db)
        self.planner=Planner(self.db)
        self.participation=Participation(self.db)
        # Bootstrap only from confirmed outgoing evidence, never contacts.subject
        # (which may be the latest bystander rather than the reply recipient).
        restored=set()
        for scope,subject,at in self.db.execute("SELECT scope,subject,at FROM dialogue_feedback WHERE scope LIKE 'group:%' AND at>? AND at<=? ORDER BY at DESC LIMIT 128",(time.time()-300,time.time())).fetchall():
            if scope not in restored:
                restored.add(scope)
                row=self.db.execute('SELECT last_reply FROM autonomy_groups WHERE scope=?',(scope,)).fetchone()
                if not row or row[0]<at:
                    autonomy.note_reply(self.db,scope,subject,at,touch_interaction=True)
        for scope,subject,at in self.db.execute('SELECT scope,partner,last_reply FROM autonomy_groups WHERE last_reply>? AND last_reply<=? ORDER BY last_reply DESC LIMIT 128',(time.time()-300,time.time())):
            self.participation.scopes.setdefault(scope,{'topic':set(),'sent':[at],'last':at,'subject':subject,'participants':{subject:at} if subject else {},'turns':[]})
        self.presence=autonomy.GroupPresence(self.db)
        self.pipeline=bot_pipeline.BotPipeline(self.db)
        periodic_guidance.initialize(self.db)
        group_actions.initialize(self.db)
        qq_favorite_tools.initialize(self.db)
        self.social=SocialStore(self.db,Path(path).parent)
        self.last={};self.active=0;self.ingress=0;self.batches={};self.batch_flush={};self.ambient=Ambient();self.slot_changed=asyncio.Event();self.waiting=0
        self.compression_active=False
        self.compression_boundary=None
        self.reflection.admission=self.compression_admission
        self.freshness=group_freshness.GroupFreshness()
        self.scope_turns={}
        self.compression_jobs={}
        self.model_probe_jobs={}
        self.model_probe_task=None
        self.compression_wakeup=False
        self.local_db_workers=set()
        self.semantic_worker=None
        self.voice_inflight={}
        self.closing=False
        self.storage_closed=False

    def conversation_pending(self):
        if self.active or self.ingress or self.batches or self.waiting or any(s['users'] for s in self.scope_turns.values()):return True
        # Accepted native inputs belong to the existing durable ingress queue.
        return self.db.execute("SELECT 1 FROM pipeline_work_inputs WHERE status='queued' AND (? IS NULL OR rowid<=?) LIMIT 1",(self.compression_boundary,self.compression_boundary)).fetchone() is not None

    def before_compression(self,event):
        if self.compression_boundary is None:return True
        if event is None:return False
        if event.get_extra('_hosted_scope_owner') or event.get_extra('_hosted_scope_continuation'):return True
        scope=bot_pipeline.delivery_scope(event)
        identifier=str(getattr(event.message_obj,'message_id',''))
        return self.db.execute('SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND rowid<=?',(scope,identifier,self.compression_boundary)).fetchone() is not None

    async def wait_compression(self,event=None):
        while self.compression_active or not self.before_compression(event):
            self.slot_changed.clear()
            if self.compression_active or not self.before_compression(event):await self.slot_changed.wait()

    @asynccontextmanager
    async def compression_admission(self):
        # Freeze admission, not history. New inputs stay in the SAME durable
        # inbox; only the finite already accepted prefix drains before writer.
        self.compression_boundary=self.db.execute('SELECT coalesce(max(rowid),0) FROM pipeline_work_inputs').fetchone()[0]
        try:
            while self.conversation_pending():
                self.slot_changed.clear()
                if not self.conversation_pending():break
                await self.slot_changed.wait()
            self.compression_active=True
            await self.model_writer.acquire()
            yield
        finally:
            self.model_writer.release()
            self.compression_active=False;self.compression_boundary=None;self.slot_changed.set()

    async def acquire_scope(self,scope,valid,timeout=120):
        """FIFO main execution ownership; ingress observations stay concurrent."""
        slot=self.scope_turns.setdefault(scope,{'lock':asyncio.Lock(),'users':0})
        if slot['users']>=8:return False
        slot['users']+=1
        acquired=False;transferred=False
        try:
            await asyncio.wait_for(slot['lock'].acquire(),timeout);acquired=True
            if valid():
                transferred=True;return True
            return False
        finally:
            if not transferred:
                if acquired:slot['lock'].release()
                slot['users']-=1
                if not slot['users']:self.scope_turns.pop(scope,None)

    def release_scope(self,scope):
        slot=self.scope_turns[scope];slot['lock'].release();slot['users']-=1
        if not slot['users']:self.scope_turns.pop(scope,None)
        self.slot_changed.set()

    async def wait_capacity(self,valid,timeout=15):
        if self.waiting>=8:return False
        self.waiting+=1;deadline=time.monotonic()+timeout
        try:
            while self.active>=2:
                if not valid():return False
                remaining=deadline-time.monotonic()
                if remaining<=0:return False
                self.slot_changed.clear()
                try:await asyncio.wait_for(self.slot_changed.wait(),remaining)
                except asyncio.TimeoutError:return False
            return valid()
        finally:self.waiting-=1

    def observe(self,scope,subject,text,event,now=None,memory_enabled=True,directed=True,project_facts=True):
        now=time.time() if now is None else now
        status=self.memory.observe(scope,subject,text,event,now) if memory_enabled else '长期记忆已关闭'
        affinity,emotion=relationships.record_interaction(self.db,scope,subject,text,event,now,directed=directed)
        self.db.commit()
        facts=self.memory.recall(scope,subject,text,now) if memory_enabled and project_facts else []
        return {'memory_status':status,'facts':facts,'emotion':emotion,'relationship':'熟悉' if affinity>=5 else '初识','affinity':affinity}

    def eligible(self,cfg,scope,group,mentioned,text,now=None):
        now=time.time() if now is None else now;b=cfg.get('behavior') or {}
        if group and cfg.get('groups') and group not in cfg['groups']:return False
        if now-self.last.get(scope,-1e12)<b.get('cooldownSeconds',0):return False
        if group and not mentioned and not text.startswith('/'):return False
        self.last[scope]=now;return True

    def close(self):
        if self.closing:return
        self.closing=True
        self.slot_changed.set()
        for task in tuple(self.voice_inflight.values()):task.cancel()
        if self.local_db_workers:
            # Invalidate publication now, but keep storage and ownership alive
            # until the local transaction has actually stopped.
            self.reflection.closed=True;self.reflection.binding=None
            if self.reflection.task:self.reflection.task.cancel()
            return
        self.close_storage()

    def close_storage(self):
        if self.storage_closed:return
        self.storage_closed=True
        probe=self.model_probe_task
        if probe and not probe.done():probe.cancel()
        self.pipeline.close();self.planner.close();self.memory.close();self.reflection.close();self.social.close();autonomy.recover_unstarted(self.db);self.db.close()
