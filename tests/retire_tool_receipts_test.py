"""Minimal ownership transfer uses the original receipt table only."""
import importlib.util,json,sqlite3,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline
spec=importlib.util.spec_from_file_location('receipt_transfer',Path(__file__).resolve().parents[1]/'scripts/retire-tool-receipts.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class Transfer(unittest.TestCase):
    def test_exact_receipt_idempotency_conflict_and_journal_immutability(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'bots'/'bot';folder.mkdir(parents=True);file=folder/'behavior.sqlite'
            with closing(sqlite3.connect(file)) as db,db:
                BotPipeline(db)
                db.execute('CREATE TABLE fishbot_readonly_journals(scope TEXT,version INTEGER,state TEXT,sha TEXT,updated REAL)')
                db.execute("INSERT INTO fishbot_readonly_journals VALUES('bot_shared',2,'ready','unchanged',1)")
            receipt={'account':'owner','aid':'123','status':'confirmed','platformObjectId':'456'}
            record={'bot_id':'bot','tenant_id':'tenant','id':'step','operation':'comment','args_digest':'digest','receipt':receipt,'at':1}
            self.assertEqual(module.transfer(root,[record]),1);self.assertEqual(module.transfer(root,[record]),1)
            with self.assertRaisesRegex(ValueError,'identity_conflict'):
                module.transfer(root,[{**record,'receipt':{**receipt,'platformObjectId':'different'}}])
            with self.assertRaisesRegex(ValueError,'fields_invalid'):
                module.transfer(root,[{**record,'args':{'text':'must not transfer'}}])
            with closing(sqlite3.connect(file)) as db,db:
                self.assertEqual(db.execute('SELECT * FROM fishbot_readonly_journals').fetchall(),[('bot_shared',2,'ready','unchanged',1.0)])
                row=db.execute('SELECT method,state,receipt FROM pipeline_action_effects').fetchall()
                self.assertEqual(len(row),1);self.assertEqual(row[0][:2],('bilibili_comment','confirmed'));self.assertEqual(json.loads(row[0][2]),receipt)


if __name__=='__main__':unittest.main()
