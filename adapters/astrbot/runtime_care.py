"""Bounded ambient conversation and idle heap/cache maintenance, per AI process."""
import asyncio
from collections import OrderedDict, deque
import gc
import json
import os
from pathlib import Path
import time
import psutil
import sqlite3


class Ambient:
    def __init__(self): self.scopes = OrderedDict()
    def observe(self, scope, sender, text, now, limit=50, source=None):
        self.prune(now)
        if not scope.startswith('group:') or not text.strip(): return
        limit=max(1,min(50,int(limit)))
        rows = self.scopes.pop(scope, deque(maxlen=50))
        rows.append({**{k:source[k] for k in ('source_event','sender_name','mentions','reply_to') if isinstance(source,dict) and k in source},
                     'sender_id': sender, 'text': text[:180], 'at': now})
        while len(rows)>limit:rows.popleft()
        self.scopes[scope] = rows
        while len(self.scopes) > 128: self.scopes.popitem(last=False)
    def context(self, scope, now):
        self.prune(now)
        return [dict(x) for x in self.scopes.get(scope, [])]
    def acknowledged(self,scope,through):
        rows=self.scopes.get(scope)
        if rows is not None:
            while rows and rows[0]['at']<=through:rows.popleft()
            if not rows:self.scopes.pop(scope,None)
    def prune(self, now):
        for scope, rows in list(self.scopes.items()):
            while rows and (now-rows[0]['at'] > 600 or rows[0]['at'] > now): rows.popleft()
            if not rows: self.scopes.pop(scope, None)


class RuntimeCare:
    @staticmethod
    def busy(store):
        # Admission/debounce and capacity waits own inbound work too. Counting
        # model calls alone can declare a five-second burst window idle.
        return bool(store.active or getattr(store,'ingress',0) or store.batches or getattr(store,'waiting',0) or getattr(store,'compression_active',False) or any(not task.done() for task in getattr(store,'_local_db_workers',())) or any(task and not task.done() for task in (store.social.task, store.social.stickers.task, store.reflection.task, getattr(getattr(store,'planner',None),'task',None))))

    def status(self,core):
        """Inspect native queue/task ownership; expose no message content."""
        queue=getattr(core,'event_queue',None)
        bus=getattr(core,'event_bus',None)
        tasks=getattr(bus,'_pending_tasks',{})
        queued=queue.qsize() if queue is not None else None
        pending=sum(not task.done() for task in tasks)
        active=sum(self.busy(store) for store in self.stores.values())
        return {'queued':queued,'pending':pending,'busyMembers':active,
                'idle':queued==0 and pending==0 and active==0 and not self.maintenance_active,
                'maintenanceActive':self.maintenance_active,
                'lastActivity':self.last_activity}

    def track(self, store, task):
        if not task or task.done() or getattr(task, '_hosted_care', False): return
        task._hosted_care = True
        self.stamp(store.root)
        def completed(_):
            if store in self.stores.values(): self.stamp(store.root)
        task.add_done_callback(completed)

    def __init__(self, stores,stop_requested=lambda:False):
        self.stop_requested=stop_requested
        self.stores, self.last_activity, self.closed = stores, time.time(), False
        self.created = psutil.Process().create_time()
        self.last_gc = self.last_activity
        self.trimmed_activity = None
        self.failures = 0
        self.favorite_reconciler = None
        self.maintenance_active=False
    def stamp(self, root):
        if self.closed: return
        self.last_activity = time.time()
        try:loop=asyncio.get_running_loop()
        except RuntimeError:loop=None
        for st in self.stores.values():
            if st.root!=Path(root):continue
            st.slot_changed.set()
            if loop is None or getattr(st,'_compression_wakeup',False):continue
            st._compression_wakeup=True
            def wake(st=st):
                st._compression_wakeup=False
                if self.closed or self.stop_requested():return
                try:
                    if st.reflection.retry():self.track(st,st.reflection.task)
                except Exception as error:
                    self.failures+=1
                    with st.db:st.reflection.checkpoint_issue('bot_shared' if st.shared_context else 'self',0,'compression_wakeup',{'type':type(error).__name__})
            loop.call_soon(wake)
        # Root is supplied by the platform adapter, never by a QQ message.
        roots = {Path(root)}
        if os.getenv('ASTRBOT_ROOT'): roots.add(Path(os.environ['ASTRBOT_ROOT']))
        value = {'at': self.last_activity, 'pid': os.getpid(), 'created': self.created,
                 'active': any(self.busy(s) for s in self.stores.values())}
        for directory in roots:
            try:
                owners=[s for s in self.stores.values() if directory!=Path(root) or s.root==directory]
                value['active']=any(self.busy(s) for s in owners)
                value['work']={key:sum(getattr(s,key,0) for s in owners) for key in ('active','ingress','waiting')}
                value['work']['batches']=sum(len(s.batches) for s in owners)
                value['work']['background']=sum(bool(task and not task.done()) for s in owners for task in (s.social.task,s.social.stickers.task,s.reflection.task,getattr(getattr(s,'planner',None),'task',None)))
                file = directory/'memory-activity.json'
                temp = file.with_suffix('.tmp')
                temp.write_text(json.dumps(value))
                temp.replace(file)
            except OSError: pass  # Optimization telemetry must not break chat.
    def sweep(self, now=None):
        now = time.time() if now is None else now
        if self.closed: return False
        for st in self.stores.values():
            st.ambient.prune(now)
            st.last = {key: at for key, at in st.last.items() if now-at < 86400}
        if now-self.last_activity < 300 or now-self.last_gc < 300: return False
        # Full Python GC touches cold object pages. Do it once per activity
        # epoch, rather than repeatedly faulting an unchanged idle heap back in.
        if self.trimmed_activity == self.last_activity: return False
        if any(self.busy(s) for s in self.stores.values()):return False
        for st in self.stores.values(): st.db.execute('PRAGMA shrink_memory')
        gc.collect()
        # Allocator release concerns this interpreter only, not foreign processes.
        if sys_platform_linux():
            import ctypes
            libc = ctypes.CDLL(None)
            if hasattr(libc, 'malloc_trim'): libc.malloc_trim(0)
        self.last_gc = now
        self.trimmed_activity = self.last_activity
        return True
    async def run(self):
        while not self.closed:
            await asyncio.sleep(60)
            if self.stop_requested():continue
            try:
                for st in list(self.stores.values()):
                    if self.busy(st):self.stamp(st.root)
                if self.favorite_reconciler:
                    self.maintenance_active=True
                    try:await self.favorite_reconciler()
                    finally:self.maintenance_active=False
                if self.stop_requested():continue
                # Existing process-wide maintenance clock, one bounded summary
                # at a time. Persisted rounds/retry state survive a restart.
                for st in list(self.stores.values()):
                    if st.reflection.retry():
                        self.track(st,st.reflection.task);break
                self.sweep()
            except Exception as error:
                self.failures+=1
                # The maintenance owner must survive a bad local callback.
                # Cancellation still propagates (it is a BaseException).
                for st in list(self.stores.values()):
                    try:
                        with st.db:st.reflection.checkpoint_issue('bot_shared' if st.shared_context else 'self',0,'maintenance_failure',{'type':type(error).__name__})
                    except (sqlite3.Error,RuntimeError):pass
    def close(self): self.closed = True


def sys_platform_linux():
    import sys
    return sys.platform == 'linux'
