import importlib.util,pathlib,tempfile,unittest
p=pathlib.Path(__file__).resolve().parents[1]/'scripts/windows-fixed-web-tunnel.py';s=importlib.util.spec_from_file_location('fixed_tunnel',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class FixedTunnel(unittest.TestCase):
 def test_only_named_token_file_and_http2_are_used(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);token=root/'token';token.write_text('synthetic')
   config={k:str(root/k) for k in ['sourceRoot','stateRoot','cloudflared']};config['tokenFile']=str(token)
   command=m.tunnel_command(config)
   self.assertEqual(command[-3:],['run','--token-file',str(token)])
   self.assertIn('http2',command);self.assertNotIn('--url',command);self.assertNotIn('synthetic',' '.join(command))
 def test_missing_token_and_relative_paths_do_not_launch(self):
  root=pathlib.Path.cwd();config={k:str(root/k) for k in ['sourceRoot','stateRoot','cloudflared','tokenFile']}
  with self.assertRaises(ValueError):m.tunnel_command(config)
  config['tokenFile']='relative-token'
  with self.assertRaises(ValueError):m.tunnel_command(config)
if __name__=='__main__':unittest.main()
