import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('alpha_generate', ROOT/'local_workspace/scripts/alpha_test/generate.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


class AlphaPersonaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.opening = g.generate()
        cls.expected = g.answers(cls.rows, cls.opening)

    def test_october_regression(self):
        october = self.expected['2026-10']['before']
        self.assertEqual((october['raw_rows'], october['spending_count'], october['living_spent'],
                          october['total_spending']), (147, 73, 930000, 1820000))
        self.assertEqual(october['balances'], dict(A=2160000, B=320000, C=6300000))
        self.assertEqual(october, self.expected['2026-10']['after'])

    def test_demo_changes_only_spending_not_cash(self):
        before, after = (self.expected['2026-11'][s] for s in ('before','after'))
        self.assertEqual(after['total_spending']-before['total_spending'], 40000)
        self.assertEqual(after['living_spent']-before['living_spent'], 40000)
        self.assertEqual(after['spending_count']-before['spending_count'], 2)
        self.assertEqual(after['balances'], before['balances'])

    def test_duplicate_pairs_and_input_boundaries(self):
        self.assertEqual(len({r['source_key'] for r in self.rows}), len(self.rows))
        lookup = {r['source_key']:r for r in self.rows}
        for row in self.rows:
            self.assertLessEqual(row['occurred_on'], '2026-11-05')
            if row['is_duplicate']:
                original=lookup[row['pair_key']]
                self.assertEqual((original['instrument'],row['instrument']),('D','B'))
                self.assertEqual(original['amount'],row['amount'])
                self.assertEqual(original['confirmed_category'],row['confirmed_category'])
            if row['metadata_source']=='store_snapshot':
                self.assertTrue(all(row[k] for k in ('store_id','industry_large','industry_medium','industry_small','sigungu','dong')))
                self.assertTrue(row['legal_dong']=='신림동' or (row['sigungu']=='구로구' and row['dong']=='구로3동'))

    def test_past_amount_bounds_and_future_query(self):
        rules={r[0]:r for r in g.RULES}
        for row in self.rows:
            if row['occurred_on']<'2026-10-01' and row['role'] in rules:
                rule=rules[row['role']]
                self.assertLessEqual(rule[4],row['amount'])
                self.assertLessEqual(row['amount'],rule[5])
        earlier=g.summarize(self.rows,self.opening,'2026-11-03',after=True)
        later=self.expected['2026-11']['after']
        self.assertLess(earlier['total_spending'],later['total_spending'])
        self.assertEqual(g.generate(),(self.rows,self.opening))


if __name__=='__main__': unittest.main()
