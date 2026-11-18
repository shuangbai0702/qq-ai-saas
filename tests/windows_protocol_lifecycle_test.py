import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'scripts/protocol_process.py'
CONTROL = ROOT / 'scripts/process_control.py'
SUPERVISOR = ROOT / 'scripts/windows-production-supervisor.py'
SECRET = 'synthetic-private-key'


@unittest.skipUnless(os.name == 'nt', 'requires Windows Job Objects and local WMI')
class WindowsProtocolLifecycle(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory(prefix='fishbot protocol ')
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.bot = self.root / 'bots' / 'protocol-bot'
        self.bot.mkdir(parents=True)
        self.file = self.bot / 'protocol.process.json'
        self.runtime_log = self.bot / 'runtime.log'
        self.worker = self.bot / 'protocol-worker.py'
        self.authority = self.root / 'authority'
        self.env = dict(os.environ, EXTERNAL_AGENT='true')
        self.authority.write_text('local-fixture')
        self.authority_config = self.root / 'protocol-authority.json'
        self.authority_config.write_text(json.dumps(dict(file=str(self.authority), authorityId='local-fixture')))
        self.worker.write_text(
            "import os,subprocess,sys,time\n"
            "from pathlib import Path\n"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(45)'])\n"
            "Path(sys.argv[1]).write_text(str(child.pid))\n"
            "print('synthetic-',end='',flush=True);time.sleep(.1);print('private-key',flush=True)\n"
            "print('x'*70000+os.environ['FIXTURE_SECRET'],flush=True)\n"
            "print('protocol ready',flush=True)\n"
            "while True: time.sleep(.1);print('protocol heartbeat',flush=True)\n",
            encoding='utf-8',
        )
        self.grandchild = self.bot / 'grandchild.pid'
        self.config = dict(
            executable=sys.executable,
            args=[str(self.worker), str(self.grandchild)],
            cwd=str(self.bot), env=dict(os.environ, FIXTURE_SECRET=SECRET), keys=[SECRET],
        )
        self.addCleanup(self.stop)

    def call(self, action, *args, check=True):
        return subprocess.run(
            [sys.executable, '-B', str(PROTOCOL), action, str(self.bot), *map(str, args)],
            input=json.dumps(self.config) if action == 'start' else None,
            capture_output=True, text=True, check=check, timeout=25, env=self.env,
        )

    def stop(self):
        self.call('stop')

    def wait_for(self, read):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                value = read()
                if value:
                    return value
            except (FileNotFoundError, ValueError, psutil.NoSuchProcess):
                pass
            time.sleep(.05)
        self.fail('Timed out waiting for protocol lifecycle evidence')

    def record(self):
        return json.loads(self.file.read_text())

    def test_protocol_survives_nested_agent_and_supervisor_jobs_then_stops(self):
        prior = 'rotation evidence\n' + 'x' * (2 * 1024 * 1024)
        self.runtime_log.write_text(prior, encoding='utf-8', newline='')
        starter = self.root / 'agent_test.py'
        starter.write_text(
            "import os,subprocess,sys,time\n"
            "from pathlib import Path\n"
            "subprocess.run([sys.executable,'-B',sys.argv[1],'start',sys.argv[2]],"
            "input=Path(sys.argv[3]).read_bytes(),check=True)\n"
            "Path(sys.argv[4]).write_text(str(os.getpid()))\n"
            "time.sleep(45)\n",
            encoding='utf-8',
        )
        launcher = self.root / 'supervisor_test.py'
        launcher.write_text(
            "import importlib.util,os,sys,time\n"
            "from pathlib import Path\n"
            "spec=importlib.util.spec_from_file_location('supervisor',sys.argv[1]);"
            "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n"
            "root=Path(sys.argv[2]);job=module.WindowsJob();job.assign()\n"
            "agent=module.Service('agent',[sys.executable,str(root/'agent_test.py'),*sys.argv[3:]],"
            "root,dict(os.environ),root,root,1);agent.start()\n"
            "while not (root/'restart').exists():time.sleep(.05)\n"
            "agent.stop();agent.start();time.sleep(45)\n",
            encoding='utf-8',
        )
        fixture = self.root / 'fake-launch-settings.json'
        fixture.write_text(json.dumps(self.config), encoding='utf-8')
        ready = self.root / 'agent.pid'
        supervisor = subprocess.Popen(
            [sys.executable, '-B', str(launcher), str(SUPERVISOR), str(self.root),
             str(PROTOCOL), str(self.bot), str(fixture), str(ready)], env=self.env,
        )
        try:
            old_agent = self.wait_for(lambda: int(ready.read_text()))
            before = self.record()
            qq = psutil.Process(before['pid'])
            actual = self.wait_for(lambda: qq.children()[0] if qq.children() else None)
            grandchild = psutil.Process(self.wait_for(lambda: int(self.grandchild.read_text())))
            self.wait_for(lambda: 'protocol ready' in self.runtime_log.read_text(encoding='utf-8'))
            text = self.runtime_log.read_text(encoding='utf-8')
            self.assertIn('[REDACTED]', text)
            self.assertNotIn(SECRET, text)
            self.assertNotIn('x' * 100, text)
            with (self.bot / 'runtime.log.previous').open(encoding='utf-8', newline='') as stream:
                self.assertEqual(stream.read(), prior)
            self.assertFalse((self.bot / '.protocol-launch.json').exists())

            (self.root / 'restart').touch()
            next_agent = self.wait_for(lambda: int(text) if (text := ready.read_text()) != str(old_agent) else None)
            self.assertFalse(psutil.pid_exists(old_agent))
            self.assertEqual(self.record(), before)
            before_log_size = self.runtime_log.stat().st_size
            supervisor.kill()
            supervisor.wait(timeout=5)
            self.wait_for(lambda: not psutil.pid_exists(next_agent))
            self.wait_for(lambda: self.runtime_log.stat().st_size > before_log_size)
            self.assertTrue(all(process.is_running() for process in (qq, actual, grandchild)))
            self.call('start')  # A new caller adopts the existing process.
            after = self.record()
            self.assertEqual(after, before)
            self.assertEqual(qq.children()[0].pid, actual.pid)
            def inspect(directory):
                result = subprocess.check_output(
                    [sys.executable, '-B', str(CONTROL), 'inspect', str(before['pid']),
                     str(directory), str(before['created'])],
                    text=True, timeout=5,
                )
                return json.loads(result)['valid']

            self.assertTrue(inspect(self.bot))
            short = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetShortPathNameW(str(self.bot), short, len(short)):
                self.assertTrue(inspect(short.value))
            sibling = self.bot.with_name(self.bot.name + '-other')
            sibling.mkdir()
            self.assertFalse(inspect(sibling))
            self.assertNotEqual(self.call('stop', after['pid'], after['created'] - 1, check=False).returncode, 0)
            self.assertTrue(qq.is_running())
            self.call('stop', after['pid'], after['created'])
            for process in (qq, actual, grandchild):
                process.wait(timeout=5)
            self.assertFalse(self.file.exists())
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=5)

    def test_execution_authority_is_required_and_revocation_ends_the_tree(self):
        original = self.authority_config.read_bytes()
        self.authority_config.unlink()
        refused = self.call('start', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('execution authority is unavailable', refused.stderr)
        self.assertFalse(self.file.exists())
        self.authority_config.write_bytes(original)
        self.call('start')
        qq = psutil.Process(self.record()['pid'])
        grandchild = psutil.Process(self.wait_for(lambda: int(self.grandchild.read_text())))
        self.authority.unlink()
        qq.wait(timeout=5)
        grandchild.wait(timeout=5)
        self.assertIn('execution authority revoked', self.runtime_log.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
