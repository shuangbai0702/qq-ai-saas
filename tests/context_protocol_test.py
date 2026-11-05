"""Wire-contract boundaries, including future media; no model/runtime required."""
import copy,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
import context_protocol as protocol

def message(sender='12345678',event='event',first_type='text'):
    return {'role':'user','content':[{'type':first_type,'text':protocol.SOURCE+json.dumps({'scope':'group:55555','sender_id':sender,'event_id':event})+'\nbody'}]}

class WireTests(unittest.TestCase):
    def test_origin_is_identical_at_request_and_summary_boundaries(self):
        from context_transport import model_messages
        from summary_material import source,snapshots
        for sender,event,kind,valid in [('12345678','event','text',True),('9999','event','text',False),('12345678',True,'text',False),('12345678','','text',False),('12345678','event','image_url',False)]:
            original=message(sender,event,kind);projected=model_messages([original])[0]
            self.assertEqual(bool(source(original)),valid)
            self.assertEqual(bool(source(projected)),valid)
            self.assertEqual(bool(snapshots([projected],'group:55555')),valid)
        quoted=message();quoted['content'].insert(0,{'type':'text','text':'quoted: '+quoted['content'][0]['text']})
        self.assertIsNone(protocol.source(quoted))
    def test_unknown_user_reply_or_tool_content_never_proves_a_whole_block(self):
        from summary_material import snapshots,project_history,digest
        for extra in ({'type':'input_audio','input_audio':{'data':'unknown'}},{'type':'video','video':'unknown'},'malformed'):
            old=message();old['content'].append(extra);latest=message(event='new')
            self.assertFalse(snapshots([old],'group:55555')[0]['complete'])
            proof={'event':{'subject':'12345678','digest':digest(old)}}
            self.assertEqual(project_history([old,latest],proof,'group:55555','new'),[old,latest])
        for tail in ({'role':'assistant','content':[{'type':'audio','data':'unknown'}]},{'role':'assistant','content':'text','audio':{'id':'voice'}},{'role':'function','content':'old API output'},{'role':'tool','tool_call_id':'1','content':'result'},'malformed'):
            old=message();latest=message(event='new');block=[old,tail]
            self.assertFalse(snapshots(block,'group:55555')[0]['complete'])
            proof={'event':{'subject':'12345678','digest':digest(old)}}
            self.assertEqual(project_history(block+[latest],proof,'group:55555','new'),block+[latest])
    def test_json_keeps_meaning_order_strings_and_authority(self):
        data={'note':'保持 空格\n与换行','identity':{'owner':False,'sender_id':'12345678'},'messages':[{'id':'2'},{'id':'1'}]};before=copy.deepcopy(data)
        result=protocol.model_json(data)
        self.assertEqual(json.loads(result),data);self.assertEqual(data,before)
        self.assertEqual(list(json.loads(result)),list(data));self.assertIn('保持 空格',result)
    def test_pure_contract_does_not_import_runtime_storage_or_model_dependencies(self):
        code="import sys;sys.path.insert(0,sys.argv[1]);import context_protocol;assert not any(n in sys.modules for n in ('sqlite3','summary_material','behavior','astrbot'))"
        subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'adapters/astrbot')],check=True,capture_output=True)

if __name__=='__main__':unittest.main()
