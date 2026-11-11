"""Billing bound and crash recovery of the opt-in synthetic evaluator."""
import asyncio,contextlib,importlib.util,io,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import httpx
spec=importlib.util.spec_from_file_location('prompt_quality_eval',Path(__file__).resolve().parents[1]/'scripts/prompt-quality-eval.py')
evaluation=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluation)


class EvaluationBudget(unittest.IsolatedAsyncioTestCase):
    async def test_failed_request_is_charged_once_and_not_retried_on_resume(self):
        calls=[]
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def post(self,*args,**kwargs):
                calls.append(kwargs)
                raise httpx.ReadTimeout('synthetic transport failure')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cases=root/'cases.json'
            cases.write_text(json.dumps([{'id':'case-A1','body':{'messages':[{'role':'user','content':'synthetic'}]}}]),encoding='utf-8')
            args=NS(output=str(root),cases=str(cases),base_url='https://fixture.invalid/v1',model='fixture',discover=False)
            with patch.dict(os.environ,{'FISHBOT_EVAL_API_KEY':'synthetic-secret'}),patch.object(evaluation.httpx,'AsyncClient',Client),contextlib.redirect_stdout(io.StringIO()):
                await evaluation.run(args);await evaluation.run(args)
            value=json.loads((root/'requests.json').read_text(encoding='utf-8'))
            self.assertEqual(value['attempted'],1);self.assertEqual(len(calls),1)
            self.assertEqual(value['results'][0]['status'],'transport_or_decode_error')
            self.assertNotIn('synthetic-secret',(root/'requests.json').read_text(encoding='utf-8'))

    async def test_exhausted_or_crashed_ledger_never_sends_another_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cases=root/'cases.json'
            cases.write_text(json.dumps([{'id':'next','body':{'messages':[]}}]),encoding='utf-8')
            (root/'requests.json').write_text(json.dumps({'attempted':64,'results':[{'id':'crashed','status':'attempted'}]}),encoding='utf-8')
            args=NS(output=str(root),cases=str(cases),base_url='https://fixture.invalid/v1',model='fixture',discover=False)
            with patch.dict(os.environ,{'FISHBOT_EVAL_API_KEY':'synthetic-secret'}),patch.object(evaluation.httpx.AsyncClient,'post') as post:
                with self.assertRaisesRegex(RuntimeError,'64 attempted'):
                    await evaluation.run(args)
                post.assert_not_called()
            self.assertEqual(json.loads((root/'requests.json').read_text(encoding='utf-8'))['attempted'],64)


if __name__=='__main__':unittest.main()
