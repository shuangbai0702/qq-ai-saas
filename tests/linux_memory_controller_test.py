"""Focused tests for host memory-controller process ownership and activity."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
script = Path(__file__).resolve().parents[1] / 'scripts' / 'linux-memory-controller.py'
spec = importlib.util.spec_from_file_location('linux_memory_controller', script)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ActivityTest(unittest.TestCase):
    def test_cgroup_disappearance_after_enumeration_is_benign(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'cgroup.procs'
            path.write_text('12\n34\n')
            self.assertEqual(controller.cgroup_process_ids(path), ['12', '34'])
            path.unlink()
            self.assertEqual(controller.cgroup_process_ids(path), [])
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                controller.cgroup_process_ids(path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR'))
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.now = 1_700_000_000.0
        self.boot = 1_699_000_000
        self.original_read_text = Path.read_text
        self.ticks = 100
        clock = patch.object(controller.os, 'sysconf', return_value=self.ticks, create=True)
        clock.start()
        self.addCleanup(clock.stop)
        self.rows = [{'pid': 900, 'created': str(3000 * self.ticks)}]
        self.status = 'Name:\tpython\nNSpid:\t900\t42\n'
        self.patcher = patch.multiple(controller, DATA=self.data)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.patcher_time = patch.object(controller.time, 'time', return_value=self.now)
        self.patcher_time.start()
        self.addCleanup(self.patcher_time.stop)
        self.patcher_stat = patch.object(Path, 'read_text', autospec=True, side_effect=self.read_text)
        self.patcher_stat.start()
        self.addCleanup(self.patcher_stat.stop)

    def read_text(self, path, *args, **kwargs):
        path = Path(path)
        if path == Path('/proc/stat'):
            return f'btime {self.boot}\n'
        if path == Path('/proc/900/status'):
            if self.status is None:
                raise FileNotFoundError(path)
            return self.status
        return self.original_read_text(path, *args, **kwargs)

    def write_activity(self, rel, **overrides):
        path = self.data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {'pid': 42, 'created': self.boot + 3000, 'at': self.now - 1, 'active': False}
        value.update(overrides)
        path.write_text(json.dumps(value))

    def test_completed_request_wakes_on_both_supported_paths(self):
        for rel in ('bots/a/memory-activity.json', 'bots/b/ai/memory-activity.json'):
            with self.subTest(path=rel):
                self.write_activity(rel)
                self.assertEqual(controller.activity(self.rows), (self.now - 1, False))
                (self.data / rel).unlink()

    def test_recent_active_request_is_busy_and_stale_one_is_not(self):
        self.write_activity('bots/a/ai/memory-activity.json', active=True)
        self.assertTrue(controller.activity(self.rows)[1])
        self.write_activity('bots/a/ai/memory-activity.json', active=True, at=self.now - 121)
        self.assertFalse(controller.activity(self.rows)[1])

    def test_rejects_pid_reuse_bad_time_and_nonfinite_values(self):
        path = 'bots/a/memory-activity.json'
        for fields in [
            {'pid': 43}, {'created': self.boot + 3001}, {'at': self.now + 6},
            {'at': float('nan')}, {'created': float('inf')}, {'at': self.boot + 2999},
        ]:
            self.write_activity(path, **fields)
            self.assertEqual(controller.activity(self.rows), (0, False))

    def test_process_exit_while_reading_status_is_ignored(self):
        self.write_activity('bots/a/memory-activity.json')
        self.status = None
        self.assertEqual(controller.activity(self.rows), (0, False))

    def test_shared_traffic_does_not_wake_unrelated_dedicated_ai(self):
        self.write_activity('bots/shared/ai/memory-activity.json', active=True)
        with patch.object(controller, 'EXTRA_CONTAINERS', {'postgres': 'pg', 'control-api': 'api'}):
            wakes = controller.activity_wakes(self.rows, {900: 'shared-ai'}, self.now)
        self.assertTrue(wakes['shared-ai'])
        self.assertFalse(wakes.get('napcat',False))
        self.assertFalse(wakes.get('postgres',False))
        self.assertFalse(wakes.get('control-api',False))
        self.assertFalse(wakes['dedicated-ai'])

    def test_dedicated_traffic_does_not_wake_shared_ai(self):
        self.write_activity('bots/dedicated/memory-activity.json')
        wakes = controller.activity_wakes(self.rows, {900: 'dedicated-ai'}, self.now)
        self.assertTrue(wakes['dedicated-ai'])
        self.assertFalse(wakes['shared-ai'])


class ScopeTest(unittest.TestCase):
    def test_futile_pageout_backs_off_and_effective_pageout_resets(self):
        self.assertEqual(controller.reclaim_delay(10_000_000, 10_000_000, 0), (80, 1))
        self.assertEqual(controller.reclaim_delay(None, None, 5), (600, 6))
        self.assertEqual(controller.reclaim_delay(10_000_000, 5_000_000, 5), (40, 0))
    def test_scope_restores_docker_parent_on_controller_reentry(self):
        state = {'State': {'Running': True, 'Pid': 900}}
        with patch.object(Path, 'read_text', return_value='0::/system.slice/docker-abc.scope/fishbot-api\n'):
            self.assertEqual(controller.container_scope(state), Path('/sys/fs/cgroup/system.slice/docker-abc.scope'))

    def test_stopped_or_foreign_scope_is_rejected(self):
        with self.assertRaises(RuntimeError):
            controller.container_scope({'State': {'Running': False, 'Pid': 0}})
        with patch.object(Path, 'read_text', return_value='0::/system.slice/other.service\n'):
            with self.assertRaises(RuntimeError):
                controller.container_scope({'State': {'Running': True, 'Pid': 900}})


class ClassificationTest(unittest.TestCase):
    def test_dedicated_astrbot_and_shared_worker_classification(self):
        self.assertEqual(controller.classify({'argv': 'python headless.py', 'ppid': 1}, {}), 'dedicated-ai')
        self.assertEqual(controller.classify({'argv': 'python shared-worker.py', 'ppid': 1}, {}), 'shared-ai')
        self.assertEqual(controller.classify({'argv': 'python worker.py', 'ppid': 1}, {}), 'dedicated-ai')

    def test_instance_quota_follows_tree_not_number_of_shared_members(self):
        rows = [{'pid': 10, 'ppid': 1, 'created': '100'},
                {'pid': 11, 'ppid': 10, 'created': '101'},
                {'pid': 20, 'ppid': 1, 'created': '200'},
                {'pid': 30, 'ppid': 1, 'created': '300'}]
        owners = controller.instance_owners(rows, {10:'napcat',11:'napcat',20:'napcat',30:'shared-ai'})
        self.assertEqual(owners[10], owners[11])
        self.assertNotEqual(owners[10], owners[20])
        self.assertEqual(owners[30], 'shared-ai:30-300')

    def test_only_busy_runtime_wakes_among_shared_instances(self):
        rows = [{'pid': 10}, {'pid': 20}]
        with patch.object(controller, 'activity', side_effect=lambda rs,records=None:(100, rs[0]['pid']==10) if rs else (0,False)):
            wakes=controller.activity_wakes(rows,{10:'shared-ai:10-1',20:'shared-ai:20-2'},200)
        self.assertTrue(wakes['shared-ai:10-1'])
        self.assertFalse(wakes['shared-ai:20-2'])

    def test_activity_files_are_loaded_once_for_many_instances(self):
        rows=[{'pid':n} for n in range(20)]
        with patch.object(controller,'activity_records',return_value=[]) as load, patch.object(controller,'activity',return_value=(0,False)):
            controller.activity_wakes(rows,{n:f'shared-ai:{n}-1' for n in range(20)},200)
        load.assert_called_once()


class RecoveryTest(unittest.TestCase):
    def test_exact_runtime_targets_and_aggregate_count(self):
        with patch.dict(os.environ, {}, clear=True):
            for role, mib in [('napcat', 48), ('shared-ai', 72), ('dedicated-ai', 24)]:
                self.assertEqual(controller.idle_target(role), mib*1048576)
                self.assertEqual(controller.idle_target(role, 3), 3*mib*1048576)
        with patch.dict(os.environ, {'FISHBOT_MEMORY_SHARED_AI_MIB': '80'}):
            self.assertEqual(controller.idle_target('shared-ai'), 80*1048576)

    def test_guard_cooldown_waits_without_reentering_controller(self):
        with tempfile.TemporaryDirectory() as root, patch.object(controller, 'BASE', Path(root)):
            disabled = Path(root)/'disabled'
            disabled.write_text('pressure')
            sleeps = []
            def sleep(seconds):
                sleeps.append(seconds)
                if len(sleeps) == 3:
                    disabled.unlink()
            def control():
                if control.calls == 0:
                    control.calls += 1
                    disabled.write_text('pressure-again')
                else:
                    raise SystemExit(0)
            control.calls = 0
            def resumed_sleep(seconds):
                sleep(seconds)
                if len(sleeps) == 5:
                    disabled.unlink()
            with patch.object(controller.time, 'sleep', side_effect=resumed_sleep), patch.object(controller, 'main', side_effect=control) as main:
                with self.assertRaises(SystemExit):
                    controller.run()
            self.assertEqual(main.call_count, 2)
            self.assertEqual(sleeps, [1]*5)

    def test_idle_central_agent_stays_unthrottled_while_other_groups_reclaim(self):
        class StopLoop(Exception):
            pass
        with tempfile.TemporaryDirectory() as root:
            base, parent = Path(root)/'state', Path(root)/'docker-abc.scope'
            base.mkdir(); parent.mkdir()
            (base/'guard-heartbeat').touch()
            (parent/'cgroup.procs').write_text('')
            for role in ['api', 'shared-ai', 'dedicated-ai', 'napcat', 'mock']:
                group = parent/('fishbot-'+role)
                group.mkdir()
                for name, value in [('cgroup.procs', ''), ('memory.high', 'max'), ('memory.current', '0'), ('memory.swap.current', '0')]:
                    (group/name).write_text(value)
            now = [100]
            def sleep(seconds):
                if now[0] == 100:
                    now[0] = 140
                else:
                    raise StopLoop()
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                if str(path) == str(Path('/proc/sys/kernel/random/boot_id')):
                    return 'boot'
                return original_read(path, *args, **kwargs)
            state = {'Id': 'abc', 'State': {'Pid': 1, 'StartedAt': 'start'}}
            fake_lock = types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2)
            with patch.dict(sys.modules, {'fcntl': fake_lock}), patch.multiple(controller, BASE=base, DATA=base, EXTRA_CONTAINERS={}), patch.object(controller, 'container_scope', return_value=parent), patch.object(controller.subprocess, 'check_output', return_value=json.dumps([state])), patch.object(controller.time, 'time', side_effect=lambda: now[0]), patch.object(controller.time, 'sleep', side_effect=sleep), patch.object(Path, 'read_text', autospec=True, side_effect=read):
                with self.assertRaises(StopLoop):
                    controller.main()
            groups = json.loads((base/'controller-state.json').read_text())['groups']
            self.assertEqual(groups['api']['high'], 'max')
            self.assertEqual(groups['mock']['high'], str(controller.IDLE_MIB*1048576))
            self.assertFalse((base/'armed').exists())
            self.assertEqual((parent/'fishbot-mock/memory.high').read_text(), 'max')

    def test_ramp_reaches_original_target_in_bounded_steps(self):
        mib=1048576
        value=controller.ramp_high('max',201*mib,48*mib,0)
        self.assertEqual(value,str(201*mib))
        self.assertEqual(controller.ramp_high(value,201*mib,48*mib,4),value)
        for _ in range(10):
            nxt=controller.ramp_high(value,201*mib,48*mib,5)
            self.assertLessEqual(int(value)-int(nxt),32*mib)
            value=nxt
        self.assertEqual(value,str(48*mib))

    def test_reclaim_state_survives_restart_but_not_boot_or_container_change(self):
        with tempfile.TemporaryDirectory() as root, patch.object(controller,'BASE',Path(root)):
            key=(42,'3000')
            controller.save_reclaim('container-a','boot-a',{key:1100},{key:3},{key:0x4000000})
            allowed,failures,cursors=controller.load_reclaim('container-a','boot-a',1000)
            self.assertEqual((allowed[key],failures[key],cursors[key]),(1100,3,0x4000000))
            for identity,boot in [('container-b','boot-a'),('container-a','boot-b')]:
                self.assertEqual(controller.load_reclaim(identity,boot,1000),({},{},{}))


if __name__ == '__main__':
    unittest.main()
