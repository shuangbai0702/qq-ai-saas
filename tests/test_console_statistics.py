import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('console_statistics', Path(__file__).resolve().parents[1] / 'adapters/astrbot/console_statistics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StatisticsTest(unittest.TestCase):
    def test_read_only_rounds_and_exact_request_metrics(self):
        with tempfile.TemporaryDirectory() as root:
            bot = '12345678-1234-4123-8123-123456789012'
            folder = Path(root) / 'bots' / bot
            folder.mkdir(parents=True)
            database = folder / 'behavior.sqlite'
            with sqlite3.connect(database) as db:
                db.executescript("CREATE TABLE fishbot_shared_context(id INTEGER,state TEXT);INSERT INTO fishbot_shared_context VALUES(1,'active');CREATE TABLE memory_reflection_progress(scope TEXT,summary_watermark INTEGER);INSERT INTO memory_reflection_progress VALUES('bot_shared',2);CREATE TABLE memory_rounds(id INTEGER,scope TEXT);INSERT INTO memory_rounds VALUES(1,'bot_shared'),(3,'bot_shared'),(4,'private:10000'),(5,'bot_shared');")
            db.close()
            rows = [
                dict(kind='wire_request', attemptId='a', wireIndex=1, textChars=900, toolsChars=20, imageCount=1),
                dict(kind='wire_request', attemptId='a', wireIndex=2, textChars=1200, toolsChars=30, imageCount=2),
                dict(kind='wire_request', attemptId='a', wireIndex=2, appendOnlyStatus='verified'),
                dict(kind='request_result', attemptId='a', wireCount=2, inputTokens=0, cachedTokens=0, status='completed'),
                dict(kind='request_result', attemptId='b', wireCount=1, status='failed'),
                dict(kind='request_result', attemptId='probe', wireCount=1, phase='model_probe:models', inputTokens=99),
                dict(kind='request_result', attemptId='other', botId='other', wireCount=1, inputTokens=999),
            ]
            (folder / 'model-diagnostics.jsonl').write_text('\n'.join(json.dumps(dict(botId=bot, at=i+1, phase='foreground', **row)) if 'phase' not in row and 'botId' not in row else json.dumps({'botId':bot,'at':i+1,**row}) for i,row in enumerate(rows))+'\n{"partial":', encoding='utf-8')
            before = database.read_bytes()
            result = module.read_statistics(root, bot)
            self.assertEqual(result['rounds']['count'], 2)
            self.assertEqual([r['id'] for r in result['requests']], ['b','a'])
            self.assertIsNone(result['requests'][0]['inputTokens'])
            self.assertIsNone(result['requests'][0]['textChars'])
            self.assertEqual(result['requests'][1]['inputTokens'], 0)
            self.assertEqual(result['requests'][1]['textChars'], 1200)
            self.assertEqual(result['requests'][1]['imageCount'], 2)
            self.assertTrue(result['partial'])
            self.assertEqual(database.read_bytes(), before)
            missing = module.read_statistics(root, 'missing')
            self.assertIsNone(missing['rounds']['count'])
            self.assertFalse((Path(root)/'bots/missing').exists())


if __name__ == '__main__':
    unittest.main()
