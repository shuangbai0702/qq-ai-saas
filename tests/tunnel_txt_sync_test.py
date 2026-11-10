"""No real DNS calls: gated update boundaries and exact acknowledgement."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,MagicMock,patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tunnel_txt_sync',ROOT/'scripts/tunnel-txt-sync.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)


class TxtSync(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config=dict(domain='shuangbai',authorityValue='windows-target',healthUrl=sync.HEALTH_URL)
        for key in ('tokenFile','logFile','authorityFile','routeEnabledFile','stateFile'):
            self.config[key]=str(self.root/key)
        Path(self.config['tokenFile']).write_text('private-token-fixture')
        Path(self.config['logFile']).write_text('| https://first-tunnel.trycloudflare.com |\n| https://current-77.trycloudflare.com |')
        for key in ('authorityFile','routeEnabledFile'):Path(self.config[key]).write_text('windows-target\n')

    def test_dual_gate_health_and_late_revocation_never_write(self):
        for key in ('authorityFile','routeEnabledFile'):
            file=Path(self.config[key]);file.unlink();update=Mock()
            self.assertEqual(sync.sync_once(self.config,lambda:True,update),'gated');update.assert_not_called()
            file.write_text('windows-target')
        update=Mock()
        self.assertEqual(sync.sync_once(self.config,lambda:False,update),'unhealthy');update.assert_not_called()
        def revoke():
            Path(self.config['routeEnabledFile']).unlink();return True
        self.assertEqual(sync.sync_once(self.config,revoke,update),'gated');update.assert_not_called()

    def test_ko_never_persists_success_ok_is_restart_idempotent(self):
        update=Mock(return_value=False)
        self.assertEqual(sync.sync_once(self.config,lambda:True,update),'update-rejected')
        self.assertFalse(Path(self.config['stateFile']).exists())
        update.return_value=True
        self.assertEqual(sync.sync_once(self.config,lambda:True,update),'updated')
        state=Path(self.config['stateFile']).read_text();self.assertNotIn('private-token-fixture',state)
        self.assertEqual(json.loads(state)['url'],'https://current-77.trycloudflare.com')
        update.reset_mock()
        self.assertEqual(sync.sync_once(self.config,lambda:True,update),'unchanged');update.assert_not_called()
        Path(self.config['logFile']).write_text('https://new-url.trycloudflare.com')
        self.assertEqual(sync.sync_once(self.config,lambda:True,update),'updated')

    def test_only_complete_https_root_quicktunnel_hosts_are_accepted(self):
        file=Path(self.config['logFile'])
        for invalid in ('http://a.trycloudflare.com','https://a.trycloudflare.com.evil.test','https://a.trycloudflare.com@evil.test','https://a.trycloudflare.com/path','https://UPPER.trycloudflare.com','https://-bad.trycloudflare.com','https://bad-.trycloudflare.com','https://a.trycloudflare.com:443'):
            with self.subTest(url=invalid):
                file.write_text(invalid);self.assertIsNone(sync.latest_url(file))
        file.write_text('abc "https://legit-12.trycloudflare.com" end')
        self.assertEqual(sync.latest_url(file),'https://legit-12.trycloudflare.com')

    def test_fixed_endpoints_and_ack_body(self):
        transport=MagicMock();response=transport.open.return_value.__enter__.return_value;response.status=200
        with patch.object(sync,'opener',return_value=transport):
            response.read.return_value=b'KO';self.assertFalse(sync.publish('shuangbai','synthetic-token','https://a.trycloudflare.com'))
            response.read.return_value=b'OK\n';self.assertTrue(sync.publish('shuangbai','synthetic-token','https://a.trycloudflare.com'))
            url=transport.open.call_args.args[0]
            self.assertTrue(url.startswith('https://www.duckdns.org/update?'))
            self.assertIn('domains=shuangbai',url)
            self.assertIn('txt=https%3A%2F%2Fa.trycloudflare.com',url)
        self.config['healthUrl']='https://external.invalid/health'
        with self.assertRaises(ValueError):sync.sync_once(self.config)
        self.config['healthUrl']=sync.HEALTH_URL;self.config['domain']='shuangbai,unrelated'
        with self.assertRaises(ValueError):sync.sync_once(self.config)


if __name__=='__main__':unittest.main()
