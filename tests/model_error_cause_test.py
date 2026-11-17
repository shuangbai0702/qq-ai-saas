"""Safe cause evidence; no network, credentials or production writes."""
import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import model_cache as m

class CauseEvidence(unittest.TestCase):
    def test_real_wrapped_transport_cause_without_secret_arguments(self):
        import httpx
        from openai import APIConnectionError
        try:
            try:raise ConnectionResetError(10054,'secret-key and private URL')
            except OSError as cause:raise httpx.ReadError('private response body') from cause
        except httpx.ReadError as cause:
            error=APIConnectionError(request=httpx.Request('POST','https://private.invalid/?key=secret'))
            error.__cause__=cause
        rows=[];attempt={'ended':True,'lease':{},'attemptId':'one','phase':'small_summary','workId':None}
        with patch.object(m,'_attempt',NS(get=lambda:attempt)),patch.object(m,'record_diagnostic',side_effect=lambda _,r:rows.append(r)):
            m.record_error_site(error)
        data=json.loads(rows[0]['diagnosticDetail'])
        self.assertEqual(data['causes'],[{'type':'ReadError'},{'type':'ConnectionResetError','errno':10054}])
        raw=json.dumps(rows);self.assertNotIn('secret',raw);self.assertNotIn('private',raw)
        self.assertLess(len(rows[0]['diagnosticDetail']),1024)

    def test_cycle_and_depth_are_bounded(self):
        errors=[RuntimeError(str(i)) for i in range(9)]
        for a,b in zip(errors,errors[1:]):a.__cause__=b
        self.assertEqual(len(m.error_site_metadata(errors[0])['causes']),4)
        errors[1].__cause__=errors[0]
        self.assertEqual(len(m.error_site_metadata(errors[0])['causes']),1)

    def test_suppressed_context_and_raising_metadata_are_safe(self):
        error=ValueError('secret');error.__context__=RuntimeError('hidden');error.__suppress_context__=True
        self.assertNotIn('causes',m.error_site_metadata(error))
        class Faulty(Exception):
            @property
            def errno(self):raise RuntimeError('sensitive')
        error.__cause__=Faulty()
        self.assertEqual(m.error_site_metadata(error)['causes'],[{'type':'Faulty'}])

if __name__=='__main__':unittest.main()
