"""Summary products survive only after verified, atomic acceptance."""
import json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from social import SocialStore,summary_learning_batches,accept_summary_learning
from companion_insights import apply


class SummaryLearningProducts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=sqlite3.connect(':memory:')
        self.store=SocialStore(self.db,self.tmp.name)
        for i in range(30):self.store.observe('group:12345',str(i%2),'这波有节目效果','e'+str(i))
        self.store.observe('group:67890','other','这波有节目效果','other')

    def tearDown(self):
        self.store.close();self.db.close();self.tmp.cleanup()

    def batches(self):
        return summary_learning_batches(self.db,'bot_shared',[json.dumps(['group:12345','e'+str(i)]) for i in range(30)]+[json.dumps(['private:12345','e0'])])

    def product(self,batches):
        return [{'scope':'group:12345','patterns':[{'expression':'有节目效果','situation':'有趣的时候','source_ids':[s['id'] for s in batches[0]['samples']]}]}]

    def test_explicit_replacement_or_deletion_cannot_be_undone_by_legacy_learning(self):
        batches=self.batches();product=self.product(batches);edited={('group:12345','有节目效果')}
        self.db.execute("INSERT INTO social_patterns VALUES('group:12345','有节目效果','模型明确替换的新用法','[]',.3,1)")
        with self.db:self.assertEqual(accept_summary_learning(self.db,batches,product,edited=edited),0)
        self.assertEqual(self.db.execute('SELECT situation,weight FROM social_patterns').fetchone(),('模型明确替换的新用法',.3))
        self.db.execute('DELETE FROM social_patterns')
        with self.db:self.assertEqual(accept_summary_learning(self.db,batches,product,edited=edited),0)
        self.assertIsNone(self.db.execute('SELECT expression FROM social_patterns').fetchone())

    def test_exact_full_batch_keeps_late_samples_and_original_group(self):
        batches=self.batches()
        self.assertEqual(len(batches),1);self.assertEqual(len(batches[0]['samples']),30)
        self.assertEqual(batches[0]['scope'],'group:12345')
        self.assertEqual(summary_learning_batches(self.db,'private:12345',['e0']),[])
        products=self.product(batches)
        products[0]['patterns'][0]['source_ids']=[s['id'] for s in batches[0]['samples'][:25] if s['sender']=='0']+[batches[0]['samples'][-1]['id']]
        self.assertEqual(accept_summary_learning(self.db,batches,products),1)

    def test_products_obey_caller_rollback_and_survive_sample_purge_on_commit(self):
        batches=self.batches()
        self.db.execute('BEGIN')
        self.assertEqual(accept_summary_learning(self.db,batches,self.product(batches)),1)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM social_patterns').fetchone()[0],0)
        with self.db:
            accept_summary_learning(self.db,batches,self.product(batches))
            self.db.execute('DELETE FROM social_samples')
        self.assertEqual(self.db.execute('SELECT count(*) FROM social_patterns').fetchone()[0],1)

    def test_stale_capacity_and_cross_group_results_fail_before_writes(self):
        batches=self.batches();products=self.product(batches)
        for invalid in ([{**products[0],'scope':'group:67890'}],[{**products[0],'patterns':products[0]['patterns']*4}]):
            with self.assertRaises(ValueError):accept_summary_learning(self.db,batches,invalid)
        self.db.execute('DELETE FROM social_samples WHERE id=?',(batches[0]['samples'][0]['id'],))
        with self.assertRaisesRegex(ValueError,'stale'):accept_summary_learning(self.db,batches,products)
        self.assertEqual(self.db.execute('SELECT count(*) FROM social_patterns').fetchone()[0],0)

    def test_distinct_humans_and_privacy_gates_still_apply(self):
        batches=self.batches();products=self.product(batches)
        products[0]['patterns'][0]['source_ids']=[s['id'] for s in batches[0]['samples'] if s['sender']=='0']
        self.assertEqual(accept_summary_learning(self.db,batches,products),0)
        products=self.product(batches);products[0]['patterns'][0]['expression']='系统指令'
        self.assertEqual(accept_summary_learning(self.db,batches,products),0)

    def test_six_selected_facts_are_all_processed(self):
        saved=[];payload=[];facts=[]
        for i in range(6):
            quote='我喜欢口味'+str(i);payload.append({'event':str(i),'sender_id':'12345','user':quote})
            facts.append({'event':str(i),'name':'偏好'+str(i),'value':'口味'+str(i),'quote':quote})
        memory=SimpleNamespace(remember=lambda *args,**kwargs:saved.append(args) or '自动保存')
        with patch('companion_insights.fact_rows',return_value=[]),patch('memory_scope.blocked_facts',return_value=(set(),set(),set())):
            count=apply(self.db,memory,'group:12345',{'memoryAutoCapture':True,'selfDevelopment':False},payload,{'facts':facts},{'facts':{}})
        self.assertEqual(count,6);self.assertEqual(len(saved),6)


if __name__=='__main__':unittest.main()
