import importlib.util
import pathlib
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('web_bridge',pathlib.Path(__file__).resolve().parents[1]/'scripts/windows-web-entry-bridge.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


class BridgeContract(unittest.TestCase):
    def config(self):
        root=pathlib.Path.cwd().resolve()
        return dict(sshHost='192.0.2.1',sshUser='fishbot-web-entry',localPort=48880,httpHost='shuangbai.online',
                    **{k:str(root/k)for k in ('sourceRoot','stateRoot','ssh','privateKey','knownHosts','cloudflared','dnsConfig')})

    def test_only_loopback_and_pinned_ssh(self):
        command=mod.ssh_command(self.config())
        self.assertIn('127.0.0.1:48880:127.0.0.1:80',command)
        for option in ('BatchMode=yes','IdentitiesOnly=yes','StrictHostKeyChecking=yes','ExitOnForwardFailure=yes'):
            self.assertIn(option,command)
        self.assertIn('-N',command);self.assertIn('-T',command)

    def test_rejects_header_injection_and_business_api_port(self):
        for field,value in [('httpHost','shuangbai.online\r\nX: x'),('localPort',8787),('sshUser','root -o StrictHostKeyChecking=no')]:
            config=self.config();config[field]=value
            with self.assertRaises(ValueError):mod.validate(config)

    def test_health_cannot_fall_back_to_predeployment_api(self):
        seen=[]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"ok":true,"publicOrigin":"https://shuangbai.online"}'
        class Opener:
            def open(self,request,timeout):seen.append(request.full_url);return Response()
        with patch.object(mod.urllib.request,'build_opener',return_value=Opener()):
            self.assertTrue(mod.origin_healthy(self.config()))
        self.assertEqual(seen,['http://127.0.0.1:48880/v1/health'])
        with patch.object(mod.urllib.request,'build_opener',side_effect=OSError('down')):
            self.assertFalse(mod.origin_healthy(self.config()))


if __name__=='__main__':unittest.main()
