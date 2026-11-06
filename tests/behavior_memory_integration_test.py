import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, call

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'adapters/astrbot')]
import runtime_memory as memory
from runtime_care import Ambient, RuntimeCare
from behavior import BehaviorStore, clean_text, is_no_reply_marker, suppress_separate_reasoning


class VisibleOutputTerminalTests(unittest.TestCase):
    def test_no_reply_marker_is_exact_not_a_substring_filter(self):
        self.assertTrue(is_no_reply_marker(' [NO_REPLY] '))
        self.assertEqual(clean_text('[NO_REPLY]'),[])
        for text in ('quoted: [NO_REPLY]', '她说[NO_REPLY]，我没听懂。', '正常的 Planning reply'):
            self.assertFalse(is_no_reply_marker(text))
            self.assertTrue(clean_text(text),text)

    def test_only_typed_reasoning_only_response_is_suppressed(self):
        from types import SimpleNamespace as NS
        extra={};event=NS(set_extra=lambda k,v:extra.__setitem__(k,v))
        response=NS(raw_completion={'choices':[{'message':{'content':'','reasoning_content':'private thought'},'finish_reason':'stop'}]},completion_text='private thought')
        self.assertTrue(suppress_separate_reasoning(response,event))
        self.assertEqual(response.completion_text,'')
        self.assertTrue(extra['_hosted_reply_withheld'])
        normal=NS(raw_completion={'choices':[{'message':{'content':'**Planning**\nThe answer is ready.','reasoning_content':'separate thought'},'finish_reason':'stop'}]},completion_text='**Planning**\nThe answer is ready.')
        self.assertFalse(suppress_separate_reasoning(normal,event))
        self.assertEqual(normal.completion_text,'**Planning**\nThe answer is ready.')
        tool=NS(raw_completion={'choices':[{'message':{'content':'','reasoning_content':'private thought','tool_calls':[{'id':'call-1'}]},'finish_reason':'tool_calls'}]},completion_text='')
        self.assertFalse(suppress_separate_reasoning(tool,event))


