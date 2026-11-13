import ast,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

# Load definitions without running the existing process-control command dispatcher.
tree=ast.parse(Path('scripts/process_control.py').read_text(encoding='utf-8'))
namespace={}
exec(compile(ast.Module(body=[x for x in tree.body if isinstance(x,(ast.Import,ast.ImportFrom,ast.FunctionDef))],type_ignores=[]),'process_control.py','exec'),namespace)
activity=namespace['loop_activity']

class LoopActivityTests(unittest.TestCase):
    def test_identity_and_fresh_busy_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'ai/memory-activity.json';file.parent.mkdir()
            child=SimpleNamespace(pid=20,create_time=lambda:100)
            base={'at':190,'pid':20,'created':100,'active':True,'work':{'background':1}}
            def check(value,processes=(child,)):
                file.write_text(json.dumps(value));return activity(directory,processes,200)
            self.assertEqual(check(base)['pid'],20)
            for change in ({'pid':21},{'created':99},{'at':79},{'at':206},{'at':float('nan')},{'active':False},{'work':{}},{'work':{'background':True}},{'work':{'background':float('inf')}}):
                with self.subTest(change=change):self.assertIsNone(check({**base,**change}))
            self.assertIsNone(check(base,()))
            file.write_text('{broken');self.assertIsNone(activity(directory,[child],200))

if __name__=='__main__':unittest.main()
