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
    def observe(self, scope, sender, text, now, limit=12):
        self.prune(now)
        if not scope.startswith('group:') or not text.strip(): return
        limit=max(1,min(50,int(limit)))
        rows = self.scopes.pop(scope, deque(maxlen=50))
        rows.append({'sender_id': sender, 'text': text[:180], 'at': now})
        while len(rows)>limit:rows.popleft()
        self.scopes[scope] = rows
        while len(self.scopes) > 128: self.scopes.popitem(last=False)
    def context(self, scope, now):
        self.prune(now)
        return [{'sender_id': x['sender_id'], 'text': x['text']} for x in self.scopes.get(scope, [])]
    def prune(self, now):
        for scope, rows in list(self.scopes.items()):
            while rows and (now-rows[0]['at'] > 600 or rows[0]['at'] > now): rows.popleft()
            if not rows: self.scopes.pop(scope, None)


class RuntimeCare:
    @staticmethod
    def busy(store):
        return bool(store.active or any(task and not task.done() for task in (store.social.task, store.social.stickers.task, store.reflection.task, getattr(getattr(store,'planner',None),'task',None))))

    def track(self, store, task):
        if not task or task.done() or getattr(task, '_hosted_care', False): return
        task._hosted_care = True
        self.stamp(store.root)
        def completed(_):
            if store in self.stores.values(): self.stamp(store.root)
        task.add_done_callback(completed)

    def __init__(self, stores):
        self.stores, self.last_activity, self.closed = stores, time.time(), False
        self.created = psutil.Process().create_time()
        self.last_gc = self.last_activity
        self.trimmed_activity = None
        self.failures = 0
    def stamp(self, root):
        if self.closed: return
        self.last_activity = time.time()
        # Root is supplied by the platform adapter, never by a QQ message.
        roots = {Path(root)}
        if os.getenv('ASTRBOT_ROOT'): roots.add(Path(os.environ['ASTRBOT_ROOT']))
        value = {'at': self.last_activity, 'pid': os.getpid(), 'created': self.created,
                 'active': any(self.busy(s) for s in self.stores.values())}
        for directory in roots:
            try:
                value['active']=any(self.busy(s) for s in self.stores.values() if directory!=Path(root) or s.root==directory)
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
        if any(getattr(getattr(s,'planner',None),'task',None) and not s.planner.task.done() for s in self.stores.values()):return False
        if any(s.active or s.batches or (s.social.task and not s.social.task.done()) or (s.social.stickers.task and not s.social.stickers.task.done()) for s in self.stores.values()): return False
        if any(getattr(s,'reflection',None) and s.reflection.task and not s.reflection.task.done() for s in self.stores.values()): return False
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
            try:
                for st in list(self.stores.values()):
                    if self.busy(st):self.stamp(st.root)
                self.sweep()
            except (OSError,sqlite3.Error):self.failures+=1
    def close(self): self.closed = True


def sys_platform_linux():
    import sys
    return sys.platform == 'linux'