class MemoryPolicy(unittest.TestCase):
    def sample(self, at, **kw):
        return dict(at=at, created=1, cpu=0, activity=0, busy=False,
                    available=500*memory.MIB, total=2000*memory.MIB, platform='linux', **kw)
    def ready(self):
        state = {}
        memory.decision(state, self.sample(0))
        self.assertEqual(memory.decision(state, self.sample(301)), 'reclaim')
        return state
    def test_idle_epoch_and_activity(self):
        s=self.ready();s['trimmedEpoch']=s['lastBusy']
        self.assertEqual(memory.decision(s,self.sample(330)),'already-reclaimed')
        v=self.sample(340);v['activity']=340
        self.assertEqual(memory.decision(s,v),'waiting-idle')
        v=self.sample(641);v['activity']=340
        self.assertEqual(memory.decision(s,v),'reclaim')
    def test_generation_replacement(self):
        s=self.ready();v=self.sample(400);v['created']=2
        self.assertEqual(memory.decision(s,v),'waiting-idle')
        self.assertIsNone(s['trimmedEpoch'])
    def test_active_model_is_not_trimmed(self):
        s=self.ready();v=self.sample(500);v['busy']=True
        self.assertEqual(memory.decision(s,v),'active')
    def test_cpu_activity(self):
        s=self.ready();v=self.sample(330);v['cpu']=5
        self.assertEqual(memory.decision(s,v),'waiting-idle')
    def test_linux_pressure_gate_and_reserve(self):
        s=self.ready();v=self.sample(330);v['available']=1000*memory.MIB
        self.assertEqual(memory.decision(s,v),'no-memory-pressure')
        v['available']=383*memory.MIB;v['at']=331
        self.assertEqual(memory.decision(s,v),'reserve-protection')
        v['available']=384*memory.MIB;v['at']=332
        self.assertEqual(memory.decision(s,v),'reclaim')
    def test_windows_not_dependent_on_linux_pressure(self):
        s=self.ready();v=self.sample(330);v.update(platform='win32',available=1000*memory.MIB)
        self.assertEqual(memory.decision(s,v),'reclaim')
    def test_attempt_cooldown(self):
        s=self.ready();s['nextAllowed']=601
        self.assertEqual(memory.decision(s,self.sample(330)),'cooldown')
    def test_one_minute_idle_boundary_on_both_platforms(self):
        for platform in ('win32','linux'):
            with self.subTest(platform=platform):
                state={}
                def sample(at):
                    value=self.sample(at);value.update(platform=platform,available=500*memory.MIB)
                    return value
                self.assertEqual(memory.decision(state,sample(0)),'waiting-idle')
                self.assertEqual(memory.decision(state,sample(59)),'waiting-idle')
                self.assertEqual(memory.decision(state,sample(60)),'reclaim')
                state['trimmedEpoch']=state['lastBusy'];state['nextAllowed']=360
                value=sample(70);value['activity']=70
                self.assertEqual(memory.decision(state,value),'waiting-idle')
                value=sample(130);value['activity']=70
                self.assertEqual(memory.decision(state,value),'cooldown')
    def test_replaced_record_cannot_trigger_native_advice(self):
        with tempfile.TemporaryDirectory() as tmp:
            row=dict(id='probe',kind='protocol',pid=123,created=1,root=tmp)
            (Path(tmp)/'protocol.process.json').write_text(json.dumps({**row,'created':2}))
            with patch.object(memory,'reclaim') as native:
                result=memory.sweep([row])
                native.assert_not_called()
                self.assertEqual(result[0]['decision'],'skipped')
    def test_live_shared_activity_blocks_protocol(self):
        import psutil
        with tempfile.TemporaryDirectory() as tmp:
            time_now=__import__('time').time()
            value=dict(at=time_now,pid=os.getpid(),created=psutil.Process().create_time(),active=True)
            (Path(tmp)/'memory-activity.json').write_text(json.dumps(value))
            self.assertEqual(memory.activity(Path(tmp),time_now),(time_now,True))
    def test_stale_activity_lifetime_is_not_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            value=dict(at=1,pid=os.getpid(),created=0,active=True)
            (Path(tmp)/'memory-activity.json').write_text(json.dumps(value))
            self.assertEqual(memory.activity(Path(tmp),2),(1,False))
    def test_pid_reuse_refused(self):
        row=dict(pid=os.getpid(),created=0,root=str(ROOT))
        with self.assertRaises(ValueError):memory.owned(row)
    def test_linux_missing_swap_falls_back(self):
        with patch.object(memory,'owned'),patch.object(memory.psutil,'swap_memory') as swap:
            swap.return_value.total=0
            self.assertEqual(memory.reclaim({},'linux')['reason'],'no-swap')
    def test_ranges_exclude_locked_file_shared(self):
        # Each address is hex, including the first start.
        text='1000-2000 rw-p 00000000 00:00 0\nRss: 4 kB\nAnonymous: 4 kB\nVmFlags: rd wr\n'
        text+='2000-3000 rw-p 00000000 00:00 0\nRss: 4 kB\nAnonymous: 4 kB\nVmFlags: lo\n'
        text+='3000-4000 rw-s 00000000 00:00 0\nRss: 4 kB\nAnonymous: 4 kB\n'
        text+='4000-5000 rw-p 00000000 00:00 123\nRss: 4 kB\nAnonymous: 4 kB\n'
        self.assertEqual(list(memory.ranges(text)),[(4096,8192)])
    def test_owned_tree_includes_real_worker_but_not_foreign_process(self):
        import psutil
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp)/'worker.py'
            launcher = Path(tmp)/'launcher.py'
            worker.write_text('import time; time.sleep(30)')
            launcher.write_text('import subprocess,sys,time; subprocess.Popen([sys.executable, '+repr(str(worker))+']); time.sleep(30)')
            parent = subprocess.Popen([sys.executable,str(launcher)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            foreign_script = Path(tmp)/'foreign.py'
            foreign_script.write_text('import time; time.sleep(30)')
            foreign = subprocess.Popen([sys.executable,str(foreign_script)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                row=dict(pid=parent.pid,created=psutil.Process(parent.pid).create_time(),root=tmp)
                # The Windows venv launcher can add an interpreter between the
                # first scan and reclaim. Wait for the fixture worker, then use
                # one owned snapshot for the native API assertions.
                deadline=time.monotonic()+5
                while True:
                    tree=memory.owned_tree(row)
                    if any(str(worker) in process.cmdline() for process,_ in tree):break
                    if time.monotonic()>=deadline:self.fail('fixture worker did not start')
                    time.sleep(.02)
                pids={process.pid for process,_ in tree}
                self.assertIn(parent.pid,pids)
                self.assertNotIn(foreign.pid,pids)
                self.assertGreaterEqual(len(tree),2)
                if sys.platform=='win32':
                    with patch.object(memory,'owned_tree',return_value=tree),patch('ctypes.WinDLL') as dll:
                        kernel=dll.return_value
                        api=dll.return_value
                        handles=list(range(101,101+len(tree)))
                        kernel.OpenProcess.side_effect=handles
                        api.EmptyWorkingSet.return_value=1
                        result=memory.reclaim(row)
                    self.assertEqual(result['strategy'],'windows-idle-working-set-tree')
                    self.assertEqual(result['processesReclaimed'],len(tree))
                    opened={entry.args[2] for entry in kernel.OpenProcess.call_args_list}
                    self.assertEqual(opened,pids)
                    self.assertEqual(api.EmptyWorkingSet.call_count,len(tree))
                    self.assertEqual(kernel.CloseHandle.call_args_list,[call(handle) for handle in handles])
                else:
                    child=next(process for process,_ in tree if process.pid!=parent.pid)
                    with patch.object(memory,'owned_tree',return_value=tree), \
                         patch.object(memory.psutil,'swap_memory') as swap, \
                         patch.object(memory.os,'pidfd_open',side_effect=[10,11]) as pidfd, \
                         patch.object(memory.os,'close') as close, \
                         patch.object(memory,'ranges',return_value=[(4096,8192)]), \
                         patch.object(memory.Path,'read_text',return_value=''), \
                         patch.object(memory.ctypes,'CDLL') as libc_mock:
                        swap.return_value.total=memory.MIB
                        libc_mock.return_value.process_madvise.return_value=4096
                        result=memory.reclaim(row)
                    self.assertEqual(result['processesScanned'],2)
                    self.assertEqual(result['advisedVirtualBytes'],8192)
                    self.assertEqual(pidfd.call_count,2)
                    self.assertEqual(close.call_args_list,[call(10),call(11)])
                    self.assertEqual(libc_mock.return_value.process_madvise.call_count,2)
                    self.assertNotEqual(child.pid,foreign.pid)
            finally:
                for proc in (parent,foreign):
                    descendants=psutil.Process(proc.pid).children(recursive=True)
                    for child in reversed(descendants):
                        try:child.terminate()
                        except psutil.NoSuchProcess:pass
                    proc.terminate();proc.wait(5)
                    psutil.wait_procs(descendants,timeout=5)

    def test_actual_owned_child_native_windows_or_linux_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'fixture.py';script.write_text("import time;from pathlib import Path;Path(__file__).with_suffix('.ready').write_text('ready');time.sleep(30)")
            p=subprocess.Popen([sys.executable,str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                import psutil
                deadline=time.monotonic()+5
                while not script.with_suffix('.ready').exists():
                    if p.poll() is not None or time.monotonic()>=deadline:self.fail('owned child did not finish exec')
                    time.sleep(.01)
                row=dict(pid=p.pid,created=psutil.Process(p.pid).create_time(),root=tmp)
                self.assertEqual(memory.owned(row).pid,p.pid)
                if sys.platform=='win32':
                    result=memory.reclaim(row)
                    self.assertEqual(result['strategy'],'windows-idle-working-set-tree')
                    self.assertGreaterEqual(result['processesReclaimed'],1)
                else:
                    with patch.object(memory.psutil,'swap_memory') as swap:
                        swap.return_value.total=0
                        self.assertEqual(memory.reclaim(row)['strategy'],'system-managed')
            finally:
                descendants=psutil.Process(p.pid).children(recursive=True)
                for child in reversed(descendants):
                    try:child.terminate()
                    except psutil.NoSuchProcess:pass
                p.terminate();p.wait(5);psutil.wait_procs(descendants,timeout=5)


class BehaviorIntegration(unittest.TestCase):
    def test_ambient_scope_ttl_limit(self):
        a=Ambient();b=Ambient()
        for i in range(30):a.observe('group:1',str(i),'a'*300,i)
        self.assertEqual(len(a.context('group:1',30)),30)
        self.assertEqual(len(a.context('group:1',30)[0]['text']),180)
        self.assertEqual(b.context('group:1',30),[])
        self.assertEqual(a.context('group:2',30),[])
        self.assertEqual(a.context('group:1',700),[])
    def test_ambient_private_and_scope_bound(self):
        a=Ambient();a.observe('private:1','u','secret',1)
        self.assertEqual(a.context('private:1',1),[])
        for i in range(150):a.observe('group:'+str(i),'u','x',2)
        self.assertEqual(len(a.scopes),128)
    def test_clean_upstream_tags_not_dialogue(self):
        text='鱼鱼<system_reminder>User ID: 123, Nickname: A</system_reminder><｜DSML｜function_calls>secret[MEME:hug]</｜DSML｜function_calls>在呢'
        self.assertEqual(clean_text(text),[('text','鱼鱼在呢')])
    def test_idle_gc_keeps_durable_memory_and_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=BehaviorStore(Path(tmp)/'behavior.sqlite');stores={'a':s};care=RuntimeCare(stores)
            try:
                s.observe('group:1','u','记住：饮料=茶','e')
                care.last_activity=0;care.last_gc=0;s.active=1
                self.assertFalse(care.sweep(1000));s.active=0
                self.assertTrue(care.sweep(1000))
                self.assertFalse(care.sweep(1600),'unchanged idle heap must not be paged in by periodic GC')
                care.last_activity=1700
                self.assertTrue(care.sweep(2100),'new activity permits the next idle collection')
                self.assertEqual(s.observe('group:1','u','饮料','e2')['facts'][0]['value'],'茶')
                care.close();care.close();self.assertFalse(care.sweep(2000))
            finally:s.close()
    def test_shared_activity_is_aggregate_but_member_files_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=root/'group';group.mkdir()
            a=BehaviorStore(root/'a/behavior.sqlite');b=BehaviorStore(root/'b/behavior.sqlite')
            care=RuntimeCare({'a':a,'b':b});b.active=1
            try:
                with patch.dict(os.environ,{'ASTRBOT_ROOT':str(group)}):care.stamp(a.root)
                self.assertFalse(json.loads((a.root/'memory-activity.json').read_text())['active'])
                self.assertTrue(json.loads((group/'memory-activity.json').read_text())['active'])
                care.close()
            finally:a.close();b.close()

    def test_debounce_and_native_queue_are_not_idle(self):
        import asyncio
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            st=BehaviorStore(Path(tmp)/'behavior.sqlite');care=RuntimeCare({'a':st})
            queue=asyncio.Queue();core=SimpleNamespace(event_queue=queue,event_bus=SimpleNamespace(_pending_tasks={}))
            try:
                self.assertTrue(care.status(core)['idle'])
                for field,value in [('batches',{'group:1':[object()]}),('ingress',1),('waiting',1)]:
                    previous=getattr(st,field);setattr(st,field,value)
                    self.assertTrue(care.busy(st));self.assertFalse(care.status(core)['idle'])
                    care.last_activity=0;care.last_gc=0;self.assertFalse(care.sweep(1000))
                    setattr(st,field,previous)
                queue.put_nowait(object());self.assertFalse(care.status(core)['idle']);queue.get_nowait()
                # Match the native registry contract: tasks are its keys.
                class Pending:
                    def done(self):return False
                core.event_bus._pending_tasks={Pending():object()}
                self.assertEqual(care.status(core)['pending'],1);self.assertFalse(care.status(core)['idle'])
                core.event_bus._pending_tasks.clear();self.assertTrue(care.status(core)['idle'])
                self.assertFalse(care.status(SimpleNamespace())['idle'],'missing native queue must fail closed')
            finally:care.close();st.close()


if __name__=='__main__':unittest.main(verbosity=2)
