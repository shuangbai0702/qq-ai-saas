"""Auxiliary output framing; never recover a guessed decision from prose."""
import json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from autonomy import parse_object

class DecisionJSON(unittest.TestCase):
    def test_framing_preserves_exact_model_fields(self):
        decision={'reply':False,'target':'other','reason':'包含 {引用} 和换行\n原文 <think>引用文本</think>','confidence':'high'}
        raw=json.dumps(decision,ensure_ascii=False)
        for value in (raw,'\ufeff'+raw,'```JSON\r\n'+raw+'\r\n```',
                      '~~~json\n'+raw+'\n~~~','<think>私有推理 {不是结果}</think>\n'+raw,
                      '<think>外层 <think>内层</think></think>'+raw,
                      json.dumps(raw)):
            with self.subTest(value=value):self.assertEqual(parse_object(value),decision)

    def test_invalid_ambiguous_and_truncated_outputs_still_fail(self):
        for raw in ('', '[{"reply":true}]','{"reply":true} {"reply":false}',
                    '示例 {"reply":true}，最后选择沉默','{"reply":true',
                    "{'reply': True}",'```json\n{"reply":true}',
                    '<think>{"reply":true}',json.dumps(json.dumps('{"reply":true}'))):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_object(raw)

if __name__=='__main__':unittest.main()
