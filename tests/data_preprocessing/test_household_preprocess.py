"""Synthetic inputs only: preserve the canonical budget targets and source files."""
from contextlib import redirect_stdout
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.household_budget.preprocess import run


MAPPING = Path(__file__).resolve().parents[2] / 'scripts/household_budget/category_mapping_v2.csv'
PREFIX = '가계지출_소비지출_'


class HouseholdPreprocessTests(unittest.TestCase):
    def test_roundtrip_fixed_costs_ratios_holdout_and_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / 'raw'
            raw.mkdir()
            mapping = pd.read_csv(MAPPING, encoding='utf-8-sig').fillna('')
            source_col = '원본항목(가계지출_소비지출_ 생략)'
            fixed_col = mapping.loc[mapping['고정지출구분'].eq('고정(필수)'), source_col].iloc[0]
            row = {PREFIX + col: 0 for col in mapping[source_col]}
            row.update({'가구원수': 1, '가구주_연령': 25, '가구구분코드': 1,
                        '가구주_성별코드': 1, '도시읍면부구분코드': 1, '입주형태코드': 1,
                        '소득': 3000, '가계지출_비소비지출금액': 200,
                        '가계지출_소비지출금액': 1150, '가중값': 1,
                        '전세보증금': 0, '월세사글세': 0, '월세평가금액': 0, '자동차보유대수': 0,
                        PREFIX + '식료품비주류음료구입비': 1000,
                        PREFIX + fixed_col: 100,
                        PREFIX + '정보통신_방송시청각콘텐츠이용금액': 50})
            for year in range(2019, 2027):
                valid = dict(row, 조사연월=int(f'{year}14'))
                invalid = dict(valid, 소득=0)
                pd.DataFrame([valid, invalid]).to_csv(raw / f'{year}_household_survey.csv',
                                                     encoding='cp949', index=False)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()}
            output = root / 'processed'
            with redirect_stdout(io.StringIO()):
                run(raw, output, MAPPING)
            train = pd.read_csv(output / 'household_single_20_34_2019_2025.csv')
            holdout = pd.read_csv(output / 'household_single_20_34_2026_holdout.csv')
            excluded = pd.read_csv(output / 'excluded_rows_2019_2025.csv')
            self.assertEqual((len(train), len(holdout), len(excluded)), (7, 1, 7))
            self.assertEqual(set(holdout['연도']), {2026})
            self.assertTrue(train['고정비'].eq(100).all())
            self.assertTrue(train['생활비'].eq(1050).all())
            self.assertTrue(train['문화·여가'].eq(50).all())
            self.assertTrue(train.filter(regex='^비율_').sum(axis=1).sub(1).abs().lt(1e-12).all())
            self.assertEqual((output / MAPPING.name).read_bytes(), MAPPING.read_bytes())
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()})
            with self.assertRaises(FileExistsError):
                run(raw, output, MAPPING)

    def test_missing_inputs_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'processed'
            with self.assertRaises(FileNotFoundError):
                run(folder, output, MAPPING)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
