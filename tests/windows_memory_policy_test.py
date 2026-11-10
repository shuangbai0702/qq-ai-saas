"""Windows policy boundaries and an actual isolated owned-process reclaim."""
import importlib.util
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller = load('windows-memory-controller')
guard = load('windows-memory-guard')


class WindowsPolicy(unittest.TestCase):
    def test_linux_role_budgets_and_backoff(self):
        self.assertEqual([controller.target(role)//1048576 for role in ('napcat','shared-ai','dedicated-ai')], [48,72,24])
        self.assertEqual(controller.IDLE_SECONDS,18)
        self.assertEqual(controller.RECLAIM_SECONDS,40)
        self.assertEqual(controller.delay(10,10,0),(80,1))
        self.assertEqual(controller.delay(10,10,5),(600,6))
        self.assertEqual(controller.delay(2*1048576,0,5),(40,0))

    def test_guard_suspend_and_recovery_does_not_deadlock_on_stale_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);armed=base/'armed';armed.touch();os.utime(armed,(0,0))
            self.assertEqual(guard.reason(base,100,383*1048576,0),'host-available-below-reserve')
            self.assertEqual(guard.reason(base,100,384*1048576,2),'api-health-failed-twice')
            self.assertEqual(guard.reason(base,100,384*1048576,0),'controller-heartbeat-expired')
            (base/'disabled').touch()
            self.assertIsNone(guard.reason(base,100,384*1048576,0))

    def test_restart_retains_backoff_but_new_boot_or_root_drops_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'data';state={(123,5.0):dict(next=200,failures=3)}
            controller.persist(base,root,state)
            restored=controller.restore(base,root,100)
            self.assertEqual(restored[(123,5.0)],dict(cpu=0,busy=100,next=200,failures=3))
            self.assertEqual(controller.restore(base,root/'other',100),{})
            with patch.object(controller.psutil,'boot_time',return_value=0):
                self.assertEqual(controller.restore(base,root,100),{})

    def test_supervisor_service_contract_pins_birth_and_requires_all_base_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);directory=base/'services';directory.mkdir()
            process=types.SimpleNamespace(pid=123,create_time=lambda:5)
            with self.assertRaises(ValueError):guard.service_lifetimes(base)
            for role in ('control-api','agent','postgres'):
                (directory/(role+'.process.json')).write_text(json.dumps(dict(pid=123,created=5,root=tmp)))
            with patch.object(guard,'owned',return_value=process):
                self.assertEqual(guard.service_lifetimes(base),{role:(123,5) for role in ('control-api','agent','postgres')})
            with patch.object(controller,'owned_tree',return_value=[(process,5)]):
                discovered=list(controller.services(base))
                self.assertEqual([item[1] for item in discovered],['control-api','agent','postgres'])
                self.assertTrue(all(item[0]['recordFile'].endswith('.process.json') for item in discovered))
            with patch.object(guard,'owned',side_effect=ValueError('lifetime-mismatch')):
                with self.assertRaisesRegex(ValueError,'lifetime-mismatch'):guard.service_lifetimes(base)

    def test_failed_native_reclaim_backs_off_instead_of_retrying_every_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            row=dict(pid=1,created=2,root=tmp,kind='ai')
            (Path(tmp)/'ai.process.json').write_text(json.dumps(row))
            process=types.SimpleNamespace(cpu_times=lambda:(0,0),memory_info=lambda:types.SimpleNamespace(rss=100*1048576))
            tree=[(process,2)];state={};native=unittest.mock.Mock(side_effect=PermissionError('denied'))
            with patch.object(controller,'activity',return_value=(0,False)):
                controller.step(row,'dedicated-ai',tree,state,100,native)
                result=controller.step(row,'dedicated-ai',tree,state,118,native)
                self.assertEqual(result['decision'],'reclaim-failed')
                self.assertEqual(result['nextAllowed'],198)
                self.assertEqual(controller.step(row,'dedicated-ai',tree,state,119,native)['decision'],'cooldown')
                native.assert_called_once()

    def test_busy_cooldown_record_replacement_and_process_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            row=dict(pid=1,created=2,root=tmp,kind='ai')
            file=Path(tmp)/'ai.process.json';file.write_text(json.dumps(row))
            process=types.SimpleNamespace(cpu_times=lambda:(0,0),memory_info=lambda:types.SimpleNamespace(rss=100*1048576))
            tree=[(process,2)];state={};native=unittest.mock.Mock(return_value={})
            with patch.object(controller,'activity',return_value=(0,False)),patch.object(controller,'owned_tree',return_value=tree):
                self.assertEqual(controller.step(row,'dedicated-ai',tree,state,100,native)['decision'],'active')
                self.assertEqual(controller.step(row,'dedicated-ai',tree,state,117,native)['decision'],'active')
                self.assertEqual(controller.step(row,'dedicated-ai',tree,state,118,native)['decision'],'reclaim')
                native.assert_called_once()
                self.assertEqual(controller.step(row,'dedicated-ai',tree,state,119,native)['decision'],'cooldown')
                file.write_text(json.dumps({**row,'created':3}))
                with self.assertRaisesRegex(ValueError,'process-record-replaced'):
                    controller.step(row,'dedicated-ai',tree,state,200,native)
                native.assert_called_once()
                replacement={**row,'created':3}
                self.assertEqual(controller.step(replacement,'dedicated-ai',tree,state,201,native)['decision'],'active')

    @unittest.skipUnless(sys.platform=='win32','Windows native API')
    def test_actual_owned_worker_trim_survives_and_keeps_memory_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);worker=root/'worker.py'
            worker.write_text("import sys\nb=bytearray(b'x')*(64*1048576)\nprint('ready',flush=True)\nsys.stdin.readline()\nprint(b[0],b[-1],len(b),flush=True)\n")
            child=subprocess.Popen([sys.executable,str(worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                row=dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),root=tmp,kind='ai')
                (root/'ai.process.json').write_text(json.dumps(row))
                tree=controller.owned_tree(row);state={};now=time.time()
                controller.step(row,'dedicated-ai',tree,state,now)
                result=controller.step(row,'dedicated-ai',tree,state,now+18)
                self.assertEqual(result['decision'],'reclaim')
                self.assertGreaterEqual(result['processesReclaimed'],1)
                child.stdin.write('check\n');child.stdin.flush()
                self.assertEqual(child.stdout.readline().strip(),'120 120 67108864')
                self.assertEqual(child.wait(5),0)
            finally:
                if child.poll() is None:
                    child.kill();child.wait(5)
                child.stdin.close();child.stdout.close();child.stderr.close()

    @unittest.skipUnless(sys.platform=='win32','Windows service adapters')
    def test_independent_guard_and_controller_stop_advice_when_guard_exits(self):
        class Health(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'{}')
            def log_message(self,*args):
                pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Health)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        processes=[]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'data';state=Path(tmp)/'state';directory=root/'bots'/'probe'
                directory.mkdir(parents=True)
                worker=directory/'worker.py';worker.write_text("import time\nb=bytearray(b'x')*(64*1048576)\nprint('ready',flush=True)\ntime.sleep(60)\n")
                child=subprocess.Popen([sys.executable,str(worker)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True);processes.append(child)
                self.assertEqual(child.stdout.readline().strip(),'ready')
                row=dict(pid=child.pid,created=psutil.Process(child.pid).create_time())
                (directory/'ai.process.json').write_text(json.dumps(row))
                def start(script,*args,**kwargs):
                    process=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts'/script),*map(str,args)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
                    processes.append(process);return process
                watcher=start('windows-memory-guard.py','--state',state,'--health-url',f'http://127.0.0.1:{server.server_port}/v1/health')
                def wait_for(predicate,seconds=10):
                    end=time.monotonic()+seconds
                    while not predicate():
                        if time.monotonic()>end:self.fail('service adapter deadline exceeded')
                        time.sleep(.05)
                wait_for(lambda:(state/'guard-heartbeat').exists())
                manager=start('windows-memory-controller.py','--root',root,'--state',state,env={**os.environ,'FISHBOT_MEMORY_IDLE_SECONDS':'0.05'})
                def reclaimed():
                    try:
                        value=json.loads((state/'controller-state.json').read_text())
                        return any(row.get('decision')=='reclaim' for row in value['groups'])
                    except (OSError,ValueError):return False
                wait_for(reclaimed)
                watcher.terminate();watcher.wait(5)
                wait_for(lambda:manager.poll() is not None)
                self.assertNotEqual(manager.returncode,0)
                self.assertIsNone(child.poll(),'controller exit must not kill the owned runtime')
        finally:
            for process in reversed(processes):
                if process.poll() is None:process.kill();process.wait(5)
                if process.stdout:process.stdout.close()
            server.shutdown();server.server_close();thread.join(5)


if __name__ == '__main__':
    unittest.main()
